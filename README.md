# YeQu-Gateway

个人数据中心汇总中心 — AI Agent 驱动的终端管理与运维平台。

## 架构

```
终端 (Win/Android/Linux) ── YQP 协议 ──→ Gateway Core ──→ Web Dashboard
                                            │                    │
                                       Redis Stream           SSE 实时推送
                                            │
                                      Executor Service (本机命令执行)
```

## 安装

```bash
pip install -e ".[dev]"
```

依赖：Python 3.11+, Redis（可选，自动降级内存模式）

## 快速开始

```bash
yequ serve                          # 启动 Gateway
python3 services/executor/executor.py  # 启动本机命令执行器
```

浏览器打开 `http://127.0.0.1:9800` 进入 Dashboard。

## 功能

- **设备管理** — 终端注册、审批、在线状态追踪、source_type 分类（Gateway/Service/Device）
- **AI Agent** — 自然语言对话，13 个工具（查询/审批/下发指令/巡检控制），多提供商支持
- **指令执行** — Agent 下发指令 → 终端轮询 → 执行 → 结果自动回调
- **巡检引擎** — 心跳超时、磁盘告警、capability 静默检测，规则热加载
- **事件系统** — 25 种事件类型，Redis Stream 持久化，SSE 实时推送
- **数据管理** — SQLite 存储，snapshot/metric/event 三类数据，自动归档清理
- **Web Dashboard** — 标签式页面，设备面板、AI 对话、事件时间线

## 配置

编辑 `config/gateway.yaml`：

```yaml
agent:
  provider: deepseek
  model: deepseek-v4-flash
  api_key: "sk-..."

redis:
  enabled: true         # Redis Stream MQ，不可用时自动降级
```

## 协议

终端通过 YQP v2.0 协议接入。规范见：
- `docs/YQP-v1.0-protocol.md` — 协议规范
- `docs/YQP-terminal-dev-guide.md` — 终端开发指南

## 目录

```
YeQu-gateway/
├── src/yequ/           # Gateway Core
│   ├── agent/          # AI Agent (StreamFn + 工具)
│   ├── transport/      # HTTP Server + SSE
│   ├── storage/        # SQLite + 归档
│   ├── registry/       # 设备注册中心
│   ├── monitor/        # 巡检引擎
│   ├── collector/      # 本机数据采集
│   └── notify/         # 通知适配器
├── services/
│   └── executor/       # 本机命令执行器
├── config/             # 配置文件
├── docs/               # 文档
└── tests/              # 测试 (68 passed)
```
