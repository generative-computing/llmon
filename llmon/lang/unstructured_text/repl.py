# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Interactive mode for `llmon-segment --repl`.

Loads the model once and keeps it, so after the first prompt each result is
immediate. Built for someone learning LLMON: type a prompt, see how it was cut
up and what each piece was called.

    :help              list commands
    :spans :short :llmon :json       change output format
    :backend rules|tagger
    :taxonomy three|nine
    :paste             multi-line input, end with a blank line
    :quit
"""

import sys

_BANNER = """\
llmon-segment interactive mode.  :help for commands, :quit to exit.
Type a prompt and press Enter; :paste for multi-line input."""

_HELP = """\
  :help                        this message
  :spans | :short | :llmon | :json
                               output format (currently {fmt})
  :backend rules|tagger        which backend to use (currently {backend})
  :taxonomy three|nine         label space (currently {taxonomy})
  :paste                       multi-line input; end with an empty line
  :quit | :q                   exit
Anything else is treated as a prompt."""


def _read_paste():
    print("  (paste; end with an empty line)", file=sys.stderr)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def run_repl(args):
    from . import config
    from .api import segment, BackendError, InjectionRefused
    from .cli import _render
    from .weights import CheckpointUnavailable

    state = {
        "fmt": args.format if args.format != "llmon" else "spans",
        "backend": args.backend or config.default_backend(),
        "taxonomy": args.taxonomy or config.default_taxonomy(),
    }

    print(_BANNER, file=sys.stderr)
    print(f"  backend={state['backend']} taxonomy={state['taxonomy']} "
          f"format={state['fmt']}", file=sys.stderr)

    def show(text):
        if not text.strip():
            return
        try:
            result = segment(text, backend=state["backend"],
                             taxonomy=state["taxonomy"], model=args.model,
                             rewrite=args.rewrite)
        except (BackendError, CheckpointUnavailable, config.ConfigError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return
        if result.injection_detected:
            print("  ** injection tripwire fired: "
                  + ", ".join(repr(h.get("text", ""))
                              for h in result.injection_evidence[:3])
                  + "\n  ** (a heuristic keyword match, not a security verdict)",
                  file=sys.stderr)
        try:
            print(_render(result, state["fmt"], args.rewrite,
                          allow_injection=True))
        except InjectionRefused as exc:      # pragma: no cover - allow=True above
            print(f"error: {exc}", file=sys.stderr)

    while True:
        try:
            line = input("llmon> ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 0

        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith(":"):
            cmd, _, rest = stripped[1:].partition(" ")
            cmd, rest = cmd.lower(), rest.strip().lower()
            if cmd in ("quit", "q", "exit"):
                return 0
            if cmd == "help":
                print(_HELP.format(**state), file=sys.stderr)
            elif cmd in ("spans", "short", "llmon", "json"):
                state["fmt"] = cmd
                print(f"  format={cmd}", file=sys.stderr)
            elif cmd == "backend":
                if rest in ("rules", "tagger"):
                    state["backend"] = rest
                    print(f"  backend={rest}", file=sys.stderr)
                else:
                    print("  usage: :backend rules|tagger", file=sys.stderr)
            elif cmd == "taxonomy":
                if rest in ("three", "nine"):
                    state["taxonomy"] = rest
                    print(f"  taxonomy={rest}", file=sys.stderr)
                else:
                    print("  usage: :taxonomy three|nine", file=sys.stderr)
            elif cmd == "paste":
                show(_read_paste())
            else:
                # Never fall through to classifying an unknown command -- a typo
                # would come back as a confident segmentation of ":backedn".
                print(f"  unknown command :{cmd} -- try :help", file=sys.stderr)
            continue

        show(line)
