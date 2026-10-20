This directory contains code and tests related to converters between LLMON and JSON.

Here are the relevant files:
| File | Description |
|---|---|
| `llmon_converters.py` | a class containing the JSON <-> LLMON converters and LLMON parsers |
| `json_to_llmon.py` | main file that converts JSON to LLMON using llmon_converters.py |
| `llmon_to_json.py` | main file that converts LLMON to JSON using llmon_converters.py |

# Developers
Relevant regressions tests are run by ../tests/run_json_tests.sh

Please run the regression tests before submitting a PR
