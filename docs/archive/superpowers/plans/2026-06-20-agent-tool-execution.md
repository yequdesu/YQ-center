# Agent Tool Execution — Complete Execution Pipeline

> Execute plan with superpowers:subagent-driven-development

**Goal:** Rewrite /agent/invoke to fully execute tool calls through Invocation→Job→wait→result, returning complete AgentInvokeResponse.

**Architecture:** Provider returns tool_calls only (no execution). AgentService orchestrates: validate→select node→policy→create Invocation→wait→collect result. Fixed response schema, backward-compatible field additions only.

---

### Task 1: Data Structures + Capability Resolver

Create `agent/tool_execution.py` (all Pydantic models) + `services/capability_resolver.py`.

### Task 2: Provider + DeepSeek Provider Update

Update AgentProvider return type. DeepSeekProvider: sanitize names, parse raw tool_calls, return AgentToolCall list.

### Task 3: AgentService Rewrite

Rewrite invoke(): prompt→provider→validate→execute_tool_calls→wait→collect→final_response. Add execute_tool_call(), wait_invocation_terminal().

### Task 4: /agent/invoke Response + Timeline

Update route response model to AgentInvokeResponse. Add Timeline events: agent.prompt.received, agent.tool.selected/denied/completed/failed, agent.final_response.

### Task 5: Tests + Verification

test_agent_tool_execution.py covering 12 scenarios. All existing tests pass.
