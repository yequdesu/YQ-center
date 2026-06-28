# YeQu Center: CLI + Token Auth + Real LLM Provider

> For agentic workers: Use superpowers:subagent-driven-development

**Goal:** Complete all remaining Center-side features: CLI management tool, token scope hardening, real LLM Provider.

---

### Task 1: CLI Management Tool

**Files:**
- Create: `src/yequ/cli.py` — click-based CLI
- Modify: `pyproject.toml` — add `[project.scripts]` entry point
- Add: `click>=8.0` to pyproject.toml dependencies

Commands:
```
yequ health
yequ nodes list
yequ nodes show <node_id>
yequ capabilities list [--node <node_id>]
yequ invoke <function_name> --node <node_id> [--timeout 30]
yequ jobs list [--node <id>] [--status <s>] [--limit 50]
yequ jobs show <job_id>
yequ invocations show <invocation_id>
yequ timeline tail [--node <id>] [--job <id>] [--limit 20]
yequ agent session create [--mode auto]
yequ agent invoke <session_id> <prompt>
```

Config: `~/.config/yequ/config.toml` with `[center] base_url`.

---

### Task 2: Token Auth Hardening

**Files:**
- Create: `src/yequ/models/api_token.py` — ApiToken model (id, token_hash, scope, label, created_at)
- Create: `src/yequ/services/token_auth.py` — scope validation middleware/dependency
- Modify: `src/yequ/api/deps.py` — add get_admin_token, get_agent_token dependencies
- Modify: `src/yequ/api/routes/admin.py` — add POST /admin/tokens + protect endpoints
- Modify: `src/yequ/api/routes/agent.py` — protect endpoints
- Modify: `src/yequ/api/routes/yqp.py` — validate node token scope
- Create: `alembic/versions/002_api_tokens.py` — migration

Token types and scopes:
| Token | Scope | Access |
|---|---|---|
| Node token (existing) | node | /yqp/* only |
| Admin token | admin | /admin/* only |
| Agent token | agent | /agent/* only |

Rules:
- Node token → /admin/* or /agent/* → 403 + audit event (auth.forbidden)
- Admin token → /yqp/* or /agent/* → 403
- Agent token → /yqp/* or /admin/* → 403
- No token → 401
- Auth failures logged as TimelineEvent (auth.failed / auth.forbidden / token.scope_denied)

---

### Task 3: Real LLM Provider

**Files:**
- Create: `src/yequ/agent/anthropic_provider.py` — AnthropicProvider implementing AgentProvider
- Modify: `pyproject.toml` — add `anthropic>=0.30` dependency
- Modify: `src/yequ/config.py` — add anthropic settings
- Modify: `src/yequ/api/routes/agent.py` — auto-load AnthropicProvider when configured

Uses Anthropic Python SDK. Converts AgentFunction[] to Claude tool definitions. Validates tool responses. Never calls nodes directly — returns function_calls for the Center to execute.

---

### Task 4: Tests + Final Verification
