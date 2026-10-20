# Corpora

No corpus ships with this repository. The published tagger checkpoint was trained
on 1,219 annotated prompts and evaluated on a 134-prompt holdout, drawn from four
public benchmarks — IFEval, MMLU, InfoBench and ComplexBench (see
[NOTICE](../NOTICE)) — and those annotations are not distributed.

Inference needs no corpus. To evaluate, retrain, or re-sweep the post-processing
thresholds, bring your own in the schema below; the rest of this document is how
to build one.

## Schema

One JSON file holding an array of prompts:

```json
[
  {
    "key": "mysource_1",
    "prompt": "You are a pirate. Write a poem about dogs in under 50 words.",
    "complete": true,
    "labels": [
      {"class": "Role",        "span": {"start":  0, "end": 17}},
      {"class": "Instruction", "span": {"start": 18, "end": 59}},
      {"class": "Constraint",  "span": {"start": 42, "end": 58}}
    ]
  }
]
```

| field | meaning |
|---|---|
| `key` | `<source>_<id>`. The part before the **last** underscore is the source name, which is what holdout stratification and per-source reporting group by. Must be unique across every file you merge. |
| `prompt` | The full prompt text, verbatim. |
| `complete` | `false` marks an unfinished annotation; those prompts are dropped. |
| `labels` | May overlap, may nest, and one span may carry several classes. |
| `class` | A class name from your taxonomy — see [ARCHITECTURE.md](ARCHITECTURE.md). |
| `span` | Character offsets into `prompt`. |

### The one invariant

`prompt[start:end]` **is** the span. Offsets are never re-derived from text,
because a prompt can contain the same substring twice and searching for it
silently picks the wrong occurrence. `llmon-seg-train check` verifies this and
refuses rather than letting you train on misaligned targets.

## Which taxonomy to annotate in

**Annotate in the finest space you have — nine classes.** Collapsing nine to
three on load is exact; expanding three to nine is impossible. A nine-class
corpus gives you both views for free:

```python
from llmon.lang.unstructured_text.training.corpus import load

nine  = load("train.json")                     # as annotated
three = load("train.json", taxonomy="three")   # collapsed on load
```

If you only care about three classes, annotating directly in them is fine — you
just lose the option of ever training a nine-class model, or of scoring the `rules`
backend in its native label space, without re-annotating.

[`guidelines.html`](guidelines.html) is the authoritative definition of what each
of the nine classes means. Read it before annotating; the `Constraint` vs
`Output Format` boundary in particular is not guessable.

## Producing annotations

[`labeler.html`](labeler.html) is a self-contained browser tool — open it
locally, no server, no install. Open a `.jsonl` file of unlabeled prompts (one
`{"key": ..., "prompt": ...}` object per line) or a `.json` corpus in the schema
above, select text to label a span, pick a class, and export JSON in the schema
above. There is no autosave: export before closing the tab.

## Building train/holdout

```bash
llmon-seg-train build --sources batch1.json batch2.json --out-dir data/
```

This merges, cleans and splits, writing `data/train.json` and
`data/holdout.json`. `data/` is gitignored, so a local corpus is never committed,
and it is where the test suite looks for one.

**The holdout is stratified by source**, taking 15% of each source rather than
15% of the pool. This matters more than it sounds. An unstratified holdout ends
up dominated by whichever source is largest and most templated, and then
overstates real-world accuracy — measured at about 12 points on this corpus. If
you take one thing from this document, take that.

Useful flags:

| flag | why |
|---|---|
| `--train-only FILE...` | Prompts from these files always go to train. Use it for data an existing checkpoint already saw, so a before/after comparison stays honest. |
| `--holdout-fraction` | Default 0.15. |
| `--seed` | Default 1337. Changing it reshuffles the split and makes previously reported numbers incomparable. |
| `--taxonomy` | Label space to validate against. Default `nine`. |
| `--report FILE` | Write the build report as JSON. |

### What cleaning does

Four anomaly classes, each counted rather than silently swallowed:

| anomaly | handling |
|---|---|
| `complete: false` | drop the prompt |
| a class outside the taxonomy | drop the **label**, then the prompt if nothing valid survives |
| a span reaching past the end of the prompt | clip it, and report it |
| an empty or whitespace-only span | drop the label |

The off-taxonomy case is handled rather than ignored because the label space is
closed: an unknown class has no BIO tag to train on, and every later load that
collapses the corpus would refuse it, so one stray label would block the whole
file.

The overrun check is not theoretical either. The first time it ran over an
existing evaluation set it found 5 spans whose annotation ended 1–3 characters
past the end of the prompt, which every prior load had silently accepted.

## Checking a corpus

```bash
llmon-seg-train check --corpus data/train.json --taxonomy nine
```

Reports prompt and span counts, per-source composition, per-class support, and
verifies the offset invariant and the label space. No ML dependencies needed —
run it before asking for GPU time.

## Sizing

For reference, the corpus behind the published checkpoint is 1,219 training
prompts / 5,987 span records, from four sources. Below roughly this size,
training-seed variance alone was measured at up to **8.8 points**, which is
larger than most effects worth measuring — so at a smaller scale, train several
seeds and compare distributions, not single numbers.

Watch per-class support especially. Under nine classes the rarest class had a
support of 1 in the holdout, which makes its F1 meaningless; collapsing to three
brought the largest/smallest class ratio from 15:1 to under 2:1.
