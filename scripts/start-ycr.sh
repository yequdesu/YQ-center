#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

PORT="${1:-9810}"
BASE_URL="http://127.0.0.1:${PORT}"

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}       YeQu Context Router -- start script            ${NC}"
echo -e "${CYAN}======================================================${NC}"

if [ ! -d ".venv" ]; then
  echo -e "${RED}[ERROR] .venv not found${NC}"
  exit 1
fi

source .venv/bin/activate

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export YEQU_YCR_BACKEND="${YEQU_YCR_BACKEND:-http}"
export YEQU_YCR_BASE_URL="${YEQU_YCR_BASE_URL:-${BASE_URL}}"

if [ -z "${YEQU_YCR_SERVICE_TOKEN:-}" ]; then
  echo -e "${RED}[ERROR] YEQU_YCR_SERVICE_TOKEN is not set${NC}"
  echo "Add YEQU_YCR_SERVICE_TOKEN to .env. Center must use the same value."
  exit 1
fi

export YEQU_YCR_EMBEDDING_BASE_URL="${YEQU_YCR_EMBEDDING_BASE_URL:-http://127.0.0.1:9820}"

export YEQU_YCR_EMBEDDING_API_KEY="${YEQU_YCR_EMBEDDING_API_KEY:-${YEQU_EMBEDDER_API_KEY:-local-dev}}"

echo -e "${YELLOW}[1/3] Ensure PostgreSQL is running ...${NC}"
docker compose up -d postgres

echo -e "${YELLOW}[2/3] Check pgvector extension availability ...${NC}"
python - <<'PY'
import asyncio
from sqlalchemy import text
from yequ.db import async_session_factory

async def main():
    async with async_session_factory() as db:
        await db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await db.commit()

asyncio.run(main())
PY

echo -e "${YELLOW}[3/3] Start YCR on ${BASE_URL} ...${NC}"
echo -e "${GREEN}health: curl -H 'Authorization: Bearer <token>' ${BASE_URL}/v1/context/status${NC}"
exec python -m uvicorn yequ.ycr_app:app --host 127.0.0.1 --port "${PORT}"
