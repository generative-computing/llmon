# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Loading, validating and building span-annotation corpora.

No corpus ships with this repository or the package; this module is how you
load, check and build your own -- see docs/CORPUS.md. docs/labeler.html is a
browser tool that produces the schema below.

Schema, one JSON array of:

    {"key": "mysource_1", "prompt": "<the full prompt text>",
     "complete": true,
     "labels": [{"class": "Instruction", "span": {"start": 0, "end": 12}}, ...]}

`key` must be `<source>_<id>`: the part before the last underscore is the source
name, which is what holdout stratification and per-source reporting group by.
Spans may overlap and nest, and one span may carry several classes.

THE INVARIANT
-------------
`prompt[start:end]` is the span. Offsets are never recomputed from text, because
a prompt can contain the same substring twice and searching for it silently
picks the wrong one. `validate()` checks this on load and refuses rather than
training on misaligned targets.
"""

import collections
import json
import os
import random

from ..taxonomy import NINE, TaxonomyLeak, get as _get_taxonomy

__all__ = [
    "load", "save", "flatten_spans", "clean", "build", "validate",
    "source_of", "composition", "class_counts", "CorpusError",
    "DEFAULT_HOLDOUT_FRACTION", "DEFAULT_SPLIT_SEED",
]

#: Fraction of prompts held out for evaluation.
DEFAULT_HOLDOUT_FRACTION = 0.15

#: Fixed so the split is reproducible from the same inputs. Changing it
#: reshuffles train/holdout and makes previously reported numbers incomparable.
DEFAULT_SPLIT_SEED = 1337


class CorpusError(RuntimeError):
    """A corpus is malformed, misaligned, or in the wrong label space."""


def source_of(key):
    """`"ifeval_1069"` -> `"ifeval"`."""
    return key.rsplit("_", 1)[0]


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load(path, taxonomy=None, validate_offsets=True):
    """Read a corpus, optionally collapsing it into `taxonomy`.

    Collapsing on load is what lets one annotated corpus serve both label
    spaces: the nine-class annotation is the source of truth on disk, and the
    three-class view is derived. It is exact -- deriving the three-class corpus
    this way reproduces a separately-built one byte for byte.
    """
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except FileNotFoundError:
        raise CorpusError(
            f"no corpus at {path}. No corpus ships with this repository; see "
            f"docs/CORPUS.md for the schema and how to build your own."
        ) from None
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{path} is not valid JSON: {exc}") from None

    if not isinstance(rows, list):
        raise CorpusError(f"{path} must hold a JSON array of prompts")

    if taxonomy is not None:
        tax = _get_taxonomy(taxonomy)
        out = []
        for ex in rows:
            new = dict(ex)
            try:
                new["labels"] = tax.collapse_labels(ex["labels"])
            except TaxonomyLeak as exc:
                raise CorpusError(f"{path}, prompt {ex.get('key')!r}: {exc}") from None
            out.append(new)
        rows = out

    if validate_offsets:
        validate(rows, path=path)
    return rows


def save(rows, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    return path


def validate(rows, path="<corpus>", taxonomy=None):
    """Check the offset invariant, key format and (optionally) the label space."""
    problems = []
    seen = set()
    for i, ex in enumerate(rows):
        for required in ("key", "prompt", "labels"):
            if required not in ex:
                problems.append(f"row {i}: missing {required!r}")
        if problems:
            break
        key, prompt = ex["key"], ex["prompt"]
        if key in seen:
            problems.append(f"duplicate key {key!r}")
        seen.add(key)
        if "_" not in key:
            problems.append(f"key {key!r} must be <source>_<id> for stratification")
        for lab in ex["labels"]:
            start, end = lab["span"]["start"], lab["span"]["end"]
            if not (0 <= start < end <= len(prompt)):
                problems.append(
                    f"{key}: span ({start},{end}) outside prompt of length {len(prompt)}"
                )
            elif not prompt[start:end].strip():
                problems.append(f"{key}: span ({start},{end}) is empty or whitespace")
            if taxonomy is not None and lab["class"] not in _get_taxonomy(taxonomy):
                problems.append(
                    f"{key}: class {lab['class']!r} is not in taxonomy "
                    f"{_get_taxonomy(taxonomy).name!r}"
                )
    if problems:
        shown = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise CorpusError(f"{path} failed validation:\n  {shown}{more}")
    return rows


# ---------------------------------------------------------------------------
# Span records
# ---------------------------------------------------------------------------

def flatten_spans(rows):
    """One record per unique (prompt, span), carrying every class on that span.

    A span may carry several classes, so labels are grouped by exact offsets
    rather than emitted one row per label. Used for corpus statistics, and by any
    model that trains on spans rather than whole prompts.
    """
    records = []
    for ex in rows:
        prompt = ex["prompt"]
        by_span = collections.defaultdict(list)
        for lab in ex["labels"]:
            start, end = lab["span"]["start"], lab["span"]["end"]
            cls = lab["class"]
            if cls and cls not in by_span[(start, end)]:
                by_span[(start, end)].append(cls)
        for (start, end), classes in sorted(by_span.items()):
            records.append({
                "key": ex["key"],
                "text": prompt[start:end],
                "span": {"start": start, "end": end},
                "classes": classes,
            })
    return records


# ---------------------------------------------------------------------------
# Cleaning and building
# ---------------------------------------------------------------------------

def clean(rows, taxonomy=NINE, origin="<input>"):
    """Drop or repair rows a model cannot learn from. Returns (kept, report).

    Four anomaly classes, each counted rather than silently swallowed:

      * `complete: false` -> drop the prompt (annotation was never finished).
      * a class outside the taxonomy -> drop the LABEL, then the prompt if
        nothing valid survives. The label space is closed: an off-taxonomy class
        has no BIO tag to train on, and every later load that collapses the
        corpus would refuse it, so one stray label would block the whole file.
      * a span reaching past the end of the prompt -> clip it, and count it.
      * an empty or whitespace-only span -> drop the label.
    """
    tax = _get_taxonomy(taxonomy)
    kept, stats = [], collections.Counter()
    dropped_prompts, dropped_labels, clipped = [], [], []

    for ex in rows:
        key, prompt = ex["key"], ex["prompt"]
        n = len(prompt)

        if not ex.get("complete", True):
            stats["prompt_dropped_incomplete"] += 1
            dropped_prompts.append((key, origin, "complete=false"))
            continue

        labels = []
        for lab in ex["labels"]:
            cls = lab["class"]
            if cls not in tax:
                stats["label_dropped_off_taxonomy"] += 1
                dropped_labels.append((key, origin, cls, f"not in taxonomy {tax.name!r}"))
                continue
            raw_start, raw_end = lab["span"]["start"], lab["span"]["end"]
            start, end = max(0, raw_start), min(n, raw_end)
            if end <= start or not prompt[start:end].strip():
                stats["label_dropped_degenerate"] += 1
                dropped_labels.append(
                    (key, origin, cls, f"empty/whitespace span ({raw_start},{raw_end})"))
                continue
            if (start, end) != (raw_start, raw_end):
                stats["label_clipped_out_of_range"] += 1
                clipped.append((key, origin, cls, (raw_start, raw_end), (start, end), n))
            labels.append({"class": cls, "span": {"start": start, "end": end}})

        if not labels:
            stats["prompt_dropped_no_valid_labels"] += 1
            dropped_prompts.append((key, origin, "no valid labels survived cleaning"))
            continue

        kept.append({"key": key, "prompt": prompt, "labels": labels, "complete": True})

    return kept, {
        "stats": stats,
        "dropped_prompts": dropped_prompts,
        "dropped_labels": dropped_labels,
        "clipped": clipped,
    }


def stratified_holdout_keys(rows, fraction=DEFAULT_HOLDOUT_FRACTION,
                            seed=DEFAULT_SPLIT_SEED):
    """Hold out `fraction` of prompts from EACH source, not from the pool.

    Stratifying matters: an unstratified holdout on this kind of corpus ends up
    dominated by whichever source is largest and easiest, which overstates
    real-world accuracy. Iteration is over a sorted key list so the split is
    reproducible from the same inputs and seed.
    """
    by_source = collections.defaultdict(list)
    for ex in rows:
        by_source[source_of(ex["key"])].append(ex["key"])

    rng = random.Random(seed)
    holdout = set()
    for source in sorted(by_source):
        keys = sorted(by_source[source])
        rng.shuffle(keys)
        take = max(1, round(len(keys) * fraction)) if keys else 0
        holdout.update(keys[:take])
    return holdout


def build(sources, holdout_fraction=DEFAULT_HOLDOUT_FRACTION,
          seed=DEFAULT_SPLIT_SEED, taxonomy=NINE, train_only=()):
    """Merge, clean and split annotation files into a train/holdout pair.

    Args:
        sources: Paths to corpus JSON files, merged in order.
        holdout_fraction: Fraction of each source held out.
        seed: Split seed.
        taxonomy: Label space to validate against. Annotate in the finest space
            you have -- collapsing on load is exact, expanding is not possible.
        train_only: Paths whose prompts always go to train, never holdout. Use
            for data an existing checkpoint already trained on, so a
            before/after comparison stays honest.

    Returns:
        `(train, holdout, report)`.
    """
    tax = _get_taxonomy(taxonomy)
    merged, pinned, report = [], set(), {"per_source": {}, "cleaning": []}

    for path in list(train_only) + list(sources):
        rows = load(path, validate_offsets=False)
        kept, notes = clean(rows, taxonomy=tax, origin=os.path.basename(path))
        report["cleaning"].append((path, notes))
        if path in train_only:
            pinned.update(ex["key"] for ex in kept)
        merged.extend(kept)

    keys = [ex["key"] for ex in merged]
    if len(keys) != len(set(keys)):
        dupes = [k for k, c in collections.Counter(keys).items() if c > 1]
        raise CorpusError(
            f"{len(dupes)} key(s) appear in more than one source file, e.g. "
            f"{dupes[:5]}. Keys must be globally unique or a prompt can land in "
            f"both train and holdout."
        )

    splittable = [ex for ex in merged if ex["key"] not in pinned]
    holdout_keys = stratified_holdout_keys(splittable, holdout_fraction, seed)
    train = [ex for ex in merged if ex["key"] not in holdout_keys]
    holdout = [ex for ex in merged if ex["key"] in holdout_keys]

    validate(train, "train", taxonomy=tax)
    validate(holdout, "holdout", taxonomy=tax)
    overlap = {e["key"] for e in train} & {e["key"] for e in holdout}
    if overlap:
        raise CorpusError(f"train and holdout share {len(overlap)} keys: {sorted(overlap)[:5]}")

    report["train"] = composition(train)
    report["holdout"] = composition(holdout)
    report["train_spans"] = len(flatten_spans(train))
    report["holdout_spans"] = len(flatten_spans(holdout))
    report["class_counts"] = class_counts(flatten_spans(train))
    return train, holdout, report


def composition(rows):
    return dict(sorted(collections.Counter(source_of(r["key"]) for r in rows).items()))


def class_counts(span_records):
    counter = collections.Counter()
    for r in span_records:
        counter.update(r["classes"])
    return dict(sorted(counter.items(), key=lambda kv: -kv[1]))
