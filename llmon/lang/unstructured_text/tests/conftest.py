# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared pytest fixtures.

Two things matter here. Paths are anchored to the repo root rather than the
current directory, because several tests used to read `examples/*.txt` relatively
and only passed when pytest happened to be invoked from the project root. And
tests that need a corpus or a checkpoint SKIP rather than fail: checkpoints are
hundreds of megabytes and are not committed, and no corpus ships with this
repository, so the suite must come back green without either.
"""

import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def root():
    """Absolute path to the project root."""
    return ROOT


@pytest.fixture(scope="session")
def examples_dir():
    """Directory holding the sample prompts used as fixtures."""
    return os.path.join(ROOT, "examples")


@pytest.fixture(scope="session")
def example_texts(examples_dir):
    """`{name: text}` for every examples/*.txt."""
    out = {}
    for name in sorted(os.listdir(examples_dir)):
        if name.endswith(".txt"):
            with open(os.path.join(examples_dir, name), encoding="utf-8") as f:
                out[name[:-4]] = f.read()
    return out


def _require_dir(path, what, how):
    if not os.path.isdir(path):
        pytest.skip(f"no {what} at {path} -- {how}")
    return path


@pytest.fixture(scope="session")
def corpus_path():
    """A corpus to test against, or skip.

    Looks for a locally built corpus at data/holdout.json (gitignored).
    """
    path = os.path.join(ROOT, "data", "holdout.json")
    if not os.path.exists(path):
        pytest.skip("no corpus at data/holdout.json. No corpus ships with this "
                    "repository; build one locally (docs/CORPUS.md) with "
                    "`llmon-seg-train build --sources ... --out-dir data/`")
    return path


@pytest.fixture(scope="session")
def tagger_ckpt():
    """A tagger checkpoint, or skip."""
    return _require_dir(
        os.path.join(ROOT, "tagger_ckpt"), "tagger checkpoint",
        "set LLMON_SEG_TAGGER_PATH or LLMON_SEG_TAGGER_REPO (see .env.example)")


@pytest.fixture(scope="session")
def tokenizer_dir(tagger_ckpt):
    """A directory a tokenizer can be loaded from, or skip.

    Separate from `tagger_ckpt` only to say what the test actually needs: the BIO
    round-trip test tokenizes real prompts and does not care whose weights sit
    beside the vocabulary. Any checkpoint directory serves.
    """
    return tagger_ckpt


@pytest.fixture(scope="session")
def transformers_available():
    """Skip unless transformers imports and a tokenizer can be built offline."""
    pytest.importorskip("torch", reason="needs pip install 'llmon[unstructured-text]'")
    pytest.importorskip("transformers",
                        reason="needs pip install 'llmon[unstructured-text]'")
    return True
