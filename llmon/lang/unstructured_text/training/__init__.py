# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Corpus building, training and threshold sweeps.

Stdlib only at import time; the actual training entrypoints live on the backend
module (`tagger.train`), which pulls torch lazily.
"""

from .corpus import load, save, build, clean, flatten_spans, validate, CorpusError

__all__ = ["load", "save", "build", "clean", "flatten_spans", "validate",
           "CorpusError"]
