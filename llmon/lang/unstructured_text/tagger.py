#!/usr/bin/env python3
# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Token-level BIO tagger over the 3-class taxonomy — the default backend.

WHY THIS EXISTS
---------------
The `rules` backend segments first, with punctuation and structure rules, and then
classifies each span. Measured on the holdout, that segmenter finds 81.5% of
ground-truth boundaries that sit at a sentence break but only **18.3%** of boundaries
that fall MID-SENTENCE -- and mid-sentence boundaries are 31% of all boundaries. A
span covering both an Instruction and its trailing Constraint has no correct label, so
no amount of better labelling can recover from it (docs/ARCHITECTURE.md).

This module deletes the segment-then-classify split: every token is tagged directly,
and spans are decoded from runs of tags. Boundaries therefore never depend on
punctuation, which is exactly the failure mode above. With a corpus of 1353 prompts /
~6000 spans and a 3-class label space, this is an ordinary sequence-labeling problem.

LABEL SCHEME
------------
7 labels: B-/I- for each of instruction, constraint, data, plus O.

O is real, not padding: docs/guidelines.html has annotators leave trailing punctuation
and inter-span whitespace unlabeled, so some tokens genuinely carry no class.

INNERMOST-WINS, AND WHY IT IS THE WHOLE POINT
---------------------------------------------
Ground truth nests: 834 nested pairs survive the 3-class collapse, dominated by
Instruction containing a Constraint. When a token falls inside several spans it takes
the INNERMOST (shortest) one.

That single choice is what produces intra-sentence splits. "Write a poem about dogs
using less than 50 words." is annotated as an Instruction with a nested Constraint;
the `rules` backend emits only the enclosing span (design invariant 2) and so returns
one instruction span. Innermost-wins tags "Write a poem about dogs" instruction and
"using less than 50 words" constraint, which is the split that was being asked for.

Consequence to be aware of: this model therefore targets a DIFFERENT ground truth than
the `rules` backend, which is scored against outermost labels only. Numbers from the
two are not directly comparable -- `llmon-seg-eval --mode` selects which target is
scored, so the comparison is explicit rather than implied.
"""

import json
import math
import os
import re
import sys
import threading
from typing import Any

import torch
from torch import nn

from . import config
from . import injection
from .taxonomy import THREE, TaxonomyLeak, get as _get_taxonomy

MODEL_NAME = "answerdotai/ModernBERT-base"
MAX_SEQ_LEN = config.get_int("MAX_SEQ_LEN")

# Index 0 is O so an all-zeros prediction is "no class", never a confident class.
def tags_for(taxonomy):
    """BIO tag set for a taxonomy: "O" plus B-/I- per class, in label order.

    The order is the head's output-index order, so it is a function of
    `taxonomy.labels` and changing that order invalidates every saved head.
    """
    taxonomy = _get_taxonomy(taxonomy)
    return ("O",) + tuple(
        f"{p}-{c}" for c in taxonomy.labels for p in ("B", "I")
    )


# The tagger is trained on the three-class space only: a per-token BIO head over
# nine classes has 19 tags and far too little support per tag in a corpus this
# size. This is a module-level constant rather than a parameter for that reason,
# but it is recorded in every checkpoint and validated on load all the same.
TAGGER_TAXONOMY = THREE
TAGS = tags_for(TAGGER_TAXONOMY)
TAG_TO_IDX = {t: i for i, t in enumerate(TAGS)}
IGNORE_INDEX = -100  # special tokens / padding: excluded from the loss


def _pick_device():
    return config.get("DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")


# ---------------------------------------------------------------------------
# Encode: char spans -> per-token BIO. Pure function, directly unit-testable.
# ---------------------------------------------------------------------------


def spans_to_tags(offset_mapping, labels):
    """Per-token BIO tag ids for one prompt.

    offset_mapping: (char_start, char_end) per token from a fast tokenizer.
    labels: [{"class": <collapsed class>, "span": {"start", "end"}}, ...]

    Special tokens (HF emits (0, 0)) get IGNORE_INDEX so they contribute no loss.
    A token inside several spans takes the SHORTEST one (innermost-wins).
    """
    # Sort so that, at equal length, assignment is deterministic across runs.
    ranked = sorted(
        ((l["span"]["start"], l["span"]["end"], l["class"]) for l in labels),
        key=lambda t: (t[1] - t[0], t[0], t[2]),
    )
    tags = []
    prev_owner = None
    for cs, ce in offset_mapping:
        if ce <= cs:
            tags.append(IGNORE_INDEX)
            # A special token does not break a run: [CLS] sits before everything and
            # [SEP] after, so treating it as a boundary would emit a spurious B- on
            # the following token.
            continue
        owner = None
        for s, e, cls in ranked:          # shortest first => innermost wins
            if cs < e and ce > s:
                owner = (s, e, cls)
                break
        if owner is None:
            tags.append(TAG_TO_IDX["O"])
            prev_owner = None
            continue
        prefix = "I" if owner == prev_owner else "B"
        tags.append(TAG_TO_IDX[f"{prefix}-{owner[2]}"])
        prev_owner = owner
    return tags


# ---------------------------------------------------------------------------
# Decode: per-token BIO -> char spans
# ---------------------------------------------------------------------------


def tags_to_spans(offset_mapping, tag_ids, text, scores=None):
    """Decode tag ids into a flat, non-overlapping, offset-exact span list.

    Guarantees text[span["start"]:span["end"]] == span["text"] (design invariant 1:
    offsets are never model arithmetic -- they come from offset_mapping, and the only
    adjustment is trimming whitespace inward).

    An I- tag with no matching open span is treated as a B- rather than dropped: a
    stray I- is a model slip, and inventing a span start loses less information than
    silently discarding a labeled token.
    """
    spans = []
    cur = None  # {"cls", "start", "end", "scores": [...]}

    def flush():
        nonlocal cur
        if cur is None:
            return
        s, e = cur["start"], cur["end"]
        # Trim to the labeled text's own extent; annotators exclude trailing
        # punctuation/space and a tokenizer may include it.
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        # Drop spans with no word character at all -- a lone "." or "***" carries no
        # class signal, and guidelines.html has annotators leave trailing punctuation
        # unlabeled, so such a span can never match ground truth. Degenerate 1-2 word
        # spans are handled separately, by absorb_degenerate_spans below.
        if e > s and re.search(r"\w", text[s:e]):
            conf = (sum(cur["scores"]) / len(cur["scores"])) if cur["scores"] else None
            spans.append({
                "start": s, "end": e, "text": text[s:e], "label": cur["cls"],
                "confidence": round(conf, 4) if conf is not None else None,
                # Probability the model put on this span's OPENING tag. For a B- token
                # that is literally "how sure am I a new span starts here", which is the
                # signal merge_adjacent_same_class() gates on -- far better than the
                # span's mean confidence, which says nothing about its left boundary.
                "open_score": (round(cur["open"], 4)
                               if cur.get("open") is not None else None),
                "n_tokens": len(cur["scores"]),
            })
        cur = None

    for i, (cs, ce) in enumerate(offset_mapping):
        if ce <= cs:
            continue                      # special token: neither opens nor closes
        tag = TAGS[tag_ids[i]] if 0 <= tag_ids[i] < len(TAGS) else "O"
        score = scores[i] if scores is not None else None
        if tag == "O":
            flush()
            continue
        prefix, cls = tag.split("-", 1)
        if prefix == "B" or cur is None or cur["cls"] != cls:
            flush()
            cur = {"cls": cls, "start": cs, "end": ce, "scores": [], "open": score}
        else:
            cur["end"] = ce
        if score is not None:
            cur["scores"].append(score)
    flush()
    return spans


# ---------------------------------------------------------------------------
# Over-split repair
# ---------------------------------------------------------------------------

# Sentence terminators. A boundary with one of these between the two spans is a real
# break regardless of what the model thought, so it is never merged away.
_HARD_BREAK = re.compile(r"[.!?;:\n]")

# DEFAULT: OFF (0.0). Swept across 3 seeds on the holdout with training/sweep.py.
# The pass was built to repair this model's ~40% over-split
# and it does not earn its place:
#
#   threshold | holdout F1 | holdout MID-SENTENCE boundary recall
#   off       |    75.5    |  61.9%
#   0.90      |    76.0    |  43.7%   <- F1-optimal, and 18 points WORSE at the one
#                                        metric this model exists to move
#
# Buying +0.5 F1 for -18 pt boundary recall is the wrong trade: many genuine
# ground-truth boundaries sit between two SAME-class spans (29 of 48 remaining
# mid-sentence misses are same-class pairs), so merging them destroys correct cuts.
#
# It also could never have fixed over-splitting: only 34% of adjacent predicted pairs
# are same-class, so the mergeable set is a third of the problem at best.
#
# Kept as opt-in, measured, for a consumer that values precision over boundary recall.
DEFAULT_MERGE_THRESHOLD = config.get_float("MERGE_THRESHOLD")

# Escalation gate for requires_review. Calibrated for THIS model and not shared with
# the rule engine's CONFIDENCE_THRESHOLD: that one gates a weighted sum over matched
# patterns, while this confidence is the MEAN of per-token max-probabilities and lives
# in a different range.
#
# Calibrated on the holdout, 3 seeds pooled (training/sweep.py --calibrate). Confidence is
# well behaved -- accuracy rises monotonically 0% / 2.6% / 11.1% / 38.6% / 56.5% /
# 80.4% by decile -- so a gate is meaningful. Choosing 0.60 by flag PRECISION at low
# volume:
#
#   gate | spans flagged | share of spans | of those, actually wrong
#   0.70 |      610      |      27%       |   94.3%
#   0.60 |      393      |      17%       |   97.2%   <- chosen
#   0.50 |      141      |       6%       |   97.9%
#
# 0.60 keeps the flag almost always right while staying a minority of spans. Nothing
# consumes the flag automatically -- it surfaces as `requires_review` on the public
# result, for a caller or a human.
DEFAULT_ESCALATION_THRESHOLD = config.get_float("ESCALATION_THRESHOLD")


def merge_adjacent_same_class(spans, text, threshold=None):
    """Merge a span into its predecessor when the split between them looks spurious.

    The tagger's failure mode is the mirror image of the rule segmenter's: the rule
    segmenter UNDER-splits about 3-4:1, while this model OVER-splits by roughly 40%
    (a mean of 749 predicted spans across the 3 sweep seeds, against 535 ground-truth
    labels on the holdout), which costs precision.

    A merge happens only when all three hold:
      1. both spans carry the same class -- merging different classes would destroy
         information, and a same-class pair renders to identical LLMON either way, so
         this direction is nearly free;
      2. the text between them contains no sentence terminator -- a "." is a real
         boundary whatever the model predicted;
      3. the second span's OPENING-tag probability is below `threshold` -- i.e. the
         model was not confident a new span began there.

    threshold=0 disables the pass; threshold=1 merges every eligible same-class pair.
    """
    if threshold is None:
        threshold = DEFAULT_MERGE_THRESHOLD
    if not spans or threshold <= 0.0:
        return spans

    out = [dict(spans[0])]
    for sp in spans[1:]:
        prev = out[-1]
        gap = text[prev["end"]:sp["start"]]
        opens = sp.get("open_score")
        if (sp["label"] == prev["label"]
                and not _HARD_BREAK.search(gap)
                and opens is not None and opens < threshold):
            # Token-count weighted, so a long span is not dragged around by a short one.
            n_a, n_b = prev.get("n_tokens") or 1, sp.get("n_tokens") or 1
            ca = prev.get("confidence")
            cb = sp.get("confidence")
            if ca is not None and cb is not None:
                prev["confidence"] = round((ca * n_a + cb * n_b) / (n_a + n_b), 4)
            prev["end"] = sp["end"]
            prev["text"] = text[prev["start"]:sp["end"]]
            prev["n_tokens"] = n_a + n_b
            prev["merged"] = prev.get("merged", 1) + 1
        else:
            out.append(dict(sp))
    return out


# Absorb degenerate spans. ON by default at 0.60, swept across 3 seeds on the holdout
# with training/sweep.py. Unlike the same-class merge this earns its place -- it
# improves recall AND precision:
#
#   absorb< | holdout F1 | holdout MID-bnd
#   off     |    75.5    |      61.9%
#   0.50    |    76.9    |      59.3%
#   0.60    |    78.8    |      55.8%   <- chosen
#   0.80    |    79.7    |      54.2%
#
# 0.60 is deliberately the same value as DEFAULT_ESCALATION_THRESHOLD: spans below it
# are wrong 97% of the time, so a 1-2 word span under that bar is not worth keeping.
# The cost is 6.1 points of mid-sentence boundary recall, leaving 55.8% -- still 3x the
# rule segmenter's 18.3%. Raise toward 0.80 for more precision, lower toward 0 to
# maximise boundary recall.
DEFAULT_ABSORB_MAX_WORDS = config.get_int("ABSORB_MAX_WORDS")
DEFAULT_ABSORB_CONFIDENCE = config.get_float("ABSORB_CONFIDENCE")


def absorb_degenerate_spans(spans, text, max_words=None, confidence=None):
    """Fold a very short, low-confidence span into its more confident neighbour.

    A 1-2 word span -- a bare connective, or a fragment split off from its clause --
    carries no class signal and is essentially never a correct prediction. The tagger
    produces such spans by over-splitting: it cuts
    "You | are a pirate" into a 1-word `instruction` at confidence 0.45 plus a
    `constraint`.

    Unlike merge_adjacent_same_class this crosses class boundaries, which is only safe
    because the span being absorbed is short AND the model was unsure about it: the
    surviving label is the CONFIDENT neighbour's. A hard break in the gap blocks it.

    Runs to a fixed point, since absorbing one span can make two others adjacent.
    """
    if max_words is None:
        max_words = DEFAULT_ABSORB_MAX_WORDS
    if confidence is None:
        confidence = DEFAULT_ABSORB_CONFIDENCE
    if not spans or confidence <= 0.0 or max_words <= 0:
        return spans

    cur = [dict(s) for s in spans]
    for _ in range(len(cur)):           # bounded: each pass removes >= 1 span or stops
        target = None
        for i, sp in enumerate(cur):
            if len(sp["text"].split()) > max_words:
                continue
            if (sp.get("confidence") or 1.0) >= confidence:
                continue
            left = cur[i - 1] if i > 0 and not _HARD_BREAK.search(
                text[cur[i - 1]["end"]:sp["start"]]) else None
            right = cur[i + 1] if i + 1 < len(cur) and not _HARD_BREAK.search(
                text[sp["end"]:cur[i + 1]["start"]]) else None
            if left is None and right is None:
                continue
            best = max((n for n in (left, right) if n is not None),
                       key=lambda n: n.get("confidence") or 0.0)
            if (best.get("confidence") or 0.0) <= (sp.get("confidence") or 0.0):
                continue                # neighbour is no more trustworthy; leave it
            target = (i, best)
            break
        if target is None:
            break
        i, best = target
        sp = cur[i]
        best["start"] = min(best["start"], sp["start"])
        best["end"] = max(best["end"], sp["end"])
        best["text"] = text[best["start"]:best["end"]]
        best["n_tokens"] = (best.get("n_tokens") or 1) + (sp.get("n_tokens") or 1)
        best["absorbed"] = best.get("absorbed", 0) + 1
        cur.pop(i)
    return cur


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class TokenTagger(nn.Module):
    """ModernBERT encoder + a per-token linear head over the BIO tag set.

    No span-indicator embedding: there is no target span
    to point at, because every token is being labeled at once.
    """

    def __init__(self, taxonomy=TAGGER_TAXONOMY, model_name=MODEL_NAME, dropout=0.1):
        """Build a fresh tagger. `taxonomy` fixes the BIO tag set and head width.

        Was `num_tags=len(TAGS)` -- a default argument, so the width froze when
        the class body executed and reassigning the module global afterwards had
        no effect. See taxonomy.py.
        """
        super().__init__()
        from transformers import AutoModel

        self.taxonomy = _get_taxonomy(taxonomy)
        self.tags = tags_for(self.taxonomy)
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden = self.encoder.config.hidden_size
        self.dropout = nn.Dropout(dropout)
        self.head = nn.Linear(hidden, len(self.tags))
        self.loss_fn = nn.CrossEntropyLoss(ignore_index=IGNORE_INDEX)

    def forward(self, input_ids, attention_mask, labels=None):
        out = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        logits = self.head(self.dropout(out.last_hidden_state))
        loss = None
        if labels is not None:
            loss = self.loss_fn(logits.reshape(-1, logits.size(-1)), labels.reshape(-1))
        return {"loss": loss, "logits": logits}

    def save(self, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        self.encoder.save_pretrained(os.path.join(output_dir, "encoder"))
        torch.save({"head": self.head.state_dict(), "tags": list(self.tags),
                    "taxonomy": self.taxonomy.name},
                   os.path.join(output_dir, "head.pt"))

    @classmethod
    def load(cls, output_dir):
        from transformers import AutoModel

        state = torch.load(os.path.join(output_dir, "head.pt"), map_location="cpu")
        taxonomy = _get_taxonomy(state.get("taxonomy", TAGGER_TAXONOMY.name))
        expected = tags_for(taxonomy)
        saved = tuple(state.get("tags", expected))
        if saved != expected:
            raise TaxonomyLeak(
                f"checkpoint {output_dir} has tag set {saved}, but taxonomy "
                f"{taxonomy.name!r} is {expected}. The tag order is baked into "
                "the head and cannot be reinterpreted."
            )
        model = cls.__new__(cls)
        nn.Module.__init__(model)
        model.taxonomy = taxonomy
        model.tags = saved
        model.encoder = AutoModel.from_pretrained(os.path.join(output_dir, "encoder"))
        model.dropout = nn.Dropout(0.0)
        model.head = nn.Linear(model.encoder.config.hidden_size, len(saved))
        model.head.load_state_dict(state["head"])
        model.loss_fn = nn.CrossEntropyLoss(ignore_index=IGNORE_INDEX)
        model.eval()
        return model


class TaggerCheckpointNotFound(FileNotFoundError):
    """Raised instead of silently running an untrained encoder + random head."""


_REQUIRED = ("head.pt", "tokenizer.json", os.path.join("encoder", "config.json"))


def _require_checkpoint(path):
    if not os.path.isdir(path):
        raise TaggerCheckpointNotFound(
            f"tagger checkpoint not found: {path!r}\n"
            "Train one:  llmon-seg-train tagger --corpus <train.json> "
            "--out tagger_ckpt\n"
            "A missing checkpoint must never fall back to a random head -- that "
            "produces confident-looking output that is numerically meaningless."
        )
    missing = [f for f in _REQUIRED if not os.path.exists(os.path.join(path, f))]
    if missing:
        raise TaggerCheckpointNotFound(
            f"tagger checkpoint {path!r} incomplete; missing: {', '.join(missing)}")


_CACHE: dict[str, tuple[TokenTagger, Any]] = {}
# Two threads racing a cold cache would each
# load a ~570 MB encoder. Contended only on the first call per checkpoint.
_CACHE_LOCK = threading.Lock()


def _get(model_path):
    cached = _CACHE.get(model_path)
    if cached is not None:
        return cached
    from transformers import AutoTokenizer

    _require_checkpoint(model_path)
    with _CACHE_LOCK:
        cached = _CACHE.get(model_path)
        if cached is not None:
            return cached
        tok = AutoTokenizer.from_pretrained(model_path)
        model = TokenTagger.load(model_path).to(_pick_device())
        _CACHE[model_path] = (model, tok)
    return model, tok


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def tag_text(text, model_path=None, model=None, tokenizer=None,
             merge_threshold=None, absorb_max_words=None, absorb_confidence=None):
    """Return the decoded span list for one prompt.

    The over-split repair runs by DEFAULT (merge_threshold=None resolves to
    DEFAULT_MERGE_THRESHOLD). Pass 0 to see the raw per-token decode.
    """
    if model is None or tokenizer is None:
        from .weights import resolve
        model, tokenizer = _get(model_path or resolve("tagger"))
    device = next(model.parameters()).device
    enc = tokenizer(text, return_offsets_mapping=True, truncation=True,
                    max_length=MAX_SEQ_LEN, return_tensors="pt")
    with torch.no_grad():
        logits = model(input_ids=enc["input_ids"].to(device),
                       attention_mask=enc["attention_mask"].to(device))["logits"][0]
    probs = torch.softmax(logits, dim=-1)
    conf, pred = probs.max(dim=-1)
    spans = tags_to_spans(enc["offset_mapping"][0].tolist(), pred.tolist(), text,
                          scores=conf.tolist())
    spans = absorb_degenerate_spans(spans, text, max_words=absorb_max_words,
                                    confidence=absorb_confidence)
    return merge_adjacent_same_class(spans, text, threshold=merge_threshold)


def segment_and_classify_tagger(text, model_path=None, threshold=None,
                                merge_threshold=None, absorb_max_words=None,
                                absorb_confidence=None):
    """Same result shape the `rules` backend emits, so the harness, renderer and
    CLI work unchanged.

    One segment covering the whole prompt: this model has no notion of segments, and
    inventing them would imply structure it never predicted.
    """
    threshold = DEFAULT_ESCALATION_THRESHOLD if threshold is None else threshold
    spans = tag_text(text, model_path=model_path, merge_threshold=merge_threshold,
                     absorb_max_words=absorb_max_words,
                     absorb_confidence=absorb_confidence)
    for sp in spans:
        sp["requires_review"] = (sp["confidence"] or 0.0) < threshold
        sp["source"] = "tagger"
        sp["hint_only"] = False
    primary = max(spans, key=lambda s: s["confidence"] or 0.0) if spans else None
    seg = {
        "segment_index": 0,
        "start": 0, "end": len(text), "text": text,
        "spans": spans,
        # Which backend labelled this. Reported on every span and segment so a
        # consumer, or the evaluation harness, can tell rule output from model
        # output in a mixed result.
        "source": "tagger",
        "primary_class": primary["label"] if primary else None,
        "confidence": primary["confidence"] if primary else 0.0,
        "classifications": [],
        "evidence": [],
        "requires_review": any(s["requires_review"] for s in spans),
        # Filled in by injection.scan_segments below. This model has no notion of
        # injection -- it predicts a span type per token -- so the shared stdlib
        # tripwire supplies it. It must not be left hardcoded False: that silently
        # drops injection detection from the default path.
        "injection_detected": False,
        "abstention_reason": "low_token_confidence" if spans and any(
            s["requires_review"] for s in spans) else None,
        # The tagger predicts labels, not planes, so read the plane off the
        # label rather than reporting None.
        "semantic_plane": (TAGGER_TAXONOMY.plane_of[primary["label"]]
                           if primary else None),
        "semantic_confidence": primary["confidence"] if primary else 0.0,
        "structural_confidence": 1.0,
    }
    result = {"segments": [seg], "is_confident_global": not seg["requires_review"]}
    injection.scan_segments(result, text)
    # scan_segments may have set requires_review, so recompute the global flag.
    result["is_confident_global"] = all(
        not sg["requires_review"] for sg in result["segments"])
    return result


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def _load(corpus_path):
    """Load a corpus and collapse it into the tagger's label space.

    The corpus on disk is annotated in the finest taxonomy available, which is
    coarser-grained here: the tagger's BIO head has one tag pair per class, so it
    is three-class only. Collapsing on load is exact, and skipping it means
    spans_to_tags() meets a class it has no tag for and dies with a bare
    KeyError partway into the first epoch.
    """
    from .training.corpus import load
    return load(corpus_path, taxonomy=TAGGER_TAXONOMY)


def _split_by_key(rows, test_fraction=0.2, seed=42):
    if not test_fraction:
        return list(rows), []
    import random

    keys = sorted({r["key"] for r in rows})
    rng = random.Random(seed)
    rng.shuffle(keys)
    n = max(1, int(len(keys) * test_fraction))
    held = set(keys[:n])
    return ([r for r in rows if r["key"] not in held],
            [r for r in rows if r["key"] in held])


def _encode(row, tokenizer, max_len=MAX_SEQ_LEN):
    enc = tokenizer(row["prompt"], return_offsets_mapping=True, truncation=True,
                    max_length=max_len)
    return {"input_ids": enc["input_ids"],
            "attention_mask": enc["attention_mask"],
            "labels": spans_to_tags(enc["offset_mapping"], row["labels"])}


def _collate(batch):
    n = max(len(b["input_ids"]) for b in batch)
    pad = lambda s, v: s + [v] * (n - len(s))  # noqa: E731
    return {
        "input_ids": torch.tensor([pad(b["input_ids"], 0) for b in batch]),
        "attention_mask": torch.tensor([pad(b["attention_mask"], 0) for b in batch]),
        "labels": torch.tensor([pad(b["labels"], IGNORE_INDEX) for b in batch]),
    }


def train(corpus_path, output_dir, epochs=4, batch_size=8, learning_rate=2e-5,
          test_fraction=0.2, seed=42, early_stopping_patience=2, model_name=None):
    """Fine-tune the tagger.

    Batching is by TOKEN BUDGET rather than a fixed record count, for the reason
    documented there: 8 records x 2048 tokens spikes a single batch to 16384 padded
    tokens, which on CPU thrashed into swap instead of merely running slowly.
    """
    import random
    import time

    from transformers import AutoTokenizer

    rows = _load(corpus_path)
    tr, te = _split_by_key(rows, test_fraction, seed)
    print(f"train prompts: {len(tr)}, val prompts: {len(te)}")

    # Resolved at CALL time, not from the class's default argument. TokenTagger's
    # `model_name=MODEL_NAME` default is bound when the class body executes, so
    # reassigning the module global afterwards would silently have no effect -- the
    # same trap documented in taxonomy.py.
    # Threading it explicitly also lets an offline run point at a local encoder.
    model_name = model_name or MODEL_NAME
    print(f"base encoder: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = TokenTagger(model_name=model_name)
    device = _pick_device()
    model.to(device)

    token_budget = batch_size * MAX_SEQ_LEN // 4

    def batches(recs):
        lens = [len(tokenizer(r["prompt"], truncation=True,
                              max_length=MAX_SEQ_LEN)["input_ids"]) for r in recs]
        order = sorted(range(len(recs)), key=lambda i: lens[i])
        out, cur, cur_max = [], [], 0
        for i in order:
            cand = max(cur_max, lens[i])
            if cur and (len(cur) >= batch_size or cand * (len(cur) + 1) > token_budget):
                out.append(cur); cur, cur_max = [], 0
            cur.append(recs[i]); cur_max = max(cur_max, lens[i])
        if cur:
            out.append(cur)
        return out

    train_batches = batches(tr)
    print(f"train batches: {len(train_batches)} "
          f"(sizes {sorted({len(b) for b in train_batches})}, budget {token_budget})")

    opt = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    log_every = max(1, len(train_batches) // 10)
    best, best_epoch, best_state, stale = float("inf"), 0, None, 0

    for epoch in range(epochs):
        model.train()
        rng = random.Random(seed + epoch)
        order = list(range(len(train_batches)))
        rng.shuffle(order)
        total, t0 = 0.0, time.monotonic()
        for step, bi in enumerate(order, 1):
            batch = _collate([_encode(r, tokenizer) for r in train_batches[bi]])
            batch = {k: v.to(device) for k, v in batch.items()}
            out = model(**batch)
            out["loss"].backward()
            opt.step(); opt.zero_grad()
            total += out["loss"].item()
            if step % log_every == 0 or step == len(order):
                el = time.monotonic() - t0
                print(f"  epoch {epoch+1}/{epochs} batch {step}/{len(order)} "
                      f"seq_len={batch['input_ids'].shape[1]} "
                      f"loss={out['loss'].item():.4f} elapsed={el:.1f}s")
        print(f"epoch {epoch+1}/{epochs} avg_loss={total/max(1,len(order)):.4f}")

        if te:
            vl = _val_loss(model, te, tokenizer, device)
            print(f"epoch {epoch+1}/{epochs} val_loss={vl:.4f}")
            if vl < best - 1e-4:
                best, best_epoch = vl, epoch + 1
                best_state = {k: v.detach().cpu().clone()
                              for k, v in model.state_dict().items()}
                stale = 0
            else:
                stale += 1
                print(f"  no improvement over {best:.4f} (epoch {best_epoch}); "
                      f"{stale}/{early_stopping_patience}")
                if early_stopping_patience and stale >= early_stopping_patience:
                    print(f"early stopping; reverting to epoch {best_epoch}")
                    model.load_state_dict(best_state)
                    break

    if te:
        _report(model, te, tokenizer, device)
    model.eval(); model.to("cpu")
    model.save(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"saved tagger to {output_dir}")


def _val_loss(model, rows, tokenizer, device):
    model.eval()
    tot, n = 0.0, 0
    with torch.no_grad():
        for i in range(0, len(rows), 4):
            batch = _collate([_encode(r, tokenizer) for r in rows[i:i + 4]])
            batch = {k: v.to(device) for k, v in batch.items()}
            tot += model(**batch)["loss"].item(); n += 1
    model.train()
    return tot / max(1, n)


def _report(model, rows, tokenizer, device):
    """Per-token tag accuracy on the held-out split. A coarse sanity signal only --
    token accuracy is inflated by the many easy tokens inside long spans, so the
    real metric is span-level and comes from `llmon-seg-eval`."""
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for i in range(0, len(rows), 4):
            batch = _collate([_encode(r, tokenizer) for r in rows[i:i + 4]])
            batch = {k: v.to(device) for k, v in batch.items()}
            pred = model(input_ids=batch["input_ids"],
                         attention_mask=batch["attention_mask"])["logits"].argmax(-1)
            mask = batch["labels"] != IGNORE_INDEX
            correct += ((pred == batch["labels"]) & mask).sum().item()
            total += mask.sum().item()
    model.train()
    print(f"val per-token tag accuracy: {correct}/{total} "
          f"({100.0*correct/max(1,total):.1f}%)")
