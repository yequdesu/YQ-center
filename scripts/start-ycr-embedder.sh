#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${1:-9820}"

if [ ! -d ".venv" ]; then
  echo "[ERROR] .venv not found"
  exit 1
fi

source .venv/bin/activate

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export YEQU_EMBEDDER_MODEL="${YEQU_EMBEDDER_MODEL:-BAAI/bge-m3}"
export YEQU_EMBEDDER_DEVICE="${YEQU_EMBEDDER_DEVICE:-cpu}"
export YEQU_EMBEDDER_USE_FP16="${YEQU_EMBEDDER_USE_FP16:-false}"

echo "Starting YeQu local embedder on http://127.0.0.1:${PORT}"
echo "Model: ${YEQU_EMBEDDER_MODEL} device=${YEQU_EMBEDDER_DEVICE} fp16=${YEQU_EMBEDDER_USE_FP16}"
exec python -m uvicorn yequ.ycr_embedder_app:app --host 127.0.0.1 --port "${PORT}"
