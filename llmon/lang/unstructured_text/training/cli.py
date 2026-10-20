# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`llmon-seg-train` -- build a corpus, train a backend, or check a corpus.

    llmon-seg-train check       --corpus mine.json
    llmon-seg-train build       --sources a.json b.json --out-dir data/
    llmon-seg-train tagger      --corpus data/train.json --out tagger_ckpt

No corpus ships with this repository; bring your own (docs/CORPUS.md).
`check` and `build` need no ML dependencies; `tagger` needs
`pip install 'llmon[unstructured-text]'`.

A note on reading results afterwards: training-seed variance on a corpus of a few
thousand spans was measured at up to 8.8 points, so a single checkpoint's score is
not a point estimate. Train three seeds and report the mean and the spread.
"""

import argparse
import json
import os
import sys

from .. import config
from ..taxonomy import NINE, get as get_taxonomy
from . import corpus as _corpus


def _cmd_check(args):
    tax = get_taxonomy(args.taxonomy) if args.taxonomy else None
    rows = _corpus.load(args.corpus, taxonomy=tax, validate_offsets=True)
    spans = _corpus.flatten_spans(rows)
    _corpus.validate(rows, args.corpus, taxonomy=tax)
    print(f"  corpus      : {args.corpus}")
    print(f"  prompts     : {len(rows)}")
    print(f"  span records: {len(spans)}")
    print(f"  sources     : {_corpus.composition(rows)}")
    print(f"  classes     : {_corpus.class_counts(spans)}")
    if tax is not None:
        print(f"  taxonomy    : {tax.name} -- all labels valid")
    print("  offset invariant prompt[start:end] == span text: holds")
    return 0


def _cmd_build(args):
    train, holdout, report = _corpus.build(
        args.sources, holdout_fraction=args.holdout_fraction, seed=args.seed,
        taxonomy=get_taxonomy(args.taxonomy or "nine"),
        train_only=tuple(args.train_only or ()),
    )
    os.makedirs(args.out_dir, exist_ok=True)
    train_path = os.path.join(args.out_dir, "train.json")
    holdout_path = os.path.join(args.out_dir, "holdout.json")
    for path in (train_path, holdout_path):
        if os.path.exists(path) and not args.force:
            sys.exit(f"error: {path} exists; pass --force to overwrite")
    _corpus.save(train, train_path)
    _corpus.save(holdout, holdout_path)

    print(f"  train   : {len(train):>5} prompts  {report['train_spans']:>5} spans  "
          f"{report['train']}")
    print(f"  holdout : {len(holdout):>5} prompts  {report['holdout_spans']:>5} spans  "
          f"{report['holdout']}")
    print(f"  classes : {report['class_counts']}")
    for path, notes in report["cleaning"]:
        stats = dict(notes["stats"])
        if stats:
            print(f"  cleaning {os.path.basename(path)}: {stats}")
    print(f"\n  wrote {train_path}\n  wrote {holdout_path}")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in report.items() if k != "cleaning"}, f, indent=2)
        print(f"  wrote {args.report}")
    return 0


def _cmd_train(args):
    tax = get_taxonomy(args.taxonomy) if args.taxonomy else None
    if os.path.exists(args.out) and not args.force:
        sys.exit(f"error: {args.out} exists; pass --force to overwrite")

    from ..tagger import TAGGER_TAXONOMY, train
    if tax is not None and tax is not TAGGER_TAXONOMY:
        sys.exit(f"error: the tagger is {TAGGER_TAXONOMY.name}-class only -- its "
                 f"BIO head carries one tag pair per class, so the label space is "
                 f"fixed at training time. Annotate in the finer space and let it "
                 f"collapse on load; see docs/CORPUS.md.")
    train(args.corpus, args.out, epochs=args.epochs,
          batch_size=args.batch_size, learning_rate=args.learning_rate,
          test_fraction=args.test_fraction, seed=args.seed,
          early_stopping_patience=args.patience, model_name=args.base_model)
    print(f"\n  checkpoint written to {args.out}")
    print(f"  evaluate it:  llmon-seg-eval --backend {args.backend} "
          f"--model {args.out} --file <holdout.json>")
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="llmon-seg-train",
        description="Build a corpus, train a backend, or validate a corpus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Corpus schema: docs/CORPUS.md.  Training guide: docs/TRAINING.md.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="validate a corpus (no ML dependencies)")
    c.add_argument("--corpus", required=True)
    c.add_argument("--taxonomy", choices=("three", "nine"), default=None,
                   help="also assert every label is in this label space")
    c.set_defaults(func=_cmd_check)

    b = sub.add_parser("build", help="merge annotation files into train/holdout")
    b.add_argument("--sources", nargs="+", required=True, metavar="FILE")
    b.add_argument("--out-dir", required=True)
    b.add_argument("--train-only", nargs="*", metavar="FILE",
                   help="files whose prompts always go to train -- use for data an "
                        "existing checkpoint already saw, so before/after stays honest")
    b.add_argument("--holdout-fraction", type=float,
                   default=_corpus.DEFAULT_HOLDOUT_FRACTION)
    b.add_argument("--seed", type=int, default=_corpus.DEFAULT_SPLIT_SEED,
                   help="split seed; changing it makes earlier numbers incomparable")
    b.add_argument("--taxonomy", choices=("three", "nine"), default="nine",
                   help="label space to validate against (default nine: annotate in "
                        "the finest space you have, since collapsing is exact but "
                        "expanding is impossible)")
    b.add_argument("--report", metavar="PATH", help="write the build report as JSON")
    b.add_argument("--force", action="store_true")
    b.set_defaults(func=_cmd_build)

    for name, default_epochs in (("tagger", 4),):
        t = sub.add_parser(name, help=f"train the {name} backend")
        t.add_argument("--corpus", required=True)
        t.add_argument("--out", required=True, metavar="DIR")
        t.add_argument("--taxonomy", choices=("three", "nine"), default=None)
        t.add_argument("--epochs", type=int, default=default_epochs)
        t.add_argument("--batch-size", type=int, default=8)
        t.add_argument("--learning-rate", type=float, default=2e-5)
        t.add_argument("--test-fraction", type=float, default=0.2)
        t.add_argument("--seed", type=int, default=42,
                       help="train several and report mean +/- spread; seed alone "
                            "moved results by up to 8.8 points on a small corpus")
        t.add_argument("--patience", type=int, default=2,
                       help="early-stopping patience (0 disables)")
        t.add_argument("--base-model", default=None, metavar="NAME_OR_DIR",
                       help="base encoder to fine-tune (default ModernBERT-base "
                            "from the Hub). Point at an existing checkpoint's "
                            "encoder/ directory to train without network access.")
        t.add_argument("--force", action="store_true")
        t.set_defaults(func=_cmd_train, backend=name)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (_corpus.CorpusError, config.ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
