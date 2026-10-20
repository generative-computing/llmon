# Examples

This directory contains worked examples of using LLMON in practice. The language itself,
including its expressivity, Shorthand syntax, grammar, and the converters between the two
representations, is documented in the [lang README](../lang/README.md).

Each notebook is self-contained. It loads the model and defines its own helper functions, so
there is no shared module that must be installed beforehand.

## Setup

The notebooks use
[`ibm-granite/granite-4.1-3b-llmon-preview`](https://huggingface.co/ibm-granite/granite-4.1-3b-llmon-preview),
which is a Granite 4.1 3B checkpoint whose vocabulary contains the six LLMON delimiters as
single tokens. Access to the model is gated, so you must request access on the model page
before you can download it. Once access has been granted, run the following commands.

```bash
hf auth login
pip install -r requirements.txt
jupyter lab
```

Version 5.x of transformers is required, because the checkpoint's `config.json` uses keys
such as `dtype` and `rope_parameters` that version 4.x cannot read. 

If a notebook reports that a module is missing, your Jupyter kernel is probably running in a
different Python environment than your terminal. Install the dependencies into the kernel's
own environment by running the following in a notebook cell, then restart the kernel.

```python
import sys
!{sys.executable} -m pip install -r ../requirements.txt
```

## [`existing-llmon-model/`](existing-llmon-model/)

These two notebooks show how to prompt a model that already understands LLMON. They run on a
CPU or on a single GPU, and they do not perform any training.

| Notebook | Description |
|---|---|
| [`core_use_cases.ipynb`](existing-llmon-model/core_use_cases.ipynb) | Demonstrates seven content-grounded tasks: extraction, classification, grounded question answering, translation, rewriting, tool calling, and multi-turn conversation. Each task passes a list of messages to the model's chat template, using the `instruction`, `data`, and `requirement` fields, together with the `documents` and `tools` arguments for retrieval and tool calling. The multi-turn task replays the earlier turns as separate messages. |
| [`prompt_injection.ipynb`](existing-llmon-model/prompt_injection.ipynb) | Runs six prompt injection attacks and one benign control against the same model in two ways. In the first, the instruction and the untrusted text are concatenated into a single `instr` channel. In the second, the untrusted text is isolated in its own `data` channel. The notebook reports both the cases in which channel separation holds and the cases in which it does not. |

## [`lora-finetuning/`](lora-finetuning/)

This notebook shows how to teach a model new domain concepts by fine-tuning it. It requires a
GPU.

| Notebook | Description |
|---|---|
| [`byo-vocab_LoRA_for_agents.ipynb`](lora-finetuning/byo-vocab_LoRA_for_agents.ipynb) | Fine-tunes the model with LoRA to predict the next tool call in a conversation, using a publicly available tau-bench retail dataset. It converts the conversation history into LLMON with labelled fields, trains the adapter, and then verifies the result on a held-out example. |

This notebook needs more than the shared requirements. It uses the fine-tuning libraries
`peft`, `trl`, and `datasets`, and it imports the repository's own `llmon.lang` package for the
JSON to LLMON converter. Install both from this directory with the following commands.

```bash
pip install -r lora-finetuning/requirements.txt
pip install -e ../..
```

Training artefacts are written to `./finetune_out`. Set the `LLMON_OUT` environment variable
to write them elsewhere.
