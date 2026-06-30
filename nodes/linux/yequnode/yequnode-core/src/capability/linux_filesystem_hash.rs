use std::fs::File;
use std::io::{BufReader, Read};
use std::path::Path;

use async_trait::async_trait;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxFilesystemHash;

#[async_trait]
impl Capability for LinuxFilesystemHash {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.filesystem.hash".into(),
            description: "Compute the SHA-256 hash for a local Linux file.".into(),
            agent_description: Some(
                "Compute sha256 for a readable local file on the Linux node. \
                 Use this to verify file integrity after transfer or artifact restore."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or runtime-visible local file path."
                    },
                    "algorithm": {
                        "type": "string",
                        "enum": ["sha256"],
                        "default": "sha256"
                    }
                },
                "required": ["path"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "algorithm": {"type": "string"},
                    "sha256": {"type": "string"},
                    "size_bytes": {"type": "integer"}
                },
                "required": ["path", "algorithm", "sha256", "size_bytes"],
                "additionalProperties": false
            })),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 60,
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
                "fact": "source.path_readable",
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
        let algorithm = input
            .get("algorithm")
            .and_then(|v| v.as_str())
            .unwrap_or("sha256");
        if algorithm != "sha256" {
            return Err(CapabilityError::InvalidInput {
                field: "algorithm".into(),
                message: "only sha256 is supported".into(),
            });
        }

        let path_owned = path.to_string();
        tokio::task::spawn_blocking(move || hash_file(Path::new(&path_owned)))
            .await
            .map_err(|e| CapabilityError::Internal(format!("hash worker failed: {}", e)))?
    }
}

fn hash_file(path: &Path) -> Result<Value, CapabilityError> {
    let meta = std::fs::metadata(path).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => CapabilityError::NotFound {
            path: path.display().to_string(),
        },
        std::io::ErrorKind::PermissionDenied => CapabilityError::PermissionDenied {
            path: Some(path.display().to_string()),
            detail: "cannot stat file".into(),
        },
        _ => CapabilityError::Internal(format!("stat failed for {}: {}", path.display(), e)),
    })?;
    if !meta.is_file() {
        return Err(CapabilityError::InvalidInput {
            field: "path".into(),
            message: "path must point to a regular file".into(),
        });
    }

    let file = File::open(path).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => CapabilityError::NotFound {
            path: path.display().to_string(),
        },
        std::io::ErrorKind::PermissionDenied => CapabilityError::PermissionDenied {
            path: Some(path.display().to_string()),
            detail: "cannot open file for reading".into(),
        },
        _ => CapabilityError::Internal(format!("open failed for {}: {}", path.display(), e)),
    })?;
    let mut reader = BufReader::new(file);
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let read = reader.read(&mut buffer).map_err(|e| {
            CapabilityError::Internal(format!("read failed for {}: {}", path.display(), e))
        })?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    let sha256 = hex::encode(hasher.finalize());

    Ok(json!({
        "path": path.display().to_string(),
        "algorithm": "sha256",
        "sha256": sha256,
        "size_bytes": meta.len(),
    }))
}
