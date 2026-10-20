# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys

import pytest

import llmon.lang.common.common as C
from llmon.lang.common.errors import LLMONError
from llmon.lang.json.llmon_converters import LLMONConverters
from llmon.lang.shorthand.llmon_to_short import LLMON_to_Shorthand_Converter
from llmon.lang.shorthand.short_to_llmon import Shorthand_to_LLMON_Converter

MISMATCHED_LLMON = "<|open|>a<|close|>1<|open_end|>b<|close|>"
UNCLOSED_SHORTHAND = "/a\\"
STRAY_CLOSE_LLMON = "<|close|>"

LIBRARY_CASES = {
    "llmon_to_json": lambda: LLMONConverters(
        converting_to_json=True, no_openend_tagname=False
    ).llmon_str_to_json_str(MISMATCHED_LLMON),
    "process_llmon": lambda: LLMONConverters(
        no_openend_tagname=False
    ).process_llmon_str(MISMATCHED_LLMON),
    "short_to_llmon": lambda: Shorthand_to_LLMON_Converter().process_line(
        UNCLOSED_SHORTHAND, 1
    ),
    "llmon_to_short": lambda: LLMON_to_Shorthand_Converter(
        C.LLMON_TAGS | {":"}, True
    ).process_line(STRAY_CLOSE_LLMON, 1),
}


@pytest.mark.parametrize("convert", LIBRARY_CASES.values(), ids=LIBRARY_CASES.keys())
def test_malformed_input_raises_catchable_value_error(convert):
    with pytest.raises(LLMONError) as exc_info:
        convert()
    assert isinstance(exc_info.value, ValueError)
    assert str(exc_info.value).startswith("**")



@pytest.mark.parametrize("method", ["llmon_str_to_json_str", "process_llmon_str"])
def test_converter_is_reusable_after_error(method):
    conv = LLMONConverters(converting_to_json=True)
    convert = getattr(conv, method)
    expected = convert("<|open|>a<|close|>x<|open_end|>")
    with pytest.raises(LLMONError):
        convert("<|open|>a<|open_end|>1<|open_end|>b<|close|>")
    assert convert("<|open|>a<|close|>x<|open_end|>") == expected


CLI_CASES = {
    "llmon-to-json": ("llmon.lang.json.llmon_to_json", ["-eot"], MISMATCHED_LLMON),
    "process-llmon": ("llmon.lang.llmon_parsing.process_llmon", ["-eot"], MISMATCHED_LLMON),
    "short-to-llmon": ("llmon.lang.shorthand.short_to_llmon", [], UNCLOSED_SHORTHAND),
    "llmon-to-short": ("llmon.lang.shorthand.llmon_to_short", [], STRAY_CLOSE_LLMON),
}


@pytest.mark.parametrize(("module", "args", "stdin"), CLI_CASES.values(), ids=CLI_CASES.keys())
def test_cli_reports_malformed_input_without_traceback(module, args, stdin):
    result = subprocess.run(
        [sys.executable, "-c", f"from {module} import main; main()", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert result.stderr.startswith("**")
    assert "Traceback" not in result.stderr
