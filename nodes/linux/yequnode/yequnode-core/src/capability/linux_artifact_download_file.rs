use std::path::PathBuf;

use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxArtifactDownloadFile;

#[async_trait]
impl Capability for LinuxArtifactDownloadFile {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.artifact.download_file".into(),
            description: "Download a Center artifact to a local Linux filesystem path.".into(),
            agent_description: Some(
                "Materialize a Center artifact on this Linux node. Use only when the user has specified the artifact_id and exact output_path; probe the destination parent first when path permissions are uncertain."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "Center artifact id to download"
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Absolute local path to write on the Linux node"
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["fail_if_exists", "overwrite"],
                        "default": "fail_if_exists",
                        "description": "Whether to overwrite an existing file"
                    }
                },
                "required": ["artifact_id", "output_path"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "output_path": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "sha256": {"type": "string"},
                    "expected_sha256": {"type": ["string", "null"]},
                    "content_type": {"type": ["string", "null"]},
                    "verified": {"type": "boolean"}
                },
                "required": ["artifact_id", "output_path", "size_bytes", "sha256", "verified"]
            })),
            risk: "maintenance".into(),
            effect: "write".into(),
            timeout_sec: 300,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "artifact"]
            })),
            resource_keys: Some(vec!["node.filesystem".into(), "center.artifact".into()]),
            conflict_policy: Some("serialize".into()),
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![
                json!({
                    "name": "artifact_id_known",
                    "description": "The Center artifact id must be known before invocation."
                }),
                json!({
                    "name": "destination_parent_writable",
                    "description": "The output path parent must exist or be creatable by this runtime."
                }),
            ],
            required_intent_slots: vec!["artifact_id".into(), "output_path".into()],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let artifact_id = required_string(&input, "artifact_id")?;
        let output_path = required_string(&input, "output_path")?;
        if !output_path.starts_with('/') {
            return Err(CapabilityError::InvalidInput {
                field: "output_path".into(),
                message: "output_path must be absolute".into(),
            });
        }
        let mode = input
            .get("mode")
            .and_then(|v| v.as_str())
            .filter(|value| !value.is_empty())
            .unwrap_or("fail_if_exists");
        let overwrite = match mode {
            "fail_if_exists" => false,
            "overwrite" => true,
            other => {
                return Err(CapabilityError::InvalidInput {
                    field: "mode".into(),
                    message: format!("unsupported mode: {}", other),
                })
            }
        };
        let output = PathBuf::from(&output_path);
        if output.exists() && !overwrite {
            return Err(CapabilityError::TargetExists { path: output_path });
        }

        let client = super::YQP_CLIENT.get().ok_or_else(|| {
            CapabilityError::Internal(
                "YQP client not initialized; set_yqp_client() must be called at daemon startup"
                    .into(),
            )
        })?;
        let result = client
            .download_artifact_to_file(&artifact_id, &output, overwrite)
            .await
            .map_err(|e| map_download_error(&artifact_id, &output_path, e))?;
        let verified = result
            .expected_sha256
            .as_ref()
            .map(|expected| expected.eq_ignore_ascii_case(&result.sha256))
            .unwrap_or(true);

        Ok(json!({
            "artifact_id": result.artifact_id,
            "output_path": result.output_path,
            "size_bytes": result.size_bytes,
            "sha256": result.sha256,
            "expected_sha256": result.expected_sha256,
            "content_type": result.content_type,
            "verified": verified,
        }))
    }
}

fn map_download_error(
    artifact_id: &str,
    output_path: &str,
    error: crate::yqp::envelope::YqpError,
) -> CapabilityError {
    let code = error.error_code().to_string();
    let message = error.error_message().to_string();
    match code.as_str() {
        "artifact_not_found" => CapabilityError::NotFound {
            path: artifact_id.to_string(),
        },
        "network_error" if message.contains("sha256 mismatch") => {
            CapabilityError::IntegrityMismatch { detail: message }
        }
        "network_error"
            if message.starts_with("failed to create output directory")
                || message.starts_with("failed to create ")
                || message.starts_with("failed to write ")
                || message.starts_with("failed to flush ") =>
        {
            CapabilityError::PermissionDenied {
                path: Some(output_path.to_string()),
                detail: message,
            }
        }
        "artifact_download_failed" | "network_error" => {
            CapabilityError::ExternalServiceFailed { detail: message }
        }
        _ => CapabilityError::Internal(format!("artifact download failed: {}", error)),
    }
}

fn required_string(input: &Value, field: &str) -> Result<String, CapabilityError> {
    input
        .get(field)
        .and_then(|v| v.as_str())
        .filter(|s| !s.trim().is_empty())
        .map(|s| s.trim().to_string())
        .ok_or_else(|| CapabilityError::InvalidInput {
            field: field.into(),
            message: "must be a non-empty string".into(),
        })
}
