use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxServiceStatus;

#[async_trait]
impl Capability for LinuxServiceStatus {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.service.status".into(),
            description: "Return detailed status of a named systemd service.".into(),
            agent_description: Some(
                "Inspect a specific systemd service's status, including process info, logs, and state.".into()
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 5,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
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
            .ok_or_else(|| CapabilityError::InvalidInput { field: "name".into(), message: "missing required field: name".into() })?;

        let name = name.to_owned();

        let output = tokio::task::spawn_blocking(move || {
            std::process::Command::new("systemctl")
                .args(["status", &name, "--no-pager"])
                .output()
        })
        .await
        .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))?
        .map_err(|e| CapabilityError::Internal(format!("systemctl execution failed: {}", e)))?;

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        let mut status_text = stdout.to_string();
        if !stderr.is_empty() {
            if !status_text.is_empty() {
                status_text.push('\n');
            }
            status_text.push_str(&stderr);
        }

        Ok(json!({
            "name": input.get("name").and_then(|v| v.as_str()).unwrap_or(""),
            "status_text": status_text,
        }))
    }
}
