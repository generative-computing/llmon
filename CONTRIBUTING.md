# Contributing to LLMON

Thank you for your interest in contributing. This document describes how to set up a development environment, run the tests, and propose a change.

By participating in this project you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting issues

Please use the issue tracker for bugs, questions and feature requests. Include the command you ran, the input that caused the problem if you can share it, the output you expected, and the output you received.

Do not report security vulnerabilities in a public issue. See [SECURITY.md](SECURITY.md).

## Repository layout

| Path | Contents |
|---|---|
| `llmon/lang/` | The language: converters between Shorthand, LLMON and JSON, and the tests for them |
| `llmon/lang/unstructured_text/` | Segmentation and classification of free text into LLMON blocks |
| `llmon/plugins/` | Editor support for VS Code and Emacs |
| `llmon/examples/` | Example notebooks |

## Setting up

You need Python 3.10 or later. From the root of a clone, create a virtual environment and install the package with its development dependencies.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

The install adds the command-line tools `short-to-llmon`, `llmon-to-short`, `json-to-llmon`, `llmon-to-json` and `process-llmon` to your path.

## Running the tests

Run the Python tests from the root of the repository.

```bash
pytest
```

The converters also have regression suites, which run each tool on a set of inputs and compare the output with the expected files. They call the command-line tools, so run them with the virtual environment active.

```bash
sh llmon/lang/tests/run_json_tests.sh
sh llmon/lang/tests/run_shorthand_tests.sh
sh llmon/lang/tests/run_process_llmon_tests.sh
```

Each suite ends with a line that reports the number of failures. See `llmon/lang/tests/README.md` for details. Please make sure that all of these pass before you open a pull request.

## Proposing a change

1. Open an issue first for anything beyond a small fix, so that the approach can be agreed before you invest time in it.
2. Create a branch from `main` and keep the change focused on one concern.
3. Add or update tests for any change in behaviour. Expected-output files for the regression suites live next to their inputs under `llmon/lang/tests/`.
4. Update the documentation when you change behaviour that a README describes.
5. Open a pull request and describe what changed and why. Link the issue it addresses.

## Source file headers

Every Python, JavaScript and Emacs Lisp source file begins with the following two lines, written with the comment syntax of the language.

```
# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
```

Please add them to any new source file. Test fixtures and data files such as `.json`, `.llmon` and `.short` do not carry the header.

## Licence

By contributing, you agree that your contribution is licensed under the Apache License, Version 2.0, the same licence as the rest of the project. See [LICENSE](LICENSE).
