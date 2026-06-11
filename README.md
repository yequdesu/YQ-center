# YeQu-Gateway

个人数据中心汇总中心 — 汇集终端信息，AI Agent 驱动分析与运维。

## 安装

```bash
git clone <repo-url>
cd YeQu-gateway
pip install -e ".[dev]"
```

## 快速开始

```bash
# 启动 Gateway（HTTP + 本机采集 + 巡检）
yequ serve

# 查看设备列表
yequ devices

# 查看系统状态
yequ status

# 查看告警事件
yequ events

# 问一个问题
yequ ask "状态怎么样？"
```

## 配置

编辑 `config/gateway.yaml` 修改端口、采集间隔等配置。

## 协议

YeQu-Gateway 使用 YQP v1.0 (YeQu Protocol) 协议进行设备间通信。

协议规范见 `docs/superpowers/specs/2026-06-12-yequ-gateway-design.md`。
