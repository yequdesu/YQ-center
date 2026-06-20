#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

echo "=== 1. Apply migrations ==="
alembic upgrade head

echo ""
echo "=== 2. Create agent token ==="
python -c "
import asyncio
from yequ.db import async_session_factory
from yequ.models.api_token import ApiToken
from yequ.services.token_auth import hash_token
from sqlalchemy import select

async def fix():
    async with async_session_factory() as db:
        h = hash_token('qq756522327')
        result = await db.execute(
            select(ApiToken).where(ApiToken.token_hash == h, ApiToken.scope == 'agent')
        )
        if result.scalar_one_or_none() is None:
            db.add(ApiToken(token_hash=h, scope='agent', label='default agent'))
            await db.commit()
            print('[OK] Agent token created')
        else:
            print('[OK] Agent token already exists')

        print('')
        print('Current tokens for qq756522327:')
        result = await db.execute(select(ApiToken).where(ApiToken.token_hash == h))
        for t in result.scalars().all():
            print(f'  scope={t.scope}  label={t.label}')
asyncio.run(fix())
"

echo ""
echo "=== 3. Restart Center ==="
fuser -k 9800/tcp 2>/dev/null || true
sleep 1
echo "Starting Center..."
python src/yequ/main.py &
sleep 3

echo ""
echo "=== 4. Verify ==="
echo -n "Admin endpoint: "
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer qq756522327" \
  http://127.0.0.1:9800/admin/nodes
echo ""

echo -n "Agent endpoint: "
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer qq756522327" \
  -H "Content-Type: application/json" \
  -d '{"actor_id":"test","execution_mode":"auto"}' \
  http://127.0.0.1:9800/agent/sessions
echo ""

echo ""
echo "=== Done ==="
echo "Both should return 200/201. If not, check logs above."
