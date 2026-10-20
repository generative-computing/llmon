# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Evaluation harness for prompt segmentation.

`harness.run()` scores any `prompt -> result` callable against a labeled corpus,
so it works for every backend and for a caller's own model. Nothing here is
needed at inference time.
"""

from .harness import run, outermost_labels, load_labeled

__all__ = ["run", "outermost_labels", "load_labeled"]
