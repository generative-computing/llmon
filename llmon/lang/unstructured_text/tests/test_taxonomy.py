# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the label spaces and the traps they exist to close.

The first two tests are the load-bearing ones. They pin down failure modes that
are SILENT -- they produce plausible numbers rather than errors -- and each of
them has actually cost this project measurable accuracy, so neither guard should
ever be quietly dropped.
"""

import pytest

from llmon.lang.unstructured_text import heuristic, render, rules
from llmon.lang.unstructured_text.taxonomy import (
    CONTENT, CONTROL, DEFAULT, NINE, STRUCTURAL, THREE, Taxonomy, TaxonomyLeak, get,
)


# ---------------------------------------------------------------------------
# The two silent traps
# ---------------------------------------------------------------------------

def test_tag_lookup_is_strict():
    """An unknown label must RAISE, never fall back to a default tag.

    This is the trap. Scoring once compared the LLMON tags two labels render to,
    and the tag lookup defaulted unknown labels to "data". So when a label space
    was not registered, every one of its labels mapped to the same tag and
    therefore compared equal to every other -- the harness reported near-perfect
    accuracy that meant nothing. Both halves are fixed (scoring compares classes
    now), and this asserts the second half stays fixed.
    """
    with pytest.raises(TaxonomyLeak):
        render._tag_for("NotARealLabel")
    with pytest.raises(TaxonomyLeak):
        THREE.tag("Instruction")          # a nine-class name, wrong space
    with pytest.raises(TaxonomyLeak):
        NINE.tag("instruction")           # a three-class name, wrong space


def test_three_class_tags_are_distinct():
    """The three collapsed labels must not share an LLMON tag.

    If any two shared one, tag-based comparison would silently merge them. They
    are distinct by construction, and this keeps it that way.
    """
    tags = {c: THREE.tag(c) for c in THREE.labels}
    assert len(set(tags.values())) == len(THREE.labels), (
        f"collapsed labels share a tag: {tags}"
    )


def test_rule_engine_keeps_the_nine_class_space():
    """The rule engine must stay on NINE no matter what the default is.

    Its ~50 regexes, its compatibility sets and its confidence calibration are
    all written against the nine class NAMES. Point it at a coarser space and
    every plane lookup misses -- and a missed lookup returns None, so `None ==
    None` compares equal for every label and confidence inflates rather than
    raising.
    """
    assert heuristic.PLANE_OF_LABEL is NINE.plane_of
    assert len(heuristic.PLANE_OF_LABEL) == 9
    assert "Instruction" in heuristic.PLANE_OF_LABEL


def test_plane_lookups_never_return_none_in_practice():
    """The tripwire for the failure above, on a real prompt."""
    result = heuristic.segment_and_classify(
        "You are a pirate. Write a poem about dogs in under 50 words."
    )
    segments = result["segments"]
    assert segments
    assert all(s["semantic_plane"] is not None for s in segments), (
        "a segment came back with semantic_plane=None, so a plane lookup missed"
    )
    assert any(s["structural_confidence"] > 0 for s in segments), (
        "every structural_confidence is 0, so the plane split saw no planes"
    )


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_no_module_level_taxonomy_globals_remain():
    """The label space must not be reachable as rebindable module state.

    It used to be `rules.PLANE_OF_LABEL`, rebound in a required import order.
    That is the mechanism this design replaced; leaving the old name in place
    would let it come back.
    """
    assert not hasattr(rules, "PLANE_OF_LABEL")


def test_default_is_three():
    assert DEFAULT is THREE
    assert get(None) is THREE
    assert get("nine") is NINE
    assert get(NINE) is NINE
    with pytest.raises(ValueError):
        get("seventeen")


def test_label_order_is_pinned():
    """Label order IS a model head's output-index order.

    Reordering silently mis-decodes every saved checkpoint's logits into the wrong
    class names, so the order is asserted rather than merely documented.
    """
    assert NINE.labels == (
        "Instruction", "Constraint", "Output Format",
        "Content", "Structured Data", "Quoted Content", "Example",
        "Role", "Context",
    )
    assert THREE.labels == ("instruction", "constraint", "data")


def test_every_label_has_a_plane_and_a_tag():
    for tax in (NINE, THREE):
        for label in tax.labels:
            assert label in tax.plane_of
            assert tax.tag(label)


def test_incomplete_taxonomy_is_rejected_at_construction():
    with pytest.raises(ValueError):
        Taxonomy(name="broken", labels=("a", "b"),
                 plane_of={"a": "P"}, tag_of={"a": "x", "b": "y"})


def test_nine_class_planes():
    assert NINE.plane_of["Instruction"] == CONTROL
    assert NINE.plane_of["Content"] == CONTENT
    assert NINE.plane_of["Role"] == STRUCTURAL


def test_each_collapsed_class_has_its_own_plane():
    """So the harness's "same plane, different class" bucket reads 0, not inflated."""
    planes = {THREE.plane_of[c] for c in THREE.labels}
    assert len(planes) == len(THREE.labels)


# ---------------------------------------------------------------------------
# Collapsing
# ---------------------------------------------------------------------------

def test_every_nine_class_label_collapses():
    for label in NINE.labels:
        assert THREE.collapse(label) in THREE.labels
    assert set(THREE.collapse_from) == set(NINE.labels)


def test_documented_collapse_decisions():
    """The two judgement calls, asserted so a refactor cannot quietly flip them."""
    assert THREE.collapse("Example") == "constraint"
    assert THREE.collapse("Role") == "constraint"
    assert THREE.collapse("Context") == "data"
    assert THREE.collapse("Output Format") == "constraint"


def test_collapse_is_idempotent():
    """Needed because a result can hold a MIXED label space.

    A result that has already been collapsed, wholly or in part, can be collapsed
    again, so collapsing has to be safe to apply to a whole result rather than to
    individually-known-fine labels.
    """
    for label in THREE.labels:
        assert THREE.collapse(label) == label


def test_collapse_rejects_unknown_labels():
    with pytest.raises(TaxonomyLeak):
        THREE.collapse("Unsure")
    with pytest.raises(TaxonomyLeak):
        THREE.collapse("Injection")


def test_injection_is_in_neither_label_space():
    """It is a safety flag, not a class -- orthogonal to what kind of span this is."""
    assert "Injection" not in NINE
    assert "Injection" not in THREE.labels
    assert "Injection" not in NINE.plane_of


def test_collapse_labels_dedupes_on_offsets():
    labels = [
        {"class": "Constraint", "span": {"start": 0, "end": 5}},
        {"class": "Output Format", "span": {"start": 0, "end": 5}},   # same class now
        {"class": "Instruction", "span": {"start": 0, "end": 5}},     # different class
    ]
    out = THREE.collapse_labels(labels)
    assert len(out) == 2
    assert sorted(l["class"] for l in out) == ["constraint", "instruction"]


def test_collapse_labels_preserves_extra_keys():
    labels = [{"class": "Role", "span": {"start": 0, "end": 4}, "annotator": "a1"}]
    out = THREE.collapse_labels(labels)
    assert out[0]["annotator"] == "a1"
    assert out[0]["class"] == "constraint"


def test_collapse_result_rewrites_labels_and_planes():
    result = heuristic.segment_and_classify("You are a pirate. Write a poem.")
    THREE.collapse_result(result)
    for seg in result["segments"]:
        assert seg["primary_class"] in THREE.labels
        assert seg["semantic_plane"] == THREE.plane_of[seg["primary_class"]]
        for sp in seg["spans"]:
            assert sp["label"] in THREE.labels


def test_taxonomies_are_frozen():
    with pytest.raises(Exception):
        NINE.name = "something else"
