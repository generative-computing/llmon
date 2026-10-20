#!/usr/bin/env python3
# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Behavioral checks for the rule-based rewriter.

Run:  python test_rewriter.py
"""

import sys
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from llmon.lang.unstructured_text.rewriter import rewrite, rewrite_constraint



# ---------------------------------------------------------------------------
# Clean imperatives: no rewrite needed.
# ---------------------------------------------------------------------------

def test_no_rewrite_for_imperatives():
    assert rewrite("Summarize the report.") is None, "no-rewrite: plain imperative returns None"
    assert rewrite("Write a poem about spring. Keep it under 10 lines.") is None, "no-rewrite: imperative with constraint returns None"
    assert rewrite("") is None, "no-rewrite: empty string returns None"


# ---------------------------------------------------------------------------
# Polite opener transforms.
# ---------------------------------------------------------------------------

def test_can_you():
    r = rewrite("Can you write a poem about spring?")
    assert r is not None, "can-you: not None"
    assert r is not None and r.startswith("Write"), "can-you: starts with Write"
    assert r is not None and "Can you" not in r, "can-you: no leading 'Can you'"

def test_could_you():
    r = rewrite("Could you explain the theory of relativity?")
    assert r is not None, "could-you: not None"
    assert r is not None and "Could you" not in r, "could-you: no leading 'Could you'"

def test_please():
    r = rewrite("Please summarize the article.")
    assert r is not None, "please: not None"
    assert r is not None and r.startswith("Summarize"), "please: starts with Summarize"

def test_your_task():
    r = rewrite("Your task is to translate the following text.")
    assert r is not None, "your-task: not None"
    assert r is not None and r.startswith("Translate"), "your-task: starts with Translate"

def test_i_want():
    r = rewrite("I want you to explain photosynthesis.")
    assert r is not None, "i-want: not None"
    assert r is not None and r.startswith("Explain"), "i-want: starts with Explain"

def test_i_need():
    r = rewrite("I need you to generate a summary.")
    assert r is not None, "i-need: not None"
    assert r is not None and r.startswith("Generate"), "i-need: starts with Generate"

def test_i_would_like():
    r = rewrite("I would like you to write a cover letter.")
    assert r is not None, "i-would-like: not None"
    assert r is not None and r.startswith("Write"), "i-would-like: starts with Write"


# ---------------------------------------------------------------------------
# Why/What/How interrogatives.
# ---------------------------------------------------------------------------

def test_why():
    r = rewrite("Why is the sky blue?")
    assert r is not None, "why-is: not None"
    assert r is not None and r.startswith("Explain why"), "why-is: starts with Explain why"

def test_why_does():
    r = rewrite("Why does water boil at 100 degrees?")
    assert r is not None, "why-does: not None"
    assert r is not None and r.startswith("Explain why"), "why-does: starts with Explain why"

def test_what_is():
    r = rewrite("What is the capital of France?")
    assert r is not None, "what-is: not None"
    assert r is not None and r.startswith("Explain what"), "what-is: starts with Explain what"

def test_what_are():
    r = rewrite("What are the main causes of climate change?")
    assert r is not None, "what-are: not None"
    assert r is not None and r.startswith("Explain what"), "what-are: starts with Explain what"

def test_how_do():
    r = rewrite("How do I sort a list in Python?")
    assert r is not None, "how-do: not None"
    assert r is not None and r.startswith("Explain how to"), "how-do: starts with Explain how to"

def test_how_can():
    r = rewrite("How can you improve the performance of a database?")
    assert r is not None, "how-can: not None"
    assert r is not None and r.startswith("Explain how to"), "how-can: starts with Explain how to"


# ---------------------------------------------------------------------------
# Multi-sentence: only first sentence rewritten, rest preserved.
# ---------------------------------------------------------------------------

def test_multisent_polite():
    raw = "Can you explain why the sky is blue? Keep it under 100 words."
    r = rewrite(raw)
    assert r is not None, "multisent: not None"
    assert r is not None and "Can you" not in r, "multisent: no leading 'Can you'"
    assert r is not None and "Keep it under 100 words." in r, "multisent: rest preserved"

def test_multisent_what():
    raw = "What is machine learning? Give two examples."
    r = rewrite(raw)
    assert r is not None, "multisent-what: not None"
    assert r is not None and "Give two examples." in r, "multisent-what: remainder preserved"

def test_multisent_clean_unchanged():
    # A multi-sentence prompt starting with an imperative: no rewrite.
    raw = "Write a blog post. Keep it under 500 words."
    assert rewrite(raw) is None, "multisent-clean: returns None"


# ---------------------------------------------------------------------------
# Invariant: rewritten text is never the same object as input.
# ---------------------------------------------------------------------------

def test_returns_new_string():
    raw = "Can you list five fruits?"
    r = rewrite(raw)
    assert r is not raw, "new-string: result is not the same object as input"


# ---------------------------------------------------------------------------
# rewrite_constraint: surface normalisation of constraint/format spans.
# ---------------------------------------------------------------------------

def test_constraint_strip_output_ref():
    # Trailing "in your response/song/etc." is redundant in an isolated span.
    r = rewrite_constraint("The word robber should appear at least 2 times in the song.")
    assert r is not None, "constraint-strip: not None"
    assert r is not None and "in the song" not in r, "constraint-strip: output ref removed"
    assert r is not None and "at least 2 times" in r, "constraint-strip: core preserved"

def test_constraint_flatten_marked_by():
    # "(that are marked by an asterisk, *)" → "marked with an asterisk, *"
    r = rewrite_constraint(
        "It must contain exactly 3 bullet points (that are marked by an asterisk, *)."
    )
    assert r is not None, "constraint-flatten: not None"
    assert r is not None and "(" not in r, "constraint-flatten: no parens"
    assert r is not None and "3 bullet points" in r, "constraint-flatten: core preserved"

def test_constraint_strip_ie():
    # "i.e. <<title>>" appended as example is redundant in a constraint span.
    r = rewrite_constraint(
        "The title should be wrapped in double angular brackets, i.e. <<title>>."
    )
    assert r is not None, "constraint-ie: not None"
    assert r is not None and "i.e." not in r, "constraint-ie: i.e. removed"

def test_constraint_clean_unchanged():
    # A constraint with no normalisable patterns returns None.
    r = rewrite_constraint("Use only lowercase letters.")
    assert r is None, "constraint-clean: returns None"

def test_constraint_strips_in_response():
    # "in your response" is redundant in an isolated constraint span.
    r = rewrite_constraint("Do not use commas in your response.")
    assert r is not None, "constraint-in-response: not None"
    assert r is not None and "in your response" not in r, "constraint-in-response: phrase removed"
    assert r is not None and "Do not use commas" in r, "constraint-in-response: core preserved"

def test_constraint_clean_short():
    # Very short clean constraint — no change.
    r = rewrite_constraint("Use only lowercase letters.")
    assert r is None, "constraint-clean-short: returns None"


    print("All checks passed.")
