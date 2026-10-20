# Architecture

The library turns a prompt into a flat list of typed, non-overlapping spans, then
renders them as LLMON markup (shown below in its human-readable Shorthand form).

```
  "You are a pirate. Write a poem about dogs in under 50 words."
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │   one of two interchangeable backends   │
        └─────────────────────────────────────────┘
                              │
                              ▼
   constraint  "You are a pirate"        \constraint:a\ … /constraint:a/
   instruction "Write a poem about dogs" \instr:a\ … /instr:a/
   constraint  "in under 50 words"       \constraint:b\ … /constraint:b/
```

## Backends

Two interchangeable backends, producing the same result type.

| Backend | Segments? | Classifies? | Cost / prompt | Taxonomies | Needs |
|---|---|---|---|---|---|
| `rules` | yes, by regex | yes | **0.22 ms** | three, nine | nothing (stdlib) |
| **`tagger`** (default) | **yes, its own boundaries** | yes | ~100 ms CPU | three only | a checkpoint |

### The distinction that matters

Any design that segments first and labels second **cannot recover a boundary the
segmenter missed**, however good its labelling. The segmenter's boundary recall is
therefore a hard ceiling on segment-then-label designs, including the `rules`
backend itself:

| boundary type | share of all boundaries | `rules` finds |
|---|---|---|
| at a sentence break | ~69% | **81.5%** |
| mid-sentence | ~31% | **18.3%** |

The ceiling holds no matter how good the labelling gets: "Write a poem about dogs"
and "in under 50 words" are one sentence and two spans, so anything that only
assigns types to spans it was handed can never separate them.

The `tagger` exists to break that ceiling. It assigns a BIO tag to every token and
decodes spans from runs of tags, so it produces its own boundaries and finds
**61.1%** of mid-sentence ones — over three times the rule segmenter. That is why it
is the default. See [RESULTS.md](RESULTS.md).

### Choosing one

- **`tagger`** unless you have a reason. Best accuracy, and the only backend that
  reliably splits within a sentence.
- **`rules`** when you cannot take a ~570 MB checkpoint and a torch dependency, or
  when 0.22 ms matters. It under-splits, and it is honest about it: low-confidence
  spans come back with `requires_review=True`.

Nine classes come only from `rules`. Nothing model-based produces them: the
tagger's BIO head carries one tag pair per class, so nine classes means 19 tags
with far too little support per tag in a corpus this size.

## Taxonomies

Two closed label spaces ship. Both are values in
[`taxonomy.py`](../taxonomy.py), not global state.

**`three`** (default) — `instruction`, `constraint`, `data`.

**`nine`** — what the annotation guidelines define
([guidelines.html](guidelines.html) is the authority), across three planes:

| plane | classes |
|---|---|
| control | `Instruction`, `Constraint`, `Output Format` |
| content | `Content`, `Structured Data`, `Quoted Content`, `Example` |
| structural | `Role`, `Context` |

The collapse:

| three | ← nine |
|---|---|
| `instruction` | `Instruction` |
| `constraint` | `Constraint`, `Output Format`, `Example`, `Role` |
| `data` | `Content`, `Structured Data`, `Quoted Content`, `Context` |

Collapsing is exact and one-way. Annotate in the finest space you have: a
nine-class corpus yields the three-class view on load, and the reverse is
impossible.

**A consequence worth knowing before you pick:** `three` can only emit three
tag kinds — `instr`, `constraint`, `data`. The `format` and `context` tags exist
only under `nine`, which means only from the `rules` backend, at markedly lower
accuracy. And a `Role` span renders as `instr` under `nine` but as `constraint`
under `three`, because `Role` collapses into `constraint`.

## Injection detection

Cross-cutting, not a backend. Seven keyword patterns in
[`injection.py`](../injection.py) run on **every** backend
including the dependency-free one, and OR into `result.injection_detected`.

`Injection` is deliberately not a class in either taxonomy. It is orthogonal:
a span can be a `Constraint` *and* an injection attempt. When the flag is set,
`to_llmon()` and `to_short()` **refuse to render** unless you pass
`allow_injection=True` — rendering wraps the injected text in markup that a
downstream model then reads as structure.

**It is a heuristic tripwire, not a security boundary.** "Ignore all previous
instructions" is caught. A paraphrase, another language, or an encoding is not.
A positive is a reason to look; a negative is not evidence of safety.

## Invariants

1. **Offsets are never model arithmetic.** `prompt[span.start:span.end] ==
   span.text`, always. Offsets come from the tokenizer's own character mapping.
   Re-deriving them by searching for the span text silently picks the wrong
   occurrence when a prompt repeats a phrase.
2. **Output is a flat, non-overlapping span list.** Ground-truth annotation
   nests; output does not. The `rules` backend emits the enclosing span; the
   `tagger` is trained on the innermost, which is what lets it cut within a
   sentence. The two are therefore scored against different
   targets and their numbers are not comparable — see `--mode` in
   [RESULTS.md](RESULTS.md).
3. **The label space is closed.** Every backend predicts within one taxonomy. An
   unrecognised label raises `TaxonomyLeak` rather than falling back to a default,
   and every checkpoint records the label list it was trained on and validates it
   on load.

## Confidence

`requires_review` on a span is the honest flag that it fell below the trust threshold,
for a caller or a human to act on. Nothing downstream consumes it automatically.

**Confidence is not comparable across models.** The tagger's is a mean over per-token
max-probabilities and sits at 0.85–0.99 on real spans; the rule engine's is a weighted
sum over matched patterns. The two thresholds in `config.py` are calibrated separately
for that reason, and a gate tuned for one label source is meaningless for the other.

## Module map

| module | role | imports torch? |
|---|---|---|
| `api.py` | `segment()`, `Span`/`Segment`/`SegmentationResult` | no |
| `taxonomy.py` | the label spaces, as values | no |
| `rules.py` | the weighted regex table | no |
| `heuristic.py` | the `rules` backend | no |
| `injection.py` | the safety tripwire | no |
| `render.py` | LLMON / Shorthand output | no |
| `rewriter.py` | optional span-text normalisation | no |
| `tagger.py` | the `tagger` backend | **yes** |
| `cli.py` / `repl.py` | `llmon-segment` and its interactive mode | no |
| `config.py` / `weights.py` | settings, checkpoint resolution | no |
| `training/` | corpus building, training, threshold sweeps | **yes** |
| `evaluation/` | the scoring harness and `llmon-seg-eval` | no |

`import llmon.lang.unstructured_text` pulls **none** of the torch-dependent code:
`tagger.py` is imported inside `segment()`. That keeps a bare `pip install llmon`
free of the ML stack and the `rules` path at ~0.15 s startup instead of ~0.7 s.
