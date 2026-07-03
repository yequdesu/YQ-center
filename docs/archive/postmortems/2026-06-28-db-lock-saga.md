# DB 锁问题系列：从定位到根治

> 日期：2026-06-28
> 影响范围：Agent 非流式调用超时、YQP 请求排队、timeline 写入阻塞
> 根因：两套独立的锁竞争链——Agent 长事务 + YQP dedup 热路径 DELETE

---

## 发现链

### 第一次：Agent 非流式路径（`agent_service.py`）

**症状**：Agent invoke 超时，`pg_stat_activity` 显示 `idle in transaction` 会话持有 `INSERT INTO timeline_events` 不提交，`timeline_sequences` 上的 `FOR UPDATE` 锁被阻塞。

**根因**：`agent_invoke` 接收外部传入的 `db: AsyncSession`，一条事务跨越 LLM 调用（45s）+ 工具执行（30s+）+ Job 轮询（0.5s × N），期间 `allocate_global_seq()` 获取的 `FOR UPDATE` 锁一直不释放。

**修复**：
- `agent_invoke`、`agent_plan`、`create_agent_session`、`_execute_tool_call_v2` 全部移除 `db` 参数
- 每个 DB 操作包裹在 `async with async_session_factory()` 短事务块中
- `_execute_tool_call_v2` 中 `wait_for_result=True` 改为 `False`，先 commit 再轮询
- `_write_timeline(db, ...)` 全部改为 `_write_timeline(None, ...)`（自 commit）
- 路由层 5 个端点移除 `Depends(get_db)`

### 第二次：YQP dedup 热路径 DELETE

**症状**：多个 `DELETE FROM yqp_messages WHERE expires_at <= ...` 同时在 `tuple` 锁上互相等待，连锁阻塞。

**根因**：`message_dedup.py:87` 在每个 YQP 请求（heartbeat/poll/signal report）中执行过期消息清理。3 Node × (heartbeat 10s + poll 3s + signal 5s) ≈ 每秒 1+ 次 DELETE，并发扫描 `yqp_messages` 表导致 tuple 锁竞争。

**修复**：将过期清理从请求热路径移出，改为后台定时任务（`app.py` lifespan 中注册）。

### 第三次：修复未完全部署

**症状**：修复推送到远端后锁仍出现。

**根因**：`start-center.sh` 用 `python src/yequ/main.py &` 启动，旧进程未完全终止时新进程因端口占用无法启动，实际运行的仍是旧代码。此外 `_execute_tool_call_v2` 内 `async with` 块结束后有 2 处 `_write_timeline(db, ...)` 未改为 `_write_timeline(None, ...)`，`db` 变量已不存在（NameError），rebase 时遗漏。

**修复**：补全最后 2 处 `db,` → `None,`，确认所有修复推送。

---

## 关键教训

1. **DB session 不应跨越 await 边界**。`allocate_global_seq()` 获取的 `FOR UPDATE` 锁在 commit 前不释放，一旦跨越 HTTP/LLM 调用就会阻塞所有 timeline 写入者。

2. **DELETE/UPDATE 不应放在请求热路径**。每个 YQP 请求都扫全表删过期消息，并发下必然互锁。清理类操作应放后台定时任务。

3. **代码修复后验证部署状态**。`start-center.sh` 用 `&` 启动，旧进程残留时新进程静默失败。修复后应先 `pkill -f "yequ.main"` 再启动。

4. **`git add -A` 危险**。多次将 `node_modules/`、`.playwright-mcp/` 等误提交。始终用 `git add <specific-files>`。

---

## 排查工具

```bash
# 实时监控数据库活动
watch -n 2 ./scripts/watch-db.sh

# 手动清除阻塞会话
.venv/bin/python -c "
import asyncio
from sqlalchemy import text
from yequ.db import async_session_factory
async def kill():
    async with async_session_factory() as db:
        r = await db.execute(text(\"\"\"SELECT pid FROM pg_stat_activity WHERE state != 'idle' AND pid != pg_backend_pid()\"\"\"))
        for (pid,) in r.fetchall():
            await db.execute(text(f'SELECT pg_terminate_backend({pid})'))
            print(f'killed {pid}')
        await db.commit()
asyncio.run(kill())
"
```
