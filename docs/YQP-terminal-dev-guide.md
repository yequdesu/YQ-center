# YeQu Gateway 终端开发指南

> 版本：2.0  
> 面向终端开发者 — 如何让你的设备接入 YeQu Gateway  
> 协议规范详见 `docs/YQP-v1.0-protocol.md`

---

## 1. 快速开始

终端接入 Gateway 只需三步：**注册 → 心跳 → 上报**。

### 最小可运行客户端（Python）

```python
import requests, time, uuid

GATEWAY = "https://gtw.yequdesu.top"  # 公网地址
DEVICE_ID = "my-device"
TOKEN = None

def api(method, path, data=None):
    url = f"{GATEWAY}{path}"
    if method == "GET":
        return requests.get(url, params=data).json()
    return requests.post(url, json=data).json()

# 1. 注册
while TOKEN is None:
    resp = api("POST", "/hello", {
        "hello_type": "registration",
        "device_id": DEVICE_ID,
        "device_info": {"os": "Windows 11", "hostname": "DESKTOP-XXX"},
        "capabilities": [...],  # 见 §2
        "actions": [...]        # 见 §3
    })
    if resp["status"] == "approved":
        TOKEN = resp["token"]
        print(f"Approved! Token: {TOKEN[:16]}...")
        break
    time.sleep(resp.get("retry_after", 30))

# 2. 主循环
while True:
    # 心跳
    api("POST", "/hello", {
        "hello_type": "heartbeat", "device_id": DEVICE_ID, "token": TOKEN
    })
    # 上报数据
    api("POST", "/ingest", {
        "capability": "system_metrics",
        "payload": collect_metrics(),
        "device_id": DEVICE_ID,
        "token": TOKEN,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": "v1",
    })
    time.sleep(60)
```

---

## 2. Capability — 声明你能提供什么数据

### 2.1 定义格式

```json
{
  "name": "system_metrics",
  "display": "系统指标",
  "data_type": "snapshot",
  "interval": 60,
  "retention_days": 30,
  "schema": {
    "type": "object",
    "properties": {
      "cpu_percent": {"type": "number"},
      "memory_percent": {"type": "number"},
      "disk_percent": {"type": "object", "additionalProperties": {"type": "number"}}
    },
    "required": ["cpu_percent", "memory_percent"]
  }
}
```

| 字段 | 必需 | 说明 |
|------|------|------|
| `name` | 是 | 唯一标识，`snake_case` |
| `display` | 否 | 人类可读名称，Dashboard 显示用 |
| `data_type` | 是 | `snapshot`（后值覆盖前值）或 `metric`（时间序列追加） |
| `interval` | 否 | 建议采集间隔（秒），Gateway 用于超时检测。默认 60 |
| `retention_days` | 否 | 数据保留天数，默认 30 |
| `sensitive` | 否 | 是否敏感数据。截图等设为 `true`，Gateway 会生成一次性访问 token |
| `schema` | 否 | JSON Schema。Gateway 会校验上报数据是否符合，不符合产生 warning 事件但不拒绝数据 |

### 2.2 上报数据

```json
POST /ingest
{
  "capability": "system_metrics",
  "schema_version": "v1",
  "payload": {
    "cpu_percent": 23.5,
    "memory_percent": 62.1,
    "disk_percent": {"c": 45.0, "d": 72.3}
  },
  "device_id": "my-device",
  "token": "<token>",
  "timestamp": "2026-06-12T10:30:00Z",
  "message_type": "ingest",
  "protocol": "yqp/1.0"
}
```

- `snapshot` 类型：每次上报覆盖之前的值
- `metric` 类型：payload 中每个数值字段作为独立时间序列追加

---

## 3. Action — 声明你能执行什么操作

### 3.1 定义格式

```json
{
  "name": "take_screenshot",
  "display": "屏幕截图",
  "description": "截取当前桌面并返回 PNG 图片",
  "params": {}
}
```

### 3.2 执行流程

```
Gateway send_command → 终端轮询 GET /commands/pending 拿到指令
    → 终端执行 → 结果随下次请求上报
```

### 3.3 轮询指令（2-3s 间隔）

```python
resp = api("GET", "/commands/pending", {
    "device_id": DEVICE_ID, "token": TOKEN
})
for cmd in resp.get("pending_commands", []):
    result = execute_command(cmd["action"], cmd.get("params", {}))
    results.append({
        "command_id": cmd["command_id"],
        "status": "ok" if result.success else "error",
        "output": result.text
    })
```

### 3.4 上报执行结果

结果随**任意已认证请求**（心跳或 ingest）上报：

```json
POST /hello (heartbeat) 或 POST /ingest
{
  ... 正常字段 ...,
  "command_results": [
    {
      "command_id": "abc-123",
      "status": "ok",
      "output": "Screenshot captured"
    }
  ]
}
```

### 3.5 图片类结果

```json
{
  "command_id": "abc-123",
  "status": "ok",
  "image_base64": "iVBORw0KGgo...",
  "image_mime": "image/png"
}
```

- 图片建议压缩到 1920px 以内
- Gateway 自动解码存储，返回访问 URL

---

## 4. 退出

退出前发送 goodbye 让 Gateway 立即标记离线：

```python
atexit.register(lambda: api("POST", "/hello", {
    "hello_type": "goodbye",
    "device_id": DEVICE_ID,
    "token": TOKEN
}))
```

---

## 5. 完整循环（推荐架构）

```python
import threading, time

# 循环 A: 心跳 + 指标 (60s)
def heartbeat_loop():
    while running:
        api("POST", "/hello", {"hello_type": "heartbeat", ...})
        api("POST", "/ingest", {"capability": "system_metrics", ...})
        time.sleep(60)

# 循环 B: 指令轮询 (3s) — 独立于心跳
def command_loop():
    while running:
        resp = api("GET", "/commands/pending", {"device_id": DEVICE_ID, "token": TOKEN})
        if resp["pending_commands"]:
            results = []
            for cmd in resp["pending_commands"]:
                r = execute(cmd)
                results.append(r)
            # 立刻上报结果，不等心跳
            api("POST", "/ingest", {
                "capability": "system_metrics",
                "payload": collect_metrics(),
                "command_results": results,
                "device_id": DEVICE_ID, "token": TOKEN,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "schema_version": "v1"
            })
        time.sleep(3)

threading.Thread(target=heartbeat_loop, daemon=True).start()
threading.Thread(target=command_loop, daemon=True).start()
```

---

## 6. 端点速查

| 端点 | 方法 | 频率 | 说明 |
|------|------|------|------|
| `/hello` | POST | 60s | 注册 / 心跳 / goodbye |
| `/ingest` | POST | 按 capability interval | 数据上报 + command_results |
| `/commands/pending` | GET | 2-3s | 轻量指令轮询 |

---

## 7. 注册字段一览

```json
POST /hello
{
  "hello_type": "registration",
  "device_id": "DESKTOP-8SQBU8K-windows",
  "device_info": {
    "os": "Windows 11",
    "hostname": "DESKTOP-8SQBU8K",
    "source_type": "device"
  },
  "capabilities": [
    {"name": "system_metrics", "display": "系统指标", "data_type": "snapshot", "interval": 60, "schema": {...}},
    {"name": "windows_services", "display": "Windows 服务", "data_type": "snapshot", "interval": 300, "schema": {...}}
  ],
  "actions": [
    {"name": "take_screenshot", "display": "屏幕截图", "description": "截取当前桌面", "params": {}}
  ]
}
```

| 字段 | 必需 | 说明 |
|------|------|------|
| `device_id` | 是 | 全局唯一，建议 `hostname-platform` 格式 |
| `device_info.os` | 否 | 系统类型，Gateway 据此推导默认 role |
| `device_info.source_type` | 否 | `device`(默认) / `service` / `gateway` |
| `capabilities` | 否 | 数据能力声明 |
| `actions` | 否 | 操作能力声明 |

---

## 8. 禁止指令

以下指令即使声明了也会被 Gateway 拒绝：

`shutdown` `reboot` `format` `rm` `delete_all` `set_gateway_config` `access_other_device`

---

## 9. 调试建议

1. 先在本机 `http://127.0.0.1:9800` 调试，确认协议正确
2. 再切到公网地址 `https://gtw.yequdesu.top`
3. 注册后检查 Dashboard 的 Pending Approvals 面板确认设备可见
4. 批准后检查 Devices 面板确认状态为 online
5. 使用 `yequ events` 查看事件流确认数据上报正常
