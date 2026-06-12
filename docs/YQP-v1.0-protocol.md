# YQP v2.0 — YeQu Protocol

> 版本：2.0  
> YeQu Gateway 设备接入协议规范  
> 实现本协议即可将任意终端接入 YeQu Gateway 个人数据中心

---

## 1. 概述

YQP（YeQu Protocol）是设备与 YeQu Gateway 之间的通信协议。设备通过 YQP 宣告身份、声明能力、上报数据，Gateway 通过 YQP 下发指令、推送通知。

### 设计原则

- **语义与传输分离**：消息格式（语义层）与网络传输（传输层）解耦，传输层可替换
- **最小依赖**：任何能发 HTTP POST + 解析 JSON 的运行环境都可实现
- **设备自描述**：设备在注册时声明自己能提供什么数据（Capability），Gateway 据此存储和路由

### 传输层

当前唯一传输层：**HTTP/JSON**。设备通过 HTTP POST 将 YQP 消息发送到 Gateway 端点。

```
Gateway URL:  https://gtw.yequdesu.top   (公网)
              或 http://<局域网IP>:9800     (局域网)
              或 http://<WG_IP>:9800       (WireGuard)
```

---

## 2. 消息格式

所有 YQP 消息均为 JSON 对象，包含公共字段：

```json
{
  "protocol": "yqp/1.0",
  "message_type": "hello | ingest",
  // ... 消息特定字段
}
```

### 2.1 Hello — 注册/心跳

Hello 是设备接入的入口，有两种子类型。

#### 2.1.1 Registration Hello（首次注册，无 Token）

设备首次联系 Gateway 时发送。不需要 Token，Gateway 会返回 `pending` 状态，等待管理员审批。

**请求：** `POST /hello`

```json
{
  "protocol": "yqp/1.0",
  "message_type": "hello",
  "hello_type": "registration",
  "device_id": "my-phone",
  "device_info": {
    "os": "Android 15",
    "hostname": "pixel-8a",
    "model": "Google Pixel 8a",
    "version": "1.0.0"
  },
  "capabilities": [
    {
      "name": "location",
      "display": "设备位置",
      "data_type": "snapshot",
      "interval": 300,
      "retention_days": 30,
      "schema": {
        "type": "object",
        "properties": {
          "lat": {"type": "number"},
          "lng": {"type": "number"},
          "accuracy_m": {"type": "number"},
          "source": {"enum": ["gps", "wifi", "cell"]}
        },
        "required": ["lat", "lng"]
      }
    }
  ]
}
```

**字段说明：**

| 字段 | 必需 | 说明 |
|------|------|------|
| `device_id` | 是 | 全局唯一设备标识，建议用 `hostname-model` 格式 |
| `device_info` | 否 | 设备描述信息（OS、型号、客户端版本等），自由格式 |
| `capabilities` | 否 | 设备能提供的数据类型列表，每个 capability 见 §3 |

**响应（pending — 等待审批）：**

```json
{
  "status": "pending",
  "retry_after": 30,
  "protocol": "yqp/1.0"
}
```

设备应在 `retry_after` 秒后重新发送 Registration Hello 轮询审批结果。

**轮询策略：**
- 前 10 次：每 30 秒
- 之后：指数退避（30→60→120→240...），最长 10 分钟
- 总超时：1 小时。超时后设备提示用户「联系管理员审批」
- 超过 20 次轮询后，响应中会附带 `"note": "请联系管理员审批"`

**响应（approved — 已批准）：**

```json
{
  "status": "approved",
  "token": "a1b2c3d4e5f6...（64 字符 hex）",
  "protocol": "yqp/1.0",
  "config": {
    "collector": {"interval_seconds": 60}
  }
}
```

设备收到 `approved` 后：
1. 安全存储 `token`（所有后续请求必须携带）
2. 根据 `config` 调整采集行为
3. 开始发送 Heartbeat Hello 和 Ingest

#### 2.1.2 Heartbeat Hello（心跳保活）

已注册设备定期发送，表示在线并续约连接。

**请求：** `POST /hello`

```json
{
  "protocol": "yqp/1.0",
  "message_type": "hello",
  "hello_type": "heartbeat",
  "device_id": "my-phone",
  "token": "a1b2c3d4e5f6..."
}
```

**响应：**

```json
{
  "protocol": "yqp/1.0",
  "message_type": "ack",
  "message_id": "heartbeat",
  "status": "ok",
  "pending_commands": [
    {
      "command_id": "uuid",
      "action": "set_interval",
      "params": {"capability": "location", "interval": 30}
    }
  ]
}
```

- `pending_commands`：Gateway 下发给该设备的待执行指令。设备应据此调整行为
- 如果 `status` 为 `"error"` 且 HTTP 状态码为 401，说明 Token 无效（被撤销或设备不存在）
- 心跳间隔建议：60 秒。若超过 `3 × 最长 capability interval` 无心跳，Gateway 会判定设备离线

### 2.2 Ingest — 数据上报

**请求：** `POST /ingest`

```json
{
  "protocol": "yqp/1.0",
  "message_type": "ingest",
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "device_id": "my-phone",
  "token": "a1b2c3d4e5f6...",
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

**字段说明：**

| 字段 | 必需 | 说明 |
|------|------|------|
| `message_id` | 否 | 客户端生成 UUID，Gateway Ack 会回显用于去重 |
| `timestamp` | 是 | ISO 8601 UTC 时间戳 |
| `capability` | 是 | 匹配注册时声明的 capability name |
| `schema_version` | 是 | capability 的 schema 版本号 |
| `payload` | 是 | 符合 capability schema 的数据对象 |

**响应：**

```json
{
  "protocol": "yqp/1.0",
  "message_type": "ack",
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "ok",
  "pending_commands": []
}
```

### 2.3 Ack — 确认回执

所有 Hello 和 Ingest 的响应均为 Ack 格式。

| 字段 | 说明 |
|------|------|
| `message_id` | 回显请求的 message_id |
| `status` | `"ok"` 或 `"error"` |
| `error` | status 为 error 时的错误描述（可选） |
| `pending_commands` | Gateway 下发的待执行指令列表（可为空） |

### 2.4 Action 声明

设备在注册时通过 `actions` 字段声明自己能执行的**操作**（区别于 `capabilities` 声明的**数据**）。

```json
{
  "hello_type": "registration",
  "device_id": "my-device",
  "capabilities": [...],
  "actions": [
    {
      "name": "take_screenshot",
      "display": "屏幕截图",
      "description": "截取当前桌面并返回 PNG 图片",
      "params": {}
    },
    {
      "name": "run_diagnostics",
      "display": "运行诊断",
      "description": "运行系统诊断并返回报告",
      "params": {"target": {"type": "string"}}
    }
  ]
}
```

| 字段 | 必需 | 说明 |
|------|------|------|
| `name` | 是 | 唯一标识，`snake_case` |
| `display` | 否 | 人类可读名称 |
| `description` | 否 | 功能描述，Agent 据此判断何时调用 |
| `params` | 否 | JSON Schema，定义参数格式 |

Gateway 批准设备后，actions 自动可用。Agent 可调用 `list_device_actions` 查看，`send_command` 下发。

### 2.5 Command — 网关下发指令

Gateway 通过心跳/ingest 的 Ack 中 `pending_commands` 数组下发指令。

```json
// ← 心跳/ingest 响应
{
  "status": "ok",
  "pending_commands": [
    {
      "command_id": "550e8400-e29b-41d4-a716-446655440000",
      "action": "take_screenshot",
      "params": {}
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `command_id` | Gateway 生成的唯一 ID，结果上报时回显 |
| `action` | 操作名，匹配设备声明的 actions |
| `params` | 操作参数 |

设备收到后执行，已执行的指令不会重复下发（`delivered=1`）。

内置指令（无需声明即可使用）：

| action | 说明 |
|--------|------|
| `set_interval` | 修改采集间隔 |
| `restart_collector` | 重启采集 |
| `ping` | 立即发送一次心跳 |

### 2.6 Command Result — 设备上报执行结果

设备执行完指令后，在**下一次**心跳或 ingest 中携带 `command_results`。**两种请求均可，Gateway 两路都处理。**

```json
// POST /hello (heartbeat) 或 POST /ingest
{
  "hello_type": "heartbeat",
  "device_id": "my-device",
  "token": "...",
  "command_results": [
    {
      "command_id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "ok",
      "output": "Screenshot captured successfully",
      "image_base64": "iVBORw0KGgo...",
      "image_mime": "image/png"
    }
  ]
}
```

| 字段 | 必需 | 说明 |
|------|------|------|
| `command_id` | 是 | 对应指令 ID |
| `status` | 是 | `"ok"` 或 `"error"` |
| `output` | 否 | 文本输出，Agent 直接读取 |
| `image_base64` | 否 | 图片类结果的 base64 编码 |
| `image_mime` | 否 | 图片 MIME 类型，默认 `image/png` |

**规范：**

- 结果随下一次**任意**已认证请求（heartbeat 或 ingest）上报，不做限制
- Gateway 解码 `image_base64` → 存为文件 → 替换为 `/api/media/{id}` URL
- Agent 通过 `check_command_result(command_id)` 查询执行结果

**完整生命周期：**

```
Gateway send_command → 指令入队 (pending_commands)
设备下次心跳/ingest → Ack 中拿到 pending_commands
设备执行指令
设备再下次心跳/ingest → 携带 command_results
Gateway 存储结果 → Agent 可查询
```

---

## 3. Capability — 数据能力声明

Capability 是设备对自己能提供的数据类型的自描述。Gateway 不预设任何 capability 类型——设备声明什么，Gateway 存储什么。

### 3.1 定义格式

```json
{
  "name": "windows_services",
  "display": "Windows 服务列表",
  "data_type": "snapshot",
  "interval": 300,
  "retention_days": 30,
  "schema": {
    "type": "object",
    "properties": {
      "services": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": {"type": "string"},
            "status": {"enum": ["running", "stopped"]},
            "start_type": {"enum": ["auto", "manual", "disabled"]}
          }
        }
      }
    }
  }
}
```

### 3.2 字段说明

| 字段 | 必需 | 说明 |
|------|------|------|
| `name` | 是 | 唯一标识，使用 `snake_case`，如 `system_metrics`，`installed_software` |
| `display` | 否 | 人类可读的名称，显示在 Dashboard 中 |
| `data_type` | 是 | `snapshot`（最新状态，后值覆盖前值）、`metric`（时序数据，每次追加）、与 capability 同名的 event 类型 |
| `interval` | 否 | 建议采集间隔（秒），Gateway 用于超时检测，默认 60 |
| `retention_days` | 否 | 数据保留天数，默认 30；事件类型永久保留 |
| `schema` | 否 | JSON Schema，描述 payload 结构 |

### 3.3 数据上报示例

**snapshot 类型**（每 300 秒上报一次，新值覆盖旧值）：

```
POST /ingest  {"capability": "windows_services", "payload": {"services": [...]}}
```

**metric 类型**（每 60 秒上报一次，Gateway 按时间序列存储，payload 中每个数值键作为独立指标）：

```
POST /ingest  {"capability": "windows_perf", "payload": {"cpu_temp": 62.5, "gpu_temp": 71.0}}
```

### 3.4 动态增加 Capability

设备注册后可以随时声明新的 capability。已批准的设备发送请求：

```
POST /api/devices/{device_id}/capabilities
Content-Type: application/json

{"name": "new_capability", "display": "新能力", "data_type": "snapshot", ...}
```

新 capability 立刻生效，设备可以开始上报对应数据。

---

## 4. 完整接入流程

```
  设备                              Gateway
    │                                  │
    │  ① POST /hello (registration)    │
    │  ──────────────────────────────→  │
    │                                  │
    │  ② ←── { status: "pending" } ──  │
    │                                  │
    │        [管理员在 Dashboard 批准]    │
    │                                  │
    │  ③ POST /hello (registration)    │
    │  ───── 轮询 ──────────────────→   │
    │  ←── { status: "approved",      │
    │         token: "..." } ────────  │
    │                                  │
    │  ④ POST /hello (heartbeat)       │
    │  ───── token ────────────────→   │
    │  ←── Ack + pending_commands ──  │
    │                                  │
    │  ⑤ POST /ingest (定时上报数据)     │
    │  ───── token + payload ──────→   │
    │  ←── Ack + pending_commands ──  │
    │                                  │
    │  ⑥ 循环 ④ + ⑤                    │
```

---

## 5. 错误处理

| HTTP 状态码 | 含义 | 设备行为 |
|------------|------|---------|
| 200 | 正常 | 正常处理 |
| 400 | 消息格式错误 | 检查 JSON 结构和必需字段 |
| 401 | Token 无效或设备已被撤销 | 清除本地 Token，回到步骤 ① 重新注册 |
| 500 | Gateway 内部错误 | 指数退避重试 |

---

## 6. Goodbye — 主动下线

终端退出前通知 Gateway 立即标记离线，不等心跳超时。

```
POST /hello

{
  "protocol": "yqp/1.0",
  "message_type": "hello",
  "hello_type": "goodbye",
  "device_id": "my-device",
  "token": "<token>"
}
```

Gateway 收到后立刻清除 `last_hello_at`，设备状态变为 `offline`，创建 `device_offline` 事件。

---

## 7. 指令轮询 — commands/pending

**独立于心跳周期的轻量轮询**，2-3 秒间隔。用于快速接收 Gateway 下发的指令。

```
GET /commands/pending?device_id=my-device&token=<token>
```

响应：

```json
{
  "status": "ok",
  "pending_commands": [
    {
      "command_id": "uuid",
      "action": "take_screenshot",
      "params": {}
    }
  ]
}
```

收到指令后立即执行，执行结果随下一次**任意已认证请求**（心跳或 ingest）通过 `command_results` 字段上报。

---

## 8. 参考实现

### Python 最小客户端

```python
import requests
import time
import uuid

GATEWAY = "http://127.0.0.1:9800"
DEVICE_ID = "my-device"
TOKEN = None

def send_hello():
    global TOKEN
    if TOKEN is None:
        # Registration
        resp = requests.post(f"{GATEWAY}/hello", json={
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "registration",
            "device_id": DEVICE_ID,
            "device_info": {"os": "Linux", "version": "1.0"},
            "capabilities": [{
                "name": "cpu_temp",
                "display": "CPU 温度",
                "data_type": "snapshot",
                "interval": 60,
                "schema": {"type": "object", "properties": {"value": {"type": "number"}}}
            }]
        }).json()
        if resp.get("status") == "approved":
            TOKEN = resp["token"]
            print(f"Approved! Token: {TOKEN[:16]}...")
        else:
            retry = resp.get("retry_after", 30)
            print(f"Pending, retry in {retry}s")
            return retry
    else:
        # Heartbeat
        resp = requests.post(f"{GATEWAY}/hello", json={
            "protocol": "yqp/1.0",
            "message_type": "hello",
            "hello_type": "heartbeat",
            "device_id": DEVICE_ID,
            "token": TOKEN,
        }).json()
        for cmd in resp.get("pending_commands", []):
            handle_command(cmd)
    return 60

def send_ingest(cap, payload):
    if TOKEN is None:
        return
    requests.post(f"{GATEWAY}/ingest", json={
        "protocol": "yqp/1.0",
        "message_type": "ingest",
        "message_id": str(uuid.uuid4()),
        "device_id": DEVICE_ID,
        "token": TOKEN,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "capability": cap,
        "schema_version": "v1",
        "payload": payload,
    })

def handle_command(cmd):
    action = cmd["action"]
    if action == "set_interval":
        print(f"Set interval: {cmd['params']}")
    elif action == "ping":
        print("Ping received")

# Main loop
hello_interval = 30
last_hello = 0
while True:
    now = time.time()
    if now - last_hello >= hello_interval:
        hello_interval = send_hello()
        last_hello = now
    # Collect and send data...
    time.sleep(5)
```

### Shell 最小客户端（curl）

```bash
#!/bin/bash
GATEWAY="http://127.0.0.1:9800"
DEVICE_ID="shell-device"

# Registration
RESP=$(curl -s -X POST "$GATEWAY/hello" \
  -H "Content-Type: application/json" \
  -d "{
    \"protocol\": \"yqp/1.0\",
    \"message_type\": \"hello\",
    \"hello_type\": \"registration\",
    \"device_id\": \"$DEVICE_ID\",
    \"device_info\": {\"os\": \"$(uname -s)\", \"hostname\": \"$(hostname)\"}
  }")

echo "Registration: $RESP"
```

---

## 9. API 端点总览

| 端点 | 方法 | 用途 | 需要 Token |
|------|------|------|-----------|
| `/hello` | POST | 注册 / 心跳 / goodbye | 心跳需要 |
| `/ingest` | POST | 上报数据 + command_results | 是 |
| `/commands/pending` | GET | 轻量指令轮询（2-3s 间隔） | 是 |
| `/api/devices/{id}/capabilities` | POST | 动态声明新 capability | 否 |
| `/api/devices/{id}/actions` | GET | 查询已声明的操作列表 | 否 |
| `/api/devices/{id}/rotate-token` | POST | Token 轮换 | 否 |

Gateway 的完整 REST API（Dashboard、Agent 问答、审批等）不在 YQP 协议范围内，详见 Dashboard 交互。
