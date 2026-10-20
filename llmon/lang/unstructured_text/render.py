# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""LLMON / Shorthand renderer.

Takes the result dict a backend returns (`segment_and_classify` or
`segment_and_classify_tagger`) and converts it to either LLMON or Shorthand.

Entry points
------------
render_llmon(result, use_rewritten=False) -> str
    Returns a .llmon string, with special tokens -- what a model sees.

render_short(result, use_rewritten=False) -> str
    Returns a .short string, the human-friendly Shorthand form.

Both work at the *span* level — each span in each segment becomes one tagged
unit.

Tag mapping
-----------
All directive labels use `instr` as the tag name — `instruction` (written out
in full) is reserved for exec-tag references and is never used here.
Constraint and Output Format use user-defined custom tag names for additional
semantic clarity. Content-plane labels map to `data` or descriptive variants.

  Instruction    -> instr       (the primary task directive)
  Constraint     -> constraint  (user tag: a rule or limit on the output)
  Output Format  -> format      (user tag: required shape/structure of the output)
  Role           -> instr       (persona directive — still a core instr)
  Content        -> data        (plain text payload)
  Quoted Content -> data        (verbatim / fenced payload)
  Structured Data-> data        (machine-structured payload)
  Example        -> data        (illustrative sample)
  Context        -> context     (user tag: background framing — a data-plane payload
                                 that is distinct enough from raw content to name)
  None / unknown -> data        (safe fallback)

Tags are given sequential alphabetic IDs within their tag-name group
(instr:a, instr:b, ..., constraint:a, ..., format:a, ..., data:a, ...).
IDs reset per render call.

LLMON syntax
  opening : <|open|>tag<|:|>id<|close|>
  closing : <|open_end|>   (bare, the canonical end_user_tag -- matches what
            json-to-llmon and short-to-llmon emit)
  body    : the span text (or rewritten_text for the segment if use_rewritten=True
            AND the span is an Instruction span for which a rewrite was produced)

Shorthand syntax
  opening : \\tag:id\\
  closing : /tag:id/
  body    : same text as LLMON
"""

import string

from llmon.lang.common.common import (
    CLOSE_TAG as _CLOSE,
    COLON_SEPARATOR_TAG as _COLON,
    OPEN_END_TAG as _OPEN_END,
    OPEN_TAG as _OPEN,
)

from .taxonomy import NINE, THREE, TaxonomyLeak

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Label -> LLMON tag name. Owned by llmon.lang.unstructured_text.taxonomy, not restated
# here. `instruction` written out in full is reserved for exec-tag references, so
# every directive span uses `instr` or a descriptive user-defined tag name.
#
# The two shipped label spaces have disjoint names -- NINE is capitalised
# ("Instruction"), THREE is lower-case ("instruction") -- so one merged lookup is
# unambiguous, and a caller does not have to tell the renderer which space a
# result came from.
_LABEL_TO_TAG = {**NINE.tag_of, **THREE.tag_of}

# Used ONLY for a span with label None, which is the rule engine's genuine
# "unclassified" outcome. There is deliberately no fallback for an unrecognised
# NON-None label: a `.get(label, "data")` default here is what once let an
# unregistered label space score as near-perfect, because every unknown label
# collapsed onto the same tag and so compared equal to every other. Anything
# unrecognised now raises.
_UNCLASSIFIED_TAG = "data"


# ---------------------------------------------------------------------------
# ID generator
# ---------------------------------------------------------------------------

def _id_generator():
    """Yield a, b, ..., z, aa, ab, ... indefinitely."""
    alphabet = string.ascii_lowercase
    n = 1
    while True:
        # Treat as base-26 with 1-indexed digits (a=1, z=26)
        i = n
        letters = []
        while i > 0:
            i, r = divmod(i - 1, 26)
            letters.append(alphabet[r])
        yield "".join(reversed(letters))
        n += 1


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _tag_for(label):
    """LLMON tag for a label. Strict for anything that is not None."""
    if label is None:
        return _UNCLASSIFIED_TAG
    try:
        return _LABEL_TO_TAG[label]
    except KeyError:
        raise TaxonomyLeak(
            f"no LLMON tag for label {label!r}; known labels are "
            f"{sorted(_LABEL_TO_TAG)}. If this is a new class, add it to a "
            f"Taxonomy in llmon.lang.unstructured_text.taxonomy rather than defaulting it."
        ) from None


def _collect_tagged_spans(result, use_rewritten):
    """Walk segments → spans and produce a flat list of dicts:
        {tag, id, body}
    IDs are assigned per-tag-name (instr:a, constraint:a, format:a, data:a, ...).
    """
    from .rewriter import rewrite_constraint as _rewrite_constraint

    id_counters = {}   # tag_name -> generator
    tagged = []

    for seg in result.get("segments", []):
        spans = seg.get("spans", [])
        seg_rewrite = seg.get("rewritten_text") if use_rewritten else None

        for sp in spans:
            label = sp.get("label") or seg.get("primary_class")
            tag = _tag_for(label)

            if tag not in id_counters:
                id_counters[tag] = _id_generator()
            tag_id = next(id_counters[tag])

            body = sp["text"]

            if use_rewritten:
                if tag == "instr" and seg_rewrite is not None:
                    # Apply instruction rewrite only when this span covers the
                    # whole segment (the rewrite was computed at segment level).
                    if sp["start"] == seg["start"] and sp["end"] == seg["end"]:
                        body = seg_rewrite
                elif tag in ("constraint", "format", "context"):
                    # Apply constraint/format surface normalisation.
                    rw = _rewrite_constraint(sp["text"])
                    if rw is not None:
                        body = rw

            tagged.append({"tag": tag, "id": tag_id, "body": body})

    return tagged


# ---------------------------------------------------------------------------
# Shorthand renderer
# ---------------------------------------------------------------------------

def render_short(result, use_rewritten=False):
    """Render classification result as Shorthand.

    Each span becomes:
        \\tag:id\\ body /tag:id/

    One span per line.
    """
    tagged = _collect_tagged_spans(result, use_rewritten)
    lines = []
    for item in tagged:
        tag_id = f"{item['tag']}:{item['id']}"
        body = item["body"].strip()
        lines.append(f"\\{tag_id}\\ {body} /{tag_id}/")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLMON renderer
# ---------------------------------------------------------------------------

def render_llmon(result, use_rewritten=False):
    """Render classification result as LLMON.

    Each span becomes:
        <|open|>tag<|:|>id<|close|> body <|open_end|>

    The closer is bare, so this is byte-identical to piping render_short's
    output through short-to-llmon.
    """
    tagged = _collect_tagged_spans(result, use_rewritten)
    lines = []
    for item in tagged:
        tag = item["tag"]
        tag_id = item["id"]
        body = item["body"].strip()
        open_tok  = f"{_OPEN}{tag}{_COLON}{tag_id}{_CLOSE}"
        lines.append(f"{open_tok} {body} {_OPEN_END}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI (for quick manual testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys
    from .heuristic import segment_and_classify

    fmt = "short"
    rewrite = False
    path = None
    for arg in sys.argv[1:]:
        if arg in ("--llmon", "-l"):
            fmt = "llmon"
        elif arg in ("--rewrite", "-r"):
            rewrite = True
        elif not arg.startswith("-"):
            path = arg

    if path:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    result = segment_and_classify(text, rewrite=rewrite)

    if fmt == "llmon":
        print(render_llmon(result, use_rewritten=rewrite))
    else:
        print(render_short(result, use_rewritten=rewrite))
