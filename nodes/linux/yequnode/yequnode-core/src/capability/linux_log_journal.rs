use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxLogJournal;

#[async_trait]
impl Capability for LinuxLogJournal {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.log.journal".into(),
            description: "Query system journal with optional filters.".into(),
            agent_description: Some(
                "Inspect systemd journal entries filtered by unit, priority, or time range.".into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 50
                    },
                    "unit": {
                        "type": "string",
                        "description": "Service unit name filter (e.g. sshd.service)"
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["err", "warning", "info"],
                        "description": "Log priority filter"
                    },
                    "since": {
                        "type": "string",
                        "description": "Time range (e.g. '1 hour ago', 'yesterday')"
                    }
                },
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

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let lines = input
            .get("lines")
            .and_then(|v| v.as_u64())
            .unwrap_or(50)
            .min(500) as usize;

        let mut args: Vec<String> = Vec::new();
        args.push("--no-pager".into());
        args.push(format!("--lines={}", lines));

        if let Some(unit) = input.get("unit").and_then(|v| v.as_str()) {
            args.push("-u".into());
            args.push(unit.to_string());
        }

        if let Some(priority) = input.get("priority").and_then(|v| v.as_str()) {
            args.push("-p".into());
            args.push(priority.to_string());
        }

        if let Some(since) = input.get("since").and_then(|v| v.as_str()) {
            args.push("--since".into());
            args.push(since.to_string());
        }

        let output = tokio::task::spawn_blocking(move || {
            std::process::Command::new("journalctl")
                .args(&args)
                .output()
        })
        .await
        .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))?
        .map_err(|e| CapabilityError::Internal(format!("journalctl execution failed: {}", e)))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(CapabilityError::Internal(format!(
                "journalctl exited with {}: {}",
                output.status,
                stderr.trim()
            )));
        }

        let stdout = String::from_utf8_lossy(&output.stdout);
        let content = stdout.to_string();
        let line_count = content.lines().count();

        Ok(json!({
            "content": content,
            "line_count": line_count,
        }))
    }
}
