This directory contains various test of the LLMON converters. It contains regression testing scripts that will automatically
run these tests and report differences. The tests are partitioned into 3 subdirectories: json, shorthand, and process-llmon. Each set of tests
has a corresponding script to run the tests

| Script | Purpose |
|---|---|
| `run_json_tests.sh`| runs regression tests for JSON <--> LLMON converters
| `run_shorthand_tests.sh`| runs regression tests related to the Shorthand <--> LLMON converters
| `run_process_llmon_tests.sh` | runs regression tests related to general LLMON parsing
| `pytest` (from the repo root) | runs `test_converter_errors.py`, which checks that malformed input raises `LLMONError`

| Directory | Contents |
|---|---|
|`json/` | JSON <--> LLMON tests and expected results |
|`shorthand/` | Shorthand <--> LLMON tests and expected results |
|`process_llmon/` | process LLMON tests and expected results |

