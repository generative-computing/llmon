#!/usr/bin/env python3
# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `rules` backend: regex-based segmentation and classification for prompts.

Pipeline:
    raw text
      -> segment()          : offset-preserving, markdown/structure-aware split
      -> classify_segment() : weighted regex scoring per class
      -> escalation logic    : confidence < 0.8  =>  requires_review = True

Run:
    llmon-segment --backend rules --input prompt.txt
    echo "..." | llmon-segment --backend rules
    python -m llmon.lang.unstructured_text.heuristic prompt.txt   # raw result dict, as JSON

The library entry point is `segment_and_classify(text) -> dict`, which
`api.segment()` wraps.
"""

import json
import re
import sys

from .rules import RULES, CONTENT_FALLBACK_WEIGHT
from .taxonomy import NINE, CONTROL, CONTENT, STRUCTURAL

# The rule table, its confidence calibration and its compatibility sets are
# all written against the NINE class names, so this is pinned to NINE explicitly.
# Never DEFAULT: a three-class label would miss every plane_of lookup, and a
# missing lookup returns None, at which point None == None compares equal for
# every label and confidence inflates instead of raising. See taxonomy.py.
PLANE_OF_LABEL = NINE.plane_of

# ---------------------------------------------------------------------------
# Tunable thresholds (kept as named constants so they are easy to adjust).
# ---------------------------------------------------------------------------

# Two-tier escalation thresholds.
#
# CONFIDENCE_THRESHOLD_HARD applies when a segment is genuinely ambiguous:
#   - two incompatible heads competing (multi_class), or
#   - the margin between the top two conflicting heads is narrow (weak_margin), or
#   - an injection is present.
# In these cases we demand high confidence before trusting a rule match.
#
# CONFIDENCE_THRESHOLD_CLEAN applies when the primary is a clean control-plane
# prediction with only compatible modifiers (the overwhelmingly common shape:
# Instruction/Constraint/Output Format with no rival head). The rule engine is
# reliably right here even at moderate confidence, so a lower bar avoids
# flagging clearly-correct prompts for review unnecessarily.
#
# 0.68 rationale: on IFEval prompts, "Constraint @ 0.6" (27 cases, the
# second-largest escalation cluster) were overwhelmingly single-class
# control-plane segments whose only weakness was a sole moderate-weight keyword
# hit (e.g. "should"), and all of them were correctly labelled
# Instruction/Constraint. Dropping from 0.72 to 0.68 stops flagging these
# without touching ambiguous or multi-class cases (those use HARD).
CONFIDENCE_THRESHOLD_HARD  = 0.8   # ambiguous / conflicted segments
CONFIDENCE_THRESHOLD_CLEAN = 0.68  # clean compatible-cluster segments

# Kept as a single name for external callers and for the evaluation harness.
# that import CONFIDENCE_THRESHOLD.  Points at the hard threshold so downstream
# logic (which sees escalated segments) still uses the conservative value.
CONFIDENCE_THRESHOLD = CONFIDENCE_THRESHOLD_HARD
LENGTH_SOFT_LIMIT = 280        # chars; beyond this a length penalty kicks in
LENGTH_FLOOR = 0.55            # length penalty never drops below this factor
WEAK_MARGIN = 0.15             # top-vs-second score gap considered "ambiguous"

# ---------------------------------------------------------------------------
# Co-label compatibility model (calibration; see classify_segment).
#
# Real prompts overwhelmingly take the shape "do X, with constraints/format Y"
# -- e.g. "Write a review ... in all lowercase, in JSON". Instruction,
# Constraint and Output Format there are NOT rival labels competing for one
# slot; the constraint and format MODIFY the instruction. Treating them as
# rivals (dividing confidence by their combined mass + a multi-class penalty)
# made the single most common, easiest prompt shape escalate needlessly.
#
# So we model which labels are "directive heads" and which are "modifiers"
# that legitimately attach to a head. When the extra labels in a segment are
# all compatible modifiers/companions of the primary, they do NOT suppress its
# confidence. We only treat co-occurrence as genuine ambiguity (and escalate)
# when two *incompatible heads* compete, or an injection is present.

# Labels that can stand as the main intent of a segment.
DIRECTIVE_HEADS = {"Instruction", "Role", "Constraint", "Output Format"}

# For each primary label, the set of other labels that are compatible *with it
# as modifiers/companions* -- their presence is expected, not contradictory.
COMPATIBLE_MODIFIERS = {
    "Instruction":   {"Constraint", "Output Format", "Example", "Context"},
    "Role":          {"Instruction", "Constraint", "Output Format", "Context"},
    "Constraint":    {"Output Format", "Context", "Example"},
    "Output Format": {"Constraint", "Context", "Example", "Structured Data"},
    "Context":       {"Example", "Structured Data", "Quoted Content"},
    "Example":       {"Structured Data", "Quoted Content", "Context"},
    "Content":       {"Context"},
    "Structured Data": {"Output Format", "Context"},
    "Quoted Content":  {"Context", "Example"},
}

# How much a *compatible* modifier REINFORCES the primary. Two signals that
# agree -- a directive head plus a constraint/format that legitimately modifies
# it -- are corroborating evidence that the segment really is what the head says
# it is, so they lift confidence toward a ceiling rather than discounting it.
# The lift saturates (diminishing returns): the first compatible modifier helps
# most, a pile of them doesn't make the segment magically certain. Bounded by
# MODIFIER_REINFORCE_CAP so corroboration alone can never manufacture certainty
# from a weak head.
MODIFIER_REINFORCE = 0.12        # max fractional lift toward the ceiling
MODIFIER_REINFORCE_CAP = 0.95    # reinforcement may never push confidence above this

# How much an *incompatible* competing label's mass discounts the primary
# (large): incompatible heads genuinely contend, so confidence drops and the
# segment escalates.
CONFLICT_DISCOUNT = 1.0

# Compatibility is symmetric: if a Constraint legitimately modifies an
# Instruction, then an Instruction-signal is equally compatible alongside a
# Constraint -- it must not matter which of the two happened to score higher
# and become "primary". We build the symmetric closure of COMPATIBLE_MODIFIERS
# once, so "Write a poem of at least 350 words" is coherent whether Instruction
# or Constraint wins the score race.
_SYMMETRIC_COMPAT = {lbl: set(mods) for lbl, mods in COMPATIBLE_MODIFIERS.items()}
for _lbl, _mods in COMPATIBLE_MODIFIERS.items():
    for _m in _mods:
        _SYMMETRIC_COMPAT.setdefault(_m, set()).add(_lbl)


def _compatible_with(label):
    """Labels that legitimately co-occur with `label` (symmetric)."""
    return _SYMMETRIC_COMPAT.get(label, set())


# ===========================================================================
# 1. SEGMENTATION
# ===========================================================================
#
# Goal: produce segments that are already plausibly single-class, without any
# ML. We do a single forward pass that carves the document into atomic blocks
# in priority order, always preserving original character offsets:
#
#   1. fenced code blocks  ``` ... ```        -> atomic (never split inside)
#   2. paired tag blocks   <data>...</data>   -> atomic
#   3. markdown heading groups (# Heading + following body until next break)
#   4. blank-line-separated paragraphs
#
# Offsets are recorded against the RAW input. We then strip whitespace and
# adjust the offsets inward so that raw[start:end] == emitted text exactly.

_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_TAG_BLOCK_RE = re.compile(r"^[ \t]*<([a-zA-Z][\w\-]*)>[\s\S]*?</\1>[ \t]*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+\S", re.MULTILINE)

# Triple-quote fences delimit pasted reference data (the common RAG / "answer
# based on the following: \"\"\" ... \"\"\"" pattern). A fenced block is ONE data
# artifact in correct LLMON, not a place to look for instructions/constraints, so
# we carve it atomic and emit a single span for it rather than clause-splitting
# the interior. This keys purely on the structural delimiter, not on any prompt
# content, so it generalizes to any document-stuffed prompt.
# Match a closed fence ("""..."""), or an UNTERMINATED opening fence that runs to
# end-of-text. Real prompts often open a data block with """ and never close it
# (the pasted document simply runs to the end), and that trailing block is just as
# much a single data artifact as a closed one.
_DATA_FENCE_RE = re.compile(r'"""[\s\S]*?(?:"""|\Z)')

# Markers that signal machine-structured records inside a block: bracketed
# indices ([1], [3.0 ...]), key:value lines, and bullet/section rows. Used to
# decide whether a carved data block is "Structured Data" vs free-text "Content".
_RECORD_LINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"\[[^\]]+\]"                       # [1]  [3.0 Altay ...]
    r"|[-*•]\s+\S"                       # bullet
    r"|[\w \t]{1,40}:\s*\S"              # key: value
    r"|\d+[.)]\s+\S"                     # 1.  2)
    r")",
    re.MULTILINE,
)


def _strip_span(text, start, end):
    """Trim whitespace from raw[start:end], returning the adjusted span.

    Returns (new_start, new_end, stripped_text), or None if all whitespace.
    """
    chunk = text[start:end]
    lstripped = chunk.lstrip()
    left = len(chunk) - len(lstripped)
    stripped = lstripped.rstrip()
    if not stripped:
        return None
    new_start = start + left
    new_end = new_start + len(stripped)
    return new_start, new_end, stripped


def _carve_atomic(text):
    """Find atomic (never-split) spans: fenced code, paired tags, data fences.

    Returns a sorted, non-overlapping list of (start, end) spans.
    """
    spans = []
    for rx in (_FENCE_RE, _TAG_BLOCK_RE, _DATA_FENCE_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    # Resolve overlaps: keep earliest-starting, drop anything it covers.
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s < merged[-1][1]:
            # overlaps a span we already kept; extend if it reaches further
            ps, pe = merged[-1]
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def _data_block_label(block_text):
    """Label a carved data block by its dominant surface markers.

    Returns "Structured Data" when the block is mostly machine-structured record
    lines (bracketed indices, key:value, bullets, numbered rows), else "Content".
    A fenced reference block is one data artifact either way; this only chooses
    which content-plane tag best describes it.
    """
    lines = [ln for ln in block_text.splitlines() if ln.strip()]
    if not lines:
        return "Content"
    record_lines = sum(1 for ln in lines if _RECORD_LINE_RE.match(ln))
    return "Structured Data" if record_lines >= max(2, len(lines) * 0.4) else "Content"


def _split_freeform(text, base, segments):
    """Split a freeform region (no atomic blocks) into heading-groups and
    paragraphs, appending (start, end) raw spans (offset by `base`) to
    `segments`.
    """
    if not text.strip():
        return

    # Find heading line starts; a heading begins a new group that runs until
    # the next heading or a blank-line paragraph break.
    heading_starts = [m.start() for m in _HEADING_RE.finditer(text)]

    # Build cut points: split on blank lines (paragraph breaks) AND just
    # before every heading, so a heading always starts its own segment.
    cuts = {0, len(text)}
    for m in re.finditer(r"\n[ \t]*\n", text):
        cuts.add(m.end())          # paragraph break: new segment after blanks
    for h in heading_starts:
        cuts.add(h)                # heading boundary
    ordered = sorted(cuts)

    for i in range(len(ordered) - 1):
        s, e = ordered[i], ordered[i + 1]
        adj = _strip_span(text, s, e)
        if adj is None:
            continue
        ns, ne, _ = adj
        segments.append((base + ns, base + ne))


def segment(text):
    """Split `text` into a list of (start, end) spans over the raw input.

    Guarantees: spans are non-overlapping, in order, contain no leading or
    trailing whitespace, and text[start:end] is non-empty.
    """
    atomic = _carve_atomic(text)
    segments = []
    cursor = 0
    for a_start, a_end in atomic:
        # Freeform region before this atomic block.
        if a_start > cursor:
            _split_freeform(text[cursor:a_start], cursor, segments)
        # The atomic block itself, whitespace-trimmed.
        adj = _strip_span(text, a_start, a_end)
        if adj is not None:
            segments.append((adj[0], adj[1]))
        cursor = a_end
    # Trailing freeform region.
    if cursor < len(text):
        _split_freeform(text[cursor:], cursor, segments)

    return segments


# ===========================================================================
# 2. CLASSIFICATION
# ===========================================================================


def _anchor_positions(seg_text):
    """Positions an anchored rule may fire at: the segment start, every line
    start, and every sentence start (immediately after [.!?] + whitespace).

    Anchored rule patterns hardcode a literal leading ``^\\s*`` (see
    _ANCHOR_STRIP_RE), so re.finditer/match can only ever offer a candidate
    at true line starts (with MULTILINE) -- never at a position mid-line,
    even if that position is the start of a new sentence. That meant a
    genuine directive clause beginning mid-paragraph ("Melbourne has a
    newspaper... Can you suggest a name...?") lost its evidence entirely
    during the whole-segment _scan pass -- clause-splitting never got a
    chance to isolate it because the anchored Instruction rule never had a
    match to offer in the first place. A confusion-matrix sweep on the
    held-out set found 15 real prompts hitting exactly this gap
    (all Content/Instruction mislabels).

    _scan tries each anchored rule's un-anchored pattern body via
    `pattern.match(seg_text, pos)` at every position this yields, rather than
    relying on finditer to happen to produce a hit there. Sentence-boundary
    anchoring doesn't open up to arbitrary mid-clause matches: quoted
    dialogue like `"Well," he said, "can you..."` has no [.!?] immediately
    before "can", so that position isn't offered.
    """
    positions = {0}
    for i, ch in enumerate(seg_text):
        if ch == "\n":
            positions.add(i + 1)
    for m in re.finditer(r"[.!?](?=[ \t]|\n|$)", seg_text):
        j = m.end()
        while j < len(seg_text) and seg_text[j] in " \t":
            j += 1
        positions.add(j)
    return positions


# Anchored rule patterns are authored with a literal leading "^\s*" so the
# pattern reads naturally on its own; _scan strips it and instead tries the
# remaining pattern body with pattern.match(text, pos) at each candidate
# anchor position, since a literal "^" only ever matches absolute position 0
# regardless of MULTILINE/pos (see _anchor_positions docstring).
_ANCHOR_STRIP_RE = re.compile(r"^\^\\s\*")


def _scan(seg_text):
    """Run every rule against the segment text.

    Returns a list of evidence dicts with offsets RELATIVE TO THE SEGMENT.
    Anchored rules count at the start of the segment, the start of a line, or
    the start of a sentence (see _anchor_positions).
    """
    evidence = []
    anchor_positions = None
    for rule in RULES:
        matches = []
        if rule.anchored:
            if anchor_positions is None:
                anchor_positions = _anchor_positions(seg_text)
            body = _ANCHOR_STRIP_RE.sub("", rule.pattern.pattern, count=1)
            body_re = re.compile(body, rule.pattern.flags)
            for pos in anchor_positions:
                m = body_re.match(seg_text, pos)
                if m:
                    matches.append(m)
        else:
            matches = list(rule.pattern.finditer(seg_text))
        for m in matches:
            matched = m.group(0)
            # Trim surrounding whitespace from the reported match for clarity.
            lead = len(matched) - len(matched.lstrip())
            trail = len(matched) - len(matched.rstrip())
            start = m.start() + lead
            end = m.end() - trail
            if end <= start:
                continue
            evidence.append({
                "label": rule.label,
                "text": seg_text[start:end],
                "start": start,
                "end": end,
                "weight": round(rule.weight, 4),
            })
    # Collapse duplicate (label, span) hits to the strongest, so neither the
    # scores nor the displayed evidence are inflated by overlapping rules.
    best = {}
    for e in evidence:
        key = (e["label"], e["start"], e["end"])
        if key not in best or e["weight"] > best[key]["weight"]:
            best[key] = e
    return list(best.values())


def _aggregate_scores(evidence):
    """Aggregate evidence into a per-class score in [0, 1].

    A class's score is its strongest single match, plus a damped contribution
    from additional matches (so repeated weaker signals add a little, but the
    score stays bounded and a single strong hit dominates).
    """
    by_label = {}
    for ev in evidence:
        by_label.setdefault(ev["label"], []).append(ev["weight"])

    scores = {}
    for label, weights in by_label.items():
        weights = sorted(weights, reverse=True)
        score = weights[0]
        for w in weights[1:]:
            score += w * 0.15 * (1.0 - score)   # diminishing returns, capped <1
        scores[label] = min(1.0, score)
    return scores


def _length_penalty(seg_text):
    """Decaying multiplier for long segments (rule-based reliability drops)."""
    n = len(seg_text)
    if n <= LENGTH_SOFT_LIMIT:
        return 1.0
    over = n - LENGTH_SOFT_LIMIT
    # Halve the remaining headroom for each ~600 chars over the limit.
    factor = 0.5 ** (over / 600.0)
    return max(LENGTH_FLOOR, 1.0 - (1.0 - LENGTH_FLOOR) * (1.0 - factor))


def classify_segment(seg_text):
    """Classify one segment. Returns the per-segment result dict (without the
    segment_index / start / end, which the caller attaches).
    """
    evidence = _scan(seg_text)

    # Injection is a safety flag, not a content class. Separate it out so it
    # never becomes a primary_class but always forces escalation.
    injection_hits = [e for e in evidence if e["label"] == "Injection"]
    class_evidence = [e for e in evidence if e["label"] != "Injection"]

    scores = _aggregate_scores(class_evidence)

    # No positive class signal at all -> tentative Content fallback or Unknown.
    if not scores:
        return _unknown_or_fallback(seg_text, injection_hits, evidence)

    # Rank classes; build the multi-label classification list.
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    classifications = [{"label": lbl, "score": round(s, 4)} for lbl, s in ranked]
    primary_label, primary_score = ranked[0]

    total_mass = sum(scores.values())

    # --- Compatibility-aware confidence (calibration) ---
    # Partition the *other* labels into compatible modifiers/companions of the
    # primary vs. incompatible rival heads. Compatible mass is expected for the
    # prompt shape and barely discounts confidence; incompatible mass is a real
    # contention signal and discounts heavily.
    compatible = _compatible_with(primary_label)
    primary_plane = PLANE_OF_LABEL.get(primary_label)
    reinforce_mass = 0.0   # same-plane compatible mass: genuine corroboration
    neutral_mass = 0.0     # cross-plane compatible mass: co-occurs, doesn't vote
    conflict_mass = 0.0
    incompatible_labels = []
    for lbl, sc in ranked[1:]:
        if lbl in compatible:
            # Compatible AND on the primary's own plane => the signals agree
            # about what kind of thing this is, so it reinforces. Compatible
            # but cross-plane (e.g. an Output-Format head with a Structured-Data
            # literal beside it) merely co-occurs -- coherent, but not evidence
            # the head is correct -- so it neither lifts nor sinks confidence.
            if PLANE_OF_LABEL.get(lbl) == primary_plane:
                reinforce_mass += sc
            else:
                neutral_mass += sc
        else:
            conflict_mass += sc
            incompatible_labels.append(lbl)

    # Confidence is anchored on the primary's OWN absolute strength (strong
    # local evidence dominates). Compatible modifiers REINFORCE it: a directive
    # head plus a constraint/format that legitimately modifies it are agreeing
    # signals -- "What is X? Respond in JSON, exactly two sentences" is *more*
    # clearly a control instruction than the bare head alone, so corroboration
    # lifts confidence toward a ceiling. The lift saturates (the first modifier
    # helps most) and is bounded, so corroboration can never fabricate
    # certainty from nothing. Conflicting mass still discounts fully -- those
    # are genuinely competing heads, not agreement.
    reinforce = MODIFIER_REINFORCE * (1.0 - 0.5 ** reinforce_mass)
    base_conf = primary_score + (MODIFIER_REINFORCE_CAP - primary_score) * reinforce
    base_conf = min(MODIFIER_REINFORCE_CAP, base_conf)
    if conflict_mass:
        base_conf *= max(0.0, 1.0 - CONFLICT_DISCOUNT * conflict_mass / (primary_score + 1e-9))
    base_conf = max(0.0, base_conf)

    # Only an *incompatible* rival counts as genuine multi-class ambiguity.
    multi_class = bool(incompatible_labels)

    # Weak margin only matters between competing heads, not head-vs-modifier —
    # with one exception: Role is a structural head that can genuinely frame the
    # whole prompt even when technically "compatible" with the control-plane
    # primary. If Role scores close to a control-plane primary, the rules cannot
    # reliably resolve which is the true head, so we treat it as a hard case.
    top_conflict = max((sc for lbl, sc in ranked[1:]
                        if lbl in incompatible_labels), default=0.0)
    top_role = max((sc for lbl, sc in ranked[1:] if lbl == "Role"), default=0.0)
    weak_margin = (
        (multi_class and (primary_score - top_conflict) < WEAK_MARGIN)
        or (primary_label != "Role" and (primary_score - top_role) < WEAK_MARGIN)
    )

    # --- Length penalty (conditional) ---
    # Length weakens rule-based reliability mainly when the segment is *also*
    # ambiguous: a long blob with competing heads is exactly where rules get
    # lost. But a long, high-purity directive with only compatible modifiers
    # (the typical IFEval prompt -- "Write a 400-word ... in JSON, no commas")
    # is still trustworthy. So we only apply the length penalty when there is
    # genuine conflict; clean compatible segments are exempt.
    if multi_class:
        len_factor = _length_penalty(seg_text)
    else:
        len_factor = 1.0
    confidence = base_conf * len_factor

    # Split confidence contribution by plane for the structural/semantic fields.
    structural_conf, semantic_conf = _plane_split(scores, confidence, total_mass)

    confidence = round(min(1.0, confidence), 4)

    # --- Escalation decision (two-tier threshold) ---
    # Hard cases use the conservative bar; clean control-plane predictions use
    # the relaxed bar. "Clean" means: primary is a directive head
    # (Instruction / Constraint / Output Format), no incompatible rival, and
    # no injection or weak margin. Everything else — including cases where an
    # Example or Role beat out Instruction at moderate confidence — is "hard"
    # and must clear the higher bar before the rule label is trusted.
    injected = bool(injection_hits)

    is_clean_control = (
        primary_label in DIRECTIVE_HEADS
        and primary_label in {"Instruction", "Constraint", "Output Format"}
        and not multi_class
        and not weak_margin
        and not injected
    )
    threshold = CONFIDENCE_THRESHOLD_CLEAN if is_clean_control else CONFIDENCE_THRESHOLD_HARD
    requires_review = confidence < threshold
    reason = _abstention_reason(
        injected=injected,
        confidence=confidence,
        multi_class=multi_class,
        weak_margin=weak_margin,
        len_factor=len_factor,
        primary_score=primary_score,
    )

    return {
        "text": seg_text,
        "primary_class": primary_label,
        "semantic_plane": PLANE_OF_LABEL.get(primary_label),
        "confidence": confidence,
        "structural_confidence": round(structural_conf, 4),
        "semantic_confidence": round(semantic_conf, 4),
        "requires_review": requires_review,
        "abstention_reason": reason,
        "injection_detected": injected,
        "classifications": classifications,
        "evidence": _sorted_evidence(evidence),
    }


def _unknown_or_fallback(seg_text, injection_hits, evidence):
    """Handle segments with no positive class signal.

    A non-trivial segment gets a low-confidence Content fallback (it is *some*
    text); a trivial one (e.g. a lone heading) stays Unknown at 0.0.
    """
    injected = bool(injection_hits)

    # Treat as Unknown if it's basically just a heading / very short marker.
    stripped = seg_text.strip()
    is_headingish = bool(re.fullmatch(r"#{1,6}\s+.{0,40}", stripped)) or len(stripped) < 8

    if is_headingish and not injected:
        return {
            "text": seg_text,
            "primary_class": None,
            "semantic_plane": None,
            "confidence": 0.0,
            "structural_confidence": 0.0,
            "semantic_confidence": 0.0,
            "requires_review": True,
            "abstention_reason": "low_signal",
            "injection_detected": False,
            "classifications": [],
            "evidence": _sorted_evidence(evidence),
        }

    # Tentative Content fallback, dampened by length.
    conf = round(CONTENT_FALLBACK_WEIGHT * _length_penalty(seg_text), 4)
    return {
        "text": seg_text,
        "primary_class": "Content",
        "semantic_plane": CONTENT,
        "confidence": conf,
        "structural_confidence": 0.0,
        "semantic_confidence": conf,
        "requires_review": True,                       # always: it's only a guess
        "abstention_reason": "injection_detected" if injected else "low_signal",
        "injection_detected": injected,
        "classifications": [{"label": "Content", "score": round(CONTENT_FALLBACK_WEIGHT, 4)}],
        "evidence": _sorted_evidence(evidence),
    }


def _plane_split(scores, confidence, total_mass):
    """Apportion the final confidence across structural vs semantic planes.

    `structural_confidence` reflects STRUCTURAL-plane mass (Role/Context),
    `semantic_confidence` reflects everything else (control + content). They
    are each scaled by the same final confidence so they sum to it.
    """
    if total_mass <= 0:
        return 0.0, 0.0
    structural_mass = sum(
        s for lbl, s in scores.items() if PLANE_OF_LABEL.get(lbl) == STRUCTURAL
    )
    structural_frac = structural_mass / total_mass
    structural_conf = confidence * structural_frac
    semantic_conf = confidence * (1.0 - structural_frac)
    return structural_conf, semantic_conf


def _abstention_reason(injected, confidence, multi_class, weak_margin,
                       len_factor, primary_score):
    """Pick the single most salient reason a segment is uncertain.

    Returns None when the segment is confident and clean.
    """
    if injected:
        return "injection_detected"
    if confidence >= CONFIDENCE_THRESHOLD:
        return None
    # Below threshold: report the dominant cause, most-specific first.
    if weak_margin:
        return "weak_margin"
    if multi_class:
        return "multi_class"
    if len_factor < 1.0:
        return "too_long"
    if primary_score < 0.6:
        return "low_signal"
    return "low_signal"


def _sorted_evidence(evidence):
    """Dedupe (same label + span) keeping the strongest, then sort by weight.

    Two rules for the same class can match the identical span (e.g. an anchored
    strong imperative and its weaker unanchored variant). We keep only the
    highest-weight hit per (label, start, end) so evidence stays clean.
    """
    best = {}
    for e in evidence:
        key = (e["label"], e["start"], e["end"])
        if key not in best or e["weight"] > best[key]["weight"]:
            best[key] = e
    return sorted(best.values(), key=lambda e: (-e["weight"], e["start"]))


# ===========================================================================
# 3. SUB-SEGMENT SPAN EXTRACTION
# ===========================================================================
#
# Spans are semantic-clause-level sub-spans within a segment, NOT just the
# character positions of rule-keyword matches.  For example:
#
#   "Write me a funny song with less than 10 sentences for a proposal."
#    ^Instruction: 0-21    ^Constraint: 27-49        ^Content: 50-...
#
# Strategy:
#   1. Split the segment at sentence/clause boundaries.
#   2. For each clause, aggregate rule evidence that falls within it.
#   3. Assign the label with the highest evidence score; inherit the segment's
#      primary class when no evidence falls in the clause.
#   4. Merge adjacent same-label clauses.
#   5. Always guarantee at least one fallback span (the whole segment).

# Unambiguous line-initial imperative verbs. When the whole segment reduces to
# a single span AND this pattern matches at the start, the span is forced to
# Instruction regardless of what classify_segment() said. Rationale: constraint
# evidence (high-weight "at least N words", "must", "should") can outscore a
# lone imperative verb and flip the primary_class to Constraint, but a segment
# that *starts* with one of these verbs is definitionally a directive task.
_IMPERATIVE_ANCHOR_RE = re.compile(
    r"^\s*(?:please\s+|kindly\s+)?(?:"
    r"summarize|analyze|analyse|extract|rewrite|generate|translate|"
    r"list|classify|categorize|write|explain|create|identify|describe|"
    r"compare|evaluate|produce|compute|calculate|determine|provide|give|"
    r"convert|transform|find|detect|label|annotate|paraphrase|outline|"
    r"draft|compose|suggest|brainstorm|complete|answer|solve|name|"
    r"blog|come up with|repeat|"
    r"ask|tell|show|pick|choose|select|rate|rank|order|sort|count|"
    r"estimate|predict|recommend|discuss|elaborate|expand|clarify|"
    r"justify|prove|demonstrate|illustrate"
    r")\b",
    re.IGNORECASE,
)

# Patterns that mark the START of a new clause.
# Ordered by specificity: named clause-starters first (most reliable), then
# generic sentence-boundary signals. All matches are non-consuming — the
# split point is the END of the match (i.e. the first char of the new clause).
_CLAUSE_SPLIT_RE = re.compile(
    # Named directive clause-starters after sentence punctuation or comma.
    # Consuming the leading separator prevents the generic branch below from
    # firing a second time at the same position.
    r"[.?!,]\s+(?="
    r"(?:Do not|Don't|Must not|Never|Cannot|Can't|"
    r"Make sure|Make it|Make (?:the|your|all)|"
    r"Ensure|Include|Add a|Add the|Add \d|"
    r"Avoid|Refrain|Limit|"
    r"Write it|Write in|Write the|Write your|"
    r"Start|Begin|End (?:with|your)|"
    r"Use|Put|Wrap|Format|Respond|Answer|Output|"
    r"Separate|Divided?|Break|Split|"
    r"Highlight|Italicize|Capitalize|Mark(?:ed)? (?:the|each)|"
    r"Repeat the|First repeat|"
    r"Your (?:answer|response|output|entire|whole)|"
    r"The (?:response|answer|output|result|entire|whole)|"
    r"It should|There should|There must|"
    r"First|Then|Finally|Also|Additionally|Furthermore|"
    r"In other words|In (?:the )?following|"
    r"Please (?:do not|don't|avoid|use|include|ensure|make|provide|note|follow)|"
    r"Note that|Keep in mind|"
    r"No other|no other|"
    r"Given (?:the|this|a|an|that)\b"
    r")\b)"
    # Generic: sentence-ending punctuation + whitespace + capital letter.
    r"|(?<=[.?!])\s+(?=[A-Z])"
    # After a newline.
    r"|(?<=\n)"
    # Comma + imperative verb at line-initial position (e.g. "Given that X, ask Y").
    # Only split when the word after the comma is an unambiguous line-initial
    # imperative from the main verb list — not every comma-verb sequence.
    r"|(?:,\s*)(?=(?:summarize|analyze|analyse|extract|rewrite|generate|translate|"
    r"list|classify|categorize|write|explain|create|identify|describe|compare|"
    r"evaluate|produce|compute|calculate|determine|provide|give|convert|transform|"
    r"find|detect|label|annotate|paraphrase|outline|draft|compose|suggest|"
    r"brainstorm|complete|answer|solve|name|come up with|ask|tell|show|pick|"
    r"choose|select|rate|rank|order|sort|count|estimate|predict|recommend|"
    r"discuss|elaborate|expand|clarify|justify|prove|demonstrate|illustrate)\b)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Body-vs-addendum detection
# ---------------------------------------------------------------------------
#
# Many prompts have the shape: [imperative verb + body text (context/content)]
# followed by a FINAL SENTENCE that is a constraint or format directive.
# Example:
#   "critique this startup pitch: <long pitch text>. Do not use any commas."
#   "Summarize the article below: <article>. Your response should be in English."
#
# The problem: if the only control-plane evidence keyword lives in the final
# sentence, it still wins as primary_class for the whole segment. The body text
# (which is Context or Quoted Content) gets mislabeled.
#
# Solution: detect when ALL control-plane evidence is clustered in the last
# sentence(s) and the body is long enough to be a distinct span, then inject a
# split point at the sentence boundary before the trailing addendum.
#
# This runs as a post-processing step on the clause list in _extract_sub_spans.

_ADDENDUM_CONTROL_RE = re.compile(
    # Strong constraint starters at the beginning of a sentence
    r"^(?:"
    r"Do not\b|Don't\b|Must not\b|Never\b|Cannot\b|Can't\b|"
    r"Make sure\b|Make it\b|Ensure\b|"
    r"Avoid\b|Refrain\b|Limit\b|"
    r"Use only\b|Only use\b|"
    r"Be sure\b|Be certain\b|"
    r"Note that\b|Keep in mind\b|"
    r"Your (?:response|answer|output|entire|whole)\b|"
    r"The (?:response|answer|output|result)\b|"
    r"It should\b|It must\b|There should\b|There must\b|"
    r"(?:Respond|Answer|Output|Reply|Write) (?:in|with|using|only)\b|"
    r"(?:Wrap|Enclose|Put|Place) (?:your|the|it|all)\b|"
    r"(?:Use|Follow) (?:the |this |a )?(?:exact |following |given )?format\b|"
    r"(?:Format|Structure) (?:your|the)\b|"
    r"(?:In|Using) (?:all )?(?:capital|uppercase|lowercase|JSON|YAML|XML|Markdown)\b"
    r")",
    re.IGNORECASE,
)

# Inline constraint patterns that appear mid-sentence without a sentence break.
# These are applied as additional cut points inside _split_clauses.
# Each pattern matches the SEPARATOR before the constraint clause; the split
# point is placed at the start of the constraint text (after the separator).
# ---------------------------------------------------------------------------
# Intra-sentence clause candidates (lowercase continuations)
# ---------------------------------------------------------------------------
#
# Measured bottleneck: segmentation under-splits ~3-4x more often than it
# over-splits. On the held-out set, 95 predicted spans straddled more than one
# ground-truth label while only 24 ground-truth labels were recoverable by
# merging; on the training set the ratio was 635:202. A span containing both an
# Instruction and an Output Format has NO correct single label, so this is a hard
# ceiling no amount of better labelling can break: only 38-42% of GT labels had a
# well-aligned (IoU>=0.9) predicted span.
#
# Root cause: 27-38% of GT boundaries fall INSIDE a sentence, and 17-27% of
# sentences carry 2+ labels. _CLAUSE_SPLIT_RE handles capitalized clause
# starters after terminal punctuation, but the common real-world shape is a
# LOWERCASE continuation -- "..., and use JSON", "..., otherwise output X".
#
# DELIBERATELY VOCABULARY-FREE. An earlier version of this rule keyed on a ~45
# verb whitelist ("use|write|include|wrap|capitalize|...") harvested from the
# training set. It raised train precision but MEASURABLY HURT
# generalization -- the whole point of this tool is to land in the right ballpark
# on arbitrary unseen prompts, not to fit this corpus. Held-out comparison
# (tuned on the training set, scored on the held-out set):
#
#   cue set                        train prec      test prec
#   structural only (punctuation)     31.6%          59.4%
#   + closed-class conjunctions       18.9%          45.2%
#   + 45-verb whitelist               25.6%          39.7%
#
# Every vocabulary addition LOWERED test precision (59.4% -> 39.7%), i.e. the
# verb list was memorizing corpus phrasing. So this keeps only cues that are
# structural (punctuation-shaped) plus ONE closed-class word, "otherwise", which
# is a genuine discourse connective rather than task vocabulary and is the only
# lexical addition that improved train precision (31.6% -> 38.0%) without
# costing test precision (59.4% -> 59.4%).
#
# If you extend this, re-run the held-out check before keeping the addition; a
# cue that only helps on the training set is actively harmful.
_INTRA_CLAUSE_RE = re.compile(
    # Semicolon — an explicit clause delimiter in any English prose.
    r";\s+"
    # Quote opening a new span after a colon/comma ("...following: \"...\"").
    r"|[:,]\s+(?=\")"
    # ", such as" / ", i.e." / ", e.g." / ", like:" — exemplar markers that
    # introduce an Example, which guidelines.html describes as nested inside a
    # larger Output Format span. All are punctuation-adjacent discourse markers
    # rather than task vocabulary, so they generalize (unlike a verb whitelist).
    # Broadening from "such as" alone raised train precision 33.3% -> 53.3% and
    # candidate coverage on the held-out set 9 -> 16 at 93.8% precision: the
    # single largest source of missed Example boundaries was "i.e. <<title>>"
    # and "like: *highlighted section*", which "such as" never matched.
    r"|,\s+(?=(?:such as|i\.?e\.?|e\.?g\.?|like)[\s:])"
    # Conditional branch on "otherwise" — cut on BOTH sides, since the label
    # boundary lands before it ("do X, | otherwise do Y") in some prompts and
    # after its comma ("...do X. Otherwise, | do Y") in others. Both placements
    # score ~82-85% precision on the training set and together capture 62 boundaries
    # vs 33 for the leading form alone; "Otherwise ," was the single most common
    # missed Constraint boundary (17 cases).
    #
    # CAVEAT: the held-out set contains ZERO "otherwise" instances, so this cue
    # is justified on train data only and is unverified on held-out data. It is
    # kept because "otherwise" is a domain-independent discourse connective, not
    # corpus vocabulary — but treat its precision as unconfirmed.
    r"|,?\s+(?=otherwise\b)"
    r"|\botherwise\s*,\s*",
    re.IGNORECASE,
)

# Labels whose spans must NOT be split by _INTRA_CLAUSE_RE. Inside quoted prose,
# lists, tables and example blocks, commas/semicolons/colons are the material's
# OWN punctuation, so cutting there fragments a correct span. This is the guard
# that keeps the new cues from causing the over-segmentation they otherwise
# would: measured on train, content-plane spans absorbed 103 of the false cuts.
_CONTENT_PLANE_SUPPRESSED = frozenset(
    {"Content", "Structured Data", "Quoted Content", "Example"}
)


_INLINE_CONSTRAINT_RE = re.compile(
    # Quantitative: "with at least/most/exactly/no more than N"
    # Only fire on comma+space or bare space — but ONLY when the word "with"
    # directly introduces the quantifier (not when it's a predicate adjunct
    # like "paragraphs separated by").
    r"(?:,\s*|\s+)(?=with\s+(?:at least|at most|exactly|no more than|fewer than|"
    r"less than|more than)\s+\d)"
    # Case/language constraint — only after a comma, and only when "no other"
    # or "entirely in" begin the new clause (case modifiers like "all lowercase"
    # inside a comma list are NOT split off separately).
    r"|(?:,\s*)(?=(?:no other|entirely in)\b)"
    # "starting with X" / "ending with X" — only after a comma or end-of-clause.
    r"|(?:,\s*)(?=(?:starting|ending)\s+with\b)"
    # "separated by X" — only after a comma (not mid-predicate like "paragraphs separated by").
    r"|(?:,\s*)(?=separated\s+by\b)"
    # "marked (with|by) X" — only after a comma.
    r"|(?:,\s*)(?=marked\s+(?:with|by)\b)"
    # "in the form of" / "in the format of" — only after a comma.
    r"|(?:,\s*)(?=in the (?:form|format) of\b)"
    # "and <format-directive-verb>" — a format clause appended to a constraint/instruction
    # via "and". Only split when the word after "and" is an unambiguous format-shaping verb
    # (wrap, enclose, surround, format, structure, organize, respond, answer, output) so we
    # don't over-split ordinary "and" conjunctions in content.
    r"|(?:\s+and\s+)(?=(?:wrap|enclose|surround|format|structure|organize|"
    r"respond|answer|output|reply|end|finish|close|begin|start|add a|include a postscript|"
    r"put|place)\b)"
    # "and do not / and don't / and never / and avoid" — negated constraint after a positive one.
    r"|(?:\s+and\s+)(?=(?:do not|don't|never|avoid|refrain)\b)"
    # "and use only" — a case/language constraint appended to another constraint.
    r"|(?:\s+and\s+)(?=use\s+only\b)"
    # "and (only|entirely) in" — case modifier after a constraint clause.
    r"|(?:\s+and\s+)(?=(?:only|entirely)\s+in\b)"
    # ", in a <adjective> language/style/way/manner" — prepositional constraint
    # modifier appended after the core task ("Answer the math problem in Spanish").
    r"|(?:,\s*)(?=in\s+a\s+\w+\s+(?:language|style|way|manner|format|tone)\b)"
    r"|(?:,\s*)(?=in\s+(?:a\s+)?(?:different|another|other|formal|informal|"
    r"casual|simple|complex|creative|poetic|narrative)\b)",
    re.IGNORECASE,
)

# A span is a "fragment" if it meets any of these criteria and should be
# merged back into its predecessor rather than emitted as a standalone span.
_MIN_SPAN_CHARS = 20       # spans shorter than this are almost always fragments
_TERMINAL_PUNCT = re.compile(r"[.?!)\]\"']\s*$")

# Structural hint patterns for clauses that have no rule evidence.
# These fire when a clause opens with a recognisable constraint or format signal
# but carries no keyword that the weighted rules catch (e.g. "Aim the blog post
# at teenagers" has no constraint keyword, yet it is definitionally a constraint).
# Confidence is set to 0.7 — enough to produce a distinct span but not so high
# it overrides a real evidence-based label.

_CLAUSE_CONSTRAINT_HINT_RE = re.compile(
    r"^(?:"
    # Style / audience / tone constraints after the core task
    r"Aim (?:the|your|it)\b|"
    r"Target (?:the|your|it|a|an)\b|"
    r"Make (?:it|the|your|all|sure)\b|"
    r"Make sure\b|"
    r"Ensure\b|"
    # Keyword / content / structural constraints
    r"Include (?:the keywords?|a keyword|keywords?|a title|the words?|the phrase|"
    r"a postscript|at least|exactly|the following|placeholders?)\b|"
    r"Use (?:the keywords?|only|all)\b|"
    r"The (?:word|letter|keyword|phrase|name|title|sentence|paragraph|section|response|answer|output)\b|"
    r"It should\b|"
    r"It must\b|"
    r"Your (?:response|answer|output|entire|whole)\b|"
    r"No (?:other|more|comma|commas|capital)\b|"
    r"Do not\b|Don't\b|Must not\b|Never\b|Avoid\b|Refrain\b|"
    r"(?:All|Every|Each) (?:letter|word|sentence|paragraph|section|name|bullet)\b|"
    r"(?:Write|Respond|Answer) (?:it |them |this )?in (?:all |only |entirely )?\b|"
    r"(?:In|Using) (?:all )?(?:capital|uppercase|lowercase)\b|"
    r"(?:Only|Entirely) in\b"
    r")",
    re.IGNORECASE,
)

_CLAUSE_FORMAT_HINT_RE = re.compile(
    r"^(?:"
    # Output structure directives
    r"(?:Wrap|Enclose|Put|Place) (?:your|the|it|all|this)\b|"
    r"(?:Respond|Answer|Output|Reply) (?:in|with|using)\b|"
    r"(?:Use|Follow) (?:the |this |a )?(?:exact |following |given )?format\b|"
    r"(?:Format|Structure|Organize|Present) (?:your|the|it)\b|"
    r"(?:In|Using) (?:the |a )?(?:JSON|YAML|XML|CSV|HTML|Markdown|markdown)\b|"
    # Postscript / closing structure
    r"(?:Add|Include|Append) a postscript\b|"
    r"(?:Please )?(?:finish|end|close) your (?:answer|response|output)\b|"
    r"(?:At the )?end (?:your|the) (?:response|answer)\b|"
    r"End with\b|"
    # Repetition / echo format directives
    r"First repeat\b|Repeat the\b|"
    r"(?:Before|After) (?:you |your )?(?:answer|respond|give)\b"
    r")",
    re.IGNORECASE,
)

# Context hint: a clause that describes a situation/background without directing
# the model. Fires on third-person declarative openers ("The X is/has/was...",
# "A X has...", noun phrases describing a scenario). Used to tag no-evidence
# clauses as Context rather than inheriting the segment's primary class.
_CLAUSE_CONTEXT_HINT_RE = re.compile(
    r"^(?:"
    # Third-person declarative: "The X is/was/has..." — but NOT "The X must/should/shall"
    # which are constraint directives, not context statements.
    r"The (?!.*\b(?:must|should|shall|cannot|can't|need to|has to)\b)\w[\w ]+ "
    r"(?:is|are|was|were|has|have|had|can|will|would|contains?|shows?|"
    r"includes?|involves?|describes?|represents?)\b|"
    # Indefinite: "A/An X is/was..."
    r"A(?:n)? \w[\w ]+ (?:is|are|was|were|has|have|had)\b|"
    # Possessive pronoun opener: "His/Her/Its/Their/Our/My X..."
    r"(?:His|Her|Its|Their|Our|My) \w[\w ]+\b.{0,30}(?:is|are|was|were|has)\b|"
    # Third-person pronoun + past/present verb — narrative/problem text
    r"(?:She|He|They|It|We) (?:bought|sold|paid|spent|earned|has|have|had|was|were|"
    r"went|came|took|got|made|found|saw|gave|told|asked|said|wanted|needed|"
    r"started|finished|completed|used|tried|decided|chose|picked)\b|"
    # Proper noun opener + any descriptive/stative verb — but NOT pronouns
    # (He/She/It/You/We/They are sentence-start capitals, not proper nouns)
    r"(?!(?:He|She|It|You|We|They|I)\b)[A-Z][a-z]+ (?:is|are|was|were|has|had|"
    r"bought|sold|went|came|got|made|found|started|finished|completed|earned|"
    r"spent|paid|works?|lives?|runs?|owns?|operates?|manages?|serves?)\b|"
    # First-person narrative: "I've got...", "I had...", "I found...", "My X drowned..."
    r"I(?:'ve| have| had| was| got| found| made| went| came| tried| used|'m)\b|"
    r"My (?:\w+ )+(?:drowned|died|was|were|has|had|is|are|got|went|came|broke|fell)\b|"
    # "Given the/this/a sentence/fact/statement/information..." — context framing
    r"Given (?:the|this|a|an|that)\b"
    r")",
    re.IGNORECASE,
)

# Interrogative instruction hint: a clause that asks the model a question
# mid-segment (without being at line-start, so the anchored rule didn't fire).
_CLAUSE_QUESTION_HINT_RE = re.compile(
    r"^(?:How|What|Where|When|Why|Which|Who|Whose|Can|Could|Would|Should|Is|Are|Do|Does|Did)\b"
    r"(?=[\s\S]*\?)",
    re.IGNORECASE,
)


def _inside_quote(text, pos):
    """True if `pos` falls inside an unclosed double-quoted region.

    A cheap parity check: an odd number of `"` before `pos` means the position is
    between an opening quote and its (not yet seen) closing quote, i.e. inside
    quoted material. Commas, semicolons and colons there belong to the quoted
    text itself and are not role boundaries.

    This complements _CONTENT_PLANE_SUPPRESSED rather than duplicating it: that
    guard skips segments whose PRIMARY label is content-plane, but a quotation
    embedded inside an otherwise control-plane segment ("Rewrite this: \"a, b,
    c\" in JSON") is not caught by it. Measured on the training set, the
    shipped cues fired 132 times inside an unclosed quote and were WRONG 128 of
    those (32:1), so suppressing them is +124 net; the held-out set has no
    in-quote cuts at all, so this is a pure safety guard there. O(pos) count, no
    model, no measurable cost.

    Deliberately only `"`: single quotes are ambiguous with apostrophes
    ("don't", possessives), so parity on them is unreliable.
    """
    return text.count('"', 0, pos) % 2 == 1


def _split_clauses(seg_text, primary_label=None):
    """Split seg_text into (start, end) segment-relative clause spans.

    Uses sentence boundaries, named clause-starter patterns, and inline
    constraint patterns to find split points.  Spans are non-overlapping,
    cover the whole text, and each is stripped of leading/trailing whitespace.

    primary_label is the segment's rule-assigned class. When it is a
    content-plane label, the intra-sentence cues in _INTRA_CLAUSE_RE are NOT
    applied: inside quoted prose, lists and example blocks the punctuation those
    cues key on belongs to the material itself, so splitting there fragments a
    span that was already correct. Passing None (the default) applies every
    splitter.
    """
    cuts = {0, len(seg_text)}
    # Named clause-starters and sentence boundaries.
    for m in _CLAUSE_SPLIT_RE.finditer(seg_text):
        cuts.add(m.end())
    # Inline constraints embedded mid-sentence.
    # Split AFTER the separator (m.end()) — the constraint clause starts clean.
    # The preceding clause will have a trailing comma/space that is stripped
    # by the whitespace-trimming pass below (rstrip handles ",").
    for m in _INLINE_CONSTRAINT_RE.finditer(seg_text):
        cuts.add(m.end())
    # Lowercase intra-sentence continuations (", and use JSON" / "; ..." /
    # ", otherwise ..."). Skipped entirely on content-plane segments — see
    # _CONTENT_PLANE_SUPPRESSED.
    if primary_label not in _CONTENT_PLANE_SUPPRESSED:
        for m in _INTRA_CLAUSE_RE.finditer(seg_text):
            if _inside_quote(seg_text, m.end()):
                continue
            cuts.add(m.end())

    clauses = []
    for s, e in zip(sorted(cuts), sorted(cuts)[1:]):
        chunk = seg_text[s:e]
        stripped = chunk.lstrip()
        lead = len(chunk) - len(stripped)
        # Strip trailing whitespace, commas (left by inline-constraint splitter),
        # and trailing " and" / ", and" (left when "and <format-verb>" fires).
        stripped = stripped.rstrip().rstrip(",").rstrip()
        stripped = re.sub(r",?\s+and\s*$", "", stripped, flags=re.IGNORECASE).rstrip()
        if not stripped:
            continue
        ns = s + lead
        ne = ns + len(stripped)
        clauses.append((ns, ne))
    return clauses


def _find_addendum_split(seg_text, evidence):
    """Return a character offset where a trailing control-plane addendum begins,
    or None if no such split is warranted.

    Fires when ALL high-weight (≥0.8) control-plane evidence is concentrated in
    the final sentence of a long segment, and the body before it is long enough
    to be a distinct span (≥60 chars).  The split is placed at the start of the
    final sentence.
    """
    # Only consider evidence that is strong and control-plane.
    control_labels = {"Constraint", "Output Format", "Instruction"}
    strong_ev = [e for e in evidence
                 if e["label"] in control_labels and e["weight"] >= 0.8]
    if not strong_ev:
        return None

    # Find the last sentence boundary (". " followed by capital, or ". \n").
    # We scan backwards from the start of the earliest strong evidence hit to
    # find the sentence that contains all of it.
    first_strong = min(e["start"] for e in strong_ev)

    # Body must be substantial — less than ~60% of the segment length must be
    # before the addendum, otherwise splitting doesn't help.
    seg_len = len(seg_text)
    if first_strong < 60 or first_strong < seg_len * 0.25:
        return None

    # Walk backwards from first_strong to find the sentence boundary.
    # A sentence boundary is ". " or ".\n" or "! " or "? " (terminal punct + space).
    boundary = None
    search_region = seg_text[:first_strong]
    for m in re.finditer(r"[.!?]\s+", search_region):
        boundary = m.end()   # position right after the whitespace — start of next sentence
    if boundary is None:
        return None

    # The candidate addendum is seg_text[boundary:].
    addendum = seg_text[boundary:].lstrip()
    if not addendum:
        return None

    # The addendum must start with a recognisable control directive opener.
    if not _ADDENDUM_CONTROL_RE.match(addendum):
        return None

    # The body before it must have no unaccounted strong control evidence.
    # (If there is strong evidence in the body too, a normal clause split should
    # handle it and we don't need the addendum heuristic.)
    body_strong = [e for e in strong_ev if e["end"] <= boundary]
    if body_strong:
        return None

    # Adjust boundary to skip any whitespace in the raw seg_text.
    raw_boundary = boundary
    while raw_boundary < seg_len and seg_text[raw_boundary] in " \t\n\r":
        raw_boundary += 1
    return raw_boundary if raw_boundary < seg_len else None


# Keyword hits that a confidence sweep on the training set showed are unreliable even
# as the SOLE in-span evidence (wrong more often than right when alone): the same
# surface form legitimately signals different classes by context. A span whose only
# evidence is one of these is treated as uncertain regardless of weight.
_LONE_AMBIGUOUS_KEYWORDS = (
    "output the", "need to", "the following",
)

# Minimum rule weight for a single-label span to count as certain. Below this the
# label is a weak signal and should escalate. (Sweep: single-label spans averaged
# 66% accuracy overall; requiring high weight concentrates the trustworthy ones.)
_SPAN_CERTAINTY_MIN_WEIGHT = 0.9


def _span_is_uncertain(clause_ev, hint_only):
    """Judge whether a span's rule-derived label is trustworthy.

    Uncertain (comes back requires_review=True) when ANY of:
      - it is a structural-hint guess (hint_only) or has no evidence at all,
      - its in-span evidence spans two or more DISTINCT labels (competing signal),
      - its strongest in-span hit is below the certainty weight floor,
      - its only evidence is a known lone-ambiguous keyword.
    Certain only when a single label is backed by a strong, sole-label hit.

    A confidence sweep on the training set chose this strict form: it left the
    fewest "confidently wrong" spans (28, vs 32 for a compatibility-relaxed variant)
    at essentially the same escalation rate. We deliberately do NOT exempt
    compatible co-labels here: even a Constraint+Output-Format span is worth
    flagging when the segment is already flagged for review, and treating them as
    certain let measurably more wrong labels through.
    """
    if hint_only or not clause_ev:
        return True
    if len({e["label"] for e in clause_ev}) >= 2:
        return True
    if max(e["weight"] for e in clause_ev) < _SPAN_CERTAINTY_MIN_WEIGHT:
        return True
    if len(clause_ev) == 1:
        kw = clause_ev[0]["text"].strip().lower()
        if any(a in kw for a in _LONE_AMBIGUOUS_KEYWORDS):
            return True
    return False


def _extract_sub_spans(seg_text, evidence, seg_start, primary_label, primary_confidence):
    """Return a list of absolute sub-span dicts for a segment.

    Each dict has: label, text, start, end, confidence, source="rules".
    Spans are at clause level — whole semantic clauses, not just keyword hits.
    """
    clauses = _split_clauses(seg_text, primary_label)

    # Addendum split: when clause-splitting found only 1 clause and all strong
    # control-plane evidence is in the final sentence, re-classify the body
    # independently rather than having the whole segment inherit the addendum's label.
    # Only fires when the normal clause splitter found no split (len == 1).
    addendum_pos = _find_addendum_split(seg_text, evidence) if len(clauses) <= 1 else None
    if addendum_pos is not None:
        # Relabel the body portion independently by re-classifying it.
        body_text = seg_text[:addendum_pos].rstrip()
        addendum_text = seg_text[addendum_pos:].lstrip()
        if body_text and addendum_text:
            body_result = classify_segment(body_text)
            body_label = body_result["primary_class"] or "Content"
            body_conf  = round(body_result["confidence"], 4)
            # Imperative anchor: if the body starts with an unambiguous directive
            # verb, it's an Instruction regardless of what classify_segment said.
            if _IMPERATIVE_ANCHOR_RE.match(body_text.strip()):
                body_label = "Instruction"
                body_conf  = round(body_result["confidence"], 4)
            # If body_result is itself uncertain, nudge toward Content/Context
            # rather than letting a weak constraint hit dominate.
            elif body_label in {"Constraint", "Output Format"} and body_conf < 0.7:
                body_label = "Content"
                body_conf  = round(CONTENT_FALLBACK_WEIGHT * _length_penalty(body_text), 4)
            # Build addendum span using its own evidence.
            add_ev = [e for e in evidence if e["start"] >= addendum_pos]
            add_scores = _aggregate_scores(add_ev) if add_ev else {}
            add_label = max(add_scores, key=add_scores.get) if add_scores else "Constraint"
            add_conf  = round(add_scores.get(add_label, 0.7), 4)
            body_start = seg_start
            body_end   = seg_start + len(body_text)
            add_start  = seg_start + addendum_pos
            add_end    = seg_start + addendum_pos + len(addendum_text)
            return [
                {
                    "label": body_label,
                    "text": body_text,
                    "start": body_start,
                    "end": body_end,
                    "confidence": body_conf,
                    "requires_review": body_conf < CONFIDENCE_THRESHOLD,
                    "hint_only": False,
                    "source": "rules",
                },
                {
                    "label": add_label,
                    "text": addendum_text,
                    "start": add_start,
                    "end": add_end,
                    "confidence": add_conf,
                    "requires_review": add_conf < CONFIDENCE_THRESHOLD,
                    "hint_only": False,
                    "source": "rules",
                },
            ]

    # Single clause (or no split, even after addendum check) → one whole-segment span.
    if len(clauses) <= 1:
        # Imperative-anchor override: if the segment starts with an unambiguous
        # directive verb, force Instruction regardless of primary_label.
        # Rationale: constraint evidence (high-weight quantifiers) can outscore
        # a lone imperative verb and flip primary_class to Constraint, but a
        # segment that *begins* with "Write / Create / Explain / ..." is
        # definitionally a task directive.
        label = primary_label or "Content"
        if label != "Instruction" and _IMPERATIVE_ANCHOR_RE.match(seg_text.strip()):
            label = "Instruction"
        conf = round(primary_confidence, 4)
        return [{
            "label": label,
            "text": seg_text,
            "start": seg_start,
            "end": seg_start + len(seg_text),
            "confidence": conf,
            "requires_review": conf < CONFIDENCE_THRESHOLD,
            "hint_only": False,
            "source": "rules",
        }]

    labeled = []
    prev_label = None
    for (cs, ce) in clauses:
        # Evidence items whose midpoint falls within this clause.
        clause_ev = [
            e for e in evidence
            if e["label"] != "Injection"
            and cs <= (e["start"] + e["end"]) / 2 < ce
        ]
        hint_only = False
        if clause_ev:
            scores = _aggregate_scores(clause_ev)
            label = max(scores, key=scores.get)
            conf = round(scores[label], 4)
            # When all evidence is weak (max hit ≤ 0.6) and a structural hint
            # pattern matches the clause opener, the hint is more reliable than
            # a tie-broken weak signal. Override the evidence-based label.
            max_ev_weight = max(e["weight"] for e in clause_ev)
            if max_ev_weight <= 0.6:
                clause_text_check = seg_text[cs:ce].strip()
                if _CLAUSE_CONSTRAINT_HINT_RE.match(clause_text_check):
                    label = "Constraint"
                    conf = 0.7
                    hint_only = True
                elif _CLAUSE_FORMAT_HINT_RE.match(clause_text_check):
                    label = "Output Format"
                    conf = 0.7
                    hint_only = True
                elif _CLAUSE_CONTEXT_HINT_RE.match(clause_text_check):
                    label = "Context"
                    conf = 0.6
                    hint_only = True
        else:
            # No evidence in this clause — use structural heuristics to assign
            # a label rather than blindly inheriting the segment's primary class.
            # This prevents same-label merging from collapsing genuinely distinct
            # semantic clauses when the second clause happens to fire no rules.
            clause_text = seg_text[cs:ce].strip()
            if _IMPERATIVE_ANCHOR_RE.match(clause_text):
                label = "Instruction"
                conf = round(primary_confidence, 4)
            elif _CLAUSE_QUESTION_HINT_RE.match(clause_text):
                # A question following narrative Context/Content is part of the
                # problem being posed, not a new directive to the model.
                if prev_label in {"Context", "Content"}:
                    label = "Content"
                    conf = 0.6
                else:
                    label = "Instruction"
                    conf = 0.7
                hint_only = True
            elif _CLAUSE_CONSTRAINT_HINT_RE.match(clause_text):
                label = "Constraint"
                conf = 0.7
                hint_only = True
            elif _CLAUSE_FORMAT_HINT_RE.match(clause_text):
                label = "Output Format"
                conf = 0.7
                hint_only = True
            elif _INLINE_CONSTRAINT_RE.match(clause_text) or re.match(
                r"^(?:with\s+(?:at least|at most|exactly|no more than|fewer than|"
                r"less than|more than)\s+\d|"
                r"no other\b|entirely in\b|all (?:capital|uppercase|lowercase)\b|"
                r"starting with\b|ending with\b|separated by\b|marked (?:with|by)\b|"
                r"in the (?:form|format) of\b)",
                clause_text, re.IGNORECASE
            ):
                label = "Constraint"
                conf = 0.7
                hint_only = True
            elif _CLAUSE_CONTEXT_HINT_RE.match(clause_text):
                label = "Context"
                conf = 0.6
                hint_only = True
            else:
                # If all strong (≥0.8) evidence for this segment's primary class
                # lies in LATER clauses (none in [cs:ce]), this clause is body
                # content that shouldn't inherit the control-plane label.
                # Use Content as a low-confidence fallback so it stays distinct
                # and can be merged or escalated rather than silently mislabeled.
                # Body-vs-addendum fallback: when exactly 2 clauses exist and all
                # strong evidence is in the OTHER (later) clause, this clause is
                # body content that shouldn't inherit the control-plane label.
                # Restrict to the 2-clause case to avoid over-splitting 3-clause
                # prompts where the middle clause is a genuine second instruction.
                strong_ev_here = [
                    e for e in evidence
                    if e["label"] != "Injection"
                    and e["weight"] >= 0.8
                    and cs <= (e["start"] + e["end"]) / 2 < ce
                ]
                strong_ev_later = [
                    e for e in evidence
                    if e["label"] != "Injection"
                    and e["weight"] >= 0.8
                    and (e["start"] + e["end"]) / 2 >= ce
                ]
                if (not strong_ev_here
                        and strong_ev_later
                        and primary_label in {"Constraint", "Output Format"}
                        and (ce - cs) >= 100
                        and len(clauses) == 2):
                    label = "Content"
                    conf = round(CONTENT_FALLBACK_WEIGHT * _length_penalty(seg_text[cs:ce]), 4)
                    hint_only = True
                elif not strong_ev_here and not clause_ev:
                    # General case: this clause has NO evidence at all (no
                    # keyword hit anywhere in it, weak or strong) and matched
                    # none of the structural hint patterns above. Inheriting
                    # the segment's control-plane primary_label here is a
                    # systematic undersegmentation bug -- a genuinely
                    # evidence-free clause is far more often a descriptive/
                    # framing sentence (Context/Content) than a silent
                    # continuation of a directive, and directives almost
                    # always carry SOME keyword signal. Previously this only
                    # fired in the narrow 2-clause Constraint/Output-Format
                    # case above; generalizing it here is what lets e.g.
                    # "Write a copy for an ad... It's a small sailboat..."
                    # split into Instruction + Context instead of collapsing
                    # the descriptive sentence into Instruction.
                    label = "Content"
                    conf = round(CONTENT_FALLBACK_WEIGHT * _length_penalty(seg_text[cs:ce]), 4)
                    hint_only = True
                else:
                    label = primary_label or "Content"
                    conf = round(primary_confidence, 4)

        # Honest per-span certainty. A span is trustworthy ONLY
        # when its label rests on SINGLE-label, high-weight, non-competing in-span
        # evidence. Everything else — a lone weak/ambiguous keyword, competing
        # labels in the same clause, or a structural-hint guess — is uncertain and
        # must escalate, even if the parent segment looks confident overall. This
        # is what surfaces the "confidently wrong" spans (a keyword fired one class
        # while the true role was another) instead of emitting them at conf 0.9.
        span_uncertain = _span_is_uncertain(clause_ev, hint_only)
        labeled.append({
            "label": label,
            "text": seg_text[cs:ce],
            "start": seg_start + cs,
            "end": seg_start + ce,
            # Cap reported confidence for uncertain spans so downstream consumers
            # never see a dishonest high score on a coin-flip label.
            "confidence": min(conf, 0.6) if span_uncertain else conf,
            "requires_review": span_uncertain or conf < CONFIDENCE_THRESHOLD,
            "hint_only": hint_only,
            "source": "rules",
        })
        prev_label = label

    # --- Fragment-merge pass ---
    # A span is a fragment if it lacks terminal punctuation AND is short, OR if
    # it starts with a lowercase letter (indicating a mid-sentence split). In
    # either case it cannot stand alone as a meaningful tagged unit, so we fold
    # it into the preceding span rather than emit it separately.
    # We also merge same-label consecutive spans as before.
    # Clauses that are complete standalone semantic units even when they start
    # with a lowercase letter (either inline constraint starters or any clause
    # that matches the structural hint patterns).  Never treat these as fragments.
    _INLINE_CONSTRAINT_ANCHOR = re.compile(
        r"^(?:with\s+(?:at least|at most|exactly|no more than|fewer than|"
        r"less than|more than)\s+\d|"
        r"no other\b|entirely in\b|all (?:capital|uppercase|lowercase)\b|"
        r"starting with\b|ending with\b|separated by\b|marked (?:with|by)\b|"
        r"in the (?:form|format) of\b)",
        re.IGNORECASE,
    )

    def _is_fragment(sp, prev_sp=None):
        t = sp["text"].strip()
        if not t:
            return True
        # If this span has a different label from its predecessor it represents
        # a genuine semantic boundary — never treat it as a fragment to be merged
        # away regardless of case or length.
        if prev_sp is not None and sp["label"] != prev_sp["label"]:
            return False
        # A span that matches any structural hint pattern is a complete semantic
        # unit regardless of whether it starts lowercase.
        if (_INLINE_CONSTRAINT_ANCHOR.match(t)
                or _IMPERATIVE_ANCHOR_RE.match(t)
                or _CLAUSE_CONSTRAINT_HINT_RE.match(t)
                or _CLAUSE_FORMAT_HINT_RE.match(t)
                or _CLAUSE_CONTEXT_HINT_RE.match(t)
                or _CLAUSE_QUESTION_HINT_RE.match(t)):
            return False
        starts_lowercase = t[0].islower()
        too_short = len(t) < _MIN_SPAN_CHARS
        no_terminal = not _TERMINAL_PUNCT.search(t)
        # A substantive body clause (>= 100 chars) that ends with terminal
        # punctuation is a complete independent span even if it starts lowercase.
        # This handles body text split from a trailing addendum directive.
        if starts_lowercase and len(t) >= 100 and not no_terminal:
            return False
        return starts_lowercase or (too_short and no_terminal)

    def _merge_into_prev(prev, sp):
        raw = seg_text[prev["start"] - seg_start: sp["end"] - seg_start]
        hint = prev.get("hint_only", False) or sp.get("hint_only", False)
        conf = max(prev["confidence"], sp["confidence"])
        return {
            "label": prev["label"],
            "text": raw,
            "start": prev["start"],
            "end": sp["end"],
            "confidence": conf,
            "requires_review": conf < CONFIDENCE_THRESHOLD or hint,
            "hint_only": hint,
            "source": "rules",
        }

    # Two-pass fragment merge.
    #
    # Pass 1 — forward pass: each span that is a fragment is absorbed into its
    # *successor*, not its predecessor. This handles the case where an
    # over-eager split produces a tiny opener ("Do", "In", "Also") that
    # belongs with the clause that follows it.
    #
    # Pass 2 — backward pass (same-label + trailing fragment): merge anything
    # still adjacent with the same label, or any trailing fragment that has
    # no successor to absorb it.

    # Pass 1: absorb leading fragments into their successor.
    absorbed = []
    pending = None
    prev_sp = None
    for sp in labeled:
        if _is_fragment(sp, prev_sp):
            # Accumulate: this span will be prepended to the next non-fragment.
            if pending is None:
                pending = sp
            else:
                pending = _merge_into_prev(pending, sp)
        else:
            if pending is not None:
                # Merge the accumulated fragment(s) into this well-formed span.
                combined = _merge_into_prev(pending, sp)
                combined["label"] = sp["label"]
                combined["confidence"] = sp["confidence"]
                absorbed.append(combined)
                pending = None
            else:
                absorbed.append(sp)
        prev_sp = sp
    if pending is not None:
        # Trailing fragment with no successor — attach to last well-formed span.
        if absorbed:
            absorbed[-1] = _merge_into_prev(absorbed[-1], pending)
        else:
            absorbed.append(pending)

    if not absorbed:
        absorbed = labeled  # safety: shouldn't happen

    # Pass 2: merge adjacent same-label spans (and any remaining tiny fragments).
    merged = [absorbed[0]]
    for sp in absorbed[1:]:
        prev = merged[-1]
        if sp["label"] == prev["label"] or _is_fragment(sp, prev):
            merged[-1] = _merge_into_prev(prev, sp)
        else:
            merged.append(sp)

    # Pass 3: absorb nested-role spans back into the enclosing span.
    #
    # The output format is a FLAT span list, so a role that semantically sits
    # INSIDE another is not representable and should not be emitted separately:
    # the enclosing span is both the safer and the more useful rendering.
    # Emitting the inner span instead produces things like
    #     \instr\ plow the fields \constraint\ but make the grass blue /constraint/ /instr/
    # which is no more useful downstream than the plain Instruction span.
    #
    # Measured on the held-out set: this was Example's entire error
    # profile. Scoring against outermost-only ground truth, Example had 11 false
    # positives and every one was a demonstration fragment carved out of an
    # enclosing Output Format span -- "such as [address].", "Step 1: ......",
    # "* This is bullet 1", "For example: *little me in the countryside*." --
    # each overlapping a GT Output Format label at >=0.94. guidelines.html says
    # as much directly: Example is "almost always nested inside a larger Output
    # Format span".
    #
    # Restricted to Example-inside-OutputFormat because that is the pair the data
    # supports; the general nesting problem is a schema question, not this pass.
    coalesced = [merged[0]]
    for sp in merged[1:]:
        prev = coalesced[-1]
        if sp["label"] == "Example" and prev["label"] == "Output Format":
            coalesced[-1] = _merge_into_prev(prev, sp)
        else:
            coalesced.append(sp)

    return coalesced


# ===========================================================================
# 4. TOP-LEVEL
# ===========================================================================


def segment_and_classify(text, rewrite=False):
    """Full rule pass. Returns the result dict (the backend entry point).

    rewrite=True adds a `rewritten_text` field to Instruction segments whose
    surface form is interrogative or uses polite framing.  The original `text`
    and all offset fields are never mutated; `rewritten_text` is advisory only.
    """
    if rewrite:
        from .rewriter import rewrite as _rewrite_fn
    spans = segment(text)
    # Carved triple-quote data fences: a segment whose stripped span falls inside
    # one of these is a single reference-data artifact and must NOT be clause-split.
    data_fences = [m.span() for m in _DATA_FENCE_RE.finditer(text)]
    def _is_data_fence(s, e):
        return any(fs <= s and e <= fe for fs, fe in data_fences)
    segments = []
    for idx, (start, end) in enumerate(spans):
        seg_text = text[start:end]

        # A fenced reference-data block: emit ONE span for the whole block,
        # labeled by its dominant markers. Skip clause-splitting entirely.
        if _is_data_fence(start, end):
            label = _data_block_label(seg_text)
            plane = PLANE_OF_LABEL.get(label, "CONTENT")
            seg = {
                "segment_index": idx, "start": start, "end": end,
                "text": seg_text, "primary_class": label,
                "semantic_plane": plane, "confidence": 0.9,
                "structural_confidence": 0.0, "semantic_confidence": 0.9,
                "classifications": [{"label": label, "score": 0.9}],
                "evidence": [], "requires_review": False,
                "abstention_reason": None, "injection_detected": False,
                "spans": [{
                    "label": label, "text": seg_text,
                    "start": start, "end": end,
                    "confidence": 0.9, "source": "rules",
                }],
            }
            segments.append(seg)
            continue

        result = classify_segment(seg_text)
        # Attach positional metadata.
        seg = {"segment_index": idx, "start": start, "end": end}
        seg.update(result)
        # Sub-segment spans (clause-level).
        sub_spans = _extract_sub_spans(
            seg_text,
            result.get("evidence", []),
            start,
            result.get("primary_class"),
            result.get("confidence", 0.0),
        )
        # If the whole segment is escalated, every span is also uncertain —
        # the segment-level class may be wrong, so span labels inherit that doubt.
        if result.get("requires_review"):
            for sp in sub_spans:
                sp["requires_review"] = True
        # Escalate the SEGMENT when ANY of its spans is uncertain,
        # even if the segment looked confident overall. This is what stops
        # confidently-wrong spans from reaching output unflagged.
        elif any(sp.get("requires_review") for sp in sub_spans):
            seg["requires_review"] = True
            if not seg.get("abstention_reason"):
                seg["abstention_reason"] = "uncertain_span"
        seg["spans"] = sub_spans
        # Optional rewrite for confident Instruction segments.
        if (rewrite
                and result.get("primary_class") == "Instruction"
                and not result.get("requires_review")):
            rw = _rewrite_fn(seg_text)
            if rw is not None:
                seg["rewritten_text"] = rw
        segments.append(seg)

    # --- Structural content inference ---
    # When a control-plane segment explicitly hands off to following text
    # ("summarize the text below", "elaborate on the following passage", etc.),
    # the subsequent paragraph segments are Content by structural inference —
    # they are the material to be operated on, not directives.  This handles
    # the common "Kindly summarize ... \n\n <long factual text>" shape where
    # accidental constraint keywords ("cannot", "must") in the body prose would
    # otherwise trigger a false Constraint classification.
    #
    # We only apply this to segments that:
    #   1. Are not already confidently classified as a non-Content class with
    #      strong evidence (primary_score > 0.8 AND not a single-keyword hit).
    #   2. Follow a segment that contains the referential handoff pattern.
    #   3. Have no imperative/directive opener themselves.
    _HANDOFF_RE = re.compile(
        r"\b(?:the\s+)?(?:text|passage|excerpt|content|paragraph|article|"
        r"following|below|provided|given|above)\b.{0,60}"
        r"\b(?:below|above|follows?|provided|given|following)\b"
        r"|\bthe\s+(?:text|passage|excerpt|content|article)\s+below\b"
        r"|\bthe\s+following\s+(?:text|passage|excerpt|content|paragraph|article)\b"
        r"|\belaborate\s+on\s+(?:this|the\s+following|the\s+text)\b"
        r"|\bsummariz\w+\s+(?:the\s+)?(?:text|passage|content|article)\s+below\b"
        r"|\bbelow\s+(?:is|are)\s+(?:a|an|the)\b",
        re.IGNORECASE,
    )
    _DIRECTIVE_OPENER_RE = re.compile(
        r"^\s*(?:"
        r"summarize|analyze|analyse|extract|rewrite|generate|translate|list|"
        r"classify|categorize|write|explain|create|identify|describe|compare|"
        r"evaluate|produce|compute|calculate|determine|provide|give|convert|"
        r"transform|find|detect|label|annotate|paraphrase|outline|draft|compose|"
        r"suggest|brainstorm|complete|answer|solve|name|"
        r"do not|don't|must not|never|make sure|ensure|include|use only|"
        r"you are|act as|your role|"
        r"please\s+\w|kindly\s+\w"
        r")\b",
        re.IGNORECASE,
    )
    handoff_active = False
    for seg in segments:
        seg_text = text[seg["start"]:seg["end"]]
        # Check if this segment hands off to following content.
        if _HANDOFF_RE.search(seg_text) and seg.get("primary_class") in {
            "Instruction", "Constraint", "Output Format", "Context"
        }:
            handoff_active = True
            continue
        if handoff_active:
            # Only override if: no directive opener, and current class is
            # not strongly evidenced (single weak keyword wins, not a clear directive).
            has_strong_evidence = (
                seg.get("confidence", 0) > 0.8
                and len(seg.get("evidence", [])) > 1
            )
            has_directive_opener = bool(_DIRECTIVE_OPENER_RE.match(seg_text))
            if not has_strong_evidence and not has_directive_opener:
                seg["primary_class"] = "Content"
                seg["semantic_plane"] = CONTENT
                seg["confidence"] = round(CONTENT_FALLBACK_WEIGHT * _length_penalty(seg_text), 4)
                seg["structural_confidence"] = 0.0
                seg["semantic_confidence"] = seg["confidence"]
                # This is a tentative structural GUESS at CONTENT_FALLBACK_WEIGHT
                # (0.4-ish) -- the same low-confidence value that every other path
                # in this module (_unknown_or_fallback) always escalates. Hardcoding
                # requires_review=False here would let a coin-flip guess come back
                # looking trustworthy, with nothing to prompt a second look.
                requires_review = seg["confidence"] < CONFIDENCE_THRESHOLD
                seg["requires_review"] = requires_review
                seg["abstention_reason"] = "low_signal" if requires_review else None
                seg["classifications"] = [{"label": "Content", "score": seg["confidence"]}]
                seg["source"] = "rules_structural"
                for sp in seg.get("spans", []):
                    sp["label"] = "Content"
                    sp["confidence"] = seg["confidence"]
                    sp["requires_review"] = requires_review
                    sp["hint_only"] = False
                    sp["source"] = "rules_structural"
            else:
                # A directive in the following segment ends the handoff.
                handoff_active = False

    return {
        "is_confident_global": all(not s["requires_review"] for s in segments),
        "segments": segments,
    }


def main(argv):
    if len(argv) > 1:
        with open(argv[1], "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    result = segment_and_classify(text)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
