use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxServiceRestart;

#[async_trait]
impl Capability for LinuxServiceRestart {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.service.restart".into(),
            description: "Restart a systemd service via systemctl.".into(),
            agent_description: Some(
                "Restart a specified systemd service using sudo systemctl restart. Returns the service name, status, and command output."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the systemd service to restart"
                    }
                },
                "required": ["name"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string"},
                    "output": {"type": "string"}
                }
            })),
            risk: "maintenance".into(),
            effect: "write".into(),
            timeout_sec: 30,
            idempotency: None,
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "privilege": "root",
                "labels": ["linux"]
            })),
            resource_keys: None,
            conflict_policy: None,
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let name = input
            .get("name")
            .and_then(|v| v.as_str())
            .ok_or_else(|| CapabilityError::InvalidInput {
                field: "name".into(),
                message: "name must be a non-empty string".into(),
            })?;

        let output = tokio::process::Command::new("sudo")
            .args(["systemctl", "restart", name])
            .output()
            .await
            .map_err(|e| {
                CapabilityError::Internal(format!("failed to execute systemctl restart: {}", e))
            })?;

        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let combined = if stderr.is_empty() {
            stdout
        } else {
            format!("{}\n{}", stdout, stderr)
        };

        if output.status.success() {
            Ok(json!({
                "name": name,
                "status": "restarted",
                "output": combined,
            }))
        } else {
            Ok(json!({
                "name": name,
                "status": "failed",
                "output": combined,
            }))
        }
    }
}
