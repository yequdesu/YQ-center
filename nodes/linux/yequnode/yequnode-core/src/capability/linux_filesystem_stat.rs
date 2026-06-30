use async_trait::async_trait;
use serde_json::{json, Value};
use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxFilesystemStat;

#[async_trait]
impl Capability for LinuxFilesystemStat {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.filesystem.stat".into(),
            description: "Return stat information for a path (OS permission constrained).".into(),
            agent_description: Some("Inspect Linux filesystem metadata for a path.".into()),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string" }
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
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let path = input.get("path")
            .and_then(|v| v.as_str())
            .ok_or_else(|| CapabilityError::InvalidInput {
                field: "path".into(),
                message: "path is required".into(),
            })?;

        // OS permission gate only — no allowlist
        match std::fs::metadata(path) {
            Ok(meta) => {
                let file_type = if meta.is_dir() {
                    "directory"
                } else if meta.is_symlink() {
                    "symlink"
                } else {
                    "file"
                };

                Ok(json!({
                    "path": path,
                    "exists": true,
                    "type": file_type,
                    "size_bytes": meta.len(),
                    "permissions": format_permissions(&meta),
                    "modified": format_time(meta.modified().ok()),
                    "accessed": format_time(meta.accessed().ok()),
                    "readonly": meta.permissions().readonly(),
                }))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                Ok(json!({
                    "path": path,
                    "exists": false,
                }))
            }
            Err(e) => Err(CapabilityError::PermissionDenied {
                path: Some(path.into()),
                detail: format!("cannot stat: {}", e),
            }),
        }
    }
}

fn format_permissions(meta: &std::fs::Metadata) -> String {
    use std::os::unix::fs::PermissionsExt;
    let mode = meta.permissions().mode();
    format!("{:o}", mode & 0o777)
}

fn format_time(time: Option<std::time::SystemTime>) -> Option<String> {
    time.map(|t| {
        let dur = t.duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        chrono::DateTime::from_timestamp(dur.as_secs() as i64, dur.subsec_nanos())
            .map(|dt| dt.to_rfc3339())
            .unwrap_or_default()
    })
}
