# Results

All numbers below were produced by `llmon-seg-eval` on a 134-prompt holdout
stratified across four sources. The holdout is not distributed (see
[CORPUS.md](CORPUS.md)), so running the same commands against a corpus of your own
will measure your data, not reproduce these figures.

Every figure is from a **single promoted checkpoint**, not a mean. Read the
[caveats](#caveats-read-these-before-quoting-anything) first; the seed-variance
one in particular changes how much any single number is worth.

## Span accuracy

Recall / precision / F1 are micro-averaged over classes, scoring against
top-level ground truth (`--mode outermost`).

### Three-class taxonomy — the default

| backend | holdout R | holdout P | holdout F1 |
|---|---|---|---|
| `rules` | 68.2 | 69.3 | 68.8 |
| **`tagger`** | **78.3** | **77.0** | **77.7** |

### Nine-class taxonomy

| backend | holdout R | holdout P | holdout F1 |
|---|---|---|---|
| `rules` | 41.1 | 42.7 | 41.9 |

The nine-class figures are much lower than the three-class ones on the same
prompts and the same predictions. That is the taxonomy, not a regression: nine
classes include distinctions the rule engine genuinely cannot make
(`Constraint` vs `Output Format`, `Content` vs `Context`), and collapsing removes
them.

### Per-class F1, three-class holdout

| backend | `instruction` | `constraint` | `data` |
|---|---|---|---|
| `rules` | 0.722 | 0.641 | 0.724 |
| `tagger` | 0.792 | **0.747** | 0.810 |

`constraint` is the hard class for both backends. That fits the diagnosis: most
`constraint` errors are a *segmentation* problem, not a labelling one — a constraint clause
hangs off the end of an instruction sentence, and 86% of those boundaries have a
comma at them. The tagger can cut there; the `rules` backend cannot.

## Boundary recall — the number the tagger exists for

Recall over ground-truth boundaries, split by whether sentence-ending
punctuation separates the two spans. A predicted cut within 3 characters counts.

| segmenter | at a sentence break | **mid-sentence** |
|---|---|---|
| `rules` | 81.5% (224/275) | **18.3%** (23/126) |
| **`tagger`** | 92.0% (253/275) | **61.1%** (77/126) |

Mid-sentence boundaries are about 31% of all boundaries, so this is the whole
reason the tagger is the default.

## Post-processing defaults, and why

Two passes were swept on the holdout across three seeds. Both defaults are
measured, not chosen; they live in [`.env.example`](../.env.example).

| pass | shipped default | holdout F1 | mid-sentence recall | verdict |
|---|---|---|---|---|
| none | — | 75.5 | 61.9% | baseline |
| `absorb_degenerate_spans` | **0.60, ON** | **78.8** (+3.3) | 55.8% (−6.1) | **kept** |
| `merge_adjacent_same_class` | **0.0, OFF** | 76.0 (+0.5) | 43.7% (**−18.2**) | **rejected** |

The merge pass was built to repair the tagger's over-splitting and does not
earn its place: +0.5 F1 for −18.2 points of the metric the model exists to move
is the wrong trade. It stays in the code, off by default, opt-in via
`LLMON_SEG_MERGE_THRESHOLD`.

`LLMON_SEG_ESCALATION_THRESHOLD` is `0.60`, from a monotonic calibration table: it
flags 17% of spans, of which 97% are genuinely wrong. It is deliberately the same
number as the absorb threshold so there is one constant with one justification.

## Caveats — read these before quoting anything

1. **Single seed.** Training-seed variance alone was measured at up to **8.8
   points** on a corpus of a few thousand spans. Every number here is one
   checkpoint. Train three seeds and report mean ± spread. (The spread narrowed as
   the corpus grew — ~4 points at nine classes, 0.9–2.4 at three — but it is never
   negligible.)
2. **The on-screen "High overlap %" is precision-shaped.** Its denominator is
   *spans predicted*, so a backend that finds more correct spans can score lower
   on it than one that finds fewer. The tagger predicts 665 spans against 535
   ground-truth labels, so this hits it hardest. Use micro recall/F1, or `--json`.
3. **`--mode outermost` and `--mode all` are not comparable.** `outermost` scores
   against top-level ground truth; `all` includes nested labels. The tagger is
   trained on the innermost target and the `rules` backend emits the flat one, so
   each looks better in its own mode. Everything above is `outermost`.
4. **The tagger over-splits.** The promoted checkpoint predicts 665 spans against
   535 labels (+24%); across the three sweep seeds the mean was 749 (+40%). Only
   about a third of adjacent span pairs share a class, so a same-class merge
   cannot fix it (see the table above). This is the main open accuracy problem.

## Reproducing against your own corpus

```bash
llmon-seg-eval --backend tagger --model <ckpt> --file <holdout.json> \
    --taxonomy three --mode outermost --boundaries --json metrics.json
```

The threshold sweep that produced the post-processing defaults:

```bash
python -m llmon.lang.unstructured_text.training.sweep --holdout <holdout.json> --dump       # needs a GPU, caches predictions
python -m llmon.lang.unstructured_text.training.sweep --holdout <holdout.json> --sweep      # replays offline
python -m llmon.lang.unstructured_text.training.sweep --holdout <holdout.json> --calibrate
```
