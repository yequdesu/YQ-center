use async_trait::async_trait;
use chrono::Utc;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxArtifactUploadLog;

#[async_trait]
impl Capability for LinuxArtifactUploadLog {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.artifact.upload_log".into(),
            description: "Capture and upload journald log output as an artifact.".into(),
            agent_description: Some(
                "Run journalctl to capture system logs and upload the result as a text/plain artifact to the Center."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "integer",
                        "default": 200,
                        "description": "Number of recent log lines to capture"
                    },
                    "unit": {
                        "type": "string",
                        "description": "Filter logs to a specific systemd unit (e.g. sshd, nginx)"
                    },
                    "priority": {
                        "type": "string",
                        "description": "Minimum log priority (0=emerg, 1=alert, 2=crit, 3=err, 4=warning, 5=notice, 6=info, 7=debug)"
                    }
                },
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "sha256": {"type": "string"},
                    "download_url": {"type": "string"},
                    "lines": {"type": "integer"}
                }
            })),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 10,
            idempotency: None,
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "privilege": "root",
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
        let lines = input.get("lines").and_then(|v| v.as_i64()).unwrap_or(200);
        let unit = input.get("unit").and_then(|v| v.as_str());
        let priority = input.get("priority").and_then(|v| v.as_str());

        // Build journalctl command
        let mut cmd = tokio::process::Command::new("journalctl");
        cmd.arg("--no-pager");
        cmd.arg("--lines").arg(lines.to_string());

        if let Some(unit) = unit {
            cmd.arg("--unit").arg(unit);
        }
        if let Some(priority) = priority {
            cmd.arg("--priority").arg(priority);
        }

        let output = cmd
            .output()
            .await
            .map_err(|e| CapabilityError::Internal(format!("failed to run journalctl: {}", e)))?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(CapabilityError::FunctionExecutionFailed {
                message: format!(
                    "journalctl exited with code {:?}: {}",
                    output.status.code(),
                    stderr
                ),
                exit_code: output.status.code(),
                stderr: Some(stderr.to_string()),
            });
        }

        let log_data = output.stdout;
        let line_count = log_data.split(|&b| b == b'\n').count().saturating_sub(1) as u64;

        let timestamp = Utc::now().format("%Y%m%d%H%M%S");
        let title = format!("journal-{}.log", timestamp);

        // Upload via global YQP client
        let client = super::YQP_CLIENT.get().ok_or_else(|| {
            CapabilityError::Internal(
                "YQP client not initialized; set_yqp_client() must be called at daemon startup"
                    .into(),
            )
        })?;

        let response = client
            .send_artifact_upload(
                "log",
                "text/plain",
                &title,
                &log_data,
                None, // summary
                None, // metadata
                None, // job_id
            )
            .await
            .map_err(|e| CapabilityError::Internal(format!("artifact upload failed: {}", e)))?;

        let detail = response.artifact;

        Ok(json!({
            "artifact_id": detail.artifact_id,
            "size_bytes": detail.size_bytes,
            "sha256": detail.sha256,
            "download_url": detail.download_url,
            "lines": line_count,
        }))
    }
}
