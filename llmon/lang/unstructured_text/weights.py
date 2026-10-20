# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Where model checkpoints come from: a local directory or the HuggingFace Hub.

Resolution order for each backend, first hit wins:

    1. an explicit path or repo id passed in code
    2. LLMON_SEG_<BACKEND>_PATH        -- a local directory
    3. LLMON_SEG_<BACKEND>_REPO        -- a HuggingFace repo id, plus
       LLMON_SEG_<BACKEND>_REVISION    (default "main")
    4. DEFAULT_*_REPO below            -- the published checkpoints
    5. `./<backend>_ckpt` if it exists -- convenience for a source checkout

A local path is used as-is and never downloaded. A repo id is fetched with
`huggingface_hub.snapshot_download`, which caches, so the download happens once;
the import is deferred so the base install stays dependency-free.

If nothing resolves, `resolve` raises with the three concrete things a caller can
do about it, rather than returning a path that does not exist.
"""

import os

from . import config

__all__ = ["resolve", "CheckpointUnavailable", "DEFAULT_TAGGER_REPO", "BACKENDS"]


# ---------------------------------------------------------------------------
# Published checkpoint
#
# The HuggingFace repo id of the published tagger checkpoint, as "<org>/<model>".
# This is the ONLY place it is set: the library, the CLI and the notebook all
# read it from here, and nothing else hardcodes a checkpoint location. The repo
# is private: downloading it needs a HuggingFace token with access (`hf auth
# login`, or HF_TOKEN). Without one, a caller supplies a local path or a repo id
# of their own, which `_download` explains when it fails.
# ---------------------------------------------------------------------------
DEFAULT_TAGGER_REPO = "ibm-research/llmon-span-tagger"

#: backend name -> (config key stem, default repo, conventional local dir name)
BACKENDS = {
    "tagger": ("TAGGER", lambda: DEFAULT_TAGGER_REPO, "tagger_ckpt"),
}

# Files every checkpoint directory must contain. Checked so a half-downloaded or
# wrong directory fails here rather than loading an untrained encoder with a
# random head and silently producing confident nonsense.
REQUIRED_FILES = ("head.pt", "tokenizer.json", os.path.join("encoder", "config.json"))


class CheckpointUnavailable(FileNotFoundError):
    """No checkpoint could be resolved, or the one resolved is incomplete."""


def _is_complete(path):
    return all(os.path.exists(os.path.join(path, f)) for f in REQUIRED_FILES)


def _missing(path):
    return [f for f in REQUIRED_FILES if not os.path.exists(os.path.join(path, f))]


def _download(repo_id, revision, cache_dir):
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import RepositoryNotFoundError
    except ImportError:
        raise CheckpointUnavailable(
            f"checkpoint {repo_id!r} lives on the HuggingFace Hub, but "
            "huggingface-hub is not installed. Either install the backend "
            "extra:\n"
            "    pip install 'llmon[unstructured-text]'\n"
            "\n"
            "or point at a local directory with LLMON_SEG_TAGGER_PATH."
        ) from None
    try:
        return snapshot_download(
            repo_id=repo_id, revision=revision,
            cache_dir=cache_dir or None,
        )
    except RepositoryNotFoundError:
        # The Hub answers 401 for a private repo the caller cannot see, which
        # reads like a typo or a misconfiguration on their side.
        raise CheckpointUnavailable(
            f"HuggingFace repo {repo_id!r} was not found or is not accessible "
            "with your credentials. If it is private, log in with an account "
            "that has access:\n"
            "    hf auth login        # or export HF_TOKEN=...\n"
            "\n"
            "or point at a local directory with LLMON_SEG_TAGGER_PATH, or use "
            "the rule backend:\n"
            "    llmon-segment --backend rules"
        ) from None


def resolve(backend, model=None):
    """Return a local directory holding the checkpoint for `backend`.

    `model` may be a local directory or a HuggingFace repo id; it is treated as
    a path if it exists on disk, otherwise as a repo id.
    """
    try:
        stem, default_repo, local_name = BACKENDS[backend]
    except KeyError:
        raise ValueError(
            f"no checkpoint for backend {backend!r}; expected one of "
            f"{sorted(BACKENDS)}"
        ) from None

    cache_dir = config.get(f"CACHE_DIR") or ""
    revision = config.get(f"{stem}_REVISION") or "main"

    # 1. explicit argument
    if model:
        if os.path.isdir(model):
            path = model
        else:
            path = _download(model, revision, cache_dir)
        if not _is_complete(path):
            raise CheckpointUnavailable(
                f"{path} is missing {_missing(path)}. A checkpoint needs all of "
                f"{list(REQUIRED_FILES)}; loading a partial one would run an "
                f"untrained head and produce confident nonsense."
            )
        return path

    # 2. configured local path
    configured = config.get(f"{stem}_PATH") or ""
    if configured:
        if not os.path.isdir(configured):
            raise CheckpointUnavailable(
                f"LLMON_SEG_{stem}_PATH points at {configured!r}, which is not a "
                f"directory."
            )
        if not _is_complete(configured):
            raise CheckpointUnavailable(
                f"LLMON_SEG_{stem}_PATH={configured!r} is missing "
                f"{_missing(configured)}."
            )
        return configured

    # 3/4. configured or default repo id
    repo = config.get(f"{stem}_REPO") or default_repo()
    if repo:
        return _download(repo, revision, cache_dir)

    # 5. source-checkout convenience
    if os.path.isdir(local_name) and _is_complete(local_name):
        return os.path.abspath(local_name)

    raise CheckpointUnavailable(
        f"no checkpoint found for the {backend!r} backend. Three ways to fix it:\n"
        f"\n"
        f"  1. point at a local directory:\n"
        f"       export LLMON_SEG_{stem}_PATH=/path/to/{local_name}\n"
        f"  2. point at a HuggingFace repo:\n"
        f"       export LLMON_SEG_{stem}_REPO=<org>/<model>\n"
        f"  3. train one (see docs/TRAINING.md):\n"
        f"       llmon-seg-train --backend {backend} --corpus <corpus.json> "
        f"--out {local_name}\n"
        f"\n"
        f"Or use the dependency-free rule backend, which needs no checkpoint:\n"
        f"       llmon-segment --backend rules\n"
        f"Settings can also go in a .env file; see .env.example."
    )
