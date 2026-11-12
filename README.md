# LLMON

LLMON (LLM Object Notation) is an LLM-native markup language for communicating concepts and structures with LLMs. This communication can occur as LLM inputs (training and prompts) as well as LLM outputs (responses). The goal is to encode richer meta information about the underlying text to make it easier for a model to learn and produce concepts as well as improve safety and security. 

## How to Use LLMON
Since LLMON is a markup language for text that is sent to and received from an LLM, it is natural to use it where this communication with the LLM occurs. Thus, depending on the users, they may or may not be exposed to LLMON. For example, if a user is interacting with an LLM using a python program developed using the [Mellea](https://mellea.ai/) library, the Mellea runtime will leverage LLMON without the end user being aware of its use, much like a python programmer isn't aware of the assembly instructions that are executed on its behalf. Similarly, if a user is using an agentic system or some other high-level interface to an LLM, they will not interact directly with LLMON.

However, there are other uses where it is desirable to have a model learn domain-specific concepts that can be expressed with LLMON. In these cases, the user can choose to fine-tune an existing LLMON model to understand these new concepts and then interact with the resulting model by passing in LLMON text that is annotated with these concepts. 

We provide examples of this scenario in our [`llmon/examples/`](llmon/examples/) directory.

## A Little Taste of LLMON
Here's a simple example of LLMON:
```
    <|open|>instruction<|close|>
      Suggest a title for the following email:
    <|open_end|>
    <|open|>data<|close|>
      Dear customer support, I am writing about the wifi connection on my recent
      flight from JFK to LAX. The quality was poor. I paid $25, and I want a refund.
    <|open_end|>
```
This example communicates to the model that the `Suggest ...` line should be treated as an `instruction` and the `Dear customer ...` block should be treated as `data`.
Further details about the language semantics, grammar, use of special tokens, and converters are provided in [`llmon/lang/README.md`](llmon/lang/README.md).

## Existing LLMON trained models
We provide models trained on the base LLMON constructs: [Granite-4.1-3B-LLMON-preview](https://huggingface.co/ibm-granite/granite-4.1-3b-llmon-preview), finetuned from [Granite-4.1-3B-Base](https://huggingface.co/ibm-granite/granite-4.1-3b-base), and [Granite-4.1-8B-LLMON-preview](https://huggingface.co/ibm-granite/granite-4.1-8b-llmon-preview), finetuned from [Granite-4.1-8B-Base](https://huggingface.co/ibm-granite/granite-4.1-8b-base), to consume LLMON in place of a conventional chat template. The models ship a `chat_template.jinja`, so `apply_chat_template` renders ordinary messages into LLMON and you never write the markup by hand. Instructions reached through the `exec` block are treated as authoritative, while data, documents, tool schemas and tool results reached as input are read rather than executed. See the relevant model cards ([3B](https://huggingface.co/ibm-granite/granite-4.1-3b-llmon-preview), [8B](https://huggingface.co/ibm-granite/granite-4.1-8b-llmon-preview)) for the prompt format, usage and evaluation.

### Visualizing Attention over LLMON Blocks
We have a [demo](https://huggingface.co/spaces/ibm-granite/LLMON-Attention-Visualization) that shows, layer by layer, how a model distributes its attention across LLMON blocks: how much lands on the instruction it was asked to execute, how much on the data that instruction operates on, and how much reaches content left unbound. It makes the effect of the markup measurable rather than anecdotal.

## Installation

For the general case the model's bundled `chat_template.jinja` is sufficient — it renders ordinary messages (instructions, data, documents, tool definitions) into LLMON for you, and needs nothing from this repo.

If you build prompts or training data in Python, `render_chat` from this package produces the same string without a chat template, plus reproducible, unique, and pinnable block ids. See [Rendering chat messages](llmon/lang/README.md#rendering-chat-messages).

The converters here cover what it does not: turning structured input into LLMON with explicit types and flattened nested tags, so that a JSON document arrives as typed, individually addressable blocks rather than as one opaque string of data. This is the better path whenever the content has structure worth preserving.

To install them, run the following in a virtual environment:

`pip install -e .`

This makes `json-to-llmon`, `short-to-llmon` and the rest available on the command line; see [`llmon/lang/README.md`](llmon/lang/README.md) for details.

## Getting started with LLMON
Start with the [model card](https://huggingface.co/ibm-granite/granite-4.1-3b-llmon-preview): its prompt format and generation sections carry a complete worked example, from ordinary messages through to the rendered markup. Then read [`llmon/lang/README.md`](llmon/lang/README.md) for the language and its converters, and work through the use cases in [`llmon/examples/`](llmon/examples/README.md). Those two cover prompts with richer structure and semantic content — extraction, grounded RAG, tool calling, multi-turn — which is where LLMON gives the most over a flat prompt.

## Directory structure
- `llmon/lang`: contains code and documentation of the LLMON language and converters
- `llmon/plugins`: contains 
  - VSCode extensions for viewing and converting LLMON and shorthand files
  - emacs extensions for viewing LLMON and shorthand files
- `llmon/examples`: contains example use cases of working with LLMON

## Papers
- The LLMON language and early evaluations are described in this [paper](https://arxiv.org/abs/2603.22519).
- The post-training methodology used to build the LLMON models, and its evaluations, are described in this [paper](https://arxiv.org/abs/2610.38120).
