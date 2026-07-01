use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;
use crate::config::Config;

pub struct LinuxTransferRcloneReconcile;

#[async_trait]
impl Capability for LinuxTransferRcloneReconcile {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.rclone.reconcile".into(),
            description: "Read local rclone transfer workspace state for reconciliation.".into(),
            agent_description: Some(
                "Inspect the local rclone transfer workspace for a transfer id.".into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {"transfer_id": {"type": "string"}},
                "required": ["transfer_id"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 10,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "transfer"]
            })),
            resource_keys: None,
            conflict_policy: None,
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![],
            required_intent_slots: vec!["transfer_id".into()],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let transfer_id = required_string(&input, "transfer_id")?;
        let config = Config::load().map_err(|e| CapabilityError::Internal(e.to_string()))?;
        let workspace = config.transfer.rclone.temp_dir.join(&transfer_id);
        Ok(json!({
            "transfer_id": transfer_id,
            "workspace": workspace.to_string_lossy(),
            "workspace_exists": workspace.exists(),
            "done_marker_exists": workspace.join(".yequ-transfer").join(transfer_id).join("done.json").exists(),
            "payload_exists": workspace.join("payload").exists(),
        }))
    }
}

fn required_string(input: &Value, field: &str) -> Result<String, CapabilityError> {
    input
        .get(field)
        .and_then(|v| v.as_str())
        .filter(|value| !value.trim().is_empty())
        .map(|value| value.to_string())
        .ok_or_else(|| CapabilityError::InvalidInput {
            field: field.into(),
            message: format!("{} is required", field),
        })
}
