# Agent Multi-Node Routing TODO

## Context

Linux Node is close to rollout. The current Agent UI and stream contract still
carry single-node assumptions:

- the chat composer pins every run to one selected node by default;
- tool call cards show only the function name;
- SSE tool lifecycle events do not consistently carry the resolved node;
- frontend tool state does not persist or render node identity;
- same-name capabilities across Windows and Linux nodes will be ambiguous.

This is a pre-rollout blocker for multi-node operation.

## Immediate Scope

1. Backend SSE contract
   - Add `target_node_id` to tool lifecycle events whenever Center knows it.
   - Keep unresolved creation events explicit with `target_node_id: null`.
   - Include `source_nodes` in `agent.prompt_context.available_functions`
     for transparent debugging of same-name capabilities.
   - Preserve strict error propagation; do not synthesize fallback text.

2. Frontend routing
   - Change Agent chat default from node-pinned to Auto routing.
   - Send no `target_node_id` when Auto is selected.
   - Keep explicit node pinning available for debugging and controlled tests.
   - Current plan/maintenance-plan flow still resolves to one target node.
     Multi-node planning should be handled as a separate plan IR change.

3. Frontend tool display
   - Add `targetNodeId` to tool call state.
   - Update state from `agent.invocation.created`,
     `agent.approval.required`, `agent.tool_call.waiting_approval`,
     `agent.job.*`, `agent.tool_call.completed`, and failure events.
   - Preserve `targetNodeId` when loading persisted session history.
   - Render a node badge in each tool call card, e.g.
     `system.info @ winClient`.

4. Fast validation
   - Avoid running full pytest by default for this change.
   - Run targeted backend tests for Agent stream/tool events only.
   - Run frontend TypeScript compile check for touched UI code.
   - Use `scripts/fast-check.ps1` for the default local feedback loop.

## Follow-Up Architecture

The immediate scope does not solve context explosion or capability discovery.
The next architectural step should introduce Center-level Agent meta tools:

- `node.list`
- `node.status`
- `capability.search`
- `capability.describe`
- `capability.invoke`

The long-term model should stop injecting every raw node capability into the
LLM tool list. The Agent should discover and invoke capabilities through Center
metadata and stable capability identifiers.
