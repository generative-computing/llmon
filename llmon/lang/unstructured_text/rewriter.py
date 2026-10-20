# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rule-based rewriter for Instruction segments.

`rewrite(text)` normalises interrogative and polite-framing surface forms
into direct imperatives, returning the rewritten string or None if no
transform applies.

Scope (narrow, targeted):
- Drop polite openers: "Can you", "Please", "I want you to", etc.
- Rewrite bare "Why/What/How" questions into imperative form.
- Multi-sentence: only the first sentence is transformed; the rest is
  appended unchanged.

The original text and all offset data are never touched here; the caller
stores the result in `rewritten_text` as an advisory field.
"""

import re
from typing import Callable


# ---------------------------------------------------------------------------
# Sentence splitter
# ---------------------------------------------------------------------------

def _split_first_sentence(text):
    """Return (first_sentence, remainder) splitting at the first sentence end.

    remainder may be empty. The split preserves the terminal punctuation on the
    first sentence.
    """
    m = re.search(r"(?<=[.?!])\s+", text)
    if m:
        return text[:m.start() + 1], text[m.end():]
    return text, ""


# ---------------------------------------------------------------------------
# Per-sentence transform rules (applied in order; first match wins)
# ---------------------------------------------------------------------------

def _capitalise(s):
    """Capitalise the first character of s."""
    return s[0].upper() + s[1:] if s else s


# Each rule is (compiled_regex, replacement_callable).
# replacement_callable(match) -> str  (the rewritten first sentence)
_Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str]]
_RULES: list[_Rule] = []


def _rule(pattern, flags=re.IGNORECASE):
    def decorator(fn):
        _RULES.append((re.compile(pattern, flags), fn))
        return fn
    return decorator


# --- Polite openers ----------------------------------------------------------

@_rule(r"^(?:Can|Could|Would)\s+you\s+(?:please\s+)?(.+?)(\?)?$")
def _can_you(m):
    body = m.group(1).rstrip()
    return _capitalise(body) + "."


@_rule(r"^Please\s+(.+?)\.?$")
def _please(m):
    return _capitalise(m.group(1).rstrip()) + "."


@_rule(r"^Kindly\s+(.+?)\.?$")
def _kindly(m):
    return _capitalise(m.group(1).rstrip()) + "."


@_rule(r"^Your\s+task\s+is\s+to\s+(.+?)\.?$")
def _your_task(m):
    return _capitalise(m.group(1).rstrip()) + "."


@_rule(
    r"^I\s+(?:want|need|would\s+like|'d\s+like|would\s+love|'d\s+love)"
    r"\s+(?:you\s+|thee\s+)?(?:to\s+)?(.+?)\.?$"
)
def _i_want(m):
    return _capitalise(m.group(1).rstrip()) + "."


# --- Interrogative Why/What/How ----------------------------------------------

@_rule(r"^Why\s+(is|are|does|do|was|were|did|can|could|would|should|has|have)\s+(.+?)(\?)?$")
def _why(m):
    aux, rest = m.group(1), m.group(2).rstrip()
    return f"Explain why {aux} {rest}."


@_rule(r"^What\s+(is|are|was|were)\s+(.+?)(\?)?$")
def _what_is(m):
    copula, rest = m.group(1), m.group(2).rstrip()
    return f"Explain what {rest} {copula}."


@_rule(
    r"^How\s+(?:do|does|can|should|could|would|did)\s+"
    r"(?:I|you|we|one)\s+(.+?)(\?)?$"
)
def _how_do(m):
    rest = m.group(1).rstrip()
    return f"Explain how to {rest}."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rewrite(text):
    """Return a rewritten version of `text`, or None if no rule applies.

    Only the first sentence is transformed; the remainder is appended as-is.
    """
    text = text.strip()
    if not text:
        return None

    first, rest = _split_first_sentence(text)
    first = first.strip()

    for pattern, fn in _RULES:
        m = pattern.match(first)
        if m:
            rewritten_first = fn(m)
            if rest:
                return rewritten_first + " " + rest
            return rewritten_first

    return None


# ---------------------------------------------------------------------------
# Constraint / format clause normaliser
# ---------------------------------------------------------------------------
#
# `rewrite_constraint` is a separate entry point for spans already classified
# as Constraint or Output Format.  Its job is not structural rewriting but
# surface clean-up: removing dangling output-referential phrases that are
# redundant once the span is isolated, and flattening parenthetical markers
# into inline prose so the tagged text reads as a clean standalone directive.
#
# Rules are narrow and conservative — only fire on clear, unambiguous patterns.
# Returns None when nothing was changed.

_CONSTRAINT_RULES: list[_Rule] = []


def _crule(pattern, flags=re.IGNORECASE):
    def decorator(fn):
        _CONSTRAINT_RULES.append((re.compile(pattern, flags), fn))
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Leading-separator normalisation (must run first so other rules see clean text)
#
# When the splitter cuts BEFORE a separator like ", with at least 500 words.",
# the constraint clause keeps the leading ", " or ", no other".
# We strip the leading comma+space and capitalise, then rewrite to a proper
# subject-verb form.
# ---------------------------------------------------------------------------

# ", with at least/most/exactly/no more than N <unit> [rest]"
# → "It must be at least N <unit> [rest]."
# Handles both "with at least 500 words." and "with less than 10 sentences for a proposal."
@_crule(r"^,?\s*with\s+(at least|at most|exactly|no more than|fewer than|less than|more than)\s+(\d+\s+\w+)([^.]*?)(\..*)?$")
def _with_quantifier(m):
    quant = m.group(1)
    amount = m.group(2).strip()
    inline_rest = m.group(3).strip()  # e.g. "for a proposal"
    sentence_rest = (m.group(4) or "").strip()  # e.g. ". The email must include keywords."
    base = f"It must be {quant} {amount}"
    if inline_rest:
        base += f" {inline_rest}"
    if sentence_rest:
        # sentence_rest starts with "." — keep it as a new sentence
        return base + sentence_rest
    return base + "."


# ", no other language is allowed" / ", no other X allowed"
@_crule(r"^,?\s*(no other\b.+)$")
def _no_other(m):
    return _capitalise(m.group(1).rstrip(".") + ".")


# ", separated by X" → "Separate the responses with X."
@_crule(r"^,?\s*separated by\s+(.+?)\s*\.?$")
def _separated_by(m):
    sep = m.group(1).strip()
    return f"Separate the responses with {sep}."


# ", starting with X" / "starting with X" → "It must start with X."
@_crule(r"^,?\s*starting with\s+(.+?)\s*\.?$")
def _starting_with(m):
    start = m.group(1).strip()
    return f"It must start with {start}."


# ", ending with X" → "It must end with X."
@_crule(r"^,?\s*ending with\s+(.+?)\s*\.?$")
def _ending_with(m):
    end = m.group(1).strip()
    return f"It must end with {end}."


# ", marked with/by X" → "Mark it with X."
@_crule(r"^,?\s*marked (?:with|by)\s+(.+?)\s*\.?$")
def _marked_with(m):
    marker = m.group(1).strip()
    return f"Mark it with {marker}."


# ---------------------------------------------------------------------------
# Trailing redundancies (run after leading-separator normalisation)
# ---------------------------------------------------------------------------

# Remove "in your (response|answer|output|song|poem|essay|reply|writing)"
# when it is a trailing phrase.  The span is already scoped to the output
# by its tag, so this reference is redundant.
@_crule(
    r"\s+in (?:your|the)\s+(?:response|answer|output|song|poem|essay|reply|"
    r"writing|text|letter|riddle|email|article|blog post|story|document)\b"
    r"(?:\s*[.!?])?$"
)
def _strip_output_ref(m):
    return ""


# Flatten parenthetical format markers:
# "bullet points (that are marked by an asterisk, *)" → "bullet points marked with an asterisk (*)"
@_crule(r"\s*\(that (?:are|is) marked (?:by|with) an? ([^)]+)\)")
def _flatten_marked_by(m):
    marker = m.group(1).strip().rstrip(".")
    return f" marked with {marker}"


# "sections (marked with/by X)" → "sections marked with X"
@_crule(r"\s*\(marked (?:with|by) ([^)]+)\)")
def _flatten_marked_paren(m):
    marker = m.group(1).strip().rstrip(".")
    return f" marked with {marker}"


# "such as X" parenthetical examples at end — keep inline but remove parens
@_crule(r"\s*\(such as ([^)]+)\)\s*\.?$")
def _flatten_such_as(m):
    return f", such as {m.group(1).strip()}"


# "i.e. X" parenthetical at end → drop (it's an example, belongs in data:)
@_crule(r"\s*,?\s*i\.e\.?\s+[^.]+\.?$")
def _strip_ie(m):
    return ""


def rewrite_constraint(text):
    """Return a cleaned version of a Constraint/Output Format span, or None.

    Applies surface-level normalisation: removes trailing output-referential
    phrases, flattens parenthetical markers, and strips i.e. examples.
    Only fires when the result is actually different from the input.
    """
    text = text.strip()
    if not text:
        return None

    result = text
    changed = False
    for pattern, fn in _CONSTRAINT_RULES:
        new = pattern.sub(fn, result)
        if new != result:
            result = new.strip()
            # Ensure terminal punctuation.
            if result and result[-1] not in ".!?":
                result += "."
            changed = True

    return result if changed else None
