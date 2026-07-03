# YQP 生产可靠性报告

日期：2026-06-25

本文记录第 9 阶段的 YQP 可靠性加固工作。

## 1. 已完成

1. 将 YQP replay protection 改为数据库持久化去重。
   - 新模型：`YqpMessage`。
   - 新表：`yqp_messages`。
   - 新迁移：`e9f0a1b2c3d4_add_yqp_message_dedup_store.py`。
   - 重复 `message_id` 现在由数据库唯一约束检测，因此 Center 进程重启后仍然有效，也支持多个 worker 共享同一数据库。
2. 保留旧的内存版 `MessageDedup` 类，仅作为旧 import 和轻量单测的兼容 helper。
3. 明确空 Job polling 语义。
   - `job.poll` 有可用 Job 时返回 `message_type=job.available`。
   - `job.poll` 没有空位或没有可用 Job 时返回 `message_type=job.empty`。
   - payload 保持兼容：`{"jobs": []}`。
4. YQP response envelope 增加 `node_id`，便于协议上下文和排障。
5. 收紧 reconcile 终态仲裁。
   - 一旦 Center 已有 Job 终态，Center 保持权威。
   - daemon 迟到终态结果返回 `discard_result`。
   - daemon 迟到 running 状态返回 `cancel`。
   - Center 侧终态 Job output/status 不会被覆盖。
6. 后续补充：过期 `yqp_messages` 清理已从请求热路径移出，改为后台 `YqpMessageCleanupScanner` 周期执行。
   - 清理入口：`cleanup_expired_messages_once()`。
   - 默认每 60 秒扫描一次。
   - 默认每批最多删除 1000 条。
   - Center 非 test mode 启动时在 lifespan 中启动该 scanner。

## 2. 新增测试

1. 成功 YQP 请求后会写入持久化 message dedup 记录。
2. 空 `job.poll` 返回 `job.empty`，保留 `jobs: []`，并包含 `node_id`。
3. Center 已终态而 daemon 迟到上报终态时，reconcile 会丢弃 daemon 结果并保留 Center 状态。
4. 过期 YQP message dedup 记录可通过后台清理函数批量删除，且不会删除未过期记录。

## 3. 验证

执行过的命令：

```bash
pytest -q tests/test_yqp_protocol.py tests/test_job_poll_capacity.py
pytest -q tests/test_integration.py tests/test_agent_tool_execution.py tests/test_l2a.py tests/test_l2b.py tests/test_l2c.py
ruff check .
```

观察结果：

- YQP 聚焦测试：25 passed。
- Integration / Agent / L2 聚焦测试：46 passed。
- `ruff check .`：passed。

## 4. 剩余 YQP 工作

1. 可考虑存储 response fingerprint，以支持严格幂等的 replay response。当前行为是对重复消息返回 HTTP 409。
2. WebSocket 或 push delivery 仍是未来工作；当前生产合同仍然是 poll-based。
3. Center Execution Runtime v2 / Operation Bus 不改变当前 YQP poll-based Node 合同。未来如需 OperationEvent 推送，也应作为 Center 内部事件/客户端投影，不应要求 Node 直接理解 Operation。
