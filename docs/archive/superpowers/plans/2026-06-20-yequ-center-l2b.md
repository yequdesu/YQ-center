# YeQu Center L2-B: MaintenancePlan

### Phase 2: Data Models
5 new models: MaintenancePlan, MaintenanceStep, MaintenanceRun, MaintenanceArtifact, RollbackHint

### Phase 3: API
POST/GET /admin/maintenance/plans, approve, run, cancel. GET runs/{id}, runs/{id}/artifacts

### Phase 4: Plan Executor
Step execution with depends_on, continue_on_failure, L2 approval, per-step Job+Lock

### Phase 5: Maintenance Timeline
maintenance.plan.*, maintenance.run.*, maintenance.step.* events

### Phase 6: Agent Planner
Agent generates MaintenancePlan from maintenance prompts instead of direct L2 execution

### Phase 7: E2E Scenarios
6 scenarios: health check, DNS fix, service recovery, log cleanup, Defender scan, failure recovery
