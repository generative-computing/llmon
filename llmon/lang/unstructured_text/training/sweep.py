#!/usr/bin/env python3
# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Choose the tagger's merge and escalation thresholds by measurement, not intuition.

Two passes:

  --dump    run the model once per (seed, corpus) and cache the RAW, unmerged span
            predictions to JSON. Needs a GPU/checkpoint; this is the expensive part.
  --sweep   replay the cached predictions at many merge thresholds and score each with
            the evaluation harness's OWN matching functions, so the numbers are
            produced by the same code that scores every backend.

Splitting it this way matters: the merge pass is pure post-processing, so the model
never has to run more than once. It also means the sweep is reproducible offline.

Every pass reads the held-out corpus it scores against from --holdout. No corpus
ships with this repository; see docs/CORPUS.md.

    python -m llmon.lang.unstructured_text.training.sweep --holdout holdout.json \
        --dump --models tagger_ckpt_s42,...
    python -m llmon.lang.unstructured_text.training.sweep --holdout holdout.json --sweep
"""
import argparse
import collections
import json
import os
import statistics
import sys

#: Default location of the raw-prediction cache, relative to the working directory.
CACHE = os.path.join("out", "tagger", "raw_predictions.json")
GRID = [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.99, 1.0]


def _load_rows(path):
    """The held-out corpus in the tagger's own label space, collapsed on load."""
    from .corpus import load
    return load(path, taxonomy="three")


def do_dump(models, holdout_path, out_path):
    from ..tagger import tag_text

    rows = _load_rows(holdout_path)
    cache = {}
    for model in models:
        preds = {}
        for ex in rows:
            # merge_threshold=0 -> raw decode, so the sweep starts from unmerged.
            preds[ex["key"]] = tag_text(ex["prompt"], model_path=model,
                                        merge_threshold=0.0)
        cache[f"{os.path.basename(model)}|holdout"] = preds
        print(f"  dumped {os.path.basename(model)}: "
              f"{sum(len(v) for v in preds.values())} raw spans")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    print(f"wrote {out_path}")


def score(rows_by_key, preds, classes):
    """Recall / precision / F1 using the evaluation harness's own counting functions."""
    from ..evaluation.harness import _per_class_counts, outermost_labels

    counts = {c: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for c in classes}
    for key, ex in rows_by_key.items():
        gt = outermost_labels(ex["labels"])
        _per_class_counts(preds.get(key, []), gt, ex["prompt"], counts)
    tp = sum(c["tp"] for c in counts.values())
    fp = sum(c["fp"] for c in counts.values())
    fn = sum(c["fn"] for c in counts.values())
    sup = sum(c["support"] for c in counts.values())
    r = 100.0 * (sup - fn) / sup if sup else 0.0
    p = 100.0 * tp / (tp + fp) if (tp + fp) else 0.0
    return r, p, (2 * r * p / (r + p) if (r + p) else 0.0), counts


def do_sweep(holdout_path, cache_path):
    from ..tagger import TAGGER_TAXONOMY, merge_adjacent_same_class

    cache = json.load(open(cache_path, encoding="utf-8"))
    rows = {e["key"]: e for e in _load_rows(holdout_path)}
    models = sorted({k.split("|")[0] for k in cache if k.endswith("|holdout")})

    L = []
    w = L.append
    w("# Tagger merge-threshold sweep")
    w("")
    w("Scored with the evaluation harness's own `_per_class_counts` and")
    w("`outermost_labels`, so these numbers come from the same code that scores every")
    w("backend. Recall = `(support-fn)/support`.")
    w("")

    best = None
    w("## holdout")
    w("")
    w("| merge threshold | spans | recall | precision | **F1** |")
    w("|---|---|---|---|---|")
    for t in GRID:
        rs, ps, fs, ns = [], [], [], []
        for m in models:
            preds = cache[f"{m}|holdout"]
            merged = {k: merge_adjacent_same_class(
                          v, rows[k]["prompt"], threshold=t)
                      for k, v in preds.items()}
            r, p, f, _ = score(rows, merged, TAGGER_TAXONOMY.labels)
            rs.append(r); ps.append(p); fs.append(f)
            ns.append(sum(len(v) for v in merged.values()))
        label = {0.0: " (off)", 1.0: " (always)"}.get(t, "")
        w(f"| {t:.2f}{label} | {statistics.mean(ns):.0f} | "
          f"{statistics.mean(rs):.1f}% | {statistics.mean(ps):.1f}% | "
          f"**{statistics.mean(fs):.1f}** |")
        cand = (statistics.mean(fs), t, statistics.mean(rs),
                statistics.mean(ps), statistics.mean(ns))
        if best is None or cand[0] > best[0]:
            best = cand
    w("")

    f1, t, r, p, n = best
    w("## Chosen default")
    w("")
    w(f"`DEFAULT_MERGE_THRESHOLD = {t:.2f}` — best holdout F1 ({f1:.1f}) across "
      f"{len(models)} seeds:")
    w(f"recall {r:.1f}%, precision {p:.1f}%, {n:.0f} spans emitted.")
    w("")
    off = None
    for line in L:
        if line.startswith("| 0.00 (off)"):
            off = line
    if off:
        w(f"For reference, with the pass **off**: `{off.strip()}`")
        w("")
    print("\n".join(L))
    return t


def do_calibration(holdout_path, cache_path):
    """Confidence calibration, to set the escalation gate from data.

    A gate is only meaningful against the confidence it reads: this model's is a mean
    over per-token probabilities, which is not on the same scale as the rule engine's
    weighted sum. Bucketing accuracy by confidence shows where a useful cut is.
    """
    from ..evaluation.harness import _span_overlap, outermost_labels
    from ..tagger import (TAGGER_TAXONOMY, merge_adjacent_same_class,
                      DEFAULT_MERGE_THRESHOLD)

    cache = json.load(open(cache_path, encoding="utf-8"))
    rows = {e["key"]: e for e in _load_rows(holdout_path)}
    buckets = collections.defaultdict(lambda: [0, 0])
    for k, preds in cache.items():
        if not k.endswith("|holdout"):
            continue
        for key, spans in preds.items():
            ex = rows[key]
            gt = outermost_labels(ex["labels"])
            for sp in merge_adjacent_same_class(spans, ex["prompt"],
                                                DEFAULT_MERGE_THRESHOLD):
                ok = any(g["class"] == sp["label"]
                         and _span_overlap(g["span"]["start"], g["span"]["end"],
                                           sp["start"], sp["end"]) >= 0.5
                         for g in gt)
                c = sp.get("confidence") or 0.0
                lo = min(int(c * 10) / 10, 0.9)
                buckets[lo][0] += ok
                buckets[lo][1] += 1

    print()
    print("## Confidence calibration (holdout, all seeds pooled, after merging)")
    print()
    print("| confidence | spans | correct | accuracy |")
    print("|---|---|---|---|")
    for lo in sorted(buckets):
        ok, n = buckets[lo]
        print(f"| {lo:.1f}-{lo+0.1:.1f} | {n} | {ok} | {100.0*ok/n:.1f}% |")
    print()
    cum_n = cum_ok = 0
    print("Cumulative accuracy of everything AT OR ABOVE a gate — pick the gate where")
    print("the spans below it are meaningfully worse than the ones above:")
    print()
    print("| gate | spans kept | accuracy above | spans escalated | accuracy below |")
    print("|---|---|---|---|---|")
    tot_ok = sum(v[0] for v in buckets.values())
    tot_n = sum(v[1] for v in buckets.values())
    for gate in sorted(buckets, reverse=True):
        cum_ok += buckets[gate][0]; cum_n += buckets[gate][1]
        bel_ok, bel_n = tot_ok - cum_ok, tot_n - cum_n
        print(f"| {gate:.1f} | {cum_n} | {100.0*cum_ok/max(1,cum_n):.1f}% | {bel_n} | "
              f"{100.0*bel_ok/max(1,bel_n):.1f}% |" if bel_n else
              f"| {gate:.1f} | {cum_n} | {100.0*cum_ok/max(1,cum_n):.1f}% | 0 | n/a |")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdout", required=True, metavar="CORPUS",
                    help="held-out corpus to score against (see docs/CORPUS.md)")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--models", default="tagger_ckpt_s42,tagger_ckpt_s1,tagger_ckpt_s7")
    ap.add_argument("--cache", default=CACHE)
    args = ap.parse_args(argv)

    if args.dump:
        do_dump([m.strip() for m in args.models.split(",") if m.strip()],
                args.holdout, args.cache)
    if args.sweep:
        do_sweep(args.holdout, args.cache)
    if args.calibrate:
        do_calibration(args.holdout, args.cache)
    if not (args.dump or args.sweep or args.calibrate):
        ap.error("pass --dump and/or --sweep and/or --calibrate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
