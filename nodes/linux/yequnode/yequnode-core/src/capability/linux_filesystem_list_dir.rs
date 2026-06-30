use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;
use async_trait::async_trait;
use serde_json::{json, Value};

pub struct LinuxFilesystemListDir;

#[async_trait]
impl Capability for LinuxFilesystemListDir {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.filesystem.list_dir".into(),
            description: "List entries in a directory, optionally filtered by a shell glob pattern (*.log, *config*, etc.). Supports wildcard matching.".into(),
            agent_description: Some(
                "List files and directories at a given path. Use this to explore directory contents. \
                Supports an optional pattern filter with shell glob wildcards (*.log, *.rs, *config*). \
                When pattern is provided, only entries whose name matches the glob are returned. \
                Returns entry names with type (file/dir/symlink), size, and permissions. \
                Limited to first 500 entries."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "default": "/" },
                    "pattern": {
                        "type": "string",
                        "description": "Shell glob to filter entries: *.log, *config*, *.rs, etc. Leave empty to list all."
                    }
                },
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(), effect: "read".into(), timeout_sec: 5,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({"runtime_kind": "privileged", "labels": ["linux"]})),
            resource_keys: None, conflict_policy: None,
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![],
            required_intent_slots: vec![],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let path = input.get("path").and_then(|v| v.as_str()).unwrap_or("/");
        let pattern = input
            .get("pattern")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let dir = std::fs::read_dir(path).map_err(|e| {
            if e.kind() == std::io::ErrorKind::PermissionDenied {
                CapabilityError::PermissionDenied {
                    path: Some(path.into()),
                    detail: format!("cannot list directory: {}", e),
                }
            } else {
                CapabilityError::Internal(format!("read_dir {}: {}", path, e))
            }
        })?;

        let match_pattern = |name: &str| -> bool {
            pattern
                .as_ref()
                .map_or(true, |pat| glob_match::glob_match(pat, name))
        };

        let mut entries = Vec::new();
        for entry in dir.take(500) {
            let entry = match entry {
                Ok(e) => e,
                Err(_) => continue,
            };
            let name = entry.file_name().to_string_lossy().to_string();
            if !match_pattern(&name) {
                continue;
            }
            let meta = entry.metadata().ok();
            let file_type = meta
                .as_ref()
                .map(|m| {
                    if m.is_dir() {
                        "dir"
                    } else if m.is_symlink() {
                        "symlink"
                    } else {
                        "file"
                    }
                })
                .unwrap_or("unknown");
            entries.push(json!({
                "name": name,
                "type": file_type,
                "size_bytes": meta.as_ref().map(|m| m.len()).unwrap_or(0),
                "permissions": meta.as_ref().map(|m| {
                    use std::os::unix::fs::PermissionsExt;
                    format!("{:o}", m.permissions().mode() & 0o777)
                }).unwrap_or_default(),
            }));
        }

        Ok(json!({
            "path": path,
            "count": entries.len(),
            "truncated": entries.len() >= 500,
            "entries": entries,
        }))
    }
}
