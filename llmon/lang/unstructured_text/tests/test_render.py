# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the LLMON / Shorthand renderers.

The renderer must emit the same LLMON the other generators do. It once wrote
explicit closers (`<|open_end|>instr<|:|>a<|close|>`) while json-to-llmon and
short-to-llmon wrote the bare `<|open_end|>` the grammar defines as
end_user_tag -- so the same prompt produced two different LLMON documents
depending on which tool generated it.
"""

import re

from llmon.lang.shorthand.short_to_llmon import Shorthand_to_LLMON_Converter
from llmon.lang.unstructured_text import render


def _result():
    """A hand-built backend result: three spans over two segments, no model."""
    text = "You are a pirate. Write a poem about dogs. Do not mention cats."
    spans = [
        ("You are a pirate.", "Role"),
        ("Write a poem about dogs.", "Instruction"),
        ("Do not mention cats.", "Constraint"),
    ]
    out = []
    for body, label in spans:
        start = text.index(body)
        out.append({"text": body, "start": start, "end": start + len(body),
                    "label": label})
    return {"segments": [
        {"start": 0, "end": out[1]["end"], "primary_class": "Instruction",
         "spans": out[:2]},
        {"start": out[2]["start"], "end": out[2]["end"],
         "primary_class": "Constraint", "spans": out[2:]},
    ]}


def _short_to_llmon(short):
    """Run Shorthand through short-to-llmon at its defaults, as the CLI does."""
    conv = Shorthand_to_LLMON_Converter()
    out = ""
    for n, line in enumerate(short.splitlines(keepends=True), 1):
        out += conv.process_line(line, n)
        # process_line drops the newline; the CLI's main() puts it back.
        if line.endswith("\n") and not conv.processing_comment():
            out += "\n"
    return out


def test_llmon_closers_are_bare():
    """Every span closes with a bare <|open_end|>, never a named closer."""
    llmon = render.render_llmon(_result())
    assert llmon == (
        "<|open|>instr<|:|>a<|close|> You are a pirate. <|open_end|>\n"
        "<|open|>instr<|:|>b<|close|> Write a poem about dogs. <|open_end|>\n"
        "<|open|>constraint<|:|>a<|close|> Do not mention cats. <|open_end|>"
    )
    assert not re.search(r"<\|open_end\|>[^<\s]", llmon)


def test_llmon_matches_short_to_llmon():
    """render_llmon == render_short piped through short-to-llmon.

    This is what keeps llmon-segment in agreement with the other generators:
    `--format llmon` and `--format short | short-to-llmon` must be identical.
    """
    result = _result()
    assert render.render_llmon(result) == _short_to_llmon(render.render_short(result))
