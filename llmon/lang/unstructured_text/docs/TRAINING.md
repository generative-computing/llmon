# Training

No corpus ships with this repository; build or bring your own in the schema in
[CORPUS.md](CORPUS.md). The paths below assume you built it into `data/`, which is
gitignored. You also need the ML stack:

```bash
pip install -e '.[unstructured-text]'    # from the repository root
```

## Locally

```bash
# 1. validate the corpus first: seconds, no ML dependencies, catches the
#    misalignment that would otherwise waste an hour of GPU time
llmon-seg-train check --corpus data/train.json --taxonomy nine

# 2. train
llmon-seg-train tagger --corpus data/train.json --out tagger_ckpt

# 3. evaluate on the holdout, never on the training set
llmon-seg-eval --backend tagger --model tagger_ckpt --file data/holdout.json \
    --taxonomy three --mode outermost --boundaries
```

### Training without network access

By default the base encoder (ModernBERT-base) is fetched from the HuggingFace
Hub. `--base-model` points at a local copy instead — including an existing
checkpoint's `encoder/` contents, which is the quickest way to train on an
air-gapped machine:

```bash
mkdir -p /tmp/base && cp tagger_ckpt/encoder/* tagger_ckpt/tokenizer*.json /tmp/base/
llmon-seg-train tagger --corpus data/train.json --out new_ckpt --base-model /tmp/base
```

The tokenizer files sit at the checkpoint root rather than inside `encoder/`, so
both have to be copied.

## Options

| flag | default | |
|---|---|---|
| `--epochs` | 4 | maximum; early stopping can end training sooner |
| `--patience` | 2 | epochs without a validation-loss improvement before stopping and reverting to the best epoch; 0 disables |
| `--test-fraction` | 0.2 | share of the training corpus held back as the early-stopping validation set; the holdout is never used for it |
| `--batch-size` | 8 | records per batch, also capped by a token budget so a batch of long prompts does not spike memory |
| `--learning-rate` | 2e-5 | |
| `--seed` | 42 | see below |

## Seeds

Train at least three. Seed alone moved results by up to **8.8 points** on a
corpus of a few thousand spans — larger than most effects worth measuring. A
single checkpoint's score is not a point estimate.

```bash
for SEED in 42 1 7; do
  llmon-seg-train tagger --corpus data/train.json --out tagger_ckpt_s$SEED --seed $SEED
done
```

Report the mean and the spread, and promote on **holdout** F1 — never on the
on-screen "High overlap %", which is precision-shaped and can fall while recall
improves.

## On a Slurm cluster

[`scripts/train_slurm.sh`](../scripts/train_slurm.sh) trains several seeds and
evaluates each. Everything site-specific comes from the environment; there are no
defaults pointing at any particular cluster.

```bash
export ENV_ACTIVATE="conda activate /path/to/your/env"
export CORPUS=/path/to/train.json HOLDOUT=/path/to/holdout.json
export SBATCH_ACCOUNT=your_account SBATCH_PARTITION=your_partition
mkdir -p logs out
sbatch scripts/train_slurm.sh
```

The script validates both corpora and runs the fast unit tests **before**
requesting any GPU work.

Four things that are easy to get wrong on Slurm, all handled in that script and
worth knowing if you write your own:

- **`--time` is enforced, and a job is killed at the limit, not paused.** It also
  defaults to something long and unhelpful if you omit it. Set it.
- **`sbatch` runs a *copy* of your script** from the spool directory, so
  `dirname "${BASH_SOURCE[0]}"` no longer finds anything beside it. Use
  `SLURM_SUBMIT_DIR`.
- **`#SBATCH` lines do not expand shell variables.** Only `%x` and `%j` are
  substituted. Anything environment-dependent goes through `SBATCH_*` variables or
  command-line flags.
- **Source your shell profile before `set -u`.** Many `/etc/bashrc` files read an
  unset variable, which strict mode turns into a fatal error on line 1.

One more, if your cluster records job scripts and submission environments (Slurm
does by default): **never put a credential in an rc file, a `#SBATCH` directive, a
command-line argument, or an `--export` list.** Keep it in a mode-600 file and
read it at runtime, inside the script.

## Choosing post-processing thresholds

If you retrain, re-sweep — the shipped values were tuned on one corpus.

```bash
python -m llmon.lang.unstructured_text.training.sweep --holdout data/holdout.json --dump       # GPU; caches raw predictions
python -m llmon.lang.unstructured_text.training.sweep --holdout data/holdout.json --sweep      # replays offline, no GPU
python -m llmon.lang.unstructured_text.training.sweep --holdout data/holdout.json --calibrate  # picks the escalation gate
```

Decide on the holdout, and keep it stratified across sources (see
[CORPUS.md](CORPUS.md)): tuning on a set dominated by one easy source picks a
threshold that flatters the easy case.

## What the checkpoint contains

```
tagger_ckpt/
├── head.pt                     the head, plus `taxonomy` and `labels`
├── tokenizer.json
└── encoder/{config.json,model.safetensors}
```

~570 MB. Gitignored. `head.pt` records the label list the model was trained on,
and `load()` validates it — so a checkpoint can never be silently reinterpreted
in the wrong label space. Point at one with `LLMON_SEG_TAGGER_PATH`, or publish
it to HuggingFace and use `LLMON_SEG_TAGGER_REPO`. See
[`.env.example`](../.env.example).
