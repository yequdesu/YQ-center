# YeQu-Gateway 设计规格

> 个人数据中心汇总中心 — 汇集终端信息，AI Agent 驱动分析与运维
> 日期：2026-06-12
> 状态：设计完成，待实现

---

## 1. 项目概述

YeQu-Gateway 是一个个人 AI Agent 项目，作为个人数据中心汇总中心。它汇集各个终端（本机、手机、远程设备等）的信息，承担数据归档、异常发现、自动检查、通知等工作。

**核心原则：**
- AI 作为分析/决策层，本地逻辑作为执行层
- 所有写操作经过确认（白名单除外），审计留痕
- 协议是契约，运行时自由

---

## 2. 协议设计 — YQP v1.0 (YeQu Protocol)

### 2.1 分层模型

```
语义层（Semantic）：消息类型 / Capability Schema / 数据语义
传输层（Transport）：HTTP / Unix Socket / MQTT（可替换）
```

语义层与传输层解耦。任何能发 HTTP 的设备都可实现协议。

### 2.2 六种消息类型

| 消息 | 方向 | 职责 |
|------|------|------|
| **Hello** | 设备→Gateway | 注册/续约身份 + 声明能力。兼任心跳 |
| **Ingest** | 设备→Gateway | 推送数据，携带 capability_id + payload |
| **Query** | Gateway→设备 | 主动向设备拉数据 |
| **Ack** | Gateway→设备 | 对 Ingest/Hello 的接收确认 |
| **Command** | Gateway→设备 | 下发给设备的动作指令 |
| **Alert** | Gateway→设备 | 异常通知推送给设备 |

### 2.3 消息格式（语义层，JSON）

**Registration Hello（首次注册，无 token）：**
```json
{
  "protocol": "yqp/1.0",
  "message_type": "hello",
  "hello_type": "registration",
  "device_id": "pixel-8a",
  "device_info": {
    "os": "Android 15",
    "hostname": "pixel-8a",
    "model": "Google Pixel 8a"
  }
}
```

**Hello（已注册，心跳）：**
```json
{
  "protocol": "yqp/1.0",
  "message_type": "hello",
  "hello_type": "heartbeat",
  "device_id": "pixel-8a",
  "token": "<auth_token>"
}
```

**Ingest（推送数据）：**
```json
{
  "protocol": "yqp/1.0",
  "message_type": "ingest",
  "message_id": "<uuid>",
  "device_id": "pixel-8a",
  "token": "<auth_token>",
  "timestamp": "2026-06-12T10:30:00Z",
  "capability": "location",
  "schema_version": "v1",
  "payload": {
    "lat": 31.2304,
    "lng": 121.4737,
    "accuracy_m": 15,
    "source": "gps"
  }
}
```

**Ack（响应）：**
```json
{
  "protocol": "yqp/1.0",
  "message_type": "ack",
  "message_id": "<回执的message_id>",
  "status": "ok" | "error",
  "error": "optional error detail",
  "pending_commands": []
}
```

**Command（Hello/Ack 响应中携带）：**
```json
{
  "command_id": "<uuid>",
  "action": "set_interval",
  "params": { "capability": "location", "interval": 30 },
  "expires_at": "2026-06-12T11:00:00Z"
}
```

**Alert（Gateway→设备，未来实现）：**
```json
{
  "protocol": "yqp/1.0",
  "message_type": "alert",
  "device_id": "pixel-8a",
  "severity": "warning",
  "title": "Gateway 异常",
  "body": "本机磁盘使用率达到 92%"
}
```

### 2.4 注册流程

```
设备                                      Gateway
 │                                          │
 │ ── Registration Hello (无token) ─→       │
 │                                          │
 │ ←── { status: "pending",                 │
 │        retry_after: 30 }                 │
 │                                          │
 │                          [Agent 通知你审批]│
 │                          [你通过 Telegram │
 │                           或 CLI 批准]    │
 │                                          │
 │ ── Registration Hello (轮询) ─→          │
 │ ←── { status: "approved", token, config }│
 │                                          │
 │ ── Hello (带token, 心跳) ─→              │
 │ ←── Ack                                  │
 │                                          │
 │ ── Ingest (带token, 数据) ─→             │
 │ ←── Ack                                  │
```

**轮询退避策略：**
- 前 10 次：每 30 秒轮询
- 之后：指数退避（30→60→120→240...），最长间隔 10 分钟
- 总超时：1 小时，超时后设备提示"联系管理员审批"

### 2.5 Command 下发机制

初期：Command 通过 Ack 响应携带（设备收到 Ack 时附带待执行指令）。
后期：可扩展 WebSocket 长连接实现实时下发。

---

## 3. 设备注册中心

### 3.1 设备身份

- `device_id`：全局唯一标识（设备自报）
- `token`：Gateway 批准后签发，后续所有请求携带
- `labels`：Gateway 侧管理的标签（非设备自报）
  - `role`：phone / server / desktop / iot
  - `owner`：归属人
  - `location`：carry（随身）/ home / remote
  - 其他用户自定义标签

### 3.2 Capability 声明

设备在 Hello 中声明 capability，Gateway 审批后生效：

```json
{
  "name": "location",
  "display": "设备位置",
  "schema_version": "v1",
  "data_type": "snapshot",
  "interval": 300,
  "schema": {
    "type": "object",
    "properties": {
      "lat": { "type": "number" },
      "lng": { "type": "number" },
      "accuracy_m": { "type": "number" },
      "source": { "enum": ["gps", "wifi", "cell"] }
    },
    "required": ["lat", "lng"]
  },
  "retention_days": 30
}
```

- `data_type`：`snapshot`（当前状态）| `metric`（时序数值）| `event`（不可变事件）
- `interval`：建议采集间隔（秒），设备端实现，Gateway 可用于超时检测
- `retention_days`：数据保留天数

### 3.3 设备信任

- 本机设备（Unix Socket 来源）：自动受信，跳过审批
- 远程设备：需审批
- 设备可通过 CLI 或 Agent 对话随时撤销信任、轮换 token

---

## 4. 数据存储

### 4.1 数据分类

| 类型 | 特征 | 示例 |
|------|------|------|
| **Snapshot** | 当前状态，后值覆盖前值 | 设备位置、磁盘使用率、当前活动 |
| **Metric** | 时序数值，需看趋势 | CPU 温度、网络流量 |
| **Event** | 不可变的事件记录 | 异常告警、设备上下线、配置变更 |

全部使用 SQLite 存储。个人规模不需要时序数据库。

### 4.2 归档策略

| 层级 | 周期 | 精度 |
|------|------|------|
| 原始数据（热） | 7 天 | 完整精度 |
| 小时聚合（温） | 30 天 | 平均值/最大值/最小值 |
| 日聚合（冷） | 365 天 | 摘要 |
| 事件记录 | 永久 | 不过期 |

归档由后台定时任务执行。

### 4.3 数据目录

```
~/.local/share/yequ-gateway/
├── gateway.db           # 主 SQLite（设备注册、元数据）
├── data.db              # 数据 SQLite（Snapshot/Metric/Event）
└── archived/            # 归档文件
```

---

## 5. Agent 架构

### 5.1 角色划分

| 模式 | 名称 | 触发 | 行为 |
|------|------|------|------|
| A | **Advisor**（默认常驻） | 你通过 CLI/Telegram 对话 | 理解问题 → 查询数据 → 给出结论 |
| B | **Inspector**（可开关） | 定时触发 | 按巡检规则扫描 → 发现异常 → 通知你 |
| C | **Operator**（未来） | Inspector 白名单触发 | 发现已知问题 → 自动修复 → 记录审计 |

### 5.2 多入口设计

Agent 核心不感知对话渠道。适配器将不同渠道的消息统一转为内部对话格式。

```
CLI（yequ ask）──→┐
                  ├──→ Agent 核心 ←── Tool Set ←── 数据 API
Telegram Bot ────→┘                                   
(未来: Web UI)
```

### 5.3 Tool Set（Agent 可调用的工具）

- `list_devices` — 列出所有已注册设备及状态
- `get_device` — 查询单个设备详情
- `query_latest_snapshot` — 获取设备最新快照
- `query_metrics` — 查询设备时序指标（支持时间范围）
- `query_events` — 查询事件记录
- `approve_device` — 批准设备注册
- `revoke_device` — 撤销设备
- `set_device_labels` — 管理设备标签
- `send_command` — 向设备下发 Command
- `get_monitor_rules` — 查询巡检规则
- `get_topology` — 读取网络拓扑知识

### 5.4 可信约束

- Operator 白名单由用户显式维护，AI 不可修改
- 所有写操作记录审计日志（谁、何时、做了什么）
- Agent 对话日志保留，推理过程可回溯

---

## 6. 巡检引擎（Inspector — 模式 B）

### 6.1 Inspector 开关模式

- **开**（默认启动）：所有配置的规则生效
- **关**：只保留内置保活规则（以下）—— 防止 Gateway 运行但完全无人知道异常
- 开关方式：CLI `yequ monitor on/off` 或对话 "关闭巡检"

**保活规则（Inspector 关闭时仍生效）：**

| 规则 | 条件 | 动作 |
|------|------|------|
| 设备离线 | `now - last_hello > 3 × hello_interval`（仅已注册设备） | 创建 Event，通知 |
| 本机磁盘告警 | 根分区 `/` 使用率 > 90% | 创建 Event，通知 |

### 6.2 引擎机制

- 每 30 秒扫描一次
- 规则热加载（修改 yaml 不重启）
- 同一条件连续触发不重复通知（有冷却时间）

### 6.3 巡检规则配置

```yaml
# config/monitor_rules.yaml
rules:
  - name: device_offline
    description: 设备心跳超时
    condition:
      type: heartbeat_timeout
      params:
        multiplier: 3  # hello_interval × 3
    severity: warning
    cooldown_seconds: 300
    notify: true

  - name: disk_high
    description: 磁盘使用率过高
    condition:
      type: threshold
      params:
        capability: system_metrics
        field: disk_usage_percent
        operator: ">"
        value: 90
    severity: critical
    cooldown_seconds: 1800
    notify: true
```

---

## 7. 通知系统

### 7.1 适配器模式

```
Event → Notify Router → [Telegram Adapter]
                      → [Desktop Notification]
                      → [Log File]
                      → (未来: 更多渠道)
```

### 7.2 通知路由规则

| Severity | 渠道 |
|----------|------|
| critical | Telegram + Desktop + Log |
| warning | Telegram + Log |
| info | Log only |

### 7.3 通知格式

```
⚠️ Gateway Alert: pixel-8a 离线
上次心跳: 12:03:00 (3分钟前)
严重等级: warning
```

---

## 8. 传输层

### 8.1 传输适配器

| 适配器 | 用途 | 地址 |
|--------|------|------|
| HTTP Server | 远程设备接入（本机/LAN/公网） | `127.0.0.1:9800` |
| Unix Socket | 本机采集器内部通道 | `~/.local/share/yequ-gateway/gateway.sock` |

公网设备通过 Aliyun nginx 反代到 `:9800`。TLS 由 nginx 终结，Gateway 不处理。

### 8.2 网络拓扑支持

设备配置的 Gateway URL 决定传输路径：

| 场景 | 设备配置 URL |
|------|-------------|
| 同机采集器 | `unix://~/.local/share/yequ-gateway/gateway.sock` |
| 同机 HTTP | `http://localhost:9800` |
| 局域网 | `http://192.168.x.x:9800` |
| 虚拟组网 (Tailscale/ZeroTier) | `http://<vpn_ip>:9800` |
| 公网通过 Aliyun | `https://yequ.example.com` |

---

## 9. Agent 交互示例

### 查询
```
你: "我手机在哪？"
Agent: [调用 query_latest_snapshot(device_id="pixel-8a", capability="location")]
Agent: "你的 pixel-8a 在上海市黄浦区附近（精度15m），12:28 更新。"
```

### 设备管理
```
你: "有新设备申请接入吗？"
Agent: "有一个：pixel-8a (Android 15)，30 分钟前请求注册。批准还是拒绝？"
你: "批准，标签 role=phone"
Agent: [调用 approve_device + set_device_labels]
Agent: "已批准。pixel-8a 已可接入。"
```

### 巡检通知
```
[Telegram]
⚠️ Gateway: pixel-8a 已离线 5 分钟，上次心跳: 14:22
```

---

## 10. 部署

- **Python**: 3.12+
- **依赖**: Anthropic SDK, psutil, httpx, pyyaml, SQLite (stdlib)
- **启动**: `yequ-gateway serve` 或 systemd service
- **端口**: `127.0.0.1:9800`
- **数据**: `~/.local/share/yequ-gateway/`

---

## 11. 分阶段路线

### Phase 1：本机底座
- YQP 语义层消息定义
- HTTP Server + Unix Socket 传输层
- 设备注册（手动批准，本机自动受信）
- 本机采集器（system metrics）
- SQLite 存储 + Snapshot/Metric/Event 入库
- Inspector 最小规则集（离线检测 + 磁盘告警）
- CLI：`yequ serve`, `yequ ask`, `yequ devices`, `yequ status`
- **目标**：Gateway 运行、本机数据流入、可对话查询、离线可通知

### Phase 2：Agent 对话增强
- Agent Tool Set 完整实现
- Claude API 集成
- Telegram Bot 入口
- 设备审批通过对话完成
- **目标**：多入口对话，自然语言操作一切

### Phase 3：远程设备接入
- 设备注册审批流完整体验
- 设备 Hello 心跳 + 离线检测验证
- nginx/TLS 反代配置文档
- Android 参考客户端
- **目标**：第一台远程真实设备接入

### Phase 4：高级特性
- 数据聚合归档
- Command 下发（通过 Ack 响应携带）
- Query 设备拉取
- 巡检规则扩展
- Operator 模式（白名单自动修复）
- **目标**：完整个人数据中心
