use async_trait::async_trait;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxTransferLocalStat;

#[async_trait]
impl Capability for LinuxTransferLocalStat {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.local.stat".into(),
            description: "Check local path metadata for transfer operations.".into(),
            agent_description: Some(
                "Inspect a local path for transfer readiness: size, mtime, sha256, \
                 readability, writability, and available disk space.".into()
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to check."
                    }
                },
                "required": ["path"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 30,
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
        let path_str = input.get("path")
            .and_then(|v| v.as_str())
            .ok_or_else(|| CapabilityError::InvalidInput {
                field: "path".into(),
                message: "path is required".into(),
            })?;

        let path = std::path::Path::new(path_str);

        if !path.exists() {
            return Ok(json!({
                "path": path_str,
                "exists": false,
                "readable": false,
                "writable": false,
                "size_bytes": null,
                "mtime": null,
                "sha256": null,
                "is_file": false,
                "is_dir": false,
                "disk_available_bytes": null,
                "error": "path does not exist",
            }));
        }

        let meta = std::fs::metadata(path)
            .map_err(|e| CapabilityError::Internal(format!("stat failed: {}", e)))?;

        let is_file = meta.is_file();
        let is_dir = meta.is_dir();
        let size_bytes = if is_file { Some(meta.len()) } else { None };

        let mtime = meta.modified().ok().and_then(|t| {
            let dt: chrono::DateTime<chrono::Utc> = t.into();
            Some(dt.to_rfc3339())
        });

        // Compute sha256 for files only
        let sha256 = if is_file {
            compute_file_sha256(path).ok()
        } else {
            None
        };

        // Check permissions
        let readable = is_readable(path);
        let writable = is_writable(path);

        // Disk space for parent directory
        let disk_available_bytes = get_disk_available(path.parent().unwrap_or(path));

        Ok(json!({
            "path": path_str,
            "exists": true,
            "readable": readable,
            "writable": writable,
            "size_bytes": size_bytes,
            "mtime": mtime,
            "sha256": sha256,
            "is_file": is_file,
            "is_dir": is_dir,
            "disk_available_bytes": disk_available_bytes,
            "error": null,
        }))
    }
}

fn compute_file_sha256(path: &std::path::Path) -> Result<String, std::io::Error> {
    let content = std::fs::read(path)?;
    let mut hasher = Sha256::new();
    hasher.update(&content);
    Ok(hex::encode(hasher.finalize()))
}

fn is_readable(path: &std::path::Path) -> bool {
    std::fs::metadata(path).is_ok()
}

fn is_writable(path: &std::path::Path) -> bool {
    // Try to open for writing (doesn't actually write)
    std::fs::OpenOptions::new()
        .write(true)
        .open(path)
        .is_ok()
}

fn get_disk_available(path: &std::path::Path) -> Option<u64> {
    // Use statvfs on Linux
    use std::ffi::CString;
    let path_cstr = CString::new(path.to_str()?).ok()?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    let ret = unsafe { libc::statvfs(path_cstr.as_ptr(), &mut stat) };
    if ret == 0 {
        Some(stat.f_bavail * stat.f_frsize)
    } else {
        None
    }
}
