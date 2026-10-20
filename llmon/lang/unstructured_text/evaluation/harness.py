#!/usr/bin/env python3
# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scoring harness: an annotated corpus in, a per-prompt and aggregate report out.

`run(classify, label, ...)` scores ANY `prompt -> result dict` callable, so it
works for every backend and for a caller's own model. `llmon-seg-eval` is the
CLI that drives it and is what you normally want -- it handles backend selection,
taxonomy collapsing and the boundary-recall table. Running this module directly
scores the rule backend in its native nine-class space, which is a convenience,
not the full interface.

The per-prompt report covers the prompt text, ground-truth labels, per-segment
classification, sub-segment spans with absolute offsets, span accuracy against
ground truth, and both renderings (Shorthand and LLMON).

The aggregate summary adds:
  - Per-class precision/recall/F1 (which classes are weak)
  - Per-source-prefix breakdown (which prompt source drags accuracy down)
  - Confidence-threshold sensitivity, computed from this run's span confidences
    with no re-inference
  - Worst-N prompts by high-overlap fraction (--worst N, 0 to disable)

READING THE HEADLINE
    "High overlap %" divides by spans PREDICTED, so it is precision-shaped: a
    backend that finds more correct spans can score lower on it than one that
    finds fewer. Use the per-class table or `llmon-seg-eval --json`.

Scoring target:
    By DEFAULT, ground-truth labels that are strictly nested inside a larger
    label are excluded (see outermost_labels). The rules backend emits a flat
    span list and deliberately keeps only the enclosing span when one role sits
    inside another, so scoring against nested labels charged it for spans it is
    designed never to produce -- 8.2% of the held-out labels and 18.6% of the
    training labels.

    Pass --all-spans to score against every label, including nested ones. Use
    that only when evaluating a nesting-capable representation; the two modes
    are not comparable to each other.
"""

import collections
import json
import os
import re
import sys
import time

from ..heuristic import segment_and_classify, CONFIDENCE_THRESHOLD
from ..render import render_llmon, render_short
from ..taxonomy import NINE

# Which classes get a per-class P/R/F1 row. Nine-class by default because that is
# what the rule engine emits; a caller evaluating a different label space rebinds
# this (see llmon.lang.unstructured_text.evaluation.cli), along with _COMPATIBLE_GROUPS.
ALL_CLASSES = NINE.labels
THRESHOLD_GRID = (0.6, 0.7, 0.75, 0.8, 0.85, 0.9)

# The corpus being scored. No corpus ships with this repository, so there is no
# default: `llmon-seg-eval --file` sets it, and so does `--file` when running
# this module directly.
LABELED_PATH = None
WIDTH = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(num, denom):
    return f"{100.0 * num / denom:.1f}%" if denom else "n/a"


def _truncate(text, n=80):
    text = text.replace("\n", "\\n")
    return text[:n - 3] + "..." if len(text) > n else text


def _rule(char="─", width=WIDTH):
    return char * width


def _section(title):
    pad = (WIDTH - len(title) - 2) // 2
    return "─" * pad + f" {title} " + "─" * (WIDTH - len(title) - 2 - pad)


def outermost_labels(gt_labels):
    """Drop ground-truth labels that are strictly nested inside a larger label.

    The rules backend emits a FLAT, non-overlapping span list: when one role sits
    inside another (a Constraint clause inside an Instruction, an Example inside
    an Output Format block) it deliberately keeps only the enclosing span, since
    that is the safer and more useful rendering --

        \\instr\\ plow the fields \\constraint\\ but make the grass blue /constraint/ /instr/

    is not more useful downstream than the plain Instruction span.

    Scoring against every nested label therefore charged the tool for spans it is
    designed never to produce. Measured share of GT labels that are nested inner
    spans: 8.2% of the held-out set, 18.6% of the training set. The distortion
    was heavily class-specific -- 21 of 22 Example labels in the held-out set are
    nested, which is the entire reason Example scored F1 0.074 no matter what
    upstream fix was tried; on the training set 272 nested Constraint labels were
    doing the same to Constraint.

    Excluding them raises exact-tag recall from 68.4% -> 72.1% (held-out) and
    61.0% -> 69.3% (train) with no code change: that gap was measurement
    artifact, not model error.

    Pass --all-spans to score against every label including nested ones when
    specifically evaluating a nesting-capable representation.
    """
    spans = [(lb["span"]["start"], lb["span"]["end"]) for lb in gt_labels]
    kept = []
    for i, lb in enumerate(gt_labels):
        a0, a1 = spans[i]
        nested = any(
            b0 <= a0 and a1 <= b1 and (b1 - b0) > (a1 - a0)
            for j, (b0, b1) in enumerate(spans)
            if j != i
        )
        if not nested:
            kept.append(lb)
    return kept


def load_labeled(path):
    """Yield dicts from a JSON file containing an array of labeled entries."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for obj in data:
        yield obj


def _span_overlap(a_start, a_end, b_start, b_end):
    """Character-level overlap fraction of [b_start, b_end) within [a_start, a_end)."""
    overlap = max(0, min(a_end, b_end) - max(a_start, b_start))
    a_len = a_end - a_start
    return overlap / a_len if a_len > 0 else 0.0


def _match_predicted_to_gt(pred_spans, gt_labels, prompt):
    """For each predicted span, find the best-overlapping GT label entry.

    Returns a list of dicts:
        {pred_label, pred_start, pred_end, gt_label, gt_start, gt_end, overlap}
    """
    results = []
    for sp in pred_spans:
        best_overlap = 0.0
        best_gt = None
        for gt in gt_labels:
            gs, ge = gt["span"]["start"], gt["span"]["end"]
            ov = _span_overlap(sp["start"], sp["end"], gs, ge)
            if ov > best_overlap:
                best_overlap = ov
                best_gt = gt
        results.append({
            "pred_label":  sp.get("label", "?"),
            "pred_start":  sp["start"],
            "pred_end":    sp["end"],
            "pred_text":   prompt[sp["start"]:sp["end"]],
            "gt_label":    best_gt["class"] if best_gt else "(no match)",
            "gt_start":    best_gt["span"]["start"] if best_gt else -1,
            "gt_end":      best_gt["span"]["end"]   if best_gt else -1,
            "gt_text":     (prompt[best_gt["span"]["start"]:best_gt["span"]["end"]]
                            if best_gt else ""),
            "overlap":     round(best_overlap, 3),
        })
    return results


def _label_match(pred_label, gt_label):
    """True when the predicted class is exactly the ground-truth class.

    This compares CLASSES, not the LLMON tags they render to. Comparing tags is
    wrong and silently inflates the headline: the 9-class tag map is not
    injective -- Instruction and Role both render as `instr`, and Content,
    Quoted Content, Structured Data and Example all render as `data` -- so tag
    equality scored Role-predicted-as-Instruction and Example-predicted-as-
    Content as exact matches. It also made the content row of
    _COMPATIBLE_GROUPS permanently unreachable, and mapped the "(no match)"
    sentinel onto the `data` tag, so a predicted data-family span with zero
    ground-truth overlap counted as a match and the four report buckets did not
    sum to the number of predicted spans.

    Fixing this LOWERS every 9-class number and leaves every 3-class number
    untouched -- the 3-class tag map is injective, so there class equality and
    tag equality already agreed.
    """
    return pred_label == gt_label


# Labels that share a semantic plane and are functionally interchangeable for
# classification purposes. Scored as a weaker bucket than an exact class match.
# Consumers that evaluate a taxonomy where no such grouping applies set this to
# [] (see llmon.lang.unstructured_text.evaluation).
_COMPATIBLE_GROUPS = [
    {"Instruction", "Constraint", "Output Format"},
    {"Content", "Quoted Content", "Structured Data", "Example"},
    {"Role", "Context"},
]

def _label_compatible(pred_label, gt_label):
    """True when pred and gt are in the same compatible group (same plane,
    different class) — weaker than _label_match, but not a flat failure."""
    if _label_match(pred_label, gt_label):
        return False  # that's a full match, not just compatible
    return any(pred_label in g and gt_label in g for g in _COMPATIBLE_GROUPS)


# ---------------------------------------------------------------------------
# Per-segment printer
# ---------------------------------------------------------------------------

def print_segment_verbose(seg, prompt, gt_labels):
    """Print full detail for one segment including spans and GT comparison."""
    flags = []
    if seg["injection_detected"]:
        flags.append("⚠ INJECTION")
    src = seg.get("source", "rules")
    if seg["requires_review"]:
        flags.append(f"↑ ESCALATED ({seg.get('abstention_reason', '?')})")
    else:
        flags.append(f"✓ confident [{src}]")
    print(f"\n  ┌ Segment {seg['segment_index']}  "
          f"chars [{seg['start']}:{seg['end']}]  {' '.join(flags)}")
    print(f"  │ Text    : {_truncate(seg['text'], 78)!r}")
    print(f"  │ Class   : {seg['primary_class']}  "
          f"(plane: {seg['semantic_plane']})  "
          f"conf={seg['confidence']}")
    if seg["classifications"]:
        all_cls = "  ".join(f"{c['label']}={c['score']}"
                             for c in seg["classifications"])
        print(f"  │ All cls : {all_cls}")

    # Evidence
    ev = seg.get("evidence", [])
    if ev:
        print(f"  │ Evidence ({len(ev)} hit{'s' if len(ev) != 1 else ''}):")
        for e in ev:
            snippet = e["text"].replace("\n", "\\n")
            if len(snippet) > 35:
                snippet = snippet[:32] + "..."
            print(f"  │   {e['label']:<16} w={e['weight']:<6} "
                  f"[{e['start']}:{e['end']}] {snippet!r}")
    else:
        print(f"  │ Evidence : (none)")

    # Rewrite
    rw = seg.get("rewritten_text")
    if rw:
        print(f"  │ Rewrite  : {_truncate(rw, 78)!r}")

    # Sub-spans
    spans = seg.get("spans", [])
    print(f"  │")
    print(f"  │ Sub-spans ({len(spans)}):")
    match_rows = _match_predicted_to_gt(spans, gt_labels, prompt)
    for i, (sp, mr) in enumerate(zip(spans, match_rows)):
        tag_match  = _label_match(mr["pred_label"], mr["gt_label"])
        tag_compat = _label_compatible(mr["pred_label"], mr["gt_label"])
        match_icon = ("✓" if tag_match    and mr["overlap"] > 0.3 else
                      "≈" if tag_compat   and mr["overlap"] > 0.3 else
                      "~" if mr["overlap"] > 0.1 else "✗")
        sp_text = prompt[sp["start"]:sp["end"]].replace("\n", "\\n")
        if len(sp_text) > 50:
            sp_text = sp_text[:47] + "..."
        print(f"  │   [{i}] {sp['label']:<16} [{sp['start']}:{sp['end']}]  "
              f"conf={sp['confidence']}  src={sp.get('source','?')}")
        print(f"  │       text    : {sp_text!r}")
        if mr["gt_label"] != "(no match)":
            gt_text = mr["gt_text"].replace("\n", "\\n")
            if len(gt_text) > 50:
                gt_text = gt_text[:47] + "..."
            print(f"  │       gt best : {mr['gt_label']:<16} "
                  f"[{mr['gt_start']}:{mr['gt_end']}]  "
                  f"overlap={mr['overlap']}  {match_icon}")
            print(f"  │       gt text : {gt_text!r}")
        else:
            print(f"  │       gt best : (no ground-truth overlap)  ✗")
    print(f"  └{'─' * (WIDTH - 4)}")


# ---------------------------------------------------------------------------
# Ground-truth display
# ---------------------------------------------------------------------------

def print_gt_labels(prompt, gt_labels):
    """Print the ground-truth labeled spans for one prompt."""
    print(f"\nGround-truth labels ({len(gt_labels)}):")
    for lb in gt_labels:
        s, e = lb["span"]["start"], lb["span"]["end"]
        span_text = prompt[s:e].replace("\n", "\\n")
        if len(span_text) > 60:
            span_text = span_text[:57] + "..."
        print(f"  {lb['class']:<16} [{s}:{e}]  {span_text!r}")


# ---------------------------------------------------------------------------
# Span accuracy summary (per-prompt)
# ---------------------------------------------------------------------------

def _per_class_counts(all_pred_spans, gt_labels, prompt, counts):
    """Accumulate per-class TP/FP/FN/support into `counts` (class -> dict).

    TP: a predicted span whose best-overlapping GT label is the SAME class at
    overlap >= 0.5. FP: a predicted span that didn't earn a TP for its own
    class (wrong class, or overlap too low). FN: a GT label of that class with
    no predicted span reaching it at overlap >= 0.5 with the matching class.
    `support` is counted directly from GT labels (NOT tp+fn) -- one predicted
    span can satisfy multiple GT labels of the same class (e.g. a backend
    merges two GT-split sentences into one span), so tp+fn would undercount
    true support whenever that many-GT-to-one-prediction case occurs.
    """
    match_rows = _match_predicted_to_gt(all_pred_spans, gt_labels, prompt)
    for row in match_rows:
        cls = row["pred_label"]
        if cls not in counts:
            continue
        if row["overlap"] >= 0.5 and row["gt_label"] == cls:
            counts[cls]["tp"] += 1
        else:
            counts[cls]["fp"] += 1

    for gt in gt_labels:
        cls = gt["class"]
        if cls not in counts:
            continue
        counts[cls]["support"] += 1
        gs, ge = gt["span"]["start"], gt["span"]["end"]
        hit = any(
            sp.get("label") == cls
            and _span_overlap(gs, ge, sp["start"], sp["end"]) >= 0.5
            for sp in all_pred_spans
        )
        if not hit:
            counts[cls]["fn"] += 1


def _prefix_for_key(key):
    m = re.match(r"([a-zA-Z]+)", key)
    return m.group(1) if m else key


def _span_accuracy_summary(all_pred_spans, gt_labels, prompt):
    """Return a dict with basic span-match counts for this prompt."""
    match_rows = _match_predicted_to_gt(all_pred_spans, gt_labels, prompt)

    # These four buckets are a PARTITION of the predicted spans: every row lands
    # in exactly one, so high + compat + partial + no_match == total_pred. The
    # earlier definitions overlapped and left gaps -- a row with 10-50% overlap
    # and a merely compatible label satisfied none of the four conditions and
    # vanished from the report, so the columns did not sum to the total.
    high_overlap = partial_overlap = compat_overlap = no_match = 0
    for r in match_rows:
        overlap = r["overlap"]
        exact   = _label_match(r["pred_label"], r["gt_label"])
        compat  = _label_compatible(r["pred_label"], r["gt_label"])
        if overlap >= 0.5 and exact:
            high_overlap += 1
        elif overlap >= 0.5 and compat:
            compat_overlap += 1
        elif overlap >= 0.1 and (exact or compat):
            partial_overlap += 1
        else:
            no_match += 1

    return {
        "total_pred": len(all_pred_spans),
        "total_gt":   len(gt_labels),
        "high":       high_overlap,
        "partial":    partial_overlap,
        "compat":     compat_overlap,
        "no_match":   no_match,
    }


# ---------------------------------------------------------------------------
# Main per-prompt printer
# ---------------------------------------------------------------------------

def print_prompt_report(key, prompt, gt_labels, result):
    """Print the complete evaluation report for one prompt."""
    segments = result["segments"]
    all_pred_spans = [sp for seg in segments for sp in seg.get("spans", [])]

    print(_rule("═"))
    print(f"KEY {key}   │   segments: {len(segments)}   │   "
          f"globally confident: {result['is_confident_global']}")
    print(_rule())

    # Raw prompt
    print("\nPROMPT:")
    for line in prompt.splitlines():
        print(f"  {line}")

    # Ground-truth
    print_gt_labels(prompt, gt_labels)

    # Segments + spans
    print(f"\n{_section('MATCHER  →  SEGMENTS  +  SPANS')}")
    for seg in segments:
        print_segment_verbose(seg, prompt, gt_labels)

    # Per-prompt accuracy summary
    acc = _span_accuracy_summary(all_pred_spans, gt_labels, prompt)
    print(f"\n  Span match summary: "
          f"predicted={acc['total_pred']}  gt={acc['total_gt']}  "
          f"high-overlap={acc['high']}  "
          f"compat={acc['compat']}  "
          f"partial={acc['partial']}  "
          f"no-match={acc['no_match']}")

    # Shorthand output
    print(f"\n{_section('SHORTHAND')}")
    short_out = render_short(result, use_rewritten=True)
    for line in short_out.splitlines():
        print(f"  {line}")

    # LLMON output
    print(f"\n{_section('LLMON')}")
    llmon_out = render_llmon(result, use_rewritten=True)
    for line in llmon_out.splitlines():
        print(f"  {line}")

    print()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(classify, stage_label, filter_key=None, worst_n=5, score_all_spans=False,
        rows=None):
    """Run classify() over all labeled prompts and print the full evaluation report."""
    start_time = time.time()

    # Aggregate counters
    total_prompts    = 0
    total_segments   = 0
    total_spans      = 0
    confident_prompts = 0
    flagged_segs     = 0
    # Spans by which backend actually labelled them. Counted at SPAN level, not
    # segment level: a segment's single top-level `source` does not necessarily
    # describe all of its spans.
    span_sources     = collections.Counter()
    span_high        = 0
    span_compat      = 0
    span_partial     = 0
    span_no_match    = 0
    gt_total         = 0

    class_counts = {c: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for c in ALL_CLASSES}
    prefix_stats = {}  # prefix -> {prompts, confident, high, compat, no_match, spans}
    all_span_confidences = []  # every span's confidence, for the threshold grid
    per_prompt_confidences = []  # list of (key, [span confidences]) for the grid
    worst_prompts = []  # (high_fraction, key, acc_dict) for the worst-N callout

    print(_rule("═"))
    print(f"  EVALUATION — {stage_label}")
    print(f"  Labeled corpus: {LABELED_PATH}")
    print(_rule("═"))

    # `rows` lets a caller pass an already-loaded corpus -- which is how a
    # collapsed one gets scored. Reading LABELED_PATH here instead would load
    # the raw file and compare three-class predictions against nine-class
    # ground truth, scoring every single span as a mismatch.
    corpus_rows = rows if rows is not None else load_labeled(LABELED_PATH)
    for obj in corpus_rows:
        key       = obj["key"]
        prompt    = obj["prompt"]
        gt_labels = obj.get("labels", [])
        if not score_all_spans:
            gt_labels = outermost_labels(gt_labels)

        if filter_key is not None and key != filter_key:
            continue

        total_prompts += 1
        result = classify(prompt)
        segments = result["segments"]

        total_segments += len(segments)
        all_spans = [sp for seg in segments for sp in seg.get("spans", [])]
        total_spans += len(all_spans)
        if result["is_confident_global"]:
            confident_prompts += 1
        flagged_segs += sum(1 for s in segments if s["requires_review"])
        span_sources.update(sp.get("source") or "unknown" for sp in all_spans)
        acc = _span_accuracy_summary(all_spans, gt_labels, prompt)
        span_high     += acc["high"]
        span_compat   += acc["compat"]
        span_partial  += acc["partial"]
        span_no_match += acc["no_match"]
        gt_total      += acc["total_gt"]

        _per_class_counts(all_spans, gt_labels, prompt, class_counts)

        prefix = _prefix_for_key(key)
        ps = prefix_stats.setdefault(prefix, {
            "prompts": 0, "confident": 0, "high": 0, "compat": 0,
            "no_match": 0, "spans": 0,
        })
        ps["prompts"] += 1
        ps["confident"] += 1 if result["is_confident_global"] else 0
        ps["high"] += acc["high"]
        ps["compat"] += acc["compat"]
        ps["no_match"] += acc["no_match"]
        ps["spans"] += len(all_spans)

        span_confidences = [sp.get("confidence") for sp in all_spans
                             if sp.get("confidence") is not None]
        all_span_confidences.extend(span_confidences)
        per_prompt_confidences.append((key, span_confidences))

        if acc["total_pred"] > 0:
            high_fraction = acc["high"] / acc["total_pred"]
        else:
            high_fraction = 0.0 if acc["total_gt"] > 0 else 1.0
        worst_prompts.append((high_fraction, key, acc))

        print_prompt_report(key, prompt, gt_labels, result)

    # ---- Aggregate summary ----
    elapsed = time.time() - start_time
    print(_rule("═"))
    print(f"  AGGREGATE SUMMARY — {stage_label}")
    print(_rule())
    print(f"  Total time                 : {elapsed:.2f}s")
    print(f"  Prompts processed          : {total_prompts}")
    print(f"  Total segments             : {total_segments}"
          f"  (avg {total_segments/total_prompts:.1f}/prompt)" if total_prompts else "")
    print(f"  Total predicted spans      : {total_spans}"
          f"  (avg {total_spans/total_prompts:.1f}/prompt)" if total_prompts else "")
    print(f"  GT span labels in corpus   : {gt_total}")
    print()
    print(f"  Span match vs ground-truth:")
    print(f"    High overlap  (≥50%, exact class)   : "
          f"{span_high:<5} {_pct(span_high, total_spans)}")
    print(f"    Compatible    (≥50%, same plane)    : "
          f"{span_compat:<5} {_pct(span_compat, total_spans)}")
    print(f"    Partial       (10-50% overlap)      : "
          f"{span_partial:<5} {_pct(span_partial, total_spans)}")
    print(f"    No match / incompatible mismatch    : "
          f"{span_no_match:<5} {_pct(span_no_match, total_spans)}")
    print()
    print(f"  Globally confident prompts : "
          f"{confident_prompts}  ({_pct(confident_prompts, total_prompts)})")
    print(f"  Segments flagged for review: "
          f"{flagged_segs}  ({_pct(flagged_segs, total_segments)})")
    for source, count in sorted(span_sources.items()):
        print(f"  Spans labelled by {source:<9}: "
              f"{count}  ({_pct(count, total_spans)})")
    print(f"  Confidence threshold       : {CONFIDENCE_THRESHOLD}")
    print(_rule("═"))

    _print_per_class_table(class_counts)
    _print_per_source_table(prefix_stats)
    _print_threshold_table(per_prompt_confidences, total_prompts)
    _print_worst_prompts(worst_prompts, worst_n)

    return summarize(class_counts, total_spans, gt_total,
                     span_high, span_compat, span_partial, span_no_match,
                     total_prompts, total_segments)


def summarize(class_counts, total_spans, gt_total, span_high, span_compat,
              span_partial, span_no_match, total_prompts, total_segments):
    """Machine-readable aggregate, returned by run() and written by --json.

    Recall is `(support - fn) / support`, per class and micro-averaged. It is NOT
    `sum(tp) / sum(support)`: _per_class_counts credits a true positive per
    PREDICTED span but counts support per GROUND-TRUTH label, so that ratio is
    not a recall at all and can exceed 100%. It did, and it produced a published
    conclusion that had to be retracted.

    `high_overlap_pct` is reported because it is the on-screen headline, but it is
    precision-shaped -- its denominator is spans predicted -- so prefer
    `micro.recall` and `micro.f1` for anything comparative.
    """
    per_class, tp_sum, fp_sum, fn_sum, support_sum = {}, 0, 0, 0, 0
    for cls, c in class_counts.items():
        tp, fp, fn, support = c["tp"], c["fp"], c["fn"], c["support"]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = (support - fn) / support if support else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        per_class[cls] = {"support": support, "tp": tp, "fp": fp, "fn": fn,
                          "precision": round(precision, 4),
                          "recall": round(recall, 4), "f1": round(f1, 4)}
        tp_sum += tp; fp_sum += fp; fn_sum += fn; support_sum += support

    precision = tp_sum / (tp_sum + fp_sum) if (tp_sum + fp_sum) else 0.0
    recall = (support_sum - fn_sum) / support_sum if support_sum else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)

    return {
        "prompts": total_prompts,
        "segments": total_segments,
        "predicted_spans": total_spans,
        "gt_labels": gt_total,
        "buckets": {"high": span_high, "compatible": span_compat,
                    "partial": span_partial, "no_match": span_no_match},
        "high_overlap_pct": round(100.0 * span_high / total_spans, 2) if total_spans else 0.0,
        "micro": {"precision": round(100 * precision, 2),
                  "recall": round(100 * recall, 2),
                  "f1": round(100 * f1, 2)},
        "per_class": per_class,
    }


# ---------------------------------------------------------------------------
# New aggregate report sections
# ---------------------------------------------------------------------------

def _print_per_class_table(class_counts):
    print()
    print(_section("PER-CLASS PRECISION / RECALL"))
    print(f"  {'Class':<18}{'Support':>8}{'TP':>6}{'FP':>6}{'FN':>6}"
          f"{'Precision':>11}{'Recall':>9}{'F1':>7}")
    for cls in ALL_CLASSES:
        c = class_counts[cls]
        tp, fp, fn, support = c["tp"], c["fp"], c["fn"], c["support"]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        print(f"  {cls:<18}{support:>8}{tp:>6}{fp:>6}{fn:>6}"
              f"{precision:>11.3f}{recall:>9.3f}{f1:>7.3f}")
    print(_rule())


def _print_per_source_table(prefix_stats):
    print()
    print(_section("PER-SOURCE-PREFIX BREAKDOWN"))
    print(f"  {'Prefix':<12}{'Prompts':>9}{'Confident':>11}{'Spans':>8}"
          f"{'High%':>9}{'Compat%':>10}{'NoMatch%':>11}")
    for prefix in sorted(prefix_stats):
        ps = prefix_stats[prefix]
        print(f"  {prefix:<12}{ps['prompts']:>9}"
              f"{_pct(ps['confident'], ps['prompts']):>11}"
              f"{ps['spans']:>8}"
              f"{_pct(ps['high'], ps['spans']):>9}"
              f"{_pct(ps['compat'], ps['spans']):>10}"
              f"{_pct(ps['no_match'], ps['spans']):>11}")
    print(_rule())


def _print_threshold_table(per_prompt_confidences, total_prompts):
    print()
    print(_section("CONFIDENCE THRESHOLD SENSITIVITY"))
    print(f"  {'Threshold':<12}{'Globally confident':>20}{'Escalated segments':>20}")
    for t in THRESHOLD_GRID:
        confident = 0
        escalated = 0
        total_segs_like = 0
        for _, confidences in per_prompt_confidences:
            if not confidences:
                continue
            if all(c >= t for c in confidences):
                confident += 1
            escalated += sum(1 for c in confidences if c < t)
            total_segs_like += len(confidences)
        marker = "  <- current" if abs(t - CONFIDENCE_THRESHOLD) < 1e-9 else ""
        print(f"  {t:<12}{_pct(confident, total_prompts):>20}"
              f"{_pct(escalated, total_segs_like):>20}{marker}")
    print(_rule())


def _print_worst_prompts(worst_prompts, worst_n):
    if worst_n <= 0 or not worst_prompts:
        return
    print()
    print(_section(f"WORST {worst_n} PROMPTS (by high-overlap fraction)"))
    worst_prompts.sort(key=lambda t: t[0])
    for high_fraction, key, acc in worst_prompts[:worst_n]:
        print(f"  {key:<20}  high-overlap={high_fraction:.2f}  "
              f"predicted={acc['total_pred']}  gt={acc['total_gt']}  "
              f"compat={acc['compat']}  no-match={acc['no_match']}")
    print(_rule())


# ---------------------------------------------------------------------------
# CLI
#
# Deliberately minimal: this module is a LIBRARY that `llmon-seg-eval` drives, and
# that CLI is where backend selection, taxonomy handling and the boundary report
# live. Running this module directly is a convenience for the rules backend only.
# ---------------------------------------------------------------------------

def _parse_args(argv):
    filter_key = None
    score_all_spans = False
    labeled_path = LABELED_PATH
    worst_n = 5
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("--key", "-k"):
            i += 1
            if i < len(argv):
                filter_key = argv[i]
        elif arg in ("--file", "-f"):
            i += 1
            if i < len(argv):
                labeled_path = os.path.abspath(argv[i])
        elif arg == "--all-spans":
            score_all_spans = True
        elif arg == "--worst":
            i += 1
            if i < len(argv):
                worst_n = int(argv[i])
        elif arg in ("--help", "-h"):
            print(__doc__)
            raise SystemExit(0)
        else:
            print(f"unknown argument: {arg!r} (use --help, or llmon-seg-eval "
                  f"for the full interface)", file=sys.stderr)
            raise SystemExit(2)
        i += 1
    if labeled_path is None:
        print("error: pass --file CORPUS (no corpus ships with this repository; "
              "see docs/CORPUS.md)", file=sys.stderr)
        raise SystemExit(2)
    return filter_key, labeled_path, worst_n, score_all_spans


if __name__ == "__main__":
    filter_key, LABELED_PATH, worst_n, score_all_spans = _parse_args(sys.argv)
    run(lambda p: segment_and_classify(p, rewrite=True),
        "rules (nine-class)", filter_key=filter_key, worst_n=worst_n,
        score_all_spans=score_all_spans)
