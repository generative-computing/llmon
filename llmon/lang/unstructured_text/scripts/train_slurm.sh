#!/usr/bin/env bash
#SBATCH --job-name=llmon-seg-train
#SBATCH --nodes=1
#SBATCH --gres=gpu:1
#SBATCH --time=01:30:00
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
#
# Train a segmentation backend on a Slurm cluster, over several seeds, and
# evaluate each one.
#
#   export ENV_ACTIVATE="conda activate /path/to/your/env"
#   export CORPUS=/path/to/train.json HOLDOUT=/path/to/holdout.json
#   export SBATCH_ACCOUNT=your_account SBATCH_PARTITION=your_partition
#   mkdir -p logs out
#   sbatch scripts/train_slurm.sh
#
# Everything site-specific comes from the environment; there are deliberately no
# defaults pointing at any particular cluster. Account and partition go through
# Slurm's own SBATCH_ACCOUNT / SBATCH_PARTITION variables, because #SBATCH lines
# do NOT expand shell variables -- only %x and %j are substituted there.
#
# Configuration (CORPUS and HOLDOUT are required; no corpus ships with this
# repository -- see docs/CORPUS.md):
#   CORPUS        training corpus
#   HOLDOUT       evaluation corpus
#   ENV_ACTIVATE  shell snippet that activates your Python environment
#   TAXONOMY      three                          (the tagger's label space)
#   SEEDS         space-separated                (default "42 1 7")
#   EPOCHS        (default 4)  PATIENCE (default 2)  OUT_PREFIX (default <backend>_ckpt_s)
#   REPORT_DIR    where evaluation reports land  (default out)
#
# Walltime defaults to 1:30. --time is ENFORCED on Slurm and a job is KILLED at
# the limit, not paused, so raise it rather than hoping. Note also that many
# clusters only protect a job from preemption for its first couple of hours.

# Source the shell profile BEFORE strict mode: /etc/bashrc on many clusters reads
# an unset BASHRCSOURCED, which `set -u` would turn into a fatal error on line 1.
if [ -f "$HOME/.bashrc" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.bashrc"
fi

set -euo pipefail

# NOT $BASH_SOURCE: sbatch executes a COPY of this script from the spool
# directory, so dirname "${BASH_SOURCE[0]}" no longer finds anything beside it.
# SLURM_SUBMIT_DIR is the directory the job was submitted from.
REPO_DIR="${REPO_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}"

BACKEND=tagger        # the only trainable backend
TAXONOMY="${TAXONOMY:-}"
CORPUS="${CORPUS:?set CORPUS to a training corpus (see docs/CORPUS.md)}"
HOLDOUT="${HOLDOUT:?set HOLDOUT to an evaluation corpus (see docs/CORPUS.md)}"
SEEDS="${SEEDS:-42 1 7}"
EPOCHS="${EPOCHS:-4}"
PATIENCE="${PATIENCE:-2}"
OUT_PREFIX="${OUT_PREFIX:-${BACKEND}_ckpt_s}"
REPORT_DIR="${REPORT_DIR:-out}"

# Keep writable caches off shared project storage, which is often near quota.
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-/tmp/triton-$USER}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/xdg-$USER}"
mkdir -p "$HF_HOME" "$TRITON_CACHE_DIR" "$XDG_CACHE_HOME"

# Falls back to the shell PID when run outside Slurm.
JOBID="${SLURM_JOB_ID:-$$}"

if [ -n "${ENV_ACTIVATE:-}" ]; then
    eval "$ENV_ACTIVATE"
else
    echo "note: ENV_ACTIVATE is unset; using whatever python is on PATH" >&2
fi

cd "$REPO_DIR"
mkdir -p logs "$REPORT_DIR"

echo "=== job      : ${SLURM_JOB_NAME:-manual} / $JOBID"
echo "=== host     : $(hostname)"
echo "=== python   : $(command -v python)"
echo "=== backend  : $BACKEND  taxonomy=${TAXONOMY:-<backend default>}"
echo "=== corpus   : $CORPUS"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true
python - <<'PY'
import torch, transformers
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("transformers", transformers.__version__)
PY
echo

TAX_ARG=()
[ -n "$TAXONOMY" ] && TAX_ARG=(--taxonomy "$TAXONOMY")

# Validate the corpora BEFORE asking for GPU time. This checks the offset
# invariant and the label space -- the cheap version of a failure that otherwise
# surfaces an hour into a run, or not at all.
echo "=== corpus check"
python -m llmon.lang.unstructured_text.training.cli check --corpus "$CORPUS" ${TAX_ARG[@]+"${TAX_ARG[@]}"}
python -m llmon.lang.unstructured_text.training.cli check --corpus "$HOLDOUT" ${TAX_ARG[@]+"${TAX_ARG[@]}"}
echo

# Fast unit gate: seconds, and it catches a broken label space or a broken BIO
# round-trip before any training starts.
echo "=== unit tests"
python -m pytest -q tests/test_taxonomy.py tests/test_tagger.py
echo

for SEED in $SEEDS; do
    OUT="${OUT_PREFIX}${SEED}"
    echo "=== training $BACKEND seed=$SEED -> $OUT"
    time python -m llmon.lang.unstructured_text.training.cli "$BACKEND" \
        --corpus "$CORPUS" --out "$OUT" --seed "$SEED" \
        --epochs "$EPOCHS" --patience "$PATIENCE" \
        ${TAX_ARG[@]+"${TAX_ARG[@]}"} --force
    echo

    for MODE in outermost all; do
        REPORT="$REPORT_DIR/${BACKEND}_s${SEED}__holdout_${MODE}"
        echo "=== eval seed=$SEED mode=$MODE -> $REPORT.out"
        python -m llmon.lang.unstructured_text.evaluation.cli \
            --backend "$BACKEND" --model "$OUT" --file "$HOLDOUT" \
            --mode "$MODE" --worst 0 --boundaries \
            ${TAX_ARG[@]+"${TAX_ARG[@]}"} \
            --json "$REPORT.json" > "$REPORT.out" 2>&1 || true
        grep -E 'High overlap|MID-SENTENCE|^  tagger|^  rules' "$REPORT.out" || true
        echo
    done
done

echo "=== done. checkpoints:"
for SEED in $SEEDS; do ls -d "${OUT_PREFIX}${SEED}" 2>/dev/null || true; done
echo
echo "Seed variance on a corpus of a few thousand spans was measured at up to"
echo "8.8 points, so report the MEAN and SPREAD across seeds, not one number."
