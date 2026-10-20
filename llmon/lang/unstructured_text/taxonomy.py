# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The label spaces this library can predict, as data rather than global state.

Two taxonomies ship. `THREE` is the default; `NINE` is what the human annotation
guidelines define (docs/guidelines.html is the authority for what each of the
nine classes means) and what the rule engine is written against.

    NINE   Instruction · Constraint · Output Format          (control plane)
           Content · Structured Data · Quoted Content · Example   (content plane)
           Role · Context                                    (structural plane)

    THREE  instruction  <- Instruction
           constraint   <- Constraint, Output Format, Example, Role
           data         <- Content, Structured Data, Quoted Content, Context

`Injection` is deliberately in NEITHER label space. It is not a class, it is a
safety flag that forces abstention; see `llmon.lang.unstructured_text.injection`.

Two collapse decisions are not obvious and are recorded here because the data,
not taste, settled them:

  * Example -> constraint. Example is "a template, sample, or demonstration of
    what the output's structure should look like", almost always nested inside a
    larger Output Format span. Grouping it with its usual parent is what makes
    the collapse pay: measured on the annotated corpus it halves multi-label
    collisions (12 -> 8) and dissolves 93% of Example's nesting (79 of 85
    pairs). Sending Example to `data` instead leaves 3x more nesting intact.
  * Role -> constraint. A deliberate call on what the data treats as a coin
    flip: `instruction` was mildly favoured (121 vs 111 nested pairs
    dissolved, identical collision count), and under NINE a Role span renders as
    \\instr\\. So collapsing changes a Role span's rendered tag from \\instr\\ to
    \\constraint\\. 120 spans, 1.8% of the corpus.

WHY THIS IS A DATACLASS AND NOT A SET OF MODULE GLOBALS
-------------------------------------------------------
A label space held in module globals cannot safely size a model head:

    ALLOWED_LABELS = tuple(PLANE_OF_LABEL.keys())
    class SomeTagger:
        def __init__(self, ..., num_labels=len(ALLOWED_LABELS)):   # default arg!

A default argument is evaluated when the class body executes, so the head width
freezes at import and rebinding the globals afterwards has no effect. The only
lever left is to set the taxonomy before the module is ever imported -- which
works, and is a trap: get the import order wrong and nothing raises, you just get
a model with the wrong label space and a plausible-looking accuracy report.

So: label spaces are values, `num_labels` is a required argument, and every
checkpoint records the label list it was trained on and validates it on load. A
mismatch cannot be reached silently, and import order does not matter.

Modules whose behaviour is defined against the nine class NAMES -- the regex
table in `rules` and the confidence calibration in `heuristic` -- import `NINE`
explicitly, never `DEFAULT`. They compute in nine classes and collapse on output. Importing
`DEFAULT` there would hand them a label space their own literals do not match,
and a `plane_of` lookup that misses returns None for every label -- at which
point `None == None` compares equal and confidence inflates instead of raising.
"""

from dataclasses import dataclass
from typing import Mapping

__all__ = [
    "Taxonomy", "TaxonomyLeak", "NINE", "THREE", "DEFAULT",
    "get", "CONTROL", "CONTENT", "STRUCTURAL",
]

# Semantic planes of the nine-class space.
CONTROL = "CONTROL"
CONTENT = "CONTENT"
STRUCTURAL = "STRUCTURAL"


class TaxonomyLeak(RuntimeError):
    """A label reached a boundary of a taxonomy that cannot represent it."""


@dataclass(frozen=True)
class Taxonomy:
    """A closed label space, its semantic planes, and its LLMON tag mapping.

    Attributes:
        name: Stable identifier, recorded in checkpoints. "nine" or "three".
        labels: The label space, in a FIXED order. This order is the model head's
            output-index order, so changing it makes every saved head
            uninterpretable. Append, never reorder.
        plane_of: Label -> semantic plane. Every label must appear.
        tag_of: Label -> LLMON tag name. Not injective for `nine`: Instruction
            and Role both render as `instr`, and the four content classes all
            render as `data`. That is a property of LLMON's tag vocabulary, not
            a bug -- but it is why scoring must compare labels, never tags.
        collapse_from: Label in a coarser-grained space -> label here. None for
            the finest-grained taxonomy.
    """

    name: str
    labels: tuple[str, ...]
    plane_of: Mapping[str, str]
    tag_of: Mapping[str, str]
    collapse_from: Mapping[str, str] | None = None

    def __post_init__(self):
        missing_plane = [l for l in self.labels if l not in self.plane_of]
        missing_tag = [l for l in self.labels if l not in self.tag_of]
        if missing_plane or missing_tag:
            raise ValueError(
                f"taxonomy {self.name!r} is incomplete: "
                f"labels missing a plane {missing_plane}, missing a tag {missing_tag}"
            )

    def __len__(self):
        return len(self.labels)

    def __contains__(self, label):
        return label in self.labels

    # -- label mapping ------------------------------------------------------

    def collapse(self, label):
        """Map a label from a finer-grained space into this one. Strict.

        Raises TaxonomyLeak rather than passing an unrecognised label through.
        A silently-passed label gets scored against collapsed ground truth and
        can match by accident, which is the class of bug that has cost this
        project measurable accuracy more than once.
        """
        if label in self.labels:
            return label
        if self.collapse_from is not None:
            try:
                return self.collapse_from[label]
            except KeyError:
                pass
        raise TaxonomyLeak(
            f"cannot express label {label!r} in taxonomy {self.name!r}: not one "
            f"of {list(self.labels)}"
            + (f" nor a source class {sorted(self.collapse_from)}"
               if self.collapse_from else "")
        )

    def tag(self, label):
        """LLMON tag for a label. Strict -- raises on anything unrecognised.

        Deliberately has no default. A `.get(label, "data")` fallback here is
        what once made an unregistered label space score as near-perfect: every
        unknown label mapped to the same tag, so everything matched everything.
        """
        try:
            return self.tag_of[label]
        except KeyError:
            raise TaxonomyLeak(
                f"no LLMON tag for label {label!r} in taxonomy {self.name!r}; "
                f"known labels are {list(self.labels)}"
            ) from None

    # -- corpus and result rewriting ----------------------------------------

    def collapse_labels(self, labels):
        """Collapse a corpus `labels` list, deduplicating (offset, class) triples.

        Two source labels on the SAME offsets that collapse to the same class
        become one. Two that collapse to DIFFERENT classes both survive, so the
        span stays genuinely multi-label and the multi-label head handles it.
        Any key other than `class` and `span` is preserved.
        """
        seen, out = set(), []
        for lab in labels:
            cls = self.collapse(lab["class"])
            key = (lab["span"]["start"], lab["span"]["end"], cls)
            if key in seen:
                continue
            seen.add(key)
            new = dict(lab)
            new["class"] = cls
            new["span"] = dict(lab["span"])
            out.append(new)
        return out

    def collapse_result(self, result):
        """Rewrite every label in a segmentation result into this space, in place.

        The rule engine always emits nine-class labels, and only spans a model
        actually re-labeled come back collapsed. Without this, spans that never
        escalate get scored as nine-class predictions against three-class ground
        truth and counted wrong -- which penalises the model for the rule
        engine's label space rather than for anything it did.
        """
        for seg in result.get("segments", []):
            if seg.get("primary_class") is not None:
                seg["primary_class"] = self.collapse(seg["primary_class"])
                seg["semantic_plane"] = self.plane_of[seg["primary_class"]]
            if seg.get("classifications"):
                seg["classifications"] = [
                    dict(c, label=self.collapse(c["label"]))
                    if isinstance(c, dict) and "label" in c else c
                    for c in seg["classifications"]
                ]
            for sp in seg.get("spans", []):
                if sp.get("label") is not None:
                    sp["label"] = self.collapse(sp["label"])
        return result


# ---------------------------------------------------------------------------
# The shipped taxonomies
# ---------------------------------------------------------------------------

NINE = Taxonomy(
    name="nine",
    # Order is the model head's output-index order. Do not reorder.
    labels=(
        "Instruction", "Constraint", "Output Format",
        "Content", "Structured Data", "Quoted Content", "Example",
        "Role", "Context",
    ),
    plane_of={
        "Instruction": CONTROL, "Constraint": CONTROL, "Output Format": CONTROL,
        "Content": CONTENT, "Structured Data": CONTENT,
        "Quoted Content": CONTENT, "Example": CONTENT,
        "Role": STRUCTURAL, "Context": STRUCTURAL,
    },
    # All directive labels render as `instr`; the word `instruction` written out
    # in full is reserved for exec-tag references and is never used as a tag.
    tag_of={
        "Instruction": "instr", "Role": "instr",
        "Constraint": "constraint",
        "Output Format": "format",
        "Content": "data", "Quoted Content": "data",
        "Structured Data": "data", "Example": "data",
        "Context": "context",
    },
    collapse_from=None,
)

THREE = Taxonomy(
    name="three",
    labels=("instruction", "constraint", "data"),
    # Each collapsed class gets its OWN plane. The harness reports a
    # "Compatible (same plane, different class)" bucket; with three classes and
    # no finer structure beneath them there is no meaningful weaker-than-exact
    # match left, so this makes that column read 0 rather than inflating it.
    # Merging instruction+constraint into one plane, mirroring NINE's CONTROL
    # plane, would instead invite comparing that number to a nine-class one.
    plane_of={
        "instruction": "PLANE_INSTRUCTION",
        "constraint": "PLANE_CONSTRAINT",
        "data": "PLANE_DATA",
    },
    tag_of={
        "instruction": "instr",
        "constraint": "constraint",
        "data": "data",
    },
    collapse_from={
        "Instruction": "instruction",
        "Constraint": "constraint",
        "Output Format": "constraint",
        "Example": "constraint",
        "Role": "constraint",
        "Content": "data",
        "Structured Data": "data",
        "Quoted Content": "data",
        "Context": "data",
    },
)

#: What `segment()` uses when the caller does not say. Note that `THREE` can
#: only emit three LLMON tag kinds -- \format\ and \context\ exist only under
#: NINE, which means only via the `rules` backend.
DEFAULT = THREE

_BY_NAME = {t.name: t for t in (NINE, THREE)}


def get(taxonomy=None):
    """Resolve a taxonomy from a name, an instance, or None (-> DEFAULT)."""
    if taxonomy is None:
        return DEFAULT
    if isinstance(taxonomy, Taxonomy):
        return taxonomy
    try:
        return _BY_NAME[taxonomy]
    except KeyError:
        raise ValueError(
            f"unknown taxonomy {taxonomy!r}; expected one of {sorted(_BY_NAME)}"
        ) from None
