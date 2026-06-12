# YeQu-Gateway v2 设计规格

> 个人数据中心汇总中心 — 架构审视后的完整设计
> 日期：2026-06-12
> 状态：设计完成，待实现

---

## 0. 核心定位

Gateway 是一个**解耦的处理中心**，不是管控平台。

终端是**自主的提供方**——它们声明能力、上报数据、接受调用。Gateway 不主动修改终端配置，不推送系统设置，不下发破坏性指令。

```
终端 (跨平台) ←── YQP 协议 ──→ Gateway Core ──→ Dashboard / Agent / MQ
                                      ↑
                              Redis Stream (持久消息)
                                      ↓
                           外部消费者 (QQ Bot, 通知)
```

---

## 1. 终端身份与信任

### 1.1 身份模型

| 层次 | 标识 | 说明 |
|------|------|------|
| **物理身份** | `device_id` | 终端自报，不可变。建议 `hostname-model` 格式 |
| **会话身份** | `token` | Gateway 批准后签发，可轮换、可吊销 |
| **信任状态** | `active` / `revoked` / `suspended` | Gateway 侧管理 |

### 1.2 重装/重置场景

终端格式化后重装 → 用相同 `device_id` 重新注册 → Gateway 检测到**同名设备已有 revoked 记录**：

- 选项 A：要求管理员确认，然后签发新 token，恢复 `active` 状态
- 选项 B：自动信任（如果标记过"允许重装"）

默认走选项 A，生成 `device_replaced` 事件。

### 1.3 Token 安全

- Token 通过 HTTPS 传输（TLS 在 nginx 终结）
- Dashboard 不显示完整 token（只显示前 8 位）
- Token 轮换：`POST /api/devices/{id}/rotate-token` 手动触发
- 可疑行为（短时间内大量不匹配 token 的请求）→ 生成 `auth_suspicious` warning

---

## 2. 数据时间轴

### 2.1 时间戳权威

- **以 Gateway 时间戳为准**。Ingest 的 `timestamp` 字段作为参考，Gateway 侧 `ingested_at` 作为权威时间。
- Dashboard 查询按 `ingested_at` 排序。
- 终端时间与 Gateway 偏差超过 60s → 生成 `clock_skew` warning 事件。

### 2.2 离线补传

终端离线后恢复，补传周期内的数据：**允许**。时间戳用终端本地时间，Gateway 侧 `ingested_at` 标记实际到达时间。

- 指标查询接口支持两种排序：`timestamp`（终端时间）和 `ingested_at`（到达时间）
- 默认按 `timestamp` 排序，标注"含补传数据"提示

### 2.3 空白期检测

Monitor 新增规则：**capability 静默超时**。

每个 capability 声明了 `interval`。超过 `5 × interval` 无数据 → 生成 `capability_silent` warning 事件。这不同于设备离线——设备在线但某个 capability 停止上报。

---

## 3. Capability 版本演化

### 3.1 Schema 版本字段

Capability 声明已包含 `schema_version` 字段（当前默认 `"v1"`）。终端升级后，通过 `POST /api/devices/{id}/capabilities` 更新声明——同 name、新 schema_version、新 schema。

### 3.2 版本共存

Gateway 存储端**不迁移旧数据**。不同版本的数据并存。查询时按 `schema_version` 过滤。

```json
// GET /api/metrics/...?schema_version=v2
```

### 3.3 版本不一致检测

Ingest 时比对 payload 的 `schema_version` 和已注册 capability 的 `schema_version`。不一致的：

- 如果终端声明的版本**更新**→ 自动更新 capability registry、生成 `capability_updated` 事件
- 如果 payload 的版本**不存在**→ 生成 `capability_unknown_version` warning

---

## 4. 分布式时钟

### 4.1 时钟偏差检测

设备每次心跳/ingest，Gateway 记录 `skew = |ingested_at - payload.timestamp|`。

- `skew < 5s`：正常
- `5s ≤ skew < 60s`：记录日志
- `skew ≥ 60s`：生成 `clock_skew` warning 事件，推送 Redis Stream

### 4.2 未来数据

终端时钟超前导致 `timestamp > ingested_at`：数据照收，`ingested_at` 标记实际时间。在 API 返回中标注 `"clock_status": "future"`。

---

## 5. 指令幂等与投递

### 5.1 Command ID 去重

- 终端收到 `pending_commands` 后，检查本地已执行记录（内存 set）。相同 `command_id` 不再执行。
- Gateway 侧 `dequeue_commands` 标记 `delivered=1`。未标记 delivered 的命令不会重复下发。

### 5.2 投递保证

| 场景 | 行为 |
|------|------|
| 正常 | 终端轮询 → 拿到 → 执行 → 上报结果 → Gateway 标记完成 |
| 终端轮询前断网 | 指令留在队列，下次轮询时拿到 |
| 终端拿到指令后断网（执行了但无法上报） | 下次轮询时上报 `command_results`（去重，相同 command_id 只存最后一次结果） |
| 超时（15 分钟未完成） | Gateway 生成 `command_timeout` warning 事件 → 推送 Redis Stream → Dashboard 通知用户 |

### 5.3 超时清理

Pending command 的 `created_at` 超过 15 分钟且未完成 → Monitor 生成 `command_timeout` 事件。超时指令**不从队列删除**——如果终端后来补报结果，仍然接收并存储，但附加 `"status": "late"`。

---

## 6. 终端不可达

### 6.1 指令前可用性检查

Agent 下发指令前，`send_command` 工具**自动**调用 `check_device_online` 检查心跳新鲜度：

- 状态 `online`（新鲜）→ 下发指令
- 状态 `stale`（延迟）→ 下发但告知用户"设备可能不稳定"
- 状态 `offline`→ 拒绝下发，告知用户设备已离线
- 状态 `unknown`（从未心跳）→ 拒绝下发

### 6.2 超时通知

用户发出指令后，Dashboard 通过 Redis Stream 监听 `command_completed` 和 `command_timeout` 事件。超时时聊天窗口自动显示"指令可能未送达，设备已离线或未响应"。

---

## 7. Gateway 自恢复

### 7.1 会话持久化

Agent 的对话历史从内存单例迁移到 SQLite 存储：

```sql
CREATE TABLE agent_sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    messages_json TEXT  -- [{role, content}, ...]
);
```

Gateway 启动时加载最近一次会话。重启后 Agent 仍然记得之前的上下文。

### 7.2 Redis 故障降级

Gateway 启动时检查 Redis 连通性：
- **可用** → 所有事件进 Redis Stream，SSE 从 Redis 消费
- **不可用** → 降级到内存 EventBus（当前行为），写 warning 日志

Redis 在运行中恢复连接后，Gateway 自动切回 Redis Stream。

---

## 8. 敏感数据

### 8.1 数据分级

| 等级 | 数据类型 | 处理 |
|------|---------|------|
| **公开** | 系统指标（CPU、内存、磁盘） | 无限制 |
| **受限** | 设备标签、在线状态 | API 返回，不加密 |
| **敏感** | 位置数据、屏幕截图、文件内容 | 标记 `sensitive: true` |

### 8.2 敏感数据保护

- Capability 声明新增可选字段 `"sensitive": true`
- 敏感 capability 的数据：
  - 存储时文件名加密（`secrets.token_hex(16)`），不直接用 `device_id` 命名
  - API 返回的 `image_url` 使用一次性 token（`/api/media/{id}?token=xxx`，5 分钟有效）
- SQLite 文件本身不加密（性能考虑），但建议部署在加密文件系统上

### 8.3 访问控制

当前单用户模式下不做鉴权。未来多用户时，每个用户持有独立的 API token。这里预留 `user_id` 字段。

---

## 9. Capability 对齐

### 9.1 过度声明检测

Monitor 新增规则：**声明但未上报**。

```yaml
- name: capability_unfulfilled
  condition:
    type: capability_silent
    params:
      grace_period: 3  # 3 倍 interval
  severity: info
```

终端声明了 capability，但 `3 × interval` 内未收到任何数据 → `capability_unfulfilled` info 事件。

### 9.2 Schema 校验

Ingest 时比对 `payload` 和 capability 的 `schema` 字段：

- 仅检查 `required` 字段是否存在、值类型是否匹配
- 不检查额外字段（终端可能发送比 schema 更多的数据）
- 校验失败 → 生成 `data_schema_mismatch` warning 事件，数据**仍然存储**

校验是软性的——Gateway 不拒绝数据，只是标记异常。拒绝合法数据比接收非法数据危害更大。

---

## 10. 能力边界

### 10.1 禁止指令

以下指令 Gateway **不应支持**，即使终端声明了：

- 破坏性操作：`shutdown`、`reboot`、`format`、`rm -rf` 等价物
- 修改 Gateway 自身配置：`set_gateway_config`
- 访问其他终端的数据
- 提权操作

如果终端声明的 action 名称包含以上关键字 → 批准时**拒绝**，生成 `action_blocked` warning 事件。

### 10.2 Container 化

未来可以考虑：终端暴露的 actions 在沙箱中执行。Windows Sandbox / Docker / WASM。当前不做——设计预留 `"sandbox": "optional" | "required"` 字段。

---

## 11. Redis Stream MQ

### 11.1 流设计

| Stream | Key | 内容 | 消费者 |
|--------|-----|------|--------|
| `yequ:events` | `*` (自动生成) | 所有事件 | Gateway SSE、外部通知 |
| `yequ:commands` | `*` | 指令生命周期 | Agent 上下文注入 |

### 11.2 事件 Schema

```json
{
  "event_id": "evt_xxx",
  "event_type": "command_completed",
  "source": "gateway",
  "severity": "info",
  "device_id": "DESKTOP-8SQBU8K-windows",
  "title": "截图已完成",
  "body": "",
  "data": {
    "command_id": "...",
    "image_url": "/api/media/..."
  },
  "timestamp": "2026-06-12T07:30:45Z"
}
```

### 11.3 Consumer Groups

| Group | Stream | 用途 |
|-------|--------|------|
| `gateway-sse` | `yequ:events` | Dashboard SSE 实时推送 |
| `gateway-agent` | `yequ:commands` | Agent 指令完成通知 |
| `external-*` | `yequ:events` | 外部消费者（QQ Bot 等），按需创建 |

Consumer Group 保证每个消费者组独立消费、不重复。同一组内多个消费者负载均衡。

### 11.4 消息保留

- `yequ:events`：MAXLEN ~10000（保留最近 1 万条）
- `yequ:commands`：MAXLEN ~5000

不无限增长。历史数据从 SQLite `events` 表查询。

---

## 12. 全部事件类型

| event_type | 触发 | severity |
|-----------|------|----------|
| `device_registered` | 设备首次注册 hello | info |
| `device_approved` | 管理员批准设备 | info |
| `device_revoked` | 管理员撤销设备 | warning |
| `device_online` | 心跳恢复（之前离线） | info |
| `device_offline` | Goodbye / 心跳超时 | info / warning |
| `device_replaced` | 同名设备重装后重新注册 | warning |
| `capability_added` | 终端声明新 capability | info |
| `capability_updated` | schema_version 变更 | info |
| `capability_unfulfilled` | 声明但长期未上报 | info |
| `capability_silent` | 单个 capability 静默超时 | warning |
| `data_schema_mismatch` | payload 不符合 schema | warning |
| `capability_unknown_version` | schema_version 未声明 | warning |
| `clock_skew` | 终端时钟偏差 > 60s | warning |
| `command_queued` | Gateway 下发指令 | info |
| `command_completed` | 终端上报执行结果 | info |
| `command_timeout` | 指令 15 分钟未完成 | warning |
| `command_late` | 超时后补报的结果 | warning |
| `action_blocked` | 禁止指令被声明 | warning |
| `auth_suspicious` | 可疑认证行为 | warning |
| `monitor_enabled` | 巡检开启 | info |
| `monitor_disabled` | 巡检关闭 | info |
| `disk_high` | 磁盘使用率 > 90% | critical |

---

## 13. 协议不变的部分

- `/hello` (registration/heartbeat/goodbye) — 不变
- `/ingest` — 不变
- Capability 声明 — 新增可选 `sensitive`、`actions` 字段
- Token 机制 — 不变

## 14. 协议新增的部分

| 端点 | 方法 | 说明 |
|------|------|------|
| `/commands/pending` | GET | 轻量指令轮询（已有） |
| `/api/devices/{id}/rotate-token` | POST | Token 轮换 |
| `/api/agent/sessions` | GET | 查询当前会话状态 |

## 15. 不变的部分（不需要改动）

- SQLite 存储方案 — 不变
- YQP 六种消息类型 — 不变
- Agent Tool Set — 不变（工具行为改进，不新增工具）
- Dashboard 布局 — 不变
- Provider 预设 — 不变
