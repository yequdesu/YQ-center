#!/usr/bin/env bash
# Chinese encoding diagnostic script
# Test 1: direct API call
# Test 2: raw body bytes via ncat
# Test 3: PostgreSQL direct

set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

CENTER="http://127.0.0.1:9800"
AUTH="Authorization: Bearer qq756522327"
GOAL="Windows健康巡检-UTF8测试-$(date +%s)"

echo "=== Test 1: Direct API round-trip ==="
echo "Sending goal: $GOAL"

RESP=$(curl -s -X POST "$CENTER/admin/maintenance/plans" \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "$AUTH" \
  -d "{\"goal\":\"$GOAL\",\"target_node_id\":\"test-node\",\"steps\":[{\"function_name\":\"system.metrics.snapshot\",\"input\":{}}]}")

echo "Response: $RESP"
PLAN_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('plan_id',''))" 2>/dev/null || echo "")

if [ -n "$PLAN_ID" ]; then
  echo ""
  echo "=== Test 2: Read back ==="
  READBACK=$(curl -s "$CENTER/admin/maintenance/plans/$PLAN_ID" -H "$AUTH")
  echo "Response: $READBACK"
  RETURNED_GOAL=$(echo "$READBACK" | python3 -c "import sys,json; print(json.load(sys.stdin)['goal'])")
  echo ""
  echo "Original:  $GOAL"
  echo "Returned:  $RETURNED_GOAL"
  if [ "$GOAL" = "$RETURNED_GOAL" ]; then
    echo "RESULT: UTF-8 OK — Chinese preserved"
  else
    echo "RESULT: CORRUPTED — Chinese lost"
    echo "Original bytes: $(echo -n "$GOAL" | xxd | head -3)"
    echo "Returned bytes: $(echo -n "$RETURNED_GOAL" | xxd | head -3)"
  fi
fi

echo ""
echo "=== Test 3: PostgreSQL raw query ==="
python3 -c "
import asyncio
from yequ.db import async_session_factory
from sqlalchemy import text

async def test():
    async with async_session_factory() as db:
        r = await db.execute(text(\"SHOW client_encoding\"))
        print(f'client_encoding: {r.scalar()}')
        r = await db.execute(text(\"SHOW server_encoding\"))
        print(f'server_encoding: {r.scalar()}')
asyncio.run(test())
"

echo ""
echo "=== Test 4: curl verbose with raw bytes ==="
echo "Check if curl is sending UTF-8 correctly:"
curl -s -o /dev/null -w "Content-Type sent: %{content_type}\n" \
  -X POST "$CENTER/admin/maintenance/plans" \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "$AUTH" \
  -d '{"goal":"Windows健康巡检","target_node_id":"test","steps":[{"function_name":"system.metrics.snapshot","input":{}}]}'

echo ""
echo "If all tests pass but external proxy shows corruption,"
echo "the issue is in the reverse proxy layer (Cloudflare/Nginx/etc)."
echo "Check: charset, content-type passthrough, request body handling."
