# YeQu-Gateway 多用户支持设计

> 日期：2026-07-12
> 状态：设计完成

## 配置

```yaml
# gateway.yaml
users:
  - username: yequdesu
    password: "<hashed>"
  - username: family  
    password: "<hashed>"
```

## 登录流程

```
浏览器 → gtw.yequdesu.top → 登录页
  → 输入用户名密码 → POST /api/login
  → Gateway 校验 → Set-Cookie: yequ_session=<token>
  → 302 → /dashboard (Dashboard)
```

未登录访问任何 `/dashboard` 或 `/api/*` → 302 回登录页。

## 后端

- `POST /api/login` — 校验用户名密码，返回 session cookie
- `POST /api/logout` — 清除 cookie
- Cookie 中间件 — 每个请求从 cookie 提取 username，注入 `request.state.user`
- Agent 池 — `{username: Agent}` dict，懒加载
- `agent_sessions` 表 — 加 `user_id TEXT NOT NULL DEFAULT 'default'`

## 数据隔离

| 数据 | 共享/隔离 |
|------|----------|
| 设备列表、事件、指标 | 所有用户共享 |
| Agent 对话历史 | 按用户隔离 |
| LLM 配置 | 所有用户共享（同一 Gateway） |
| 巡检状态 | 共享 |

## 前端

- 登录页 → 独立 HTML page
- Dashboard → 原 dashboard.html
- Cookie 检查 → 无 cookie 时 redirect 到 `/login`
