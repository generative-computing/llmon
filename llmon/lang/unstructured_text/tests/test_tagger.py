#!/usr/bin/env python3
# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the BIO encode/decode round-trip. Exits non-zero on failure.

This is the highest-risk logic in the tagger: a silent off-by-one in offset handling
would produce a model that trains on subtly wrong targets and still looks plausible.
"""
import json
import os


import pytest

pytest.importorskip("torch", reason="needs pip install 'llmon[unstructured-text]'")

from llmon.lang.unstructured_text.tagger import (
    IGNORE_INDEX, TAGS, TAG_TO_IDX, spans_to_tags, tags_to_spans,
)

def fake_offsets(text, words=None):
    """Whitespace tokenization with (0,0) specials at both ends, like a HF tokenizer."""
    offs, i = [(0, 0)], 0
    for w in text.split(" "):
        if w:
            s = text.index(w, i)
            offs.append((s, s + len(w)))
            i = s + len(w)
        else:
            i += 1
    offs.append((0, 0))
    return offs



def test_tag_set_is_well_formed():
    """tag set is well formed."""
    assert TAGS[0] == "O", "index 0 is O, so an all-zero prediction means 'no class'"
    assert len(TAGS) == 7, f"7 tags: {TAGS}"
    assert all(t == "O" or t.split("-")[0] in ("B", "I") for t in TAGS), "all B-/I- or O"

def test_specials_get_ignore_index_and_do_not_break_a_run():
    """specials get IGNORE_INDEX and do not break a run."""
    text = "Write a poem now"
    offs = fake_offsets(text)
    labels = [{"class": "instruction", "span": {"start": 0, "end": len(text)}}]
    tags = spans_to_tags(offs, labels)
    assert tags[0] == IGNORE_INDEX and tags[-1] == IGNORE_INDEX, "leading/trailing special tokens are ignored in the loss"
    inner = [TAGS[t] for t in tags if t != IGNORE_INDEX]
    assert inner == ["B-instruction"] + ["I-instruction"] * 3, f"one span -> one B then all I: {inner}"

def test_innermost_wins_produces_the_intra_sentence_split_the_whole_point():
    """innermost-wins produces the intra-sentence split (the whole point)."""
    text = "Write a poem about dogs using less than 50 words"
    offs = fake_offsets(text)
    i0 = text.index("using")
    labels = [
        {"class": "instruction", "span": {"start": 0, "end": len(text)}},   # outer
        {"class": "constraint",  "span": {"start": i0, "end": len(text)}},  # nested
    ]
    tags = [t for t in spans_to_tags(offs, labels) if t != IGNORE_INDEX]
    got = [TAGS[t] for t in tags]
    expect = ["B-instruction"] + ["I-instruction"] * 4 + ["B-constraint"] + ["I-constraint"] * 4
    assert got == expect, (
        f"nested constraint wins over the enclosing instruction:\n         {got}"
    )
    spans = tags_to_spans(offs, spans_to_tags(offs, labels), text)
    got_spans = [(sp["label"], sp["text"]) for sp in spans]
    assert got_spans == [
        ("instruction", "Write a poem about dogs"),
        ("constraint", "using less than 50 words"),
    ], f"decodes to two spans: {got_spans}"

def test_round_trip_on_the_real_corpus_innermost_target(corpus_path, tokenizer_dir,
                                                       transformers_available):
    """round-trip on the REAL corpus (innermost target)."""
    import re
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(tokenizer_dir)
    from llmon.lang.unstructured_text.training.corpus import load
    # The tagger's target is the THREE space, so the corpus is collapsed on load.
    rows = load(corpus_path, taxonomy="three")
    offset_bad = text_bad = order_bad = overlap_bad = 0
    n_spans = 0
    for ex in rows:
        p = ex["prompt"]
        enc = tok(p, return_offsets_mapping=True, truncation=True, max_length=2048)
        om = enc["offset_mapping"]
        got = tags_to_spans(om, spans_to_tags(om, ex["labels"]), p)
        n_spans += len(got)
        for sp in got:
            if p[sp["start"]:sp["end"]] != sp["text"]:
                offset_bad += 1
            if sp["text"] != sp["text"].strip() or not sp["text"]:
                text_bad += 1
        for a, b in zip(got, got[1:]):
            if b["start"] < a["start"]:
                order_bad += 1
            if b["start"] < a["end"]:
                overlap_bad += 1
    assert offset_bad == 0, (
        f"prompt[start:end] == text for all {n_spans} decoded spans "
        f"({offset_bad} violations)"
    )
    assert text_bad == 0, f"no empty or untrimmed span text ({text_bad} violations)"
    assert order_bad == 0, f"spans come out in document order ({order_bad} violations)"
    assert overlap_bad == 0, (
        f"spans are non-overlapping — design invariant 2 "
        f"({overlap_bad} violations)"
    )

def test_a_stray_i_with_no_open_span_is_promoted_to_b_not_dropped():
    """a stray I- with no open span is promoted to B-, not dropped."""
    text = "alpha beta"
    offs = fake_offsets(text)
    ids = [IGNORE_INDEX, TAG_TO_IDX["I-data"], TAG_TO_IDX["I-data"], IGNORE_INDEX]
    spans = tags_to_spans(offs, ids, text)
    assert len(spans) == 1 and spans[0]["text"] == "alpha beta", f"orphan I- opens a span instead of losing the tokens: {spans}"

def test_adjacent_same_class_spans_stay_separate_when_b_says_so():
    """adjacent same-class spans stay separate when B- says so."""
    text = "aaa bbb"
    offs = fake_offsets(text)
    ids = [IGNORE_INDEX, TAG_TO_IDX["B-data"], TAG_TO_IDX["B-data"], IGNORE_INDEX]
    spans = tags_to_spans(offs, ids, text)
    assert [s["text"] for s in spans] == ["aaa", "bbb"], f"two B- tags -> two spans, not one merged run: {[s['text'] for s in spans]}"

def test_o_tokens_close_the_current_span():
    """O tokens close the current span."""
    text = "aaa bbb ccc"
    offs = fake_offsets(text)
    ids = [IGNORE_INDEX, TAG_TO_IDX["B-data"], TAG_TO_IDX["O"], TAG_TO_IDX["B-data"],
           IGNORE_INDEX]
    spans = tags_to_spans(offs, ids, text)
    assert [s["text"] for s in spans] == ["aaa", "ccc"], f"O splits the run: {spans}"

def test_punctuation_only_spans_are_dropped():
    """punctuation-only spans are dropped."""
    text = "aaa . bbb"
    offs = fake_offsets(text)
    ids = [IGNORE_INDEX, TAG_TO_IDX["B-data"], TAG_TO_IDX["B-constraint"],
           TAG_TO_IDX["B-data"], IGNORE_INDEX]
    spans = tags_to_spans(offs, ids, text)
    assert [s["text"] for s in spans] == ["aaa", "bbb"], f"a lone '.' span is discarded, not emitted: {[s['text'] for s in spans]}"

def test_confidence_is_the_mean_over_a_span_s_tokens():
    """confidence is the mean over a span's tokens."""
    text = "aaa bbb"
    offs = fake_offsets(text)
    ids = [IGNORE_INDEX, TAG_TO_IDX["B-data"], TAG_TO_IDX["I-data"], IGNORE_INDEX]
    spans = tags_to_spans(offs, ids, text, scores=[0.0, 1.0, 0.5, 0.0])
    assert abs(spans[0]["confidence"] - 0.75) < 1e-6, f"mean of 1.0 and 0.5 is 0.75, got {spans[0]['confidence']}"


def test_train_load_collapses_the_corpus(corpus_path):
    """The tagger's corpus loader must collapse into the tagger's label space.

    Regression: the corpus on disk is annotated in the finest taxonomy available,
    which is finer-grained than the tagger's BIO head can represent. Without the
    collapse, spans_to_tags() meets a class it has no tag for and dies with a bare
    `KeyError: 'B-Instruction'` partway into the first epoch -- after the model has
    been built and the batches tokenized.
    """
    from llmon.lang.unstructured_text.tagger import TAGGER_TAXONOMY, _load

    rows = _load(corpus_path)
    seen = {lab["class"] for r in rows for lab in r["labels"]}
    assert seen, "corpus produced no labels"
    assert seen <= set(TAGGER_TAXONOMY.labels), (
        f"_load left classes the tagger has no BIO tag for: "
        f"{sorted(seen - set(TAGGER_TAXONOMY.labels))}"
    )
    for label in seen:
        assert f"B-{label}" in TAG_TO_IDX and f"I-{label}" in TAG_TO_IDX


def test_segment_names_its_source():
    """The segment must carry source="tagger", not just its spans.

    Every span and segment reports which backend labelled it, so a consumer or
    the evaluation harness can tell rule output from model output in a mixed
    result.
    """
    from llmon.lang.unstructured_text.tagger import segment_and_classify_tagger
    import inspect

    src = inspect.getsource(segment_and_classify_tagger)
    assert '"source": "tagger"' in src, (
        "segment_and_classify_tagger must set seg['source'] = 'tagger'"
    )
