#!/usr/bin/env python3
# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Runnable behavioral checks for the `rules` backend.

These assert structural properties (not brittle exact floats): correct
offsets, escalation behavior, injection flagging, multi-class detection, and
Unknown handling.

Run:  python -m pytest
"""

import sys, os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from llmon.lang.unstructured_text.heuristic import segment_and_classify, segment, CONFIDENCE_THRESHOLD


def _labels(seg):
    return [c["label"] for c in seg["classifications"]]



# ---------------------------------------------------------------------------
# Offset integrity: raw[start:end] must equal the segment text, for every
# segment of every example. This is the single most important invariant.
# ---------------------------------------------------------------------------

def test_offsets_round_trip(example_texts):
    for path, raw in sorted(example_texts.items()):
        result = segment_and_classify(raw)
        ok = all(raw[s["start"]:s["end"]] == s["text"] for s in result["segments"])
        assert ok, f"offsets round-trip exactly ({path})"
        # No empty segments, no leading/trailing whitespace.
        clean = all(s["text"] and s["text"] == s["text"].strip()
                    for s in result["segments"])
        assert clean, f"segments non-empty and trimmed ({path})"


# ---------------------------------------------------------------------------
# Amazon example: should NOT be globally confident; the data block is content.
# ---------------------------------------------------------------------------

def test_amazon(example_texts):
    raw = example_texts["amazon"]
    result = segment_and_classify(raw)

    assert result["is_confident_global"] is False, "amazon: not globally confident"

    # The <data>...</data> block should be one atomic segment on the CONTENT plane.
    data_segs = [s for s in result["segments"] if "<data>" in s["text"]]
    assert len(data_segs) == 1, "amazon: <data> kept as one atomic segment"
    if data_segs:
        assert data_segs[0]["semantic_plane"] == "CONTENT", "amazon: <data> is CONTENT plane"
        assert data_segs[0]["requires_review"] is True, "amazon: <data> escalates"

    # The Output Format segment (JSON schema) should be the most confident one.
    fmt = [s for s in result["segments"] if "JSON schema" in s["text"]]
    assert len(fmt) == 1, "amazon: output-format segment found"
    if fmt:
        assert fmt[0]["primary_class"] == "Output Format", "amazon: output-format primary is Output Format"

    # The Task segment carries both Instruction and Constraint signals. These
    # are COMPATIBLE (a constraint modifying an instruction), so after the
    # co-label calibration this is a confident CONTROL segment, NOT an
    # escalation -- "do X under 150 words" is an easy case, not ambiguity.
    task = [s for s in result["segments"] if s["text"].startswith("# Task")]
    assert len(task) == 1, "amazon: task segment found"
    if task:
        labs = _labels(task[0])
        assert "Instruction" in labs and "Constraint" in labs, "amazon: task segment is multi-label (Instruction+Constraint)"
        assert task[0]["semantic_plane"] == "CONTROL", "amazon: task segment stays on CONTROL plane"
        # A segment escalates when it contains a span
        # whose label rests on moderate/competing evidence, so a caller can verify
        # it. The task segment here has such a span, so it now (correctly) escalates.
        assert task[0]["requires_review"] is True, "amazon: task segment escalates for span verification"


# ---------------------------------------------------------------------------
# Injection detection: must flag and force escalation.
# ---------------------------------------------------------------------------

def test_injection(example_texts):
    raw = example_texts["injection"]
    result = segment_and_classify(raw)

    inj = [s for s in result["segments"] if s.get("injection_detected")]
    assert len(inj) >= 1, "injection: at least one segment flagged"
    if inj:
        assert all(s["requires_review"] for s in inj), "injection: flagged segment escalates"
        assert any(s["abstention_reason"] == "injection_detected" for s in inj), "injection: reason is injection_detected"
        # Injection must never be surfaced as a primary content class.
        assert all(s["primary_class"] != "Injection" for s in result["segments"]), "injection: never a primary_class"

    # The "You are ..." line should be classified Role / STRUCTURAL.
    role = [s for s in result["segments"] if s["text"].startswith("You are")]
    if role:
        assert role[0]["semantic_plane"] == "STRUCTURAL", "injection: role line is STRUCTURAL"


# ---------------------------------------------------------------------------
# Unknown handling: a lone heading produces Unknown at 0.0 confidence.
# ---------------------------------------------------------------------------

def test_unknown_heading():
    raw = "# Input\n\nGenerate a poem about the sea."
    result = segment_and_classify(raw)
    head = [s for s in result["segments"] if s["text"] == "# Input"]
    assert len(head) == 1, "unknown: lone heading found"
    if head:
        assert head[0]["primary_class"] is None, "unknown: heading primary_class is None"
        assert head[0]["confidence"] == 0.0, "unknown: heading confidence 0.0"
        assert head[0]["requires_review"] is True, "unknown: heading escalates"


# ---------------------------------------------------------------------------
# Single clean instruction should be confident (>= threshold) and not escalate.
# ---------------------------------------------------------------------------

def test_clean_single_class():
    raw = "Summarize the report."
    result = segment_and_classify(raw)
    seg = result["segments"][0]
    assert seg["primary_class"] == "Instruction", "clean: single instruction primary is Instruction"
    assert seg["confidence"] >= CONFIDENCE_THRESHOLD, "clean: single instruction is confident"
    assert seg["requires_review"] is False, "clean: single instruction does not escalate"


# ---------------------------------------------------------------------------
# Co-label calibration: compatible modifiers (Instruction + Constraint +
# Output Format) coexist and stay confident; incompatible heads still escalate.
# ---------------------------------------------------------------------------

def test_compatible_colabels_confident():
    # "Do X, with constraint Y, in format Z" -- the canonical easy prompt.
    raw = ('Write a detailed review of the movie "Heat". '
           'Your entire response must be in all lowercase and in JSON format.')
    seg = segment_and_classify(raw)["segments"][0]
    labs = _labels(seg)
    assert seg["primary_class"] == "Instruction", "compat: instruction is primary"
    assert "Constraint" in labs, "compat: constraint co-label present"
    # Segment-level confidence is still high (compatible modifiers don't suppress
    # the classify_segment score) ...
    assert seg["confidence"] >= CONFIDENCE_THRESHOLD, "compat: confident (compatible modifiers don't suppress)"
    # ... but it is escalated for a cheap span-level check,
    # because the format/constraint clause rests on moderate-weight evidence and
    # such spans were measurably error-prone. This is intended safety behavior.
    assert seg["requires_review"] is True, "compat: escalates for span-level verification"


def test_compatibility_is_symmetric():
    # Constraint may outscore Instruction here; it must still be confident,
    # not crash to ~0 just because the constraint won the score race.
    raw = "Write a poem of at least 350 words about the beauty of eucalyptus trees."
    seg = segment_and_classify(raw)["segments"][0]
    labs = _labels(seg)
    assert "Instruction" in labs and "Constraint" in labs, "symmetric: both Instruction and Constraint present"
    assert seg["confidence"] >= CONFIDENCE_THRESHOLD, "symmetric: confident regardless of which scored higher"
    assert seg["requires_review"] is False, "symmetric: does not escalate"


def test_incompatible_heads_still_escalate():
    # Two genuinely competing heads from DIFFERENT planes that do not stand in a
    # head/modifier relation (Role on STRUCTURAL vs. Output Format on CONTROL)
    # should remain ambiguous and escalate. Reinforcement only applies to
    # same-plane compatible modifiers, so this conflict is NOT rescued -- we
    # stay conservative when heads genuinely contend.
    raw = 'You are a pirate. {"schema": "value"} for example see above.'
    result = segment_and_classify(raw)
    seg = result["segments"][0]
    assert seg["abstention_reason"] in ("multi_class", "weak_margin"), "conflict: competing cross-plane heads stay multi-class"
    assert seg["requires_review"] is True, "conflict: competing cross-plane heads escalate"

    # And a pure low-signal blob (no directive at all) must still escalate.
    plain = segment_and_classify(
        "The weather was nice. Birds sang in the trees near the old house."
    )["segments"][0]
    assert plain["requires_review"] is True, "conflict: low-signal content escalates"


# ---------------------------------------------------------------------------
# Confidence in [0, 1] for every segment everywhere.
# ---------------------------------------------------------------------------

def test_interrogative_request_classified_as_instruction():
    # A question is a directive (it asks the model to answer), so a line-initial
    # interrogative head that actually ends in '?' is recognized as an
    # Instruction rather than leaving the segment with only a stray Constraint.
    # A compatible same-plane modifier (the Constraint here) reinforces that
    # corroborated control instruction, lifting confidence above the bare head.
    # We do NOT assert it always clears the 0.8 bar -- the rule engine only needs to
    # catch the easy cases, and an unsure question can safely fall back to the
    # review. We just assert the recognition and the upward (never downward) nudge.
    raw = ("What is a name that people call God? "
           "Please give exactly two different responses.")
    seg = segment_and_classify(raw)["segments"][0]
    labs = _labels(seg)
    assert seg["primary_class"] == "Instruction", "interrog: instruction is primary"
    assert "Constraint" in labs, "interrog: constraint co-label present"
    # Reinforced above the lone interrogative-head weight (0.78), never below it.
    assert seg["confidence"] >= 0.78, "interrog: same-plane modifier reinforces (>= head weight)"


def test_bare_question_still_escalates():
    # A pure question with NO constraint/format has only the weak interrogative
    # head (weight 0.78). Its single span is a moderate-confidence structural
    # guess, so it is flagged for review rather than committed to as a
    # possibly-wrong label. (Matches this test's name.)
    raw = "What is the capital of France?"
    seg = segment_and_classify(raw)["segments"][0]
    assert seg["primary_class"] == "Instruction", "bare-q: recognised as Instruction"
    assert seg["confidence"] < CONFIDENCE_THRESHOLD, "bare-q: confidence below HARD threshold"
    assert seg["requires_review"] is True, "bare-q: escalates for span-level verification"


def test_clean_single_instruction_unchanged():
    # Reinforcement must be a no-op when there are no compatible modifiers: a
    # lone strong imperative keeps its own score, neither lifted nor lowered.
    seg = segment_and_classify("Summarize the report.")["segments"][0]
    assert seg["confidence"] >= CONFIDENCE_THRESHOLD and seg["requires_review"] is False, "noop: single instruction stays confident"


def test_confidence_bounds(example_texts):
    for path, raw in sorted(example_texts.items()):
        result = segment_and_classify(raw)
        ok = all(0.0 <= s["confidence"] <= 1.0 for s in result["segments"])
        assert ok, f"confidence within [0,1] ({path})"


# ---------------------------------------------------------------------------
# Span extraction: every segment must have a non-empty spans list with valid
# absolute offsets.
# ---------------------------------------------------------------------------

def test_spans_present(example_texts):
    for path, raw in sorted(example_texts.items()):
        result = segment_and_classify(raw)
        for s in result["segments"]:
            assert s.get("spans"), f"spans present and non-empty ({path})"
            for sp in s.get("spans", []):
                in_bounds = s["start"] <= sp["start"] < sp["end"] <= s["end"]
                assert in_bounds, f"span in segment bounds ({path})"


def test_spans_absolute_offsets(example_texts):
    for path, raw in sorted(example_texts.items()):
        result = segment_and_classify(raw)
        for s in result["segments"]:
            for sp in s.get("spans", []):
                assert raw[sp["start"]:sp["end"]] == sp["text"], f"span text matches raw[start:end] ({path})"


def test_spans_clause_level():
    # A prompt with two clearly distinct clauses should produce at least two
    # spans with different labels (Instruction + Constraint).
    raw = "Write a song about the ocean. Do not use any rhymes."
    result = segment_and_classify(raw)
    seg = result["segments"][0]
    spans = seg.get("spans", [])
    labels = [sp["label"] for sp in spans]
    assert len(spans) >= 2, "clause-spans: at least 2 spans for 2-clause prompt"
    assert "Instruction" in labels, "clause-spans: Instruction span present"
    assert "Constraint" in labels, "clause-spans: Constraint span present"


# ---------------------------------------------------------------------------
# Rewrite field: absent by default, present when rewrite=True on a matching
# Instruction segment.
# ---------------------------------------------------------------------------

def test_rewrite_field_optional():
    raw = "Can you explain why the sky is blue?"
    result_no_rw = segment_and_classify(raw, rewrite=False)
    seg_no_rw = result_no_rw["segments"][0]
    assert "rewritten_text" not in seg_no_rw, "rewrite absent by default"

    result_rw = segment_and_classify(raw, rewrite=True)
    seg_rw = result_rw["segments"][0]
    # Only present if the segment is a confident Instruction.
    if seg_rw.get("primary_class") == "Instruction" and not seg_rw.get("requires_review"):
        assert "rewritten_text" in seg_rw, "rewrite present when rewrite=True"
        assert (isinstance(seg_rw.get("rewritten_text"), str)
                and seg_rw["rewritten_text"]), "rewritten_text is a non-empty string"
    # Original text must never be mutated.
    assert seg_rw["text"] == raw.strip(), "original text unchanged after rewrite"


# ---------------------------------------------------------------------------
# Imperative-anchor override: a segment starting with an imperative verb must
# produce an Instruction span even when constraint evidence outscores it.
# ---------------------------------------------------------------------------

def test_imperative_anchor_overrides_constraint_label():
    # "Write a poem of at least 350 words" — historically classified as
    # Constraint because the quantifier evidence outweighs the single imperative.
    # The span must be Instruction because the segment starts with "Write".
    raw = "Write a poem of at least 350 words about the beauty of eucalyptus trees."
    result = segment_and_classify(raw)
    spans = result["segments"][0].get("spans", [])
    assert len(spans) == 1, "anchor: single span produced"
    if spans:
        assert spans[0]["label"] == "Instruction", "anchor: span label forced to Instruction"

    # Sanity: a segment starting with a Constraint opener should NOT be forced.
    raw2 = "Do not use commas in your response."
    spans2 = segment_and_classify(raw2)["segments"][0].get("spans", [])
    if spans2:
        assert spans2[0]["label"] != "Instruction", "anchor: non-imperative start is not forced to Instruction"


# ---------------------------------------------------------------------------
# Inline constraint splitting: quantitative constraints embedded mid-sentence
# should produce a separate Constraint span even without a sentence boundary.
# ---------------------------------------------------------------------------

def test_inline_constraint_splits():
    # "Write a blog post ... with at least 300 words." should split into
    # an Instruction span and a Constraint span.
    raw = "Write a blog post about the magistrates with at least 300 words."
    result = segment_and_classify(raw)
    spans = result["segments"][0].get("spans", [])
    labels = [sp["label"] for sp in spans]
    assert len(spans) >= 2, "inline-constraint: produces multiple spans"
    assert "Instruction" in labels, "inline-constraint: Instruction span present"
    assert "Constraint" in labels, "inline-constraint: Constraint span present"
    # Offset invariant still holds.
    raw_full = raw
    for sp in spans:
        assert raw_full[sp["start"]:sp["end"]] == sp["text"], "inline-constraint: span text matches raw"


def test_data_fence_is_single_span():
    # A triple-quote reference-data block is ONE data artifact, not a place to
    # mine instructions/constraints. The fenced block must collapse to a single
    # content-plane span instead of being clause-split into many.
    raw = ('Answer based on the following:\n"""\n'
           '[1] Item one: do not enter. Must wait.\n'
           '[2] Item two: at least 5 things. Ensure quality.\n'
           '[3] Item three: in JSON format please.\n'
           '"""\nGive me a summary.')
    result = segment_and_classify(raw)
    # Find the segment covering the fenced block.
    fenced = [s for s in result["segments"] if '"""' in s["text"]]
    assert len(fenced) == 1, "data-fence: fenced block is one segment"
    assert len(fenced[0]["spans"]) == 1, "data-fence: fenced block has exactly one span"
    assert fenced[0]["primary_class"] in ("Content", "Structured Data"), "data-fence: span is content-plane"
    # Offset invariant.
    sp = fenced[0]["spans"][0]
    assert raw[sp["start"]:sp["end"]] == sp["text"], "data-fence: span text matches raw"


def test_unterminated_data_fence_runs_to_end():
    # An opening """ with no closing fence runs to end-of-text and is still one
    # data block (common when a pasted document simply ends the prompt).
    raw = ('Use the data below:\n"""\n'
           '1. first record do not skip\n2. second record must include\n'
           '3. third record at least once')
    result = segment_and_classify(raw)
    fenced = [s for s in result["segments"] if '"""' in s["text"]]
    assert len(fenced) == 1, "unterminated-fence: block is one segment"
    assert len(fenced[0]["spans"]) == 1, "unterminated-fence: block has one span"


def test_plain_prompt_still_splits():
    # No fence => normal fine-grained clause splitting must be unchanged.
    raw = "Write a blog post about magistrates with at least 300 words."
    spans = segment_and_classify(raw)["segments"][0].get("spans", [])
    labels = [sp["label"] for sp in spans]
    assert len(spans) >= 2, "plain-prompt: still multi-span"
    assert "Instruction" in labels, "plain-prompt: Instruction present"
    assert "Constraint" in labels, "plain-prompt: Constraint present"


    print("All checks passed.")
