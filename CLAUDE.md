# CLAUDE.md

This file is the Claude Code entry point for this repository.

To avoid duplicated and stale agent instructions, Claude should use the same project guidance as Codex:

1. Read `AGENTS.md` first.
2. Read `docs/documentation-index.md` before using project documents.
3. Treat archived documents under `docs/archive/` as historical context only, never as current implementation constraints.
4. Treat current Node development as governed by:
   - `YQP-Node-Protocol.md`
   - `docs/node-capability-contract.md`
   - `docs/linux-node-development-contract.md`
5. Treat current runtime work as governed by:
   - `docs/current-project-overview.md`
   - `docs/todos/2026-07-03-documentation-and-architecture-quality-gate.md`

All repository documents must be UTF-8. If this file conflicts with `AGENTS.md` or `docs/documentation-index.md`, those files win.
