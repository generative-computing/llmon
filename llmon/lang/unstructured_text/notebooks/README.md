# Notebooks

## [`tagger_walkthrough.ipynb`](tagger_walkthrough.ipynb)

A short tour of the `tagger` backend for someone who has not used it before: what it does
to a prompt, what it looks like one token at a time, and how to build on the spans it
produces.

### Running it

```bash
pip install -e '../../../..[unstructured-text]'   # torch, transformers, huggingface-hub
pip install jupyterlab                         # if you do not have it
jupyter lab tagger_walkthrough.ipynb
```

Then edit the first cell — `SOURCE`, and either `LOCAL_PATH` or `HF_REPO`. A blank
`HF_REPO` uses the published checkpoint, `DEFAULT_TAGGER_REPO` in
[`../weights.py`](../weights.py).

If the dependencies are missing the second cell installs them into the kernel's own
interpreter, which is usually what went wrong when an install "did not take". Set
`AUTO_INSTALL = False` in that cell to have it print the command and stop instead.

The notebook locates the checkout root and puts it on `sys.path`, so
`llmon.lang.unstructured_text` imports from a source tree without installing anything. A
checkpoint is required — every cell after the first uses the model.

Outputs are not committed; run it to fill them in.
