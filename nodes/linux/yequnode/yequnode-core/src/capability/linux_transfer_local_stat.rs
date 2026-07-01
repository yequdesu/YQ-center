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
                 readability, writability, and available disk space."
                    .into(),
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
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![],
            required_intent_slots: vec![],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let path_str = input.get("path").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "path".into(),
                message: "path is required".into(),
            }
        })?;

        let path = std::path::Path::new(path_str);

        if !path.exists() {
            let parent = path.parent();
            let parent_exists = parent.map(|p| p.exists()).unwrap_or(false);
            let parent_writable = parent.map(is_writable).unwrap_or(false);
            return Ok(json!({
                "path": path_str,
                "found": false,
                "exists": false,
                "parent": parent.map(|p| p.to_string_lossy().to_string()),
                "parent_exists": parent_exists,
                "parent_writable": parent_writable,
                "readable": false,
                "writable": parent_writable,
                "size_bytes": null,
                "mtime": null,
                "sha256": null,
                "is_file": false,
                "is_dir": false,
                "disk_available_bytes": parent.and_then(get_disk_available),
                "free_bytes": parent.and_then(get_disk_available),
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
        let parent = path.parent();
        let parent_exists = parent.map(|p| p.exists()).unwrap_or(false);
        let parent_writable = parent.map(is_writable).unwrap_or(false);

        // Disk space for parent directory
        let disk_available_bytes = get_disk_available(if is_dir {
            path
        } else {
            path.parent().unwrap_or(path)
        });

        Ok(json!({
            "path": path_str,
            "found": true,
            "exists": true,
            "parent": parent.map(|p| p.to_string_lossy().to_string()),
            "parent_exists": parent_exists,
            "parent_writable": parent_writable,
            "readable": readable,
            "writable": writable,
            "size_bytes": size_bytes,
            "mtime": mtime,
            "sha256": sha256,
            "is_file": is_file,
            "is_dir": is_dir,
            "disk_available_bytes": disk_available_bytes,
            "free_bytes": disk_available_bytes,
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
    if path.is_dir() {
        return directory_is_writable(path);
    }
    std::fs::OpenOptions::new().write(true).open(path).is_ok()
}

fn directory_is_writable(path: &std::path::Path) -> bool {
    let probe_name = format!(
        ".yequ-transfer-write-probe-{}-{}",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default()
    );
    let probe_path = path.join(probe_name);
    match std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&probe_path)
    {
        Ok(_) => {
            let _ = std::fs::remove_file(&probe_path);
            true
        }
        Err(_) => false,
    }
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
