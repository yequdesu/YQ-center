# YeQu Gateway 大规模阻塞事件事后分析

> 日期：2026-06-27 ~ 2026-06-28  
> 影响范围：WinClient 无法连接、Console Nodes 页面挂起、所有 YQP 请求超时  
> 持续时间：约 1.5 小时  
> 根因：PostgreSQL 孤儿事务持有锁，引发连锁阻塞

---

## 问题 Timeline

| 时间 (UTC) | 事件 |
|-------------|------|
| ~15:26 | Center 进程重启，旧进程残留一个未提交的 `INSERT INTO timeline_events` 事务 |
| 15:26 — 16:55 | 该孤儿事务持续持有锁，新 YQP 请求逐个排队，连接池逐渐耗尽 |
| 16:13 | 尝试重启 Center（无效——孤儿事务属于旧进程，不受新 Center 管控） |
| 16:55 | 添加 YQP 阶段耗时诊断日志，发现请求卡在 `check_and_record_message` |
| 16:58 | PostgreSQL 锁分析，定位到 pid 164487 孤儿事务（idle in transaction 1h26m） |
| 16:59 | `pg_terminate_backend(164487)`，所有排队请求瞬间释放，系统恢复 |

---

## 完整症状链

### 症状 1：Console `/admin/nodes` 页面挂起

```
浏览器: GET /admin/nodes → pending → ERR_ABORTED / 超时
```

**诊断过程：**

1. 后台日志中能看到所有 `/admin/sessions`、`/admin/jobs` 请求正常 200，唯独 `/admin/nodes` 从未出现。
2. 浏览器 Network 面板确认：`admin/nodes` 请求发出去了但状态是「挂起」。
3. 对比 `/admin/sessions`（正常）和 `/admin/nodes`（挂起），差异在于 `/admin/nodes` 调用了 `refresh_signal_freshness()`。
4. `refresh_signal_freshness` 内部调用 `mark_stale_signals()`，这会争用全局 `_sequence_lock`（asyncio 锁）+ `SELECT timeline_sequences ... FOR UPDATE`（PostgreSQL 行锁），与后台 TimelineWriter（每 1 秒）和 Signal Scanner（每 5 秒）产生竞争。

**修复（Commit `f0baa90`）：**
- `list_nodes()` 和 `get_node()` 移除 `refresh_signal_freshness()` 调用
- 转为纯函数 `compute_signal_freshness()` 在内存中计算信号新鲜度
- admin 读路径不再执行任何 DB 写操作

### 症状 2：WinClient 反复 `node.hello` 超时

```
WinClient: ReadTimeout after 30s (attempt 1, 2, 3, ...)
Center log: stage=node_binding_verified ✅ → dedup_recorded ❌ (永远到不了)
```

**诊断过程：**

1. 新的 YQP 阶段诊断日志（`f24e303`）精确定位了挂起点：

```
parsed               ✅ 0.25ms
authenticated        ✅ 2.41ms
node_binding_verified ✅ 0.07ms
dedup_recorded       ❌ 未出现  ← 卡在 check_and_record_message()
```

2. `check_and_record_message()` 内部：
```python
DELETE FROM yqp_messages WHERE expires_at <= now   # 清理过期
INSERT INTO yqp_messages (...)                       # 插入新记录
db.flush()                                           # ← 挂在这里
```

3. 用 PostgreSQL 锁诊断发现 **57 个** `DELETE FROM yqp_messages` 排队等锁（`AccessExclusiveLock` + `granted=f`），**22 个** `UPDATE signal_states` 同样被阻塞。

### 症状 3：DB 连接池耗尽

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached
```

后台扫描器（timeout scanner、approval scanner）也因拿不到连接而崩溃。根本原因是 57 个被阻塞的 YQP 请求各占用一个 DB 连接等待锁释放，连接池被撑爆。

**修复（Commit `7593491`）：**
- `pool_size` 5→20，`max_overflow` 10→20
- 配置化：`YEQU_DATABASE_POOL_SIZE`、`YEQU_DATABASE_MAX_OVERFLOW` 等

---

## 终极根因：PostgreSQL 孤儿事务

```sql
pid=164487  state=idle in transaction  xact_age=01:26:37
query: INSERT INTO timeline_events ...
```

### 全景图

```
pid 164487 (孤儿)
  │
  ├─ 开始: INSERT INTO timeline_events
  ├─ 状态: idle in transaction (1h26m)
  ├─ 持有: AccessExclusiveLock / ShareLock
  │
  ├─→ 阻塞 57 个 DELETE FROM yqp_messages
  │     └─ 每个 winClient node.hello 请求卡在这里 30s → 超时 → 重试
  │         └─ WinClient 反复 bootstrap.failed → retry
  │
  ├─→ 阻塞 22 个 UPDATE signal_states
  │     └─ 后台 Signal Scanner 每次扫描都排队
  │
  ├─→ 阻塞 UPDATE nodes
  │     └─ handle_heartbeat 状态更新被阻塞
  │
  └─→ 57 个被阻塞的连接占满连接池
        └─ 新请求无法获取 DB 连接
            └─ Timeout Scanner / Approval Scanner 崩溃
                └─ YQP 端点返回 500
```

### 为什么会残留孤儿事务？

1. Center 进程被 `SIGKILL`（`kill -9`）或非正常退出时，FastAPI 的 `lifespan` shutdown 没有机会执行
2. `get_db()` 依赖注入创建的 session 没有被正确关闭
3. PostgreSQL 不会自动 kill `idle in transaction` 的连接（默认 `idle_in_transaction_session_timeout = 0`，即禁用）
4. 重启 Center 创建新的连接池，但旧的 PostgreSQL 连接不受影响——它们属于已被 kill 的旧进程，但 PG 服务端不知道客户端已死（TCP keepalive 需要时间才能检测）

---

## 修复清单

| Commit | 修复内容 | 层级 |
|--------|----------|------|
| `bc67b87` | `types.py` → `shared_types.py` 避免遮蔽 stdlib | 启动修复 |
| `7593491` | DB 连接池配置化：pool_size=20, max_overflow=20, pool_timeout=10s | 预防性 |
| `c4957d4` | `refresh_signal_freshness` 新增 `write_timeline=False` | 部分缓解 |
| `f0baa90` | admin 读路径彻底移除 `refresh_signal_freshness` 调用 | 根因修复 1 |
| `209d6ce` | `handle_hello/handle_heartbeat` timeline 写入隔离到独立事务 | 根因修复 2 |
| `f24e303` | YQP 阶段耗时诊断日志 + `handle_hello` 改用 `TimelineWriter.enqueue()` | 诊断 + 彻底剥离 |
| — | `pg_terminate_backend(164487)` 杀掉孤儿事务 | 最终解药 |

---

## 预防措施建议

1. **开启 PostgreSQL `idle_in_transaction_session_timeout`**
   ```sql
   ALTER SYSTEM SET idle_in_transaction_session_timeout = '5min';
   SELECT pg_reload_conf();
   ```
   这样未来的孤儿事务最多存活 5 分钟就会被 PostgreSQL 自动清理。

2. **在 Center 启动时主动清理旧连接**
   ```sql
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'idle in transaction'
     AND xact_start < now() - interval '5 minutes';
   ```

3. **Timeline 事件写入全部走 `TimelineWriter.enqueue()`**
   不再在任何 YQP handler 中同步写 timeline，彻底消除 `_sequence_lock` 争用。

4. **`check_and_record_message` 使用独立短事务**
   减少消息去重操作持有锁的时间窗口。

---

## 关键经验

- **诊断优先于修复**：在没有 YQP 阶段诊断日志之前，只能看到 "request timed out"；加了日志后 5 分钟就定位到了 `check_and_record_message`。
- **锁分析是数据库问题的核磁共振**：`pg_locks` + `pg_stat_activity` 联合查询是定位阻塞链的终极工具。
- **重启不解决所有问题**：孤儿事务存活在 PostgreSQL 服务端，重启应用进程不会影响它。
- **读写分离**：admin 读路径不应该有写操作——`refresh_signal_freshness` 放在 `list_nodes` 里是反模式。
- **`FOR UPDATE` 锁的持有时间必须最小化**：从整个请求生命周期缩减到一次 INSERT+COMMIT（毫秒级）。
