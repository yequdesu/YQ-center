use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxServiceList;

#[async_trait]
impl Capability for LinuxServiceList {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.service.list".into(),
            description: "List systemd service units and their states.".into(),
            agent_description: Some(
                "List all systemd service units with their load, active, and sub states.".into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "array"})),
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
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![],
            required_intent_slots: vec![],
        }
    }

    async fn execute(_input: Value) -> Result<Value, CapabilityError> {
        let output = tokio::task::spawn_blocking(|| {
            std::process::Command::new("systemctl")
                .args([
                    "list-units",
                    "--type=service",
                    "--all",
                    "--no-legend",
                    "--no-pager",
                ])
                .output()
        })
        .await
        .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))?
        .map_err(|e| CapabilityError::Internal(format!("systemctl execution failed: {}", e)))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(CapabilityError::Internal(format!(
                "systemctl exited with {}: {}",
                output.status,
                stderr.trim()
            )));
        }

        let stdout = String::from_utf8_lossy(&output.stdout);
        let mut services = Vec::new();

        for line in stdout.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }

            let parts: Vec<&str> = line
                .splitn(5, char::is_whitespace)
                .filter(|s| !s.is_empty())
                .collect();

            if parts.len() >= 4 {
                let description = if parts.len() >= 5 {
                    parts[4].trim().to_string()
                } else {
                    String::new()
                };
                services.push(json!({
                    "unit": parts[0],
                    "load": parts[1],
                    "active": parts[2],
                    "sub": parts[3],
                    "description": description,
                }));
            }
        }

        Ok(json!(services))
    }
}
