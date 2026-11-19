# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the v2 LLMON format of llmon.lang.chat.render.

v2 draws the same ids as v1 in the same order and emits the same content; it differs in
mechanical ways: declarations lose their role tag, except documents and tools, which keep it and
put their id on every field block (doc<|:|>ID<|.|>title, tool<|:|>ID<|.|>name, ...) instead of
wrapping the fields; each exec is flattened into one block per pointer with the target in
<|open_ref|>...<|close_ref|>, in the order system, tool, doc, context, instruction, arg,
requirement, result. Only args are numbered: data part j is arg<|:|>j when the bound instruction
names it with <|open_parm|>j<|close_parm|>; every other input (unnamed data, the tool call, tool
responses) is context.
`test_v2_is_v1_rewritten` holds v2 to exactly that rewrite of v1, on the golden cases and on
random conversations; v1 itself is held to the template by test_chat_render.py.
"""

import json
import random
import re
from pathlib import Path

import pytest

from llmon.lang.chat import VERSIONS, parm, render_chat, split_prompt_target

GOLDEN = json.loads((Path(__file__).parent / "chat" / "render_golden.json").read_text())
EOS = "<|end_of_text|>"
ROLES = "instr|data|req|system|assistant"          # the role tags v2 drops (doc / tool stay)

_V1_PTR = r"<\|open\|>exec<\|\.\|>(\w+)<\|close\|>(\w+)<\|:\|>(.{2})<\|open_end\|>"
_V1_EXEC_RE = re.compile(r"<\|open\|>exec<\|:\|>(.{2})<\|close\|>((?:" + _V1_PTR.replace("(", "(?:")
                         + r")+)<\|open_end\|>")
_V1_PTR_RE = re.compile(_V1_PTR)
_V1_DECL_RE = re.compile(rf"<\|open\|>(?:{ROLES})<\|:\|>")
_V2_PTR_RE = re.compile(r"<\|open\|>exec<\|:\|>(.{2})<\|\.\|>(\w+(?:<\|:\|>\d+)?)<\|close\|>"
                        r"<\|open_ref\|>(?:doc|tool)?<\|:\|>(.{2})<\|close_ref\|><\|open_end\|>")
_V1_DOCTOOL_RE = re.compile(r"<\|open\|>(doc|tool)<\|:\|>(.{2})<\|close\|>")
_V1_TEXT_RE = re.compile(r"<\|open\|>(instr|data)<\|:\|>(.{2})<\|close\|>(.*?)<\|open_end\|>", re.S)
_PARM_RE = re.compile(r"<\|open_parm\|>(\d+)<\|close_parm\|>")
_V2_ORDER = ("system", "tool", "doc", "context", "instruction", "arg", "requirement")


def _split_doc_tool_blocks(text):
    """v1 doc<|:|>ID / tool<|:|>ID wrapper blocks -> v2 field blocks carrying the id."""
    out, i = [], 0
    while (m := _V1_DOCTOOL_RE.search(text, i)) is not None:
        out.append(text[i:m.start()])
        tag, id_ = m.groups()
        p, field = m.end(), f"<|open|>{tag}<|.|>"
        while not text.startswith("<|open_end|>", p):            # the wrapper's own closer
            assert text.startswith(field, p), text[p:p + 60]
            c, sc = text.find("<|close|>", p), text.find("<|self_close|>", p)
            end = (sc + len("<|self_close|>") if sc != -1 and (c == -1 or sc < c)
                   else text.find("<|open_end|>", c) + len("<|open_end|>"))
            path = text[p + len(field):end]
            p = end
            # v2 flattens message-level tools like request-level ones: no root `type` field and
            # no `function` path segment (v1 kept both there).
            if tag == "tool" and path.startswith("type<|close|>"):
                continue
            if tag == "tool" and path.startswith("function<|.|>"):
                path = path[len("function<|.|>"):]
            out.append(f"<|open|>{tag}<|:|>{id_}<|.|>" + path)
        i = p + len("<|open_end|>")
    return "".join(out) + text[i:]


def v1_to_v2(text):
    """The v2 render of a v1 render: split doc / tool wrappers into id-carrying field blocks;
    flatten each exec into one pointer block per binding, in _V2_ORDER, with data part j as
    arg<|:|>j when the bound instruction names it, every other input as context, and the output
    as `result`; then drop the remaining role tags."""
    content = {m.group(2): m.group(3) for m in _V1_TEXT_RE.finditer(text)}

    def flatten(block):
        exec_id = block.group(1)
        raw = _V1_PTR_RE.findall(block.group(2))
        named = {int(n) for role, _, id_ in raw if role == "instruction"
                 for n in _PARM_RE.findall(content.get(id_, ""))}
        ptrs, j = [], 0                                         # (role, ref, index)
        for role, tag, id_ in raw:
            ref, n = (f"{tag}<|:|>{id_}" if tag in ("doc", "tool") else f"<|:|>{id_}"), None
            if role == "input":
                if tag in ("doc", "tool"):
                    role = tag
                elif tag == "data" and not content.get(id_, "").startswith("<tool_response>"):
                    role, n = ("arg", j) if j in named else ("context", None)   # a user data part
                    j += 1
                else:
                    role = "context"                            # the tool call or a tool response
            ptrs.append(("result" if role == "output" else role, ref, n))
        ordered = [p for r in _V2_ORDER + ("result",) for p in ptrs if p[0] == r]
        return "".join(f"<|open|>exec<|:|>{exec_id}<|.|>{r if n is None else f'{r}<|:|>{n}'}<|close|>"
                       f"<|open_ref|>{ref}<|close_ref|><|open_end|>" for r, ref, n in ordered)
    text = _split_doc_tool_blocks(_V1_EXEC_RE.sub(flatten, text))
    return _V1_DECL_RE.sub("<|open|><|:|>", text)


def random_conversation(rng):
    """A random conversation covering most fields and turn shapes render_chat accepts (not pinned
    ids or unknown roles), with at most one bound instruction per user turn (v2 allows no more)."""
    def items(n, key="value", bound=0.7):
        return [{key: f"{key} {rng.random():.3f}", "exec": rng.random() < bound}
                for _ in range(rng.randint(0, n))]

    def tool(name):
        return {"type": "function", "function": {
            "name": name, "description": f"does {name}",
            "parameters": {"type": "object", "properties": {"x": {"type": "string"}},
                           "required": ["x"]}}}

    msgs = []
    if rng.random() < 0.5:
        msgs.append({"role": "system", "content": "be brief"})
    for turn in range(rng.randint(1, 3)):
        user = {"role": "user"}
        if rng.random() < 0.6:
            user["content"] = f"question {turn}"
        if rng.random() < 0.5:                                  # unbound distractors (+ one bound)
            user["instruction"] = items(3, bound=0)
            if "content" not in user and user["instruction"] and rng.random() < 0.7:
                rng.choice(user["instruction"])["exec"] = True
        for field in ("data", "requirement"):
            if rng.random() < 0.5:
                user[field] = items(3)
        n_data = sum(bool(d["exec"]) for d in user.get("data") or [])
        bound_instr = ([user] if "content" in user else
                       [it for it in user.get("instruction") or [] if it["exec"]])
        if n_data and bound_instr and rng.random() < 0.6:       # name some bound data parts
            key = "content" if "content" in user else "value"
            for j in sorted(rng.sample(range(n_data), rng.randint(1, n_data))):
                bound_instr[0][key] += " " + parm(j)
        if rng.random() < 0.3:
            user["documents"] = [dict(d, title="t") for d in items(2, "text")]
        if rng.random() < 0.2:
            user["tools"] = [dict(tool(f"m{turn}"), exec=True)]
        msgs.append(user)
        if rng.random() < 0.4:
            msgs.append({"role": "assistant", "content": None,
                         "tool_calls": [{"name": "f", "arguments": {"x": "1"}}]})
            for k in range(rng.randint(1, 2)):
                msgs.append({"role": "tool", "content": f"result {k}"})
        msgs.append({"role": "assistant", "content": f"answer {turn}"})
    if rng.random() < 0.3:
        msgs.append({"role": "user", "content": "follow-up"})
    documents = [{"title": "T", "text": "x"}] if rng.random() < 0.4 else None
    tools = [tool("g")] if rng.random() < 0.4 else None
    return msgs, documents, tools


def _cases():
    for case in GOLDEN:
        yield (case["name"], case["messages"], case["documents"], case["tools"],
               case["add_generation_prompt"], case["eos_token"], case["seed"])
    for seed in range(300):
        msgs, docs, tools = random_conversation(random.Random(seed))
        yield f"random-{seed}", msgs, docs, tools, seed % 2 == 0, EOS, seed


@pytest.mark.parametrize("case", list(_cases()), ids=lambda c: c[0])
def test_v2_is_v1_rewritten(case):
    _, msgs, docs, tools, gen, eos, seed = case
    v1 = render_chat(msgs, docs, tools, add_generation_prompt=gen, eos_token=eos,
                     rng=random.Random(seed))
    v2 = render_chat(msgs, docs, tools, add_generation_prompt=gen, eos_token=eos,
                     rng=random.Random(seed), version=2)
    assert v2 == v1_to_v2(v1)
    assert not _V1_DECL_RE.search(v2)
    assert not _V1_DOCTOOL_RE.search(v2)                       # no doc / tool wrapper blocks
    assert "<|open|>exec<|.|>" not in v2                       # no v1 exec wrapper pointers


def test_v2_single_turn_example():
    msgs = [{"role": "user", "content": "Suggest an idea for a fun activity",
             "llmon_id": "¡¡", "llmon_exec_id": "¢¢", "llmon_output_id": "££"}]
    assert render_chat(msgs, add_generation_prompt=True, version=2) == (
        "<|open|><|:|>¡¡<|close|>Suggest an idea for a fun activity<|open_end|>"
        "<|open|>exec<|:|>¢¢<|.|>instruction<|close|><|open_ref|><|:|>¡¡<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>¢¢<|.|>result<|close|><|open_ref|><|:|>££<|close_ref|><|open_end|>"
        "<|open|><|:|>££<|close|>")


def test_v2_parms_example():
    """Inputs bind to the instruction's <|open_parm|>N<|close_parm|> placeholders by number."""
    instr = "concatenate " + parm(0) + " with " + parm(1)
    assert instr == "concatenate <|open_parm|>0<|close_parm|> with <|open_parm|>1<|close_parm|>"
    msgs = [{"role": "user", "llmon_exec_id": "¤¤", "llmon_output_id": "¥¥",
             "instruction": [{"value": instr, "exec": True, "llmon_id": "¡¡"}],
             "data": [{"value": "Hello", "exec": True, "llmon_id": "¢¢"},
                      {"value": "World", "exec": True, "llmon_id": "££"}]}]
    assert render_chat(msgs, add_generation_prompt=True, version=2) == (
        "<|open|><|:|>¡¡<|close|>" + instr + "<|open_end|>"
        "<|open|><|:|>¢¢<|close|>Hello<|open_end|>"
        "<|open|><|:|>££<|close|>World<|open_end|>"
        "<|open|>exec<|:|>¤¤<|.|>instruction<|close|><|open_ref|><|:|>¡¡<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>¤¤<|.|>arg<|:|>0<|close|><|open_ref|><|:|>¢¢<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>¤¤<|.|>arg<|:|>1<|close|><|open_ref|><|:|>££<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>¤¤<|.|>result<|close|><|open_ref|><|:|>¥¥<|close_ref|><|open_end|>"
        "<|open|><|:|>¥¥<|close|>")


def test_v2_component_order_and_numbering():
    """system, tool, doc, context, instruction, arg, requirement, result; only args numbered."""
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "use " + parm(1),
             "data": [{"value": "d0", "exec": True}, {"value": "d1", "exec": True}],
             "requirement": [{"value": "r1", "exec": True}, {"value": "r2", "exec": True}]}]
    out = render_chat(msgs, documents=[{"title": "T", "text": "x"}],
                      tools=[{"name": "f", "description": "does f"}], rng=random.Random(0), version=2)
    assert [role for _, role, _ in _V2_PTR_RE.findall(out)] == [
        "system", "tool", "doc", "context", "instruction", "arg<|:|>1", "requirement", "requirement",
        "result"]                                    # d0 is unnamed (context), d1 is parm(1)


def test_v2_tool_run_keeps_indices_and_appends():
    msgs = [{"role": "user", "content": "q", "llmon_id": "¡¡"},
            {"role": "assistant", "tool_calls": [{"name": "f", "arguments": {}}]},
            {"role": "tool", "content": "r1", "llmon_id": "¢¢"},
            {"role": "tool", "content": "r2", "llmon_id": "££", "llmon_output_id": "¤¤"}]
    out = render_chat(msgs, documents=[{"title": "T", "text": "x", "llmon_id": "¥¥"}],
                      rng=random.Random(0), version=2)
    ptrs = _V2_PTR_RE.findall(out)
    last_exec = ptrs[-1][0]                                     # the continuation exec
    cont = [(role, id_) for e, role, id_ in ptrs if e == last_exec]
    assert [role for role, _ in cont] == [
        "doc", "context", "context", "context", "instruction", "result"]
    assert cont[0] == ("doc", "¥¥")                             # the turn's binding, rebound
    assert cont[2:4] == [("context", "¢¢"), ("context", "££")]  # the tool responses
    assert cont[-1] == ("result", "¤¤")
    first_exec = ptrs[0][0]
    assert [role for e, role, _ in ptrs if e == first_exec] == ["doc", "instruction", "result"]


def test_v2_one_instruction_per_exec():
    two = [{"role": "user", "content": "q", "instruction": [{"value": "i", "exec": True}]}]
    render_chat(two)                                            # v1 binds both
    with pytest.raises(ValueError):
        render_chat(two, version=2)
    distractor = [{"role": "user", "content": "q", "instruction": [{"value": "i", "exec": False}]}]
    assert "<|.|>instruction<|close|>" in render_chat(distractor, version=2)


def test_v2_split_prompt_target():
    full = render_chat([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                       eos_token=EOS, rng=random.Random(0), version=2)
    prompt, target = split_prompt_target(full, version=2)
    assert prompt + target + EOS == full
    assert target == "a<|open_end|>"
    assert prompt == render_chat([{"role": "user", "content": "q"}], add_generation_prompt=True,
                                 eos_token=EOS, rng=random.Random(0), version=2)
    assert split_prompt_target("no assistant here", version=2) == ("no assistant here", "")


@pytest.mark.parametrize("seed", range(100))
def test_v2_split_matches_v1_split(seed):
    """Same split point as v1: the prompts differ only by the rewrite, the targets not at all."""
    msgs, docs, tools = random_conversation(random.Random(seed))
    v1 = render_chat(msgs, docs, tools, rng=random.Random(seed))
    v2 = render_chat(msgs, docs, tools, rng=random.Random(seed), version=2)
    p1, t1 = split_prompt_target(v1)
    p2, t2 = split_prompt_target(v2, version=2)
    assert t2 == t1
    assert p2 == v1_to_v2(p1)


def test_v2_split_needs_final_assistant_turn():
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"},
            {"role": "user", "content": "q2"}]
    text = render_chat(msgs, eos_token=EOS, version=2)
    assert split_prompt_target(text, version=2) == (text, "")
    gen = render_chat(msgs, add_generation_prompt=True, eos_token=EOS, version=2)
    assert split_prompt_target(gen, version=2) == (gen, "")


def test_v1_is_the_default():
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    assert render_chat(msgs, rng=random.Random(3)) == \
        render_chat(msgs, rng=random.Random(3), version=1)
    text = render_chat(msgs, rng=random.Random(3))
    assert split_prompt_target(text) == split_prompt_target(text, version=1)
    assert VERSIONS == (1, 2)


def test_bad_version_and_v2_non_unique_raise():
    msgs = [{"role": "user", "content": "q"}]
    for bad in (0, 3, "2"):
        with pytest.raises(ValueError):
            render_chat(msgs, version=bad)
        with pytest.raises(ValueError):
            split_prompt_target("x", version=bad)
    with pytest.raises(ValueError):
        render_chat(msgs, unique_ids=False, version=2)


_DECL_RE = re.compile(r"<\|open\|><\|:\|>(.{2})<\|close\|>")
_FIELD_DECL_RE = re.compile(r"<\|open\|>(?:doc|tool)<\|:\|>(.{2})<\|\.\|>")


@pytest.mark.parametrize("case", list(_cases()), ids=lambda c: c[0])
def test_v2_every_ref_resolves_to_a_block(case):
    """Every pointer refers to a declared block: an input/instruction/system/requirement block
    declared before the pointer, an output block opened right after its exec."""
    _, msgs, docs, tools, gen, eos, seed = case
    out = render_chat(msgs, docs, tools, add_generation_prompt=gen, eos_token=eos,
                      rng=random.Random(seed), version=2)
    decls = {m.group(1): m.start() for m in _DECL_RE.finditer(out)}
    assert len(decls) == len(_DECL_RE.findall(out))             # each id declared once
    for m in _FIELD_DECL_RE.finditer(out):                      # a doc / tool: its first field
        assert m.group(1) not in decls or decls[m.group(1)] <= m.start()
        decls.setdefault(m.group(1), m.start())
    ptrs = list(_V2_PTR_RE.finditer(out))
    for p in ptrs:
        role, ref = p.group(2), p.group(3)
        if role == "result":
            if ref in decls:                                     # opened right after its exec
                assert decls[ref] > p.start(), ("output declared before its pointer", ref)
                tail = out[p.end():decls[ref]]
                assert all(m.group(1) == p.group(1) for m in _V2_PTR_RE.finditer(tail)), tail[:80]
                assert not _DECL_RE.search(tail)
        else:
            assert ref in decls and decls[ref] < p.start(), (role, ref)
    # every output except a still-pending final one is declared
    outputs = [p.group(3) for p in ptrs if p.group(2) == "result"]
    assert all(o in decls for o in outputs[:-1])
    if gen or (msgs and msgs[-1].get("role") == "assistant"):
        assert not outputs or outputs[-1] in decls


def test_v2_parm_placeholders_must_name_bindings():
    def user(instr, n_data):
        return [{"role": "user", "instruction": [{"value": instr, "exec": True}],
                 "data": [{"value": f"d{k}", "exec": True} for k in range(n_data)]}]
    render_chat(user("Count the words in " + parm(0), 1), version=2)
    render_chat(user("Join " + parm(0) + " and " + parm(1), 2), version=2)
    for instr, n in (("Count " + parm(1), 1), ("Count " + parm(0), 0),
                     ("Count <|open_parm|>input<|:|>0<|close_parm|>", 1),
                     ("Count <|open_parm|>x<|close_parm|>", 1)):
        with pytest.raises(ValueError):
            render_chat(user(instr, n), version=2)
    # an unbound (distractor) instruction may carry a placeholder its exec does not bind
    msgs = [{"role": "user", "instruction": [{"value": "Count " + parm(0), "exec": False},
                                             {"value": "Say hi", "exec": True}]}]
    render_chat(msgs, version=2)
    render_chat(user("Count " + parm(5), 0))                    # v1: plain text, no check


def test_v2_tool_example():
    """Tools keep their tag and carry the id on every field; the binding is an unnumbered
    `tool` with a tagged ref; the call and the tool response are context of the continuation."""
    tools = [{"type": "function", "function": {
        "name": "reverse_words", "description": "Reverses the order of words in a sentence",
        "parameters": {"type": "object", "required": ["sentence"],
                       "properties": {"sentence": {"type": "string", "description": "The input sentence"}}}}}]
    messages = [{"role": "user", "content": "Reverse the words: Hello world"},
                {"role": "assistant", "tool_calls": [{"name": "reverse_words",
                                                      "arguments": {"sentence": "Hello world"}}]},
                {"role": "tool", "content": "world Hello"}]
    out = render_chat(messages, tools=tools, add_generation_prompt=True, rng=random.Random(0), version=2)
    assert out == (
        "<|open|>tool<|:|>ãç<|.|>name<|close|>reverse_words<|open_end|>"
        "<|open|>tool<|:|>ãç<|.|>description<|close|>Reverses the order of words in a sentence<|open_end|>"
        "<|open|>tool<|:|>ãç<|.|>parameters<|.|>required<|close|>sentence<|open_end|>"
        "<|open|>tool<|:|>ãç<|.|>parameters<|.|>sentence<|.|>type<|close|>string<|open_end|>"
        "<|open|>tool<|:|>ãç<|.|>parameters<|.|>sentence<|.|>description<|close|>The input sentence<|open_end|>"
        "<|open|><|:|>¦Ä<|close|>Reverse the words: Hello world<|open_end|>"
        "<|open|>exec<|:|>óð<|.|>tool<|close|><|open_ref|>tool<|:|>ãç<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>óð<|.|>instruction<|close|><|open_ref|><|:|>¦Ä<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>óð<|.|>result<|close|><|open_ref|><|:|>åÐ<|close_ref|><|open_end|>"
        '<|open|><|:|>åÐ<|close|><tool_call>{"name": "reverse_words", "arguments": {"sentence": "Hello world"}}</tool_call><|open_end|>'
        "<|end_of_text|>"
        "<|open|><|:|>ïß<|close|><tool_response>world Hello</tool_response><|open_end|>"
        "<|open|>exec<|:|>ý¾<|.|>tool<|close|><|open_ref|>tool<|:|>ãç<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>ý¾<|.|>context<|close|><|open_ref|><|:|>åÐ<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>ý¾<|.|>context<|close|><|open_ref|><|:|>ïß<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>ý¾<|.|>instruction<|close|><|open_ref|><|:|>¦Ä<|close_ref|><|open_end|>"
        "<|open|>exec<|:|>ý¾<|.|>result<|close|><|open_ref|><|:|>ò³<|close_ref|><|open_end|>"
        "<|open|><|:|>ò³<|close|>")


def test_v2_documents():
    docs = [{"title": "T", "text": "x", "llmon_id": "¡¡"}, {"text": "y", "llmon_id": "¢¢"}]
    out = render_chat([{"role": "user", "content": "q", "llmon_exec_id": "££", "llmon_id": "¤¤"}],
                      documents=docs, version=2)
    assert out.startswith("<|open|>doc<|:|>¡¡<|.|>title<|close|>T<|open_end|>"
                          "<|open|>doc<|:|>¡¡<|.|>text<|close|>x<|open_end|>"
                          "<|open|>doc<|:|>¢¢<|.|>title<|self_close|>"
                          "<|open|>doc<|:|>¢¢<|.|>text<|close|>y<|open_end|>")
    assert ("<|open|>exec<|:|>££<|.|>doc<|close|><|open_ref|>doc<|:|>¡¡<|close_ref|><|open_end|>"
            "<|open|>exec<|:|>££<|.|>doc<|close|><|open_ref|>doc<|:|>¢¢<|close_ref|><|open_end|>"
            "<|open|>exec<|:|>££<|.|>instruction<|close|>") in out


def test_v2_parms_count_args_only():
    """A placeholder names an arg; documents and tools are numbered on their own."""
    msgs = [{"role": "user", "instruction": [{"value": "Use " + parm(0), "exec": True}],
             "data": [{"value": "d", "exec": True}]}]
    out = render_chat(msgs, documents=[{"title": "T", "text": "x"}], version=2)
    assert "<|.|>doc<|close|>" in out and "<|.|>arg<|:|>0<|close|>" in out
    with pytest.raises(ValueError):                             # a document is not an arg
        render_chat([{"role": "user", "instruction": [{"value": "Use " + parm(0), "exec": True}]}],
                    documents=[{"title": "T", "text": "x"}], version=2)


def test_v2_unnamed_data_is_context():
    msgs = [{"role": "user", "content": "Summarize this.", "data": [{"value": "d", "exec": True}]}]
    out = render_chat(msgs, rng=random.Random(0), version=2)
    assert "<|.|>context<|close|>" in out and "<|.|>arg<|:|>" not in out
