This directory contains the converters between Shorthand and LLMON.
Shorthand is the human-friendly syntax that leverages \ and / characters to distinguish tags from regular text.  
LLMON is the actual text markup that is given to a model, leveraging special tokens such as <|open|>, <|close|>, etc.
Shorthand is just a human-friendly version of LLMON. All communication with LLMs should be done via LLMON, not shorthand.

Here are the relevant files:
| File | Description |
|---|---|
| short_to_llmon.py | this file converts shorthand to LLMON, this functionality will be used frequently to go from human authored markup to LLMON to be fed to a model |
| llmon_to_short.py | this file converts LLMON back to shorthand, this provides functionality that will be less common, but could be useful if a human wants to understand a LLMON file |
| text_to_escaped.py | this file takes a regular text file and produces the same text file where any instance of \ or / is escaped. The output could then be passed into short_to_llmon.py |

Note: untagged text is legal LLMON, so the llmon-to-short converter treats any non-LLMON files as content: conversion "succeeds" (exit 0).

# Developers
Relevant regressions tests are run by ../tests/run_shorthand_tests.sh

Please run the regression tests before submitting a PR
