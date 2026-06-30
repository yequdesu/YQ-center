use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxFilesystemReadText;

const MAX_READ_BYTES: usize = 65536;

#[async_trait]
impl Capability for LinuxFilesystemReadText {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.filesystem.read_text".into(),
            description: "Read a text file (OS permission constrained, max 64KB).".into(),
            agent_description: Some(
                "Read the content of a text file, truncated to 64KB. OS file permissions are the only gate.".into()
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file to read"
                    }
                },
                "required": ["path"],
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
        let path = input.get("path").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "path".into(),
                message: "missing required field: path".into(),
            }
        })?;

        let path_clone = path.to_string();

        let (content, size_bytes, truncated) = tokio::task::spawn_blocking(move || {
            let data = std::fs::read(&path_clone).map_err(|e| {
                CapabilityError::Internal(format!("read '{}' failed: {}", path_clone, e))
            })?;

            let size = data.len();
            let truncated = size > MAX_READ_BYTES;
            let content = if truncated {
                String::from_utf8_lossy(&data[..MAX_READ_BYTES]).to_string()
            } else {
                String::from_utf8_lossy(&data).to_string()
            };

            Ok::<_, CapabilityError>((content, size, truncated))
        })
        .await
        .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))??;

        Ok(json!({
            "path": path,
            "content": content,
            "size_bytes": size_bytes,
            "truncated": truncated,
        }))
    }
}
