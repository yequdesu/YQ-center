use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxArtifactUploadFile;

#[async_trait]
impl Capability for LinuxArtifactUploadFile {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.artifact.upload_file".into(),
            description: "Read a file from disk and upload it as an artifact to the Center.".into(),
            agent_description: Some(
                "Read a file at a given path, compute its SHA-256 hash, and upload it to the Center for storage and retrieval."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to upload"
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional title for the artifact; defaults to the filename"
                    }
                },
                "required": ["path"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "sha256": {"type": "string"},
                    "download_url": {"type": "string"}
                }
            })),
            risk: "safe".into(),
            effect: "read".into(),
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
        let path = input
            .get("path")
            .and_then(|v| v.as_str())
            .ok_or_else(|| CapabilityError::InvalidInput {
                field: "path".into(),
                message: "path must be a non-empty string".into(),
            })?;

        let title = input
            .get("title")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| {
                // Use the filename as default title
                std::path::Path::new(path)
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("file")
            })
            .to_string();

        // Read the file
        let data = std::fs::read(path).map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                CapabilityError::InvalidInput {
                    field: "path".into(),
                    message: format!("file not found: {}", path),
                }
            } else if e.kind() == std::io::ErrorKind::PermissionDenied {
                CapabilityError::PermissionDenied {
                    path: Some(path.to_string()),
                    detail: e.to_string(),
                }
            } else {
                CapabilityError::Internal(format!("failed to read {}: {}", path, e))
            }
        })?;

        // Upload via global YQP client
        let client = super::YQP_CLIENT.get().ok_or_else(|| {
            CapabilityError::Internal(
                "YQP client not initialized; set_yqp_client() must be called at daemon startup".into(),
            )
        })?;

        let response = client
            .send_artifact_upload(
                "file",
                "application/octet-stream",
                &title,
                &data,
                None,  // summary
                None,  // metadata
                None,  // job_id
            )
            .await
            .map_err(|e| CapabilityError::Internal(format!("artifact upload failed: {}", e)))?;

        let detail = response.artifact;

        Ok(json!({
            "artifact_id": detail.artifact_id,
            "size_bytes": detail.size_bytes,
            "sha256": detail.sha256,
            "download_url": detail.download_url,
        }))
    }
}
