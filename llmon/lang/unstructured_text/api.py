# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The public API: `segment()` and the result types it returns.

    from llmon.lang.unstructured_text import segment

    result = segment("You are a pirate. Write a poem about dogs in 50 words.")
    for span in result.spans:
        print(span.label, round(span.confidence, 3), span.text)
    print(result.to_llmon())

Everything below is stdlib. The model backends are imported inside `segment()`
so that importing this module -- or the package -- never pulls in torch; the
`rules` backend then works on a base install with no dependencies at all.

The dataclasses are a façade over the plain dicts the backends exchange
internally. `to_dict()` gives you that dict when you need the raw form, and
`from_dict()` wraps one.
"""

import sys

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import config, taxonomy as _taxonomy

__all__ = [
    "segment", "Span", "Segment", "SegmentationResult",
    "BACKENDS", "BackendError", "BackendUnavailable", "InjectionRefused",
]

#: Backends `segment()` accepts, cheapest first. See docs/ARCHITECTURE.md.
BACKENDS = ("rules", "tagger")


class BackendError(RuntimeError):
    """The requested combination of backend, taxonomy and options is not valid."""


class BackendUnavailable(BackendError):
    """A model backend was requested but its dependencies are not installed."""


def _import_backend(name):
    """Import a model backend, turning a missing dependency into real guidance.

    A base install has no dependencies on purpose, so asking for the default
    backend on one is a completely normal thing to do -- and it used to fail with
    a bare `No module named 'torch'`, which says nothing about what to install or
    that a dependency-free backend exists.
    """
    try:
        from . import tagger
        return tagger
    except ImportError as exc:
        raise BackendUnavailable(
            f"the {name!r} backend needs PyTorch and transformers, which are not "
            f"installed ({exc}).\n"
            f"\n"
            f"    pip install 'llmon[unstructured-text]'\n"
            f"\n"
            f"Install it into the interpreter that is running, which is not "
            f"always the one on your PATH:\n"
            f"\n"
            f"    {sys.executable} -m pip install 'llmon[unstructured-text]'\n"
            f"\n"
            f"Or use the rule backend, which needs no dependencies and no "
            f"checkpoint:\n"
            f"\n"
            f"    segment(text, backend='rules')\n"
            f"    llmon-segment --backend rules\n"
            f"\n"
            f"It under-splits -- see docs/ARCHITECTURE.md -- but it is always "
            f"available."
        ) from None


class InjectionRefused(RuntimeError):
    """Refused to render a prompt that tripped the injection detector."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Span:
    """One typed, non-overlapping stretch of the prompt.

    `prompt[span.start:span.end] == span.text` always holds. Offsets are taken
    from the tokenizer's own character mapping, never reconstructed by
    arithmetic, so this is an invariant rather than an aspiration.
    """

    label: str | None
    text: str
    start: int
    end: int
    confidence: float
    #: True when the label is below the trust threshold and a human or a larger
    #: model should look at it.
    requires_review: bool
    #: Which backend produced this label: rules or tagger.
    source: str | None = None

    @property
    def word_count(self):
        return len(self.text.split())


@dataclass(frozen=True)
class Segment:
    """A structural chunk of the prompt, holding one or more spans.

    The `tagger` backend has no notion of segments -- it labels tokens -- so it
    returns exactly one segment covering the whole prompt. Read `spans`.
    """

    text: str
    start: int
    end: int
    primary_class: str | None
    semantic_plane: str | None
    confidence: float
    requires_review: bool
    abstention_reason: str | None
    injection_detected: bool
    spans: tuple[Span, ...] = ()


@dataclass(frozen=True)
class SegmentationResult:
    """What `segment()` returns."""

    text: str
    backend: str
    taxonomy: str
    segments: tuple[Segment, ...]
    #: True when every span cleared the trust threshold.
    is_confident: bool
    #: True when the injection tripwire fired anywhere in the prompt. A HEURISTIC
    #: signal from seven keyword patterns -- see llmon.lang.unstructured_text.injection. A
    #: False here is not evidence of safety.
    injection_detected: bool = False
    injection_evidence: tuple[Mapping[str, Any], ...] = ()
    _raw: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)

    # -- access ------------------------------------------------------------

    @property
    def spans(self):
        """Every span, flattened across segments, in document order."""
        return tuple(sp for seg in self.segments for sp in seg.spans)

    def spans_of(self, label):
        return tuple(sp for sp in self.spans if sp.label == label)

    @property
    def needs_review(self):
        return tuple(sp for sp in self.spans if sp.requires_review)

    def __iter__(self):
        return iter(self.spans)

    def __len__(self):
        return len(self.spans)

    # -- rendering ---------------------------------------------------------

    def to_llmon(self, *, rewrite=False, allow_injection=False):
        """Render as LLMON, with special tokens -- the form a model reads."""
        from . import render
        self._guard_injection(allow_injection)
        return render.render_llmon(self._raw, use_rewritten=rewrite)

    def to_short(self, *, rewrite=False, allow_injection=False):
        """Render as Shorthand: `\\tag:id\\ body /tag:id/`."""
        from . import render
        self._guard_injection(allow_injection)
        return render.render_short(self._raw, use_rewritten=rewrite)

    def _guard_injection(self, allow_injection):
        if self.injection_detected and not allow_injection:
            quoted = ", ".join(repr(h.get("text", "")) for h in self.injection_evidence[:3])
            raise InjectionRefused(
                "refusing to render a prompt that tripped the injection "
                f"detector ({quoted}). Rendering it wraps the injected text in "
                "LLMON markup that a downstream model then reads as structure, "
                "which is the one place this flag most needs to not be silent. "
                "Inspect result.injection_evidence, or pass "
                "allow_injection=True if you have handled it."
            )

    # -- interop -----------------------------------------------------------

    def to_dict(self):
        """The raw result dict the backends exchange internally."""
        return self._raw

    @classmethod
    def from_dict(cls, raw, text, backend, taxonomy):
        """Wrap a raw backend result. Also the seam for a caller's own model."""
        segments = []
        evidence = []
        for seg in raw.get("segments", []):
            spans = tuple(
                Span(
                    label=sp.get("label"),
                    text=sp["text"],
                    start=sp["start"],
                    end=sp["end"],
                    confidence=float(sp.get("confidence") or 0.0),
                    requires_review=bool(sp.get("requires_review")),
                    source=sp.get("source"),
                )
                for sp in seg.get("spans", [])
            )
            evidence.extend(seg.get("injection_hits", ()))
            segments.append(Segment(
                text=seg.get("text", ""),
                start=seg.get("start", 0),
                end=seg.get("end", 0),
                primary_class=seg.get("primary_class"),
                semantic_plane=seg.get("semantic_plane"),
                confidence=float(seg.get("confidence") or 0.0),
                requires_review=bool(seg.get("requires_review")),
                abstention_reason=seg.get("abstention_reason"),
                injection_detected=bool(seg.get("injection_detected")),
                spans=spans,
            ))
        return cls(
            text=text,
            backend=backend,
            taxonomy=taxonomy,
            segments=tuple(segments),
            is_confident=bool(raw.get("is_confident_global")),
            injection_detected=any(s.injection_detected for s in segments),
            injection_evidence=tuple(evidence),
            _raw=raw,
        )


# ---------------------------------------------------------------------------
# segment()
# ---------------------------------------------------------------------------

def segment(text, *, backend=None, taxonomy=None, model=None, rewrite=False):
    """Segment `text` into typed spans.

    Args:
        text: The prompt. Offsets in the result index into exactly this string.
        backend: "tagger" (default) or "rules". See docs/ARCHITECTURE.md.
        taxonomy: "three" (default) or "nine". Only the `rules` backend can
            produce "nine"; the tagger's BIO head fixes its label space at
            training time. Asking the tagger for "nine" raises.
        model: A checkpoint directory or HuggingFace repo id, overriding
            configuration. Ignored by the `rules` backend.
        rewrite: Also compute normalised span text, used by
            `to_llmon(rewrite=True)` and `to_short(rewrite=True)`.

    Returns:
        SegmentationResult.

    Raises:
        BackendError: for an unsupported backend/taxonomy combination.
        BackendUnavailable: when a model backend's dependencies are missing.
        weights.CheckpointUnavailable: when a model backend has no checkpoint.
    """
    backend = backend or config.default_backend()
    if backend not in BACKENDS:
        raise BackendError(
            f"unknown backend {backend!r}; expected one of {list(BACKENDS)}"
        )
    tax = _taxonomy.get(taxonomy or config.default_taxonomy())

    if backend == "rules":
        from . import heuristic
        raw = heuristic.segment_and_classify(text, rewrite=rewrite)
        # The rule engine is written against the nine class names and always
        # emits them; a coarser taxonomy is produced by collapsing below.
        source_tax = _taxonomy.NINE
    else:
        _tagger = _import_backend("tagger")
        from . import weights
        raw = _tagger.segment_and_classify_tagger(
            text, model_path=weights.resolve("tagger", model))
        source_tax = _tagger.TAGGER_TAXONOMY
        if tax is not source_tax:
            raise BackendError(
                f"the tagger predicts the {source_tax.name!r} taxonomy and cannot "
                f"produce {tax.name!r}: its head carries one BIO tag pair per "
                f"class, so the label space is fixed at training time. "
                f"{tax.name!r} is available from backend='rules', at markedly "
                f"lower accuracy -- see docs/RESULTS.md."
            )

    if tax is not source_tax:
        # Only ever nine -> coarser. Collapsing is exact; expanding is not
        # possible, which is why the tagger raises above instead of trying.
        tax.collapse_result(raw)

    return SegmentationResult.from_dict(raw, text, backend, tax.name)
