use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxDmesg;

#[async_trait]
impl Capability for LinuxDmesg {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.dmesg".into(),
            description: "Return kernel ring buffer messages.".into(),
            agent_description: Some("Read kernel ring buffer messages via dmesg.".into()),
            input_schema: json!({
                "type": "object",
                "properties": {},
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
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![],
            required_intent_slots: vec![],
        }
    }

    async fn execute(_input: Value) -> Result<Value, CapabilityError> {
        let content = tokio::task::spawn_blocking(|| {
            // Prefer dmesg command; fallback to /dev/kmsg is unreliable (reads once, clears)
            let output = std::process::Command::new("dmesg")
                .args([
                    "--level=emerg,alert,crit,err,warning,notice,info",
                    "--no-namespace",
                ])
                .output()
                .map_err(|e| CapabilityError::Internal(format!("dmesg execution failed: {}", e)))?;

            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                return Err(CapabilityError::Internal(format!(
                    "dmesg exited with {}: {}",
                    output.status,
                    stderr.trim()
                )));
            }

            let content = String::from_utf8_lossy(&output.stdout).to_string();
            Ok(content)
        })
        .await
        .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))??;

        let line_count = content.lines().count();

        Ok(json!({
            "content": content,
            "line_count": line_count,
        }))
    }
}
