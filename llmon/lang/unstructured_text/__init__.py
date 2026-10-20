# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Segment a prompt into typed spans and render it as LLMON markup.

    >>> from llmon.lang.unstructured_text import segment
    >>> result = segment("Write a poem about dogs in under 50 words.")
    >>> result.to_llmon()                                     # doctest: +SKIP

NOTHING imported at module scope here may pull in torch. The `rules` backend is
pure stdlib and imports in ~0.15 s; touching torch costs ~0.7 s, and a consumer
that only wants the cheap backend should never pay it. That is why the model
backends are reached through `llmon.lang.unstructured_text.api`, which imports them
lazily, rather than being re-exported from this file.

See docs/ARCHITECTURE.md for the backends and what each one actually does.
"""

from .api import (
    segment,
    Span,
    Segment,
    SegmentationResult,
    BACKENDS,
    BackendError,
    BackendUnavailable,
    InjectionRefused,
)
from .taxonomy import NINE, THREE, Taxonomy, TaxonomyLeak

__all__ = [
    "segment",
    "Span",
    "Segment",
    "SegmentationResult",
    "BACKENDS",
    "BackendError",
    "BackendUnavailable",
    "InjectionRefused",
    "NINE",
    "THREE",
    "Taxonomy",
    "TaxonomyLeak",
]
