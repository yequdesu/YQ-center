use std::ffi::CString;
use std::path::Path;

use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxFilesystemDiskUsage;

#[async_trait]
impl Capability for LinuxFilesystemDiskUsage {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.filesystem.disk_usage".into(),
            description: "Report filesystem capacity and free space for a Linux path.".into(),
            agent_description: Some(
                "Check disk capacity, free bytes, and available bytes for a runtime-visible Linux path."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path whose containing filesystem should be inspected."
                    }
                },
                "required": ["path"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "block_size": {"type": "integer"},
                    "total_bytes": {"type": "integer"},
                    "free_bytes": {"type": "integer"},
                    "available_bytes": {"type": "integer"},
                    "used_bytes": {"type": "integer"},
                    "used_percent": {"type": "number"},
                    "files_total": {"type": "integer"},
                    "files_free": {"type": "integer"}
                },
                "required": [
                    "path",
                    "block_size",
                    "total_bytes",
                    "free_bytes",
                    "available_bytes",
                    "used_bytes",
                    "used_percent"
                ],
                "additionalProperties": false
            })),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 5,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "filesystem"]
            })),
            resource_keys: None,
            conflict_policy: None,
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![json!({
                "fact": "target.path_visible",
                "source": "runtime"
            })],
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
        let path_owned = path.to_string();
        tokio::task::spawn_blocking(move || disk_usage(Path::new(&path_owned)))
            .await
            .map_err(|e| CapabilityError::Internal(format!("disk usage worker failed: {}", e)))?
    }
}

fn disk_usage(path: &Path) -> Result<Value, CapabilityError> {
    if !path.exists() {
        return Err(CapabilityError::NotFound {
            path: path.display().to_string(),
        });
    }
    let path_str = path.to_str().ok_or_else(|| CapabilityError::InvalidInput {
        field: "path".into(),
        message: "path is not valid UTF-8".into(),
    })?;
    let path_cstr = CString::new(path_str).map_err(|_| CapabilityError::InvalidInput {
        field: "path".into(),
        message: "path contains a NUL byte".into(),
    })?;

    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    let ret = unsafe { libc::statvfs(path_cstr.as_ptr(), &mut stat) };
    if ret != 0 {
        let err = std::io::Error::last_os_error();
        return Err(match err.kind() {
            std::io::ErrorKind::NotFound => CapabilityError::NotFound {
                path: path.display().to_string(),
            },
            std::io::ErrorKind::PermissionDenied => CapabilityError::PermissionDenied {
                path: Some(path.display().to_string()),
                detail: "cannot inspect filesystem usage".into(),
            },
            _ => {
                CapabilityError::Internal(format!("statvfs failed for {}: {}", path.display(), err))
            }
        });
    }

    let block_size = stat.f_frsize;
    let total_bytes = stat.f_blocks.saturating_mul(block_size);
    let free_bytes = stat.f_bfree.saturating_mul(block_size);
    let available_bytes = stat.f_bavail.saturating_mul(block_size);
    let used_bytes = total_bytes.saturating_sub(free_bytes);
    let used_percent = if total_bytes == 0 {
        0.0
    } else {
        used_bytes as f64 / total_bytes as f64 * 100.0
    };

    Ok(json!({
        "path": path.display().to_string(),
        "block_size": block_size,
        "total_bytes": total_bytes,
        "free_bytes": free_bytes,
        "available_bytes": available_bytes,
        "used_bytes": used_bytes,
        "used_percent": used_percent,
        "files_total": stat.f_files,
        "files_free": stat.f_ffree,
    }))
}
