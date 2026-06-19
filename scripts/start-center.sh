#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── 颜色 ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() {
  echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${CYAN}║          YeQu Center — 启动 & 观察脚本               ║${NC}"
  echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
}

# ── 检查 venv ──
if [ ! -d ".venv" ]; then
  echo -e "${RED}[错误] .venv 不存在，请先执行: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'${NC}"
  exit 1
fi

source .venv/bin/activate

# ── 参数 ──
PORT="${1:-9800}"
HOST="0.0.0.0"
BASE_URL="http://127.0.0.1:${PORT}"

case "${2:-start}" in
  start)
    banner
    echo ""
    echo -e "${YELLOW}[1/4] 清理旧数据库并执行 migration ...${NC}"
    rm -f yequ.db
    alembic upgrade head 2>&1 | tail -1

    echo ""
    echo -e "${YELLOW}[2/4] 预配 winClient 节点 ...${NC}"
    # 先在后台启动 Center
    python src/yequ/main.py &
    PID=$!
    sleep 3

    # 预配节点
    TOKEN="winc-token-4a7f3c9e1b2d8f6c"
    curl -s -X POST "${BASE_URL}/admin/nodes" \
      -H "Content-Type: application/json" \
      -d '{
        "node_id": "winClient",
        "node_name": "Windows Client",
        "token": "'"${TOKEN}"'",
        "role": "compute",
        "locality": "lan"
      }' | python3 -m json.tool

    echo ""
    echo -e "${GREEN}──────────────────────────────────────────────────────${NC}"
    echo -e "${GREEN}  Center 已启动: ${BASE_URL}${NC}"
    echo -e "${GREEN}  PID: ${PID}${NC}"
    echo -e "${GREEN}  健康检查: curl ${BASE_URL}/healthz${NC}"
    echo -e "${GREEN}──────────────────────────────────────────────────────${NC}"
    echo ""
    echo -e "${CYAN}按 Ctrl+C 停止${NC}"
    echo ""

    # 前台等待
    wait $PID
    ;;

  status)
    banner
    echo ""
    echo -e "${YELLOW}── 健康检查 ──${NC}"
    curl -s "${BASE_URL}/healthz" | python3 -m json.tool 2>/dev/null || echo -e "${RED}Center 未运行${NC}"

    echo ""
    echo -e "${YELLOW}── 已预配 Nodes ──${NC}"
    curl -s "${BASE_URL}/admin/nodes" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo -e "${RED}无法获取节点列表${NC}"
    ;;

  test-hello)
    banner
    echo ""
    echo -e "${YELLOW}── 发送 node.hello ──${NC}"
    TOKEN="winc-token-4a7f3c9e1b2d8f6c"
    curl -s -X POST "${BASE_URL}/yqp/" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer ${TOKEN}" \
      -d '{
        "yqp_version": "0.1",
        "message_id": "msg_hello_'$(date +%s)'",
        "message_type": "node.hello",
        "trace_id": "tr_hello_'$(date +%s)'",
        "node_id": "winClient",
        "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
        "payload": {
          "daemon_version": "0.1.0",
          "node_name": "Windows Client",
          "role": ["compute"],
          "locality": "lan",
          "platform": {"os": "windows", "arch": "amd64"}
        }
      }' | python3 -m json.tool
    ;;

  test-full)
    banner
    TOKEN="winc-token-4a7f3c9e1b2d8f6c"
    NODE="winClient"
    AUTH="Authorization: Bearer ${TOKEN}"
    MSG_PREFIX="msg_$(date +%s)"

    echo ""
    echo -e "${YELLOW}[1] node.hello${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'${MSG_PREFIX}'_hello","message_type":"node.hello","trace_id":"tr_1","node_id":"'${NODE}'","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","payload":{"daemon_version":"0.1.0","platform":{"os":"windows","arch":"amd64"}}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  → {d[\"message_type\"]} heartbeat_interval={d[\"payload\"][\"heartbeat_interval_sec\"]}s')"

    echo -e "${YELLOW}[2] node.register_capabilities${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'${MSG_PREFIX}'_reg","message_type":"node.register_capabilities","trace_id":"tr_2","node_id":"'${NODE}'","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","payload":{"plugins":[{"plugin_id":"system.metrics","plugin_version":"0.1.0","functions":[{"name":"system.metrics.snapshot","input_schema":{"type":"object","properties":{}},"output_schema":{"type":"object","properties":{"cpu":{"type":"number"},"memory":{"type":"number"}}},"risk":"safe","effect":"read","timeout_sec":5,"idempotency":"idempotent"}],"signals":[{"name":"system.cpu.usage","scope":"node","ttl_sec":15,"value_schema":{"type":"number","minimum":0,"maximum":100}}]}]}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  → {d[\"message_type\"]} registered={d[\"payload\"][\"registered_count\"]}')"

    echo -e "${YELLOW}[3] node.heartbeat${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'${MSG_PREFIX}'_hb","message_type":"node.heartbeat","trace_id":"tr_3","node_id":"'${NODE}'","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","payload":{"daemon_uptime_sec":60,"running_jobs":0,"plugin_count":1,"status":"online"}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  → {d[\"message_type\"]} OK')"

    echo -e "${YELLOW}[4] signal.report${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'${MSG_PREFIX}'_sig","message_type":"signal.report","trace_id":"tr_4","node_id":"'${NODE}'","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","payload":{"signals":[{"name":"system.cpu.usage","scope":"node","value":42.5,"collected_at":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","ttl_sec":15}]}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  → accepted={d[\"payload\"][\"accepted\"]} rejected={d[\"payload\"][\"rejected\"]}')"

    echo -e "${YELLOW}[5] job.poll (empty)${NC}"
    curl -s -X POST "${BASE_URL}/yqp/" -H "Content-Type: application/json" -H "${AUTH}" \
      -d '{"yqp_version":"0.1","message_id":"'${MSG_PREFIX}'_poll","message_type":"job.poll","trace_id":"tr_5","node_id":"'${NODE}'","timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'","payload":{"capacity":2,"running_jobs":[]}}' \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  → {d[\"message_type\"]} jobs={len(d[\"payload\"][\"jobs\"])}')"

    echo ""
    echo -e "${GREEN}── 全流程测试完成 ──${NC}"
    ;;

  *)
    echo "用法: $0 [port] {start|status|test-hello|test-full}"
    echo ""
    echo "  start       启动 Center (默认 9800 端口)"
    echo "  status      查看健康状态和已预配节点"
    echo "  test-hello  对运行中的 Center 发送 node.hello"
    echo "  test-full   运行完整 Node 协议流程"
    ;;
esac
