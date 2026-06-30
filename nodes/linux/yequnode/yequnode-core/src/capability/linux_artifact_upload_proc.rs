use async_trait::async_trait;
use chrono::Utc;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxArtifactUploadProc;

#[async_trait]
impl Capability for LinuxArtifactUploadProc {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.artifact.upload_proc".into(),
            description: "Read a /proc entry and upload it as a text artifact.".into(),
            agent_description: Some(
                "Read a file from /proc (e.g. cpuinfo, meminfo, version) and upload its contents as an artifact to the Center."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "entry": {
                        "type": "string",
                        "description": "The /proc entry to read (e.g. cpuinfo, meminfo, version, uptime)"
                    }
                },
                "required": ["entry"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "sha256": {"type": "string"},
                    "download_url": {"type": "string"},
                    "entry": {"type": "string"}
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
        let entry = input.get("entry").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "entry".into(),
                message: "entry must be a non-empty string".into(),
            }
        })?;

        // Sanitize the entry: reject paths with slashes to prevent directory traversal
        if entry.contains('/') || entry.contains("..") {
            return Err(CapabilityError::InvalidInput {
                field: "entry".into(),
                message: "entry must not contain path separators".into(),
            });
        }

        let proc_path = format!("/proc/{}", entry);

        let data = std::fs::read_to_string(&proc_path).map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                CapabilityError::InvalidInput {
                    field: "entry".into(),
                    message: format!("/proc/{} not found", entry),
                }
            } else if e.kind() == std::io::ErrorKind::PermissionDenied {
                CapabilityError::PermissionDenied {
                    path: Some(proc_path),
                    detail: e.to_string(),
                }
            } else {
                CapabilityError::Internal(format!("failed to read /proc/{}: {}", entry, e))
            }
        })?;

        let data_bytes = data.into_bytes();

        let timestamp = Utc::now().format("%Y%m%d%H%M%S");
        let title = format!("proc-{}-{}.txt", entry, timestamp);

        // Upload via global YQP client
        let client = super::YQP_CLIENT.get().ok_or_else(|| {
            CapabilityError::Internal(
                "YQP client not initialized; set_yqp_client() must be called at daemon startup"
                    .into(),
            )
        })?;

        let response = client
            .send_artifact_upload(
                "proc",
                "text/plain",
                &title,
                &data_bytes,
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
            "entry": entry,
        }))
    }
}
