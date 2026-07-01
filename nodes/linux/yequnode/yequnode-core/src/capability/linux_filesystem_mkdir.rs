use std::path::Path;

use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxFilesystemMkdir;

#[async_trait]
impl Capability for LinuxFilesystemMkdir {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.filesystem.mkdir".into(),
            description: "Create one Linux directory path using the current runtime permissions."
                .into(),
            agent_description: Some(
                "Create a Linux directory before writing, artifact deploy, or transfer receive. \
                 Use only when the user has specified the exact path and creation mode."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or runtime-visible directory path to create."
                    },
                    "parents": {
                        "type": "boolean",
                        "default": true,
                        "description": "Create missing parent directories when true."
                    },
                    "exist_ok": {
                        "type": "boolean",
                        "default": true,
                        "description": "Treat an existing directory as success when true."
                    },
                    "mode": {
                        "type": "string",
                        "pattern": "^[0-7]{3,4}$",
                        "description": "Optional Unix permission mode such as 755 or 0750."
                    }
                },
                "required": ["path"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "created": {"type": "boolean"},
                    "already_exists": {"type": "boolean"},
                    "is_dir": {"type": "boolean"},
                    "mode": {"type": "string"}
                },
                "required": ["path", "created", "already_exists", "is_dir"],
                "additionalProperties": false
            })),
            risk: "maintenance".into(),
            effect: "write".into(),
            timeout_sec: 10,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "filesystem"]
            })),
            resource_keys: Some(vec!["node.filesystem".into()]),
            conflict_policy: Some("serialize".into()),
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![
                json!({"fact": "target.path_explicit", "source": "intent"}),
                json!({"fact": "target.parent_writable", "source": "preflight"}),
            ],
            required_intent_slots: vec!["path".into()],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let path = input.get("path").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "path".into(),
                message: "path is required".into(),
            }
        })?;
        if path.trim().is_empty() {
            return Err(CapabilityError::InvalidInput {
                field: "path".into(),
                message: "path must not be empty".into(),
            });
        }
        let parents = input
            .get("parents")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        let exist_ok = input
            .get("exist_ok")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        let mode = input
            .get("mode")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());

        let path_owned = path.to_string();
        tokio::task::spawn_blocking(move || {
            create_directory(Path::new(&path_owned), parents, exist_ok, mode.as_deref())
        })
        .await
        .map_err(|e| CapabilityError::Internal(format!("mkdir worker failed: {}", e)))?
    }
}

fn create_directory(
    path: &Path,
    parents: bool,
    exist_ok: bool,
    mode: Option<&str>,
) -> Result<Value, CapabilityError> {
    if path.exists() {
        let meta = std::fs::metadata(path).map_err(|e| map_io_error(path, "stat", e))?;
        if !meta.is_dir() {
            return Err(CapabilityError::TargetExists {
                path: path.display().to_string(),
            });
        }
        if !exist_ok {
            return Err(CapabilityError::TargetExists {
                path: path.display().to_string(),
            });
        }
        if let Some(mode_text) = mode {
            set_mode(path, mode_text)?;
        }
        return Ok(json!({
            "path": path.display().to_string(),
            "created": false,
            "already_exists": true,
            "is_dir": true,
            "mode": mode,
        }));
    }

    if parents {
        std::fs::create_dir_all(path).map_err(|e| map_io_error(path, "create_dir_all", e))?;
    } else {
        std::fs::create_dir(path).map_err(|e| map_io_error(path, "create_dir", e))?;
    }
    if let Some(mode_text) = mode {
        set_mode(path, mode_text)?;
    }

    Ok(json!({
        "path": path.display().to_string(),
        "created": true,
        "already_exists": false,
        "is_dir": true,
        "mode": mode,
    }))
}

fn set_mode(path: &Path, mode_text: &str) -> Result<(), CapabilityError> {
    use std::os::unix::fs::PermissionsExt;

    let mode = u32::from_str_radix(mode_text.trim_start_matches('0'), 8).map_err(|_| {
        CapabilityError::InvalidInput {
            field: "mode".into(),
            message: "mode must be an octal string such as 755 or 0750".into(),
        }
    })?;
    let permissions = std::fs::Permissions::from_mode(mode);
    std::fs::set_permissions(path, permissions).map_err(|e| map_io_error(path, "chmod", e))
}

fn map_io_error(path: &Path, operation: &str, error: std::io::Error) -> CapabilityError {
    match error.kind() {
        std::io::ErrorKind::NotFound => CapabilityError::NotFound {
            path: path.display().to_string(),
        },
        std::io::ErrorKind::PermissionDenied => CapabilityError::PermissionDenied {
            path: Some(path.display().to_string()),
            detail: format!("{} failed: {}", operation, error),
        },
        std::io::ErrorKind::AlreadyExists => CapabilityError::TargetExists {
            path: path.display().to_string(),
        },
        _ => CapabilityError::Internal(format!(
            "{} failed for {}: {}",
            operation,
            path.display(),
            error
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn mkdir_creates_nested_directory() {
        let temp = tempfile::tempdir().unwrap();
        let target = temp.path().join("a").join("b");

        let output = LinuxFilesystemMkdir::execute(json!({
            "path": target.display().to_string(),
            "parents": true,
            "exist_ok": true
        }))
        .await
        .unwrap();

        assert!(target.is_dir());
        assert_eq!(output["created"], true);
        assert_eq!(output["already_exists"], false);
        assert_eq!(output["is_dir"], true);
    }

    #[tokio::test]
    async fn mkdir_rejects_existing_directory_when_exist_ok_false() {
        let temp = tempfile::tempdir().unwrap();

        let result = LinuxFilesystemMkdir::execute(json!({
            "path": temp.path().display().to_string(),
            "exist_ok": false
        }))
        .await;

        assert!(matches!(result, Err(CapabilityError::TargetExists { .. })));
    }
}
