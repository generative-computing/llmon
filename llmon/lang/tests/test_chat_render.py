# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for llmon.lang.chat.render.

`chat/render_golden.json` holds renders produced by the LLMON chat template that comes
with the LLMON models based on Granite-4.1.
"""

import json
import random
import re
from pathlib import Path

import pytest

from llmon.lang.chat import (EOS_TOKEN, ID_CHARS, render_assistant_body,
                             render_chat, split_prompt_target, stop_token_ids)

GOLDEN = json.loads((Path(__file__).parent / "chat" / "render_golden.json").read_text())
BLOCK_ID_RE = re.compile(r"<\|:\|>([" + re.escape(ID_CHARS) + r"]{2})<\|close\|>")
EOS = "<|end_of_text|>"
assert EOS_TOKEN == EOS


@pytest.mark.parametrize("case", GOLDEN, ids=[c["name"] for c in GOLDEN])
def test_matches_template(case):
    for unique in (False, True):          # no collision in these renders, so both must match
        got = render_chat(case["messages"], case["documents"], case["tools"],
                          add_generation_prompt=case["add_generation_prompt"],
                          eos_token=case["eos_token"], rng=random.Random(case["seed"]),
                          unique_ids=unique)
        assert got == case["expected"]


def test_ids_unique_by_default():
    msgs = [{"role": "user", "data": [{"value": str(k), "exec": True} for k in range(300)]}]
    for seed in range(20):
        ids = BLOCK_ID_RE.findall(render_chat(msgs, rng=random.Random(seed)))
        assert len(ids) == len(set(ids))


def test_pinned_ids():
    msgs = [{"role": "user", "content": "Use data<|:|>¢¢.", "llmon_id": "££",
             "llmon_output_id": "¥¥", "data": [{"value": "v", "exec": True, "llmon_id": "¢¢"}]},
            {"role": "assistant", "content": "ok"}]
    out = render_chat(msgs, rng=random.Random(0))
    assert "<|open|>instr<|:|>££<|close|>" in out
    assert "<|open|>data<|:|>¢¢<|close|>v" in out
    assert "<|open|>exec<|.|>input<|close|>data<|:|>¢¢<|open_end|>" in out
    assert "<|open|>assistant<|:|>¥¥<|close|>ok" in out
    assert "llmon_id" not in out


@pytest.mark.parametrize("msgs", [
    [{"role": "user", "content": "q", "llmon_id": "ab"}],                     # not in ID_CHARS
    [{"role": "user", "content": "q", "llmon_id": "¡"}],                      # wrong length
    [{"role": "user", "content": "q", "llmon_id": "¡¡",
      "data": [{"value": "v", "llmon_id": "¡¡"}]}],                           # repeated
    [{"role": "user", "content": "q"}, {"role": "assistant", "llmon_id": "¡¡"}],
])
def test_bad_pins_raise(msgs):
    with pytest.raises(ValueError):
        render_chat(msgs)


def test_split_prompt_target():
    full = render_chat([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                       eos_token=EOS, rng=random.Random(0))
    prompt, target = split_prompt_target(full)
    assert prompt + target + EOS == full
    assert target == "a<|open_end|>"
    assert prompt == render_chat([{"role": "user", "content": "q"}], add_generation_prompt=True,
                                 eos_token=EOS, rng=random.Random(0))
    assert split_prompt_target("no assistant here") == ("no assistant here", "")


def test_assistant_body():
    tc = {"function": {"name": "f", "arguments": {"x": "é"}}}
    assert render_assistant_body({"content": "hi"}) == "hi"
    assert render_assistant_body({"content": None, "tool_calls": [tc]}) == \
        '<tool_call>{"name": "f", "arguments": {"x": "é"}}</tool_call>'
    assert render_assistant_body({"content": "c", "tool_calls": [tc, {"name": "g", "arguments": "{}"}]}) == \
        'c\n<tool_call>{"name": "f", "arguments": {"x": "é"}}</tool_call>\n' \
        '<tool_call>{"name": "g", "arguments": {}}</tool_call>'


def test_non_string_fields_render():
    tc = {"role": "assistant", "tool_calls": [{"name": "f", "arguments": {}}]}
    for content, shown in ((None, ""), ({"a": 1}, '{"a": 1}'),
                           ([{"type": "text", "text": "hi"}], '[{"type": "text", "text": "hi"}]')):
        out = render_chat([{"role": "user", "content": "q"}, tc, {"role": "tool", "content": content}])
        assert "<tool_response>" + shown + "</tool_response>" in out
    out = render_chat([{"role": "user", "documents": [{"title": 5, "exec": True}]}])
    assert "<|open|>doc<|.|>title<|close|>5<|open_end|><|open|>doc<|.|>text<|close|><|open_end|>" in out


def test_non_json_string_arguments_are_encoded():
    assert render_assistant_body({"tool_calls": [{"name": "f", "arguments": ""}]}) == \
        '<tool_call>{"name": "f", "arguments": ""}</tool_call>'
    assert render_assistant_body({"tool_calls": [{"name": "f", "arguments": "not json"}]}) == \
        '<tool_call>{"name": "f", "arguments": "not json"}</tool_call>'


def test_split_needs_final_assistant_turn():
    text = render_chat([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"},
                        {"role": "user", "content": "q2"}], eos_token=EOS)
    assert split_prompt_target(text) == (text, "")


@pytest.mark.parametrize("msgs", [
    [{"role": "assistant", "content": "a"}],                                  # no exec before it
    [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"},
     {"role": "assistant", "content": "b"}],                                  # two in a row
    [{"role": "developer", "content": "x", "llmon_id": "¡¡"},
     {"role": "user", "content": "q"}],                                       # pin on a skipped role
])
def test_bad_structure_raises(msgs):
    with pytest.raises(ValueError):
        render_chat(msgs)


def test_template_leniency_kept():
    """As in the template: unknown roles are skipped, and a generation prompt after an
    assistant turn opens nothing."""
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    plain = render_chat(msgs, rng=random.Random(0))
    assert render_chat([{"role": "developer", "content": "x"}] + msgs, rng=random.Random(0)) == plain
    assert render_chat(msgs, add_generation_prompt=True, rng=random.Random(0)) == plain


def test_eos_defaults_to_end_of_text():
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    assert render_chat(msgs, rng=random.Random(0)) == \
        render_chat(msgs, eos_token=EOS, rng=random.Random(0))
    assert render_chat(msgs, rng=random.Random(0)).endswith("a<|open_end|>" + EOS)
    assert split_prompt_target(render_chat(msgs))[1] == "a<|open_end|>"


def test_any_eos():
    """Granite-5.1 uses <|im_end|>: render_chat emits it, split drops it, stop ids follow it."""
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"},
            {"role": "user", "content": "q2"}, {"role": "assistant", "content": "b"}]
    text = render_chat(msgs, eos_token="<|im_end|>", rng=random.Random(0))
    assert EOS not in text and text.count("<|open_end|><|im_end|>") == 2
    prompt, target = split_prompt_target(text)
    assert target == "b<|open_end|>" and prompt.count("<|im_end|>") == 1


def test_stop_token_ids():
    class Tok:
        def __init__(self, eos):
            self.eos_token = eos
        def convert_tokens_to_ids(self, t):
            return {EOS: 100257, "<|im_end|>": 7, "<|open_end|>": 100285}[t]
    assert stop_token_ids(Tok(EOS)) == [100257, 100285]
    assert stop_token_ids(Tok("<|im_end|>")) == [7, 100285]
