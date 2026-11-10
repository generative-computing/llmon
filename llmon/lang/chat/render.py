# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render a chat conversation to LLMON text, in Python.

Message fields (all optional on a user message):
    content      str  -- shorthand for a single executed instruction
    instruction  list of {value, exec}
    data         list of {value, exec}
    requirement  list of {value, exec}
    documents    list of {text, title?, exec}
    tools        list of OpenAI tool dicts + {exec}

`documents` / `tools` may also be passed as arguments, in which case they are emitted once
before the conversation and referenced by every turn.

Privilege: `exec<|.|>instruction` and `exec<|.|>system` are authoritative. Everything reached
through `exec<|.|>input` -- data, documents, tool schemas, tool results -- is material to
read, not a command to follow. No newlines are emitted between blocks.
"""

from __future__ import annotations

import json
import random as _random
import re
from collections.abc import Mapping
from typing import Any

import llmon.lang.common.common as C

# 75 characters, each exactly one token for the Granite tokenizer.
ID_CHARS = "¡¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¹º»¼½¾¿ÀÁÂÃÄÇÉÍÎÐÑÓÖ×ÚÜßàáâãäåæçèéêëìíîïðñòóôõöøùúûüý"
ID_LEN = 2

# Keys that pin an id. Prefixed so they cannot collide with an `id` field a dataset already carries.
ID_KEY = "llmon_id"
EXEC_ID_KEY = "llmon_exec_id"
OUTPUT_ID_KEY = "llmon_output_id"

# Default eos (Granite-4.1). Other models differ , so pass `eos_token=tokenizer.eos_token`.
EOS_TOKEN = "<|end_of_text|>"

TOOL_CALL_OPEN, TOOL_CALL_CLOSE = "<tool_call>", "</tool_call>"
TOOL_RESPONSE_OPEN, TOOL_RESPONSE_CLOSE = "<tool_response>", "</tool_response>"

# The opener of an assistant block: <|open|>assistant<|:|>ID<|close|>. Exec pointers that mention `assistant` are preceded by <|close|>, never <|open|>, so this matches only blocks.
ASSISTANT_OPEN_RE = re.compile(
    re.escape(C.OPEN_TAG + "assistant" + C.COLON_SEPARATOR_TAG)
    + "([" + re.escape(ID_CHARS) + "]{" + str(ID_LEN) + "})"
    + re.escape(C.CLOSE_TAG))

_END = C.OPEN_END_TAG
_OUTPUT_PTR = (C.OPEN_TAG + "exec" + C.DOT_JOINER_TAG + "output" + C.CLOSE_TAG
               + "assistant" + C.COLON_SEPARATOR_TAG)
_USER_LIST_FIELDS = ("instruction", "data", "requirement", "documents", "tools")


def _open(tag: str) -> str:
    return C.OPEN_TAG + tag + C.CLOSE_TAG


def _ref(kind: str, id_: str) -> str:
    return kind + C.COLON_SEPARATOR_TAG + id_


def _ptr(role: str, ref: str) -> str:
    """One exec pointer, e.g. <|open|>exec<|.|>input<|close|>data<|:|>ID<|open_end|>."""
    return _open("exec" + C.DOT_JOINER_TAG + role) + ref + _END


def _out(d: Mapping, key: str) -> str:
    """`{{ d[key] }}` in Jinja: a missing key renders empty, anything else as str()."""
    return str(d[key]) if key in d else ""


def _is_iterable(x: Any) -> bool:
    try:
        iter(x)
    except TypeError:
        return False
    return True


def _flatten_tool(prefix: str, value: Any, pkey: str = "") -> str:
    """One block per JSON leaf. The `function` / `properties` wrappers are dropped from the
    path, as is the redundant `type` of the schema root and of `parameters`, so
    {"function": {"name": "f", "parameters": {"properties": {"x": {"type": "string"}}}}}
    becomes tool<|.|>name and tool<|.|>parameters<|.|>x<|.|>type."""
    if isinstance(value, Mapping):
        parts = []
        for k, v in value.items():
            if k == "type" and pkey in ("", "parameters"):
                continue
            if k in ("function", "properties"):
                parts.append(_flatten_tool(prefix, v, k))
            else:
                parts.append(_flatten_tool(prefix + C.DOT_JOINER_TAG + k, v, k))
        return "".join(parts)
    if _is_iterable(value) and not isinstance(value, str):
        parts = []
        for item in value:
            if isinstance(item, Mapping):
                parts.append(_flatten_tool(prefix, item, pkey))
            else:
                parts.append(_open(prefix) + str(item) + _END)
        return "".join(parts)
    return _open(prefix) + str(value) + _END


def _doc_block(item: Mapping, doc_id: str) -> str:
    s = _open(_ref("doc", doc_id))
    if item.get("title"):
        s += _open("doc" + C.DOT_JOINER_TAG + "title") + str(item["title"]) + _END
    else:
        s += C.OPEN_TAG + "doc" + C.DOT_JOINER_TAG + "title" + C.SELF_CLOSE_TAG
    s += _open("doc" + C.DOT_JOINER_TAG + "text") + _out(item, "text") + _END
    return s + _END


def _without_id(d: Mapping) -> dict:
    return {k: v for k, v in d.items() if k != ID_KEY}


def render_assistant_body(message: Mapping) -> str:
    """The inside of an assistant block: its text content, then one
    <tool_call>{"name": ..., "arguments": ...}</tool_call> per tool call, newline-separated.
    String `arguments` that parse as JSON are emitted verbatim; anything else (including a
    non-JSON string) is JSON-encoded."""
    content = message.get("content")
    parts = [str(content)] if content else []
    for i, tc in enumerate(message.get("tool_calls") or []):
        fn = tc["function"] if "function" in tc else tc
        if i > 0 or content:
            parts.append("\n")
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                json.loads(args)
            except ValueError:
                args = json.dumps(args, ensure_ascii=False)
        else:
            args = json.dumps(args, ensure_ascii=False)
        parts.append(TOOL_CALL_OPEN + '{"name": ' + json.dumps(fn["name"], ensure_ascii=False)
                     + ', "arguments": ' + args + "}" + TOOL_CALL_CLOSE)
    return "".join(parts)


class _Ids:
    """Draws block ids exactly as the template does -- two `rng.choice(ID_CHARS)` calls per
    id, at the same sites in the same order -- so a render with no collision reproduces the
    template's output for the same seed. `unique` redraws a collision; `pinned` ids are
    reserved up front so no draw can take one."""

    def __init__(self, rng, unique: bool, pinned: set[str]):
        self.rng, self.unique = rng, unique
        self.used = set(pinned)

    def _draw(self) -> str:
        return "".join(self.rng.choice(ID_CHARS) for _ in range(ID_LEN))

    def take(self, pinned: str | None = None) -> str:
        if pinned is not None:
            self._draw()
            return pinned
        if not self.unique:
            return self._draw()
        if len(self.used) >= len(ID_CHARS) ** ID_LEN:
            raise ValueError("render needs more than 5,625 ids; the id space is exhausted")
        while True:
            id_ = self._draw()
            if id_ not in self.used:
                self.used.add(id_)
                return id_


def _validate(messages, documents, tools) -> set[str]:
    """Validate the conversation and every pinned id; return the pinned ids. Raises on a
    malformed or repeated id, a pin the renderer would silently ignore, and an assistant turn
    with no exec before it. Unknown roles are skipped, and `add_generation_prompt` after an
    assistant turn opens nothing, as in the template."""
    seen: set[str] = set()

    def add(where: str, v: Any) -> None:
        if v is None:
            return
        if not (isinstance(v, str) and len(v) == ID_LEN and all(c in ID_CHARS for c in v)):
            raise ValueError(f"{where}: {v!r} is not an LLMON id "
                             f"({ID_LEN} characters from ID_CHARS)")
        if v in seen:
            raise ValueError(f"{where}: id {v!r} is pinned more than once")
        seen.add(v)

    for name, items in (("documents", documents), ("tools", tools)):
        for i, item in enumerate(items or []):
            if isinstance(item, Mapping):
                add(f"{name}[{i}].{ID_KEY}", item.get(ID_KEY))

    exec_pending = False
    for i, m in enumerate(messages):
        role = m.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            if any(k in m for k in (ID_KEY, EXEC_ID_KEY, OUTPUT_ID_KEY)):
                raise ValueError(f"messages[{i}]: role {role!r} is not rendered, so its pin "
                                 f"would be ignored")
            continue
        if role == "assistant" and not exec_pending:
            raise ValueError(f"messages[{i}]: an assistant turn must follow a user or tool "
                             f"turn (its block id comes from that turn's exec)")
        if role == "user" or (role == "tool" and (i == len(messages) - 1
                                                  or messages[i + 1].get("role") != "tool")):
            exec_pending = True
        elif role == "assistant":
            exec_pending = False
        if role == "assistant" and any(k in m for k in (ID_KEY, EXEC_ID_KEY, OUTPUT_ID_KEY)):
            raise ValueError(f"messages[{i}]: an assistant block takes its id from the exec "
                             f"before it; pin {OUTPUT_ID_KEY} on that user/tool message")
        if role == "system" and (EXEC_ID_KEY in m or OUTPUT_ID_KEY in m):
            raise ValueError(f"messages[{i}]: a system message has no exec/output block")
        if role == "tool" and (EXEC_ID_KEY in m or OUTPUT_ID_KEY in m):
            run_ends = i == len(messages) - 1 or messages[i + 1].get("role") != "tool"
            if not run_ends:
                raise ValueError(f"messages[{i}]: {EXEC_ID_KEY}/{OUTPUT_ID_KEY} belong on the "
                                 f"LAST tool message of a run, where the exec is emitted")
        if role == "user" and ID_KEY in m and "content" not in m:
            raise ValueError(f"messages[{i}]: {ID_KEY} on a user message pins its `content` "
                             f"block, and this message has no `content`")
        for k in (ID_KEY, EXEC_ID_KEY, OUTPUT_ID_KEY):
            add(f"messages[{i}].{k}", m.get(k))
        if role == "user":
            for field in _USER_LIST_FIELDS:
                for j, item in enumerate(m.get(field) or []):
                    if isinstance(item, Mapping):
                        add(f"messages[{i}].{field}[{j}].{ID_KEY}", item.get(ID_KEY))
    return seen


def render_chat(messages: list[Mapping], documents: list[Mapping] | None = None,
                tools: list[Mapping] | None = None, *, add_generation_prompt: bool = False,
                eos_token: str | None = EOS_TOKEN, rng=None, unique_ids: bool = True) -> str:
    """Render `messages` (plus optional top-level `documents` / `tools`) to LLMON text.

    add_generation_prompt: end on the open assistant block of the final exec, to generate.
    eos_token:   emitted after every assistant turn. Pass `tokenizer.eos_token`; the default
                 is Granite-4.1's. `<|open_end|>` closes a block but is not an eos.
    rng:         anything with `.choice` -- pass random.Random(seed) for a reproducible
                 render. Defaults to the global `random` module, as the template does.
    unique_ids:  redraw an id that is already used. False reproduces the template exactly,
                 collisions included.
    """
    ids = _Ids(rng if rng is not None else _random, unique_ids,
               _validate(messages, documents, tools))
    eos = eos_token or ""
    out: list[str] = []
    w = out.append

    output_id = system_id = exec_body = ""
    kwarg_doc_ids: list[str] = []
    kwarg_tool_ids: list[str] = []
    conv_inputs: list[str] = []

    if documents is not None:
        for item in documents:
            doc_id = ids.take(item.get(ID_KEY))
            w(_doc_block(item, doc_id))
            kwarg_doc_ids.append(doc_id)
    if tools is not None:
        for tool in tools:
            tool_id = ids.take(tool.get(ID_KEY))
            w(_open(_ref("tool", tool_id)) + _flatten_tool("tool", _without_id(tool)) + _END)
            kwarg_tool_ids.append(tool_id)

    last = len(messages) - 1
    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role == "system":
            # Sticky: referenced by every later turn until another system message replaces it.
            system_id = ids.take(msg.get(ID_KEY))
            w(_open(_ref("system", system_id)) + _out(msg, "content") + _END)

        elif role == "user":
            content_id = ids.take(msg.get(ID_KEY))
            exec_id = ids.take(msg.get(EXEC_ID_KEY))
            output_id = ids.take(msg.get(OUTPUT_ID_KEY))
            conv_inputs = []
            bound: dict[str, list[str]] = {f: [] for f in _USER_LIST_FIELDS}

            if "content" in msg:
                w(_open(_ref("instr", content_id)) + _out(msg, "content") + _END)
            for field, tag in (("instruction", "instr"), ("data", "data"), ("requirement", "req")):
                for item in msg.get(field) or []:
                    block_id = ids.take(item.get(ID_KEY))
                    w(_open(_ref(tag, block_id)) + _out(item, "value") + _END)
                    if item.get("exec"):
                        bound[field].append(block_id)
            for item in msg.get("documents") or []:
                doc_id = ids.take(item.get(ID_KEY))
                w(_doc_block(item, doc_id))
                if item.get("exec"):
                    bound["documents"].append(doc_id)
            for item in msg.get("tools") or []:
                tool_id = ids.take(item.get(ID_KEY))
                w(_open(_ref("tool", tool_id)))
                for k, v in item.items():
                    if k not in ("exec", ID_KEY):
                        w(_flatten_tool("tool" + C.DOT_JOINER_TAG + k, v))
                w(_END)
                if item.get("exec"):
                    bound["tools"].append(tool_id)

            # Saved, not just emitted: a following tool turn reissues the same bindings with its own tool results appended.
            body = []
            if "content" in msg:
                body.append(_ptr("instruction", _ref("instr", content_id)))
            body += [_ptr("instruction", _ref("instr", x)) for x in bound["instruction"]]
            if system_id:
                body.append(_ptr("system", _ref("system", system_id)))
            body += [_ptr("input", _ref("doc", x)) for x in kwarg_doc_ids]
            body += [_ptr("input", _ref("data", x)) for x in bound["data"]]
            body += [_ptr("requirement", _ref("req", x)) for x in bound["requirement"]]
            body += [_ptr("input", _ref("doc", x)) for x in bound["documents"]]
            body += [_ptr("input", _ref("tool", x)) for x in kwarg_tool_ids]
            body += [_ptr("input", _ref("tool", x)) for x in bound["tools"]]
            exec_body = "".join(body)

            w(_open(_ref("exec", exec_id)) + exec_body
              + _ptr("output", _ref("assistant", output_id)) + _END)
            if i == last and add_generation_prompt:
                w(_open(_ref("assistant", output_id)))

        elif role == "assistant":
            w(_open(_ref("assistant", output_id)) + render_assistant_body(msg) + _END)
            if msg.get("tool_calls"):
                conv_inputs.append(_ref("assistant", output_id))
            w(eos)

        elif role == "tool":
            # Consecutive tool messages accumulate; the exec is emitted after the last one.
            resp_id = ids.take(msg.get(ID_KEY))
            content = msg.get("content")
            if content is None:
                content = ""
            elif not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            w(_open(_ref("data", resp_id))
              + TOOL_RESPONSE_OPEN + content + TOOL_RESPONSE_CLOSE + _END)
            conv_inputs.append(_ref("data", resp_id))
            if i == last or messages[i + 1].get("role") != "tool":
                exec_id = ids.take(msg.get(EXEC_ID_KEY))
                output_id = ids.take(msg.get(OUTPUT_ID_KEY))
                w(_open(_ref("exec", exec_id)) + exec_body
                  + "".join(_ptr("input", ref) for ref in conv_inputs)
                  + _ptr("output", _ref("assistant", output_id)) + _END)
                if i == last and add_generation_prompt:
                    w(_open(_ref("assistant", output_id)))

    return "".join(out)


def split_prompt_target(text: str) -> tuple[str, str]:
    """Split a rendered conversation at the LAST assistant opener: (everything up to and
    including it, the final assistant body + closer). Whatever follows the closer -- the eos,
    for any model -- is dropped. Returns (text, "") when the text does not end on an
    assistant turn (no assistant block, or a later exec still points at an unfilled one)."""
    matches = list(ASSISTANT_OPEN_RE.finditer(text))
    if not matches:
        return text, ""
    end = matches[-1].end()
    prompt, target = text[:end], text[end:]
    if _OUTPUT_PTR in target:
        return text, ""
    close = target.rfind(_END)
    return prompt, target[:close + len(_END)] if close >= 0 else target


def stop_token_ids(tokenizer) -> list[int]:
    """Token ids generation should stop on: the tokenizer's eos and `<|open_end|>`. Stopping on
    `<|open_end|>` is right only while assistant bodies hold no nested LLMON blocks, which is
    true of everything `render_chat` emits."""
    return [tokenizer.convert_tokens_to_ids(t) for t in (tokenizer.eos_token, C.OPEN_END_TAG) if t]
