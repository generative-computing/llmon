# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prompt-injection tripwire, shared by every backend.

This is a HEURISTIC TRIPWIRE, NOT A SECURITY BOUNDARY. It is seven English
keyword patterns. "Ignore all previous instructions" is caught; a paraphrase,
another language, an encoding, or anything mildly adversarial is not. Treat a
positive as a reason to stop and look, and treat a negative as no evidence
either way. Do not put it on a trust boundary.

Why it lives in its own stdlib module rather than inside the rule engine: the
tagger backend does its own segmentation and imports nothing from the rule
engine, so when it became the default it would have reported
`injection_detected: False` unconditionally -- silently dropping the check from
the library's default path. Seven regexes cost nothing, so every backend runs
them and ORs the result in, and the guarantee is identical whether or not torch
is installed.

`Injection` is deliberately not a class in any taxonomy. It is orthogonal to
"what kind of span is this": a span is a Constraint *and* possibly an injection
attempt. Downstream it forces abstention -- an injected segment is never sent to
a downstream model, and rendering one is refused unless explicitly allowed.
"""

import re

__all__ = ["PATTERNS", "detect", "scan_segments", "MAX_WEIGHT"]


def _c(pattern):
    return re.compile(pattern, re.IGNORECASE)


#: (compiled pattern, weight) pairs. Weights are the rule engine's original
#: confidence contributions, kept so the two agree exactly.
PATTERNS = (
    (_c(r'\bignore (all |the |any |your )?(previous|prior|above|earlier|preceding)( instructions| prompts| rules| context)?\b'), 1.0),
    (_c(r'\bdisregard\b.{0,40}\b(instructions?|prompt|rules?|guidelines?)\b'), 0.95),
    (_c(r'\bforget (everything|all|what)\b'), 0.85),
    (_c(r'\byou are now\b|\bnew instructions?\s*:|\bfrom now on\b'), 0.8),
    (_c(r'\b(reveal|show|print|repeat|leak)\b.{0,40}\b(system )?(prompt|instructions?)\b'), 0.9),
    (_c(r'\boverride\b.{0,30}\b(instructions?|rules?|settings?)\b'), 0.85),
    (_c(r'\b(developer|system|admin)\s+mode\b|\bjailbreak\b|\bDAN\b'), 0.85),
)

#: Highest weight any single pattern carries.
MAX_WEIGHT = max(w for _, w in PATTERNS)


def detect(text):
    """Return the list of injection hits in `text`, highest weight first.

    Each hit is `{"pattern": <source>, "weight": float, "start": int,
    "end": int, "text": str}`. An empty list means nothing matched, which is not
    the same as "safe" -- see the module docstring.
    """
    hits = []
    for pattern, weight in PATTERNS:
        for m in pattern.finditer(text):
            hits.append({
                "pattern": pattern.pattern,
                "weight": weight,
                "start": m.start(),
                "end": m.end(),
                "text": m.group(0),
            })
    hits.sort(key=lambda h: (-h["weight"], h["start"]))
    return hits


def scan_segments(result, text):
    """OR injection detection into every segment of a result dict, in place.

    Used by backends that do not run the rule engine and would otherwise report
    no injection at all. Offsets in each segment's hits are absolute, matching
    the invariant that every offset indexes the original prompt.
    """
    for seg in result.get("segments", []):
        start = seg.get("start", 0)
        end = seg.get("end", len(text))
        hits = detect(text[start:end])
        for h in hits:
            h["start"] += start
            h["end"] += start
        if hits:
            seg["injection_detected"] = True
            seg["injection_hits"] = hits
            # An injected segment must never be treated as resolved: the label
            # may be right while the span is still an attack, so it stays
            # flagged for review regardless of model confidence.
            seg["requires_review"] = True
            if not seg.get("abstention_reason"):
                seg["abstention_reason"] = "injection_detected"
        else:
            seg.setdefault("injection_detected", False)
    return result
