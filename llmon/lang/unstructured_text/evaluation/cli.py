# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`llmon-seg-eval` -- score a backend against an annotated corpus.

    llmon-seg-eval --backend tagger     --file holdout.json
    llmon-seg-eval --backend rules      --file holdout.json --taxonomy nine
    llmon-seg-eval --backend rules      --file holdout.json --boundaries
    llmon-seg-eval --backend tagger     --file holdout.json --json metrics.json

No corpus ships with this repository; bring your own. See docs/CORPUS.md.

READING THE OUTPUT
------------------
The headline "High overlap %" is PRECISION-SHAPED: its denominator is the number
of spans predicted, so a backend that finds more correct spans can score lower on
it than one that finds fewer. Use the per-class recall/precision/F1 table, or
`--json`, for anything you plan to quote. The two scoring modes are also not
comparable to each other -- `--mode outermost` scores against top-level ground
truth, `--mode all` scores against nested labels too, and the tagger is trained
on the nested target while the rules backend emits the flat one.
"""

import argparse
import collections
import json
import os
import re
import sys

# A ground-truth boundary counts as "at a sentence break" when any of these
# separates the two spans; otherwise it is mid-sentence, which is the case rule
# segmentation almost always misses. Kept exactly as first measured -- widening
# or narrowing this reclassifies boundaries between the two buckets and makes the
# numbers incomparable to everything published.
SENT_END = re.compile(r"[.!?;:\n]")

#: A predicted cut within this many characters of a ground-truth edge counts.
BOUNDARY_TOLERANCE = 3


def _pct(a, b):
    return f"{100.0 * a / b:.1f}%" if b else "n/a"


def boundary_report(rows, predict, tolerance=BOUNDARY_TOLERANCE):
    """Recall over ground-truth boundaries, split mid-sentence vs sentence-break.

    This is the metric the token tagger exists for. Rule segmentation finds ~82%
    of boundaries that sit at a sentence break and ~18% of those inside a
    sentence, and mid-sentence boundaries are about a third of all of them -- a
    ceiling no amount of better labelling can lift, because a label can only be
    assigned to a span the segmenter already cut.

    `predict` maps a prompt to an iterable of cut character positions.
    """
    from .harness import outermost_labels

    stats = collections.Counter()
    missed = []
    for ex in rows:
        prompt = ex["prompt"]
        gt = sorted(outermost_labels(ex["labels"]), key=lambda l: l["span"]["start"])
        cuts = set(predict(prompt))
        for a, b in zip(gt, gt[1:]):
            edge = b["span"]["start"]
            mid = not SENT_END.search(prompt[a["span"]["end"]:edge])
            hit = any(abs(c - edge) <= tolerance for c in cuts)
            kind = "mid" if mid else "sent"
            stats[f"{kind}_total"] += 1
            stats[f"{kind}_found"] += hit
            if mid and not hit:
                missed.append((ex["key"], a["class"], b["class"]))
    return stats, missed


def _print_boundaries(rows, predictors):
    print()
    print("  Ground-truth boundary recall (the segmentation ceiling)")
    print(f"  {'segmenter':<34}{'sentence-break':>16}{'MID-SENTENCE':>18}")
    for name, predict in predictors:
        stats, _ = boundary_report(rows, predict)
        print(f"  {name:<34}"
              f"{_pct(stats['sent_found'], stats['sent_total']):>16}"
              f"{_pct(stats['mid_found'], stats['mid_total']):>18}"
              f"   ({stats['sent_found']}/{stats['sent_total']},"
              f" {stats['mid_found']}/{stats['mid_total']})")


def build_parser():
    p = argparse.ArgumentParser(
        prog="llmon-seg-eval",
        description="Score a segmentation backend against an annotated corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("READING THE OUTPUT")[1].strip(),
    )
    p.add_argument("-f", "--file", required=True, metavar="CORPUS",
                   help="annotated corpus JSON (see docs/CORPUS.md)")
    p.add_argument("-b", "--backend", default="tagger",
                   choices=("rules", "tagger"))
    p.add_argument("-t", "--taxonomy", default=None, choices=("three", "nine"),
                   help="label space to score in; the corpus is collapsed to it "
                        "on load (default: the backend's own)")
    p.add_argument("-m", "--model", default=None, metavar="PATH_OR_REPO")
    p.add_argument("--mode", default=None, choices=("all", "outermost"),
                   help="scoring target: 'outermost' = top-level ground truth "
                        "(default for rules), 'all' = include nested labels "
                        "(default for tagger, which is trained on them)")
    p.add_argument("--worst", type=int, default=5,
                   help="show this many worst-scoring prompts (0 = none)")
    p.add_argument("--boundaries", action="store_true",
                   help="also print the boundary-recall table")
    p.add_argument("--boundaries-only", action="store_true",
                   help="print only the boundary table (no model inference for "
                        "the rules row, so this is fast)")
    p.add_argument("--json", metavar="PATH",
                   help="also write machine-readable metrics here")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    from ..taxonomy import NINE, get as get_taxonomy
    from ..training.corpus import load as load_corpus

    # Which label space the chosen backend can actually produce.
    if args.backend == "tagger":
        from ..tagger import TAGGER_TAXONOMY
        native = TAGGER_TAXONOMY
    else:
        native = get_taxonomy(args.taxonomy) if args.taxonomy else NINE

    tax = get_taxonomy(args.taxonomy) if args.taxonomy else native
    mode = args.mode or ("all" if args.backend == "tagger" else "outermost")

    # Collapsing on load is exact, so one nine-class annotation serves both
    # label spaces; see training/corpus.py.
    rows = load_corpus(args.file, taxonomy=None if tax is NINE else tax)

    from . import harness
    # The harness reports a per-class table over ALL_CLASSES and a weaker
    # "same plane" bucket over _COMPATIBLE_GROUPS. Both are properties of the
    # label space being scored, so they are set from it rather than left at the
    # nine-class default.
    harness.ALL_CLASSES = tax.labels
    harness._COMPATIBLE_GROUPS = (
        [{"Instruction", "Constraint", "Output Format"},
         {"Content", "Quoted Content", "Structured Data", "Example"},
         {"Role", "Context"}]
        if tax is NINE else
        # Every collapsed class has its own plane, so there is no
        # weaker-than-exact match left to report.
        []
    )
    harness.LABELED_PATH = args.file

    if args.boundaries_only:
        _print_boundaries(rows, _predictors(args, tax))
        return 0

    classify = _backend_for(args, tax)
    label = f"{args.backend}/{tax.name}"
    summary = harness.run(classify, label, worst_n=args.worst,
                          score_all_spans=(mode == "all"), rows=rows)

    if args.boundaries:
        _print_boundaries(rows, _predictors(args, tax))

    if args.json:
        payload = {
            "backend": args.backend, "taxonomy": tax.name, "mode": mode,
            "corpus": args.file, "prompts": len(rows),
            "summary": summary if isinstance(summary, dict) else None,
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\n  wrote {args.json}", file=sys.stderr)
    return 0


def _backend_for(args, tax):
    """Build the `prompt -> result dict` callable the harness scores."""
    from ..taxonomy import NINE

    if args.backend == "rules":
        from ..heuristic import segment_and_classify
        if tax is NINE:
            return segment_and_classify
        # The rule engine always emits nine-class labels, so collapse before
        # scoring or every span is counted wrong for being in the wrong label
        # space rather than for being wrong.
        return lambda p: tax.collapse_result(segment_and_classify(p))

    from ..tagger import segment_and_classify_tagger
    from ..weights import resolve
    path = resolve("tagger", args.model)
    return lambda p: segment_and_classify_tagger(p, model_path=path)


def _predictors(args, tax):
    """(name, prompt -> cut positions) pairs for the boundary table."""
    from ..heuristic import segment_and_classify

    def _edges(spans):
        # BOTH ends of every span are cut positions. Taking only starts loses the
        # final boundary of each span and undercounts recall.
        cuts = set()
        for sp in spans:
            cuts.add(sp["start"])
            cuts.add(sp["end"])
        return cuts

    def rule_cuts(prompt):
        res = segment_and_classify(prompt)
        return _edges(sp for seg in res["segments"] for sp in seg.get("spans", []))

    out = [("rules (heuristic.py)", rule_cuts)]
    if args.backend == "tagger":
        from ..tagger import tag_text
        from ..weights import resolve
        path = resolve("tagger", args.model)
        out.append((f"tagger ({os.path.basename(path)})",
                    lambda p: _edges(tag_text(p, model_path=path))))
    return out


if __name__ == "__main__":
    sys.exit(main())
