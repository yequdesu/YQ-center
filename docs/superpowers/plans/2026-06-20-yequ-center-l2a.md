# YeQu Center L2-A: Maintenance Write Operations

> Execute sequentially. Each stage is independently testable.

---

### Stage 1: Data Models
- `ApprovalRequest` model + migration
- `ResourceLock` model + migration
- Invocation state expansion (waiting_approval, rejected)
- Capability fields (approval_required, dry_run_supported, resource_key_template, rollback_supported)

### Stage 2: Approval API
- POST/GET /admin/approvals, approve, deny
- State machine: pending→approved|denied|expired, approved→consumed

### Stage 3: Policy Enforcement
- L2 write ops: readonly=reject, auto=approval_required, manual=create_approval

### Stage 4: Invocation Flow
- L2: return waiting_approval when no approval
- L2: verify approval + create Job when approved

### Stage 5: Resource Lock
- Lock on resource_key, conflict detection

### Stage 6: YQP/Job Runtime
- Job payload includes approval_id, resource_keys, dry_run

### Stage 7: Agent L2 Behavior
- L2 path: create approval → return waiting_approval

### Stage 8: Timeline/Audit
- approval.*, resource.lock.*, l2.* events

### Stage 9: Admin Query APIs
- Already mostly done, verify coverage

### Stage 10: Test Matrix
- 14 L2-specific tests

### Stage 11: Acceptance
- pytest full pass, verification checklist
