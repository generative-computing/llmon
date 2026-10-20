# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`llmon-segment` -- segment a prompt from stdin or a file.

    echo "Write a poem about dogs in under 50 words." | llmon-segment
    llmon-segment --input prompt.txt --format json
    llmon-segment --backend rules --taxonomy nine --format short
    llmon-segment --repl

Only the rendered result goes to stdout, so `--format json > out.json` is clean;
banners, warnings and the REPL's own chatter all go to stderr.
"""

import argparse
import os
import sys

from . import config

# Quieten the HuggingFace stack before any backend imports it. Undone by
# --verbose, which is the only time its progress bars are useful.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

FORMATS = ("llmon", "short", "json", "spans")


def _read_input(path):
    """Read the prompt, stripping trailing whitespace.

    The strip matters more than it looks. A trailing newline is a real token, so
    `echo "..." | llmon-segment` and the same prompt typed into --repl tokenize
    differently and come back with confidences up to ~3 points apart. For a tool
    people use to learn what the model does, that reads as flakiness. The library
    itself does not strip -- `segment()` is faithful to exactly what it is given
    -- so offsets always index the text that was actually passed in.
    """
    if path is None:
        if sys.stdin.isatty():
            print("reading prompt from stdin; end with Ctrl-D "
                  "(or pass --input FILE, or --repl)", file=sys.stderr)
        return sys.stdin.read().rstrip()
    with open(path, encoding="utf-8") as f:
        return f.read().rstrip()


def _format_spans(result):
    """Human-readable one-line-per-span table -- the quickest way to eyeball it."""
    if not result.spans:
        return "(no spans)"
    width = max(len(sp.label or "?") for sp in result.spans)
    lines = []
    for sp in result.spans:
        flag = "  <- review" if sp.requires_review else ""
        lines.append(f"  {(sp.label or '?'):<{width}}  {sp.confidence:.3f}  "
                     f"{sp.text}{flag}")
    return "\n".join(lines)


def _render(result, fmt, rewrite, allow_injection):
    if fmt == "json":
        import json
        return json.dumps(result.to_dict(), indent=2)
    if fmt == "spans":
        return _format_spans(result)
    if fmt == "short":
        return result.to_short(rewrite=rewrite, allow_injection=allow_injection)
    return result.to_llmon(rewrite=rewrite, allow_injection=allow_injection)


def _llmon_version():
    """The installed llmon version, or "unknown" when running from a bare checkout."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("llmon")
    except PackageNotFoundError:
        return "unknown"


def build_parser():
    p = argparse.ArgumentParser(
        prog="llmon-segment",
        description="Segment a prompt into typed spans and render it as LLMON.",
        epilog="Configuration: see .env.example, or the LLMON_SEG_* environment "
               "variables. Backends and their costs: docs/ARCHITECTURE.md.",
    )
    p.add_argument("-i", "--input", metavar="FILE",
                   help="read the prompt from FILE instead of stdin")
    p.add_argument("-b", "--backend", choices=("tagger", "rules"), default=None,
                   help=f"default {config.default_backend()!r}; "
                        "rules needs no checkpoint and no dependencies")
    p.add_argument("-t", "--taxonomy", choices=("three", "nine"), default=None,
                   help=f"default {config.default_taxonomy()!r}; "
                        "nine requires --backend rules")
    p.add_argument("-f", "--format", choices=FORMATS, default="llmon",
                   help="output format (default llmon; 'short' is Shorthand, "
                        "'spans' is a readable table)")
    p.add_argument("-m", "--model", metavar="PATH_OR_REPO", default=None,
                   help="checkpoint directory or HuggingFace repo id")
    p.add_argument("-r", "--rewrite", action="store_true",
                   help="normalise span text when rendering")
    p.add_argument("--allow-injection", action="store_true",
                   help="render even if the injection tripwire fired")
    p.add_argument("--repl", action="store_true",
                   help="interactive mode: type prompts, see spans, model stays loaded")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="report backend and taxonomy on stderr, and re-enable "
                        "HuggingFace progress output")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s (llmon {_llmon_version()})")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.verbose:
        for var in ("HF_HUB_DISABLE_PROGRESS_BARS", "TRANSFORMERS_VERBOSITY"):
            os.environ.pop(var, None)

    if args.repl:
        from .repl import run_repl
        return run_repl(args)

    from .api import segment, BackendError, InjectionRefused
    from .weights import CheckpointUnavailable

    text = _read_input(args.input)
    if not text.strip():
        print("error: empty input", file=sys.stderr)
        return 2

    try:
        result = segment(text, backend=args.backend, taxonomy=args.taxonomy,
                         model=args.model, rewrite=args.rewrite)
    except (BackendError, CheckpointUnavailable, config.ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.verbose:
        print(f"[{result.backend} / {result.taxonomy}] "
              f"{len(result.spans)} spans, "
              f"{'confident' if result.is_confident else 'some spans flagged'}"
              + (", INJECTION DETECTED" if result.injection_detected else ""),
              file=sys.stderr)

    try:
        print(_render(result, args.format, args.rewrite, args.allow_injection))
    except InjectionRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
