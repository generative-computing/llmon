# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Weighted regex rule sets for the `rules` backend.

Each rule is a Rule(label, pattern, weight, anchored). The matcher compiles
these once and scans every segment against all of them.

Design principles (anti-false-positive):
- Strong, unambiguous signals get weights ~0.9-1.0.
- Weak/contextual signals (Context, bare Content) get low weights on purpose,
  so a segment carried only by weak signals cannot clear the 0.8 confidence
  bar alone and is flagged for review instead of being mislabeled.
- We would rather abstain (escalate) than commit a wrong label.

Labels map onto three semantic planes (see llmon.lang.unstructured_text.taxonomy).
`Injection` is a
special label: it is not a plane, it is an override/prompt-injection flag that
forces escalation whenever it fires.
"""

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Rule table
#
# The label space itself lives in llmon.lang.unstructured_text.taxonomy. Every `label`
# below is a nine-class name, so this table is only meaningful against
# taxonomy.NINE -- callers collapse on output rather than swapping the names
# underneath these rules.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    label: str          # which class this rule is evidence for
    pattern: object      # compiled regex
    weight: float        # contribution strength in [0, 1]
    anchored: bool = False  # True if it only matters at the start of a line


def _c(pattern, flags=re.IGNORECASE):
    return re.compile(pattern, flags)


# ---------------------------------------------------------------------------
# Rule sets per class
#
# Note on weights: anchored line-initial imperatives are the single most
# reliable signal we have, so they sit near the top of the range. Generic
# keyword hits that can appear in ordinary prose are deliberately damped.
# ---------------------------------------------------------------------------

RULES = [
    # --- Instruction ------------------------------------------------------
    # Line-initial imperative verbs are the canonical "do this" signal.
    # Optionally preceded by polite openers ("Please", "Kindly") which the
    # rewriter will strip but which should not suppress the instruction signal.
    Rule("Instruction", _c(
        r"^\s*(?:please\s+|kindly\s+)?(summarize|analyze|analyse|extract|rewrite|generate|translate|"
        r"list|classify|categorize|write|explain|create|identify|describe|"
        r"compare|evaluate|produce|compute|calculate|determine|provide|give|"
        r"convert|transform|find|detect|label|annotate|paraphrase|outline|"
        r"ask|tell|show|pick|choose|select|rate|rank|order|sort|count|"
        r"estimate|predict|recommend|discuss|elaborate|expand|clarify|"
        r"justify|prove|demonstrate|illustrate)\b"
    ), weight=0.95, anchored=True),
    # Imperative verbs mid-text are weaker (could be quoted/discussed).
    Rule("Instruction", _c(
        r"\b(summarize|analyze|extract|rewrite|generate|translate|classify|"
        r"paraphrase|answer)\b"
    ), weight=0.55),
    # Polite directive framing.
    Rule("Instruction", _c(r"\byour task is\b|\bplease (summarize|write|generate|extract|list|provide)\b"),
         weight=0.85),
    # First-person request framing: "I want/need/would like you to ...",
    # "I am looking for ...", "I am planning ... and would like ...", etc.
    # These produce Content@0.4 fallback without this rule because they carry
    # no imperative verb at line-start. Weight is moderate (0.82) -- confident
    # enough to clear CONFIDENCE_THRESHOLD_CLEAN when the segment is otherwise
    # clean, but not so high it overrides a strong rival.
    Rule("Instruction", _c(
        r"^\s*I\s+(want|need|would like|'d like|would love|'d love)\s+(you\s+|thee\s+)?(to\s+)?"
        r"|\bI\s+(am|'m|was|have been)\b.{0,60}\b(looking for|planning|trying|hoping|seeking)\b"
    ), weight=0.82, anchored=True),
    # Polite second-person requests: "Can you ...", "Could you ...", "Would you ..."
    # at line-start. Already partially covered by the interrogative rule (0.78)
    # but that rule only fires when a '?' is present in the segment. These cover
    # the case where the request is a statement: "Can you write me a cover letter."
    Rule("Instruction", _c(
        r"^\s*(Can|Could|Would)\s+you\b"
    ), weight=0.78, anchored=True),
    # Interrogative requests ("What is ...?", "Can you ...?", "How many ...?").
    # A question is a directive -- it asks the model to answer. The head word
    # must be line-initial (anchored) AND the segment must actually contain a
    # '?' (the lookahead), so this fires on real questions, not on prose that
    # merely contains "is"/"how"/"are". Weighted just below 0.8 on purpose: a
    # bare question stays under threshold and escalates, but a question that
    # also carries a compatible Constraint/Output-Format modifier (the common
    # IFEval shape, "What is X? Respond in JSON, exactly two sentences") is
    # reinforced over the bar by the co-label calibration -- exactly the easy
    # case we want to keep out of review.
    Rule("Instruction", _c(
        r"^\s*(what|which|who|whom|whose|when|where|why|how|is|are|do|does|did|"
        r"can|could|would|will|shall|should|may|might)\b(?=[\s\S]*\?)"
    ), weight=0.78, anchored=True),
    # Imperative openers that the main verb list misses.
    # "Come up with", "Name a", "Draft a", "Answer (the following|this)" —
    # these are unambiguous directives. "come up with" was erroneously captured
    # by the Context rule (weight 0.35); moving it here prevents that misfire.
    Rule("Instruction", _c(
        r"^\s*(come up with|name (a|an|the|some|one|two|three|\d+)\b|draft\b|answer\b)"
    ), weight=0.90, anchored=True),

    # --- Constraint -------------------------------------------------------
    # "do not / don't / must not / may not / never" are unambiguous negation directives.
    Rule("Constraint", _c(r"\b(do not|don't|must not|may not|never)\b"), weight=0.9),
    # "cannot" is common in descriptive prose ("the state cannot be described independently").
    # Weight is lower so a lone "cannot" in a long factual paragraph does not confidently
    # claim Constraint; it needs corroboration from other control-plane signals.
    Rule("Constraint", _c(r"\bcannot\b"), weight=0.65),
    Rule("Constraint", _c(r"\b(must|should|shall|always|required to|need to)\b"), weight=0.6),
    # Word/letter/keyword OCCURRENCE requirements ("the word X appears/must appear
    # N times", "include/mention/use the word/letter/keyword X ... N times").
    # These are content requirements (a specific token must occur a number of
    # times) -- NOT a shape of the response -- so per guidelines.html they stay
    # Constraint even though they carry the same quantifier vocabulary
    # ("at least", "exactly", "N times") that Output Format also uses for
    # response-shape counts (paragraphs/sentences/stanzas). Anchoring on
    # word/letter/keyword/phrase as the counted noun is what disambiguates them.
    Rule("Constraint", _c(
        r"\b(?:word|letter|keyword|phrase)s?\b.{0,40}\b(?:appear|appears|occur|occurs|"
        r"used?|mention(?:ed)?)\b.{0,30}\b(?:at least|at most|exactly|no more than|"
        r"fewer than|more than|less than|\d+)\b"
        r"|\b(?:include|mention|use)\b.{0,20}\bthe\s+(?:word|letter|keyword|phrase)s?\b"
        r".{0,60}\b(?:at least|at most|exactly|no more than|fewer than|more than|"
        r"less than|\d+\s*times?)\b"
    ), weight=0.93),
    Rule("Constraint", _c(
        r"\b(under|at most|no more than|maximum( of)?|max|fewer than|less than|"
        r"up to|limit(ed)? to)\s+\d+\b|\b\d+\s+(words?|sentences?|characters?|"
        r"tokens?|items?|bullets?)\b"
    ), weight=0.95),
    Rule("Constraint", _c(r"\bonly\b|\bexactly\b|\bat least\b|\bmaximum\b|\bminimum\b"), weight=0.55),

    # --- Output Format ----------------------------------------------------
    # Response-SHAPE quantifiers: a count/range attached to a structural unit
    # of the response itself (paragraphs, sentences, stanzas, words-as-overall-
    # length) rather than to a specific word/letter's occurrence count. Per
    # guidelines.html, "exactly 2 paragraphs", "at least 60 sentences, but less
    # than 600 words", "exactly 6 stanzas" describe the shape of the output, so
    # they are Output Format even though they use Constraint-style quantifier
    # words ("at least", "exactly", "no more than"). Weighted above the generic
    # Constraint quantifier rule (0.95) so this wins the tie when both fire on
    # the same span.
    Rule("Output Format", _c(
        r"\b(?:at least|at most|exactly|no more than|fewer than|more than|less than|"
        r"up to|maximum( of)?|minimum( of)?|under|over)\s+\d+\s+"
        r"(?:paragraphs?|sentences?|stanzas?|sections?|bullets?|bullet\s*points?|"
        r"lines?|words?|characters?)\b"
        r"|\b\d+\s+(?:paragraphs?|sentences?|stanzas?|sections?)\b"
        r"|\b(?:exactly|precisely)\s+\d+\s+(?:words?|characters?)\b"
    ), weight=0.9),
    Rule("Output Format", _c(r"\b(JSON|YAML|XML|CSV|HTML|markdown)\b", 0), weight=0.7),
    Rule("Output Format", _c(r"\bschema\b|\bformat\b|\btemplate\b"), weight=0.6),
    Rule("Output Format", _c(
        r"\brespond with\b|\breturn (a|an|the|your|only)\b|\boutput (a|an|the|only|in)\b|"
        r"\bformat(ted)? as\b|\bin the (following )?format\b|\busing the (following )?(schema|format)\b"
    ), weight=0.9),
    # A literal JSON-object-looking schema in the text.
    Rule("Output Format", _c(r'\{\s*"[^"]+"\s*:'), weight=0.85),
    # Wrapping / enclosing directives: unambiguously format-prescriptive.
    # These cannot be Constraint (they prescribe output shape, not a limit).
    Rule("Output Format", _c(
        r"\b(wrap|enclose|surround|put|place|enclosed?)\b.{0,40}"
        r"\b(in|with|by|inside|within)\b.{0,30}"
        r"(double |single |curly |square |angle |back)?(quot(e|ation)|bracket|brace|parenthes|backtick|tick|asterisk|mark|tag|fence|delimit)"
    ), weight=0.92),
    # Structural shape directives: prescribe how output is divided or arranged.
    Rule("Output Format", _c(
        r"\b(paragraph|section|bullet|heading|subheading|numbered list|"
        r"ordered list|unordered list|chapter|subsection)\b"
    ), weight=0.65),
    # "separated by <literal-separator>" — structural output divider.
    Rule("Output Format", _c(
        r"\bseparated by\b.{0,40}("
        r"---|===|\*\*\*|---|\+\+\+|newline|blank line|line break|divider|"
        r'"\*+"|"[-]+"|\'[*-]+\''
        r")"
    ), weight=0.85),
    # Repeated separator characters used as dividers between output sections.
    # These appear in prompts as format examples: "***", "---", "===", "******".
    # Deliberately line-anchored only: the SAME marker (***, ---) is also used
    # inside Example/Structured-Data blocks (guidelines.html's own Example
    # section uses "advice 1 .... *** advice 2 ....") to separate demonstration
    # items, not to prescribe output shape. An unanchored version of this rule
    # fragmented those blocks into extra Output-Format sub-spans and measurably
    # regressed labelling accuracy (Structured Data/Example FPs roughly doubled)
    # -- so this stays anchored even though it misses some mid-clause markers.
    Rule("Output Format", _c(r"^\s*(?:\*{3,}|-{3,}|={3,}|\+{3,})\s*$"), weight=0.85),
    # "N bullet points (using|in) (the|a) (markdown|X) format" — prescribes
    # output structure with an explicit count; unambiguously Output Format.
    Rule("Output Format", _c(
        r"\b\d+\s+bullet\s+points?\b.{0,50}\b(?:format|markdown|using)\b"
        r"|\busing\b.{0,30}\bbullet\s+points?\s+format\b"
    ), weight=0.95),
    # Common postscript / structural directives. The response-noun and its
    # modifier ("your whole response", "the entire answer") are separated by
    # a wildcard gap since prompts routinely insert "whole/entire/final"
    # between the possessive and the noun ("end your whole response with...").
    Rule("Output Format", _c(
        r"\b(postscript|P\.S\.|add a (title|header|heading|footer|postscript)|"
        r"title should be|header should be|"
        r"end (your|the).{0,15}(response|answer) with)\b"
    ), weight=0.8),
    # "in/using (all )?<case> letters/words/text" — case-formatting directive.
    Rule("Output Format", _c(
        r"\bin (all |entirely )?(capital|uppercase|lowercase|upper case|lower case) (letters?|text|words?)\b|"
        r"\bentirely in (capital|uppercase|lowercase)\b"
    ), weight=0.82),

    # --- Structured Data --------------------------------------------------
    # Runs of key: value lines.
    Rule("Structured Data", _c(r"^\s*[\w .\-]{1,40}:\s+\S+", re.MULTILINE), weight=0.5),
    # CSV/TSV-ish rows: several comma/tab separated fields.
    Rule("Structured Data", _c(r"^\s*\S+(\s*[,\t]\s*\S+){2,}\s*$", re.MULTILINE), weight=0.6),
    # JSON / array literals.
    Rule("Structured Data", _c(r"^\s*[\[{].*[\]}]\s*$", re.MULTILINE | re.DOTALL), weight=0.55),
    # XML / HTML tag literals.
    Rule("Structured Data", _c(r"<[a-zA-Z][\w\-]*(\s[^>]*)?>.*</[a-zA-Z][\w\-]*>", re.DOTALL),
         weight=0.5),

    # --- Quoted Content ---------------------------------------------------
    # Wrapped in a paired delimiter tag like <data>...</data>.
    Rule("Quoted Content", _c(r"^\s*<([a-zA-Z][\w\-]*)>.*</\1>\s*$", re.DOTALL), weight=0.85),
    # Triple-quoted / fenced.
    Rule("Quoted Content", _c(r'"""[\s\S]*"""|```[\s\S]*```'), weight=0.8),
    # Markdown block quote.
    Rule("Quoted Content", _c(r"^\s*>\s+\S", re.MULTILINE), weight=0.6),

    # --- Example ----------------------------------------------------------
    Rule("Example", _c(r"\bfor example\b|\be\.g\.\b|\bfor instance\b"), weight=0.75),
    # "such as" is common in descriptive content prose and is a weaker signal.
    Rule("Example", _c(r"\bsuch as\b"), weight=0.5),
    Rule("Example", _c(r"^\s*example\s*:?\s*$|\bexample\s*:|\bsample\b", re.MULTILINE), weight=0.7),
    Rule("Example", _c(r"^\s*<example>.*</example>\s*$", re.DOTALL), weight=0.9),
    # Input/Output demonstration pairs.
    Rule("Example", _c(r"^\s*(input|output)\s*:", re.MULTILINE | re.IGNORECASE), weight=0.6),

    # --- Role -------------------------------------------------------------
    # Strong anchored persona openers at line start.
    Rule("Role", _c(r"^\s*(you are|act as|you will act as|your role is|assume the role|"
                    r"behave as|pretend to be)\b"), weight=0.92, anchored=True),
    # "Imagine you are/you're ..." — persona framing via hypothetical.
    Rule("Role", _c(r"^\s*imagine (you are|you'?re)\b"), weight=0.88, anchored=True),
    # "write/answer/respond as if you are/you're ..." — perspective persona.
    Rule("Role", _c(r"\b(write|answer|respond|reply)\b.{0,30}\bas if (you are|you'?re)\b"),
         weight=0.96),
    # "from/in the perspective of ..." — only a strong Role signal when paired
    # with a write/answer/respond verb or at line-start (bare role assignment).
    # Mid-text without a write-verb it's a style constraint on an instruction,
    # not a persona adoption — lower weight so Instruction/Constraint can win.
    Rule("Role", _c(r"\b(write|answer|respond|reply|tell|narrate|describe)\b"
                    r".{0,40}\bfrom the perspective of\b"), weight=0.93),
    Rule("Role", _c(r"^\s*from the perspective of\b"), weight=0.91, anchored=True),
    Rule("Role", _c(r"\bfrom the perspective of\b"), weight=0.65),
    Rule("Role", _c(r"\bin the perspective of\b"), weight=0.65),
    # Weaker mid-text expert/helper role signals.
    Rule("Role", _c(r"\bas (a|an) (helpful|expert|professional|senior|world[- ]class)\b"),
         weight=0.7),

    # --- Content (positive evidence that suppresses false constraint hits) ---
    # First-person narrative sentences where "don't/can't/cannot" express the
    # speaker's situation rather than a directive on the model's output.
    # Weight must exceed the `cannot` rule (0.65) and the `don't` rule (0.9)
    # to win when they co-occur in the same clause.
    Rule("Context", _c(
        r"^\s*(?:I|My\s+\w[\w ]+|We)\b.{0,80}"
        r"\b(?:don't|do not|cannot|can't|couldn't|didn't|doesn't|won't|wouldn't)\s+"
        r"(?:know|understand|have|want|need|think|remember|believe|feel|see|like|"
        r"get|find|make|use|do|go|come|tell|say|show|give|take|put|keep|let|help)\b"
    ), weight=0.95, anchored=True),

    # --- Context (intentionally weak: should escalate, not over-commit) ---
    Rule("Context", _c(r"\b(background|context|given that|for context|previously|"
                       r"earlier|so far|to recap)\b"), weight=0.45),
    Rule("Context", _c(r"\b(here is|here's|the following|below is|consider the)\b"), weight=0.35),

    # --- Injection / override detection (safety) --------------------------
    Rule("Injection", _c(
        r"\bignore (all |the |any |your )?(previous|prior|above|earlier|preceding)"
        r"( instructions| prompts| rules| context)?\b"
    ), weight=1.0),
    Rule("Injection", _c(r"\bdisregard\b.{0,40}\b(instructions?|prompt|rules?|guidelines?)\b"),
         weight=0.95),
    Rule("Injection", _c(r"\bforget (everything|all|what)\b"), weight=0.85),
    Rule("Injection", _c(r"\byou are now\b|\bnew instructions?\s*:|\bfrom now on\b"), weight=0.8),
    Rule("Injection", _c(r"\b(reveal|show|print|repeat|leak)\b.{0,40}\b(system )?(prompt|instructions?)\b"),
         weight=0.9),
    Rule("Injection", _c(r"\boverride\b.{0,30}\b(instructions?|rules?|settings?)\b"), weight=0.85),
    Rule("Injection", _c(r"\b(developer|system|admin)\s+mode\b|\bjailbreak\b|\bDAN\b"), weight=0.85),
]


# Weak "general content" fallback. This is NOT in RULES because we do not want
# it to compete as positive evidence; it only applies when a non-trivial
# segment produced no other matches, giving it a tentative low-confidence
# Content label rather than Unknown. Handled explicitly in the matcher.
CONTENT_FALLBACK_WEIGHT = 0.4
