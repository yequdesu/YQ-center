use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxTransferCrocReconcile;

#[async_trait]
impl Capability for LinuxTransferCrocReconcile {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.croc.reconcile".into(),
            description: "Report local transfer ledger state after daemon restart.".into(),
            agent_description: Some(
                "Reconcile local transfer state after a daemon restart. \
                 Returns all transfers that are in interrupted, running, or other non-terminal states, \
                 along with their details. Used by Center to repair TransferSession state.".into()
            ),
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 10,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux"]
            })),
            resource_keys: None,
            conflict_policy: None,
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![],
            required_intent_slots: vec![],
        }
    }

    async fn execute(_input: Value) -> Result<Value, CapabilityError> {
        let config = crate::config::Config::load()
            .map_err(|e| CapabilityError::Internal(format!("config load failed: {}", e)))?;

        let ledger_path = config
            .db_path
            .parent()
            .unwrap_or(std::path::Path::new("."))
            .join("transfers.db");

        let ledger = crate::transfer_ledger::TransferLedger::open(&ledger_path).map_err(|e| {
            CapabilityError::Internal(format!("failed to open transfer ledger: {}", e))
        })?;

        // Get all transfers
        let all = ledger
            .list(None)
            .map_err(|e| CapabilityError::Internal(format!("ledger list failed: {}", e)))?;

        // Categorize by status
        let mut interrupted = Vec::new();
        let mut running = Vec::new();
        let mut succeeded = Vec::new();
        let mut failed = Vec::new();
        let mut other = Vec::new();

        for entry in &all {
            let summary = json!({
                "transfer_id": entry.transfer_id,
                "role": serde_json::to_value(&entry.role).unwrap_or_default(),
                "status": serde_json::to_value(&entry.status).unwrap_or_default(),
                "source_path": entry.source_path,
                "target_path": entry.target_path,
                "output_dir": entry.output_dir,
                "source_size_bytes": entry.source_size_bytes,
                "resume_mode": serde_json::to_value(&entry.resume_mode).unwrap_or_default(),
                "attempt_count": entry.attempt_count,
                "started_at": entry.started_at,
                "last_progress_at": entry.last_progress_at,
                "completed_at": entry.completed_at,
                "last_error_code": entry.last_error_code,
                "last_error_message": entry.last_error_message,
            });

            match entry.status {
                crate::transfer_ledger::TransferStatus::Interrupted => interrupted.push(summary),
                crate::transfer_ledger::TransferStatus::Running => {
                    // Check if the process is still alive
                    let pid_alive = entry.pid.map(|p| is_pid_alive(p)).unwrap_or(false);
                    let mut s = summary.clone();
                    s["pid_alive"] = json!(pid_alive);
                    if !pid_alive {
                        s["suggested_status"] = json!("interrupted");
                    }
                    running.push(s);
                }
                crate::transfer_ledger::TransferStatus::Succeeded => succeeded.push(summary),
                crate::transfer_ledger::TransferStatus::Failed => failed.push(summary),
                _ => other.push(summary),
            }
        }

        // Mark running transfers whose processes are dead as interrupted
        for entry in &all {
            if entry.status == crate::transfer_ledger::TransferStatus::Running {
                let pid_alive = entry.pid.map(|p| is_pid_alive(p)).unwrap_or(false);
                if !pid_alive {
                    ledger
                        .update_status(
                            &entry.transfer_id,
                            crate::transfer_ledger::TransferStatus::Interrupted,
                            None,
                            None,
                        )
                        .ok();
                }
            }
        }

        Ok(json!({
            "total": all.len(),
            "interrupted": interrupted,
            "running": running,
            "succeeded": succeeded,
            "failed": failed,
            "other": other,
        }))
    }
}

fn is_pid_alive(pid: u32) -> bool {
    // Send signal 0 to check if process exists
    unsafe { libc::kill(pid as i32, 0) == 0 }
}
