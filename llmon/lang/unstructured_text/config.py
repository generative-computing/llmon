# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Configuration: environment variables, an optional `.env`, and the defaults.

Every knob has one name, prefixed `LLMON_SEG_`, and resolves in this order:

    1. an explicit argument passed in code          (always wins)
    2. a real environment variable
    3. a `.env` file                                (see `dotenv_path`)
    4. the default in this module

`.env` is parsed with about twenty lines of stdlib below rather than a
dependency, so the base install stays dependency-free. `.env.example` in the
project root documents every key; copy it to `.env` and edit.

Values are read at CALL time, not at import time, so setting an environment
variable after importing the package still takes effect -- and a test can change
one without reloading modules.
"""

import os

__all__ = [
    "PREFIX", "get", "get_bool", "get_float", "get_int",
    "default_backend", "default_taxonomy", "dotenv_path", "load_dotenv",
    "DEFAULTS", "ConfigError",
]

PREFIX = "LLMON_SEG_"


class ConfigError(RuntimeError):
    """A configuration value is missing or malformed."""


# ---------------------------------------------------------------------------
# Defaults
#
# The numeric tuning values are MEASURED, not chosen. See docs/RESULTS.md; the
# short version is that the absorb pass buys +3.3 holdout F1 and the adjacent-
# merge pass was measured, rejected and left off. Changing one of these without
# re-running the sweep will move accuracy.
# ---------------------------------------------------------------------------

DEFAULTS = {
    "BACKEND": "tagger",
    "TAXONOMY": "three",

    # Weights: a local directory wins over a HuggingFace repo id. Both are empty
    # by default -- there is no checkpoint until you train one or point at one.
    "TAGGER_PATH": "",
    "TAGGER_REPO": "",
    "TAGGER_REVISION": "main",
    "CACHE_DIR": "",

    "MAX_SEQ_LEN": "2048",

    # Torch device for the tagger: "cpu", "cuda" or "mps". Empty picks cuda when
    # it is available, else cpu.
    "DEVICE": "",

    # Tagger post-processing. Both thresholds are deliberately the same number:
    # one measured value, one justification.
    "ESCALATION_THRESHOLD": "0.60",
    "ABSORB_MAX_WORDS": "2",
    "ABSORB_CONFIDENCE": "0.60",
    "MERGE_THRESHOLD": "0.0",       # 0.0 = off; measured negative, kept opt-in

    # Rule-engine confidence gate: below this, a rule-derived span is not
    # trusted and comes back flagged for review.
    "CONFIDENCE_THRESHOLD": "0.8",
}

# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------

_dotenv_cache = None


def dotenv_path():
    """Path of the `.env` to read: `$LLMON_SEG_DOTENV`, else `./.env`."""
    explicit = os.environ.get(PREFIX + "DOTENV")
    return explicit if explicit else os.path.join(os.getcwd(), ".env")


def load_dotenv(path=None, force=False):
    """Parse `.env` into a dict. Cached; `force=True` re-reads.

    Supports `KEY=value`, `export KEY=value`, `#` comments, blank lines, and
    single- or double-quoted values. Anything else on a line is ignored rather
    than raising -- a malformed `.env` should not stop the library from running
    on its defaults.
    """
    global _dotenv_cache
    if _dotenv_cache is not None and not force and path is None:
        return _dotenv_cache

    resolved = path or dotenv_path()
    values = {}
    try:
        with open(resolved, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, sep, val = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                if key:
                    values[key] = val
    except OSError:
        pass

    if path is None:
        _dotenv_cache = values
    return values


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def get(name, default=None):
    """Resolve one setting by its un-prefixed name, e.g. `get("BACKEND")`."""
    key = PREFIX + name
    if key in os.environ:
        return os.environ[key]
    env_file = load_dotenv()
    if key in env_file:
        return env_file[key]
    if default is not None:
        return default
    return DEFAULTS.get(name)


def get_int(name, default=None):
    raw = get(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"{PREFIX}{name}={raw!r} is not an integer") from None


def get_float(name, default=None):
    raw = get(name, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"{PREFIX}{name}={raw!r} is not a number") from None


def get_bool(name, default=None):
    raw = get(name, default)
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    low = str(raw).strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("0", "false", "no", "off", ""):
        return False
    raise ConfigError(f"{PREFIX}{name}={raw!r} is not a boolean")


def default_backend():
    return get("BACKEND")


def default_taxonomy():
    return get("TAXONOMY")
