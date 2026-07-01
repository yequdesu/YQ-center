use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::process::Command;

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;
use crate::config::Config;

pub struct LinuxTransferRcloneReceive;

#[async_trait]
impl Capability for LinuxTransferRcloneReceive {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.rclone.receive".into(),
            description: "Expose a managed rclone SFTP receiver for one transfer.".into(),
            agent_description: Some(
                "Start a one-transfer SFTP endpoint and finalize the received payload locally."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "transfer_id": {"type": "string"},
                    "output_dir": {"type": "string"},
                    "target_path": {"type": "string"},
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                    "conflict_mode": {"type": "string", "enum": ["fail_if_exists", "overwrite", "reuse_complete"]},
                    "timeout_sec": {"type": "integer", "default": 3600},
                    "expected_size_bytes": {"type": "integer"},
                    "expected_sha256": {"type": "string"},
                    "cleanup_on_failure": {"type": "boolean", "default": false}
                },
                "required": ["transfer_id", "output_dir", "username", "password", "conflict_mode"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "maintenance".into(),
            effect: "external".into(),
            timeout_sec: 3600,
            idempotency: None,
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "transfer"]
            })),
            resource_keys: Some(vec!["node:transfer".into()]),
            conflict_policy: Some("reject_if_running".into()),
            supports_progress: true,
            supports_cancel: true,
            supports_resume: false,
            progress_contract: Some("receiver_ready, endpoint, phase, bytes_transferred".into()),
            preconditions: vec![json!({"fact": "target.writable", "required": true})],
            required_intent_slots: vec![
                "transfer_id".into(),
                "output_dir".into(),
                "conflict_mode".into(),
            ],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let config = Config::load().map_err(|e| CapabilityError::Internal(e.to_string()))?;
        let rclone = &config.transfer.rclone;
        if !rclone.enabled || !rclone.allow_receive {
            return Err(CapabilityError::PermissionDenied {
                path: None,
                detail: "rclone receive is disabled by node config".into(),
            });
        }

        let transfer_id = required_string(&input, "transfer_id")?;
        let output_dir = PathBuf::from(required_string(&input, "output_dir")?);
        let target_path = input
            .get("target_path")
            .and_then(|v| v.as_str())
            .filter(|value| !value.trim().is_empty())
            .map(PathBuf::from);
        let username = required_string(&input, "username")?;
        let password = required_string(&input, "password")?;
        let conflict_mode = required_string(&input, "conflict_mode")?;
        let timeout_sec = input
            .get("timeout_sec")
            .and_then(|v| v.as_u64())
            .unwrap_or(3600);
        let cleanup_on_failure = input
            .get("cleanup_on_failure")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        std::fs::create_dir_all(&output_dir).map_err(|e| CapabilityError::PermissionDenied {
            path: Some(output_dir.to_string_lossy().to_string()),
            detail: e.to_string(),
        })?;

        let workspace = rclone.temp_dir.join(&transfer_id);
        let payload_dir = workspace.join("payload");
        std::fs::create_dir_all(&payload_dir)
            .map_err(|e| CapabilityError::Internal(format!("workspace create failed: {e}")))?;

        let bind_host = rclone.bind_host.as_deref().unwrap_or("0.0.0.0");
        let advertise_host = rclone
            .advertise_host
            .clone()
            .or_else(|| local_hostname().ok())
            .ok_or_else(|| CapabilityError::InvalidInput {
                field: "advertise_host".into(),
                message: "transfer.rclone.advertise_host is required".into(),
            })?;
        let addr = format!("{bind_host}:{}", rclone.listen_port);
        let mut child = Command::new(&rclone.binary_path)
            .arg("serve")
            .arg("sftp")
            .arg(&workspace)
            .arg("--addr")
            .arg(&addr)
            .arg("--user")
            .arg(&username)
            .arg("--pass")
            .arg(&password)
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| CapabilityError::FunctionExecutionFailed {
                message: format!("failed to spawn rclone serve sftp: {e}"),
                exit_code: None,
                stderr: None,
            })?;

        report_ready(&transfer_id, &advertise_host, rclone.listen_port, &username).await;

        let marker_path = workspace
            .join(".yequ-transfer")
            .join(&transfer_id)
            .join("done.json");
        let deadline = Instant::now() + Duration::from_secs(timeout_sec);
        let result = loop {
            if let Some(ctx) = crate::execution_context::try_current() {
                if ctx.is_cancelled() {
                    break Err(CapabilityError::Cancelled {
                        message: "transfer receive cancelled".into(),
                    });
                }
            }
            if marker_path.exists() {
                break finalize_payload(
                    &workspace,
                    &marker_path,
                    &output_dir,
                    target_path.as_deref(),
                    &conflict_mode,
                    input.get("expected_size_bytes").and_then(|v| v.as_u64()),
                    input.get("expected_sha256").and_then(|v| v.as_str()),
                );
            }
            if let Ok(Some(status)) = child.try_wait() {
                break Err(CapabilityError::FunctionExecutionFailed {
                    message: "rclone serve sftp exited before done marker".into(),
                    exit_code: status.code(),
                    stderr: None,
                });
            }
            if Instant::now() >= deadline {
                break Err(CapabilityError::Timeout {
                    timeout_sec: timeout_sec as u32,
                });
            }
            report_waiting(&transfer_id, &marker_path).await;
            tokio::time::sleep(Duration::from_secs(2)).await;
        };

        let _ = child.kill().await;
        if result.is_err() && cleanup_on_failure {
            let _ = std::fs::remove_dir_all(&workspace);
        }
        result
    }
}

fn finalize_payload(
    workspace: &Path,
    marker_path: &Path,
    output_dir: &Path,
    target_path: Option<&Path>,
    conflict_mode: &str,
    expected_size: Option<u64>,
    expected_sha256: Option<&str>,
) -> Result<Value, CapabilityError> {
    let marker_text = std::fs::read_to_string(marker_path)
        .map_err(|e| CapabilityError::Internal(format!("marker read failed: {e}")))?;
    let marker: Value = serde_json::from_str(&marker_text)
        .map_err(|e| CapabilityError::Internal(format!("marker parse failed: {e}")))?;
    let payload_rel = marker
        .get("target_payload_path")
        .and_then(|v| v.as_str())
        .ok_or_else(|| CapabilityError::Internal("marker missing target_payload_path".into()))?;
    let payload_path = workspace.join(payload_rel);
    if !payload_path.is_file() {
        return Err(CapabilityError::NotFound {
            path: payload_path.to_string_lossy().to_string(),
        });
    }

    let final_path = target_path
        .map(PathBuf::from)
        .unwrap_or_else(|| output_dir.join(payload_path.file_name().unwrap_or_default()));
    if final_path.exists() {
        if conflict_mode == "fail_if_exists" {
            return Err(CapabilityError::TargetExists {
                path: final_path.to_string_lossy().to_string(),
            });
        }
        if conflict_mode == "reuse_complete"
            && target_matches(&final_path, expected_size, expected_sha256)?
        {
            return Ok(json!({
                "target_path": final_path.to_string_lossy(),
                "reused_existing": true,
                "size_bytes": expected_size,
                "sha256": expected_sha256,
            }));
        }
        if conflict_mode == "overwrite" {
            std::fs::remove_file(&final_path).map_err(|e| CapabilityError::PermissionDenied {
                path: Some(final_path.to_string_lossy().to_string()),
                detail: e.to_string(),
            })?;
        }
    }
    if let Some(parent) = final_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| CapabilityError::PermissionDenied {
            path: Some(parent.to_string_lossy().to_string()),
            detail: e.to_string(),
        })?;
    }
    std::fs::rename(&payload_path, &final_path)
        .or_else(|_| {
            std::fs::copy(&payload_path, &final_path).map(|_| {
                let _ = std::fs::remove_file(&payload_path);
            })
        })
        .map_err(|e| CapabilityError::PermissionDenied {
            path: Some(final_path.to_string_lossy().to_string()),
            detail: e.to_string(),
        })?;

    let size_bytes = final_path
        .metadata()
        .map_err(|e| CapabilityError::Internal(format!("final stat failed: {e}")))?
        .len();
    let sha256 = compute_file_sha256(&final_path)?;
    if let Some(expected) = expected_size {
        if expected != size_bytes {
            return Err(CapabilityError::IntegrityMismatch {
                detail: format!("target size {size_bytes} does not match expected {expected}"),
            });
        }
    }
    if let Some(expected) = expected_sha256 {
        if !expected.eq_ignore_ascii_case(&sha256) {
            return Err(CapabilityError::IntegrityMismatch {
                detail: "target sha256 does not match expected_sha256".into(),
            });
        }
    }
    Ok(json!({
        "target_path": final_path.to_string_lossy(),
        "reused_existing": false,
        "size_bytes": size_bytes,
        "sha256": sha256,
    }))
}

fn target_matches(
    path: &Path,
    expected_size: Option<u64>,
    expected_sha256: Option<&str>,
) -> Result<bool, CapabilityError> {
    if let Some(size) = expected_size {
        if path.metadata().map(|m| m.len()).unwrap_or_default() != size {
            return Ok(false);
        }
    }
    if let Some(sha256) = expected_sha256 {
        return Ok(sha256.eq_ignore_ascii_case(&compute_file_sha256(path)?));
    }
    Ok(expected_size.is_some())
}

async fn report_ready(transfer_id: &str, host: &str, port: u16, username: &str) {
    if let Some(ctx) = crate::execution_context::try_current() {
        ctx.report_progress(
            "job.progress",
            json!({
                "transfer_id": transfer_id,
                "receiver_ready": true,
                "phase": "receiver_ready",
                "progress_pct": 5.0,
                "progress_message": "rclone receiver ready",
                "progress_source": "rclone_receiver_ready",
                "endpoint": {
                    "host": host,
                    "port": port,
                    "username": username,
                    "remote_dir": "payload"
                }
            }),
        )
        .await;
    }
}

async fn report_waiting(transfer_id: &str, marker_path: &Path) {
    if let Some(ctx) = crate::execution_context::try_current() {
        ctx.report_progress(
            "job.progress",
            json!({
                "transfer_id": transfer_id,
                "phase": "receiving",
                "progress_pct": 10.0,
                "progress_message": "waiting for rclone payload",
                "progress_source": "rclone_receiver",
                "done_marker": marker_path.to_string_lossy(),
            }),
        )
        .await;
    }
}

fn compute_file_sha256(path: &Path) -> Result<String, CapabilityError> {
    let content = std::fs::read(path).map_err(|e| CapabilityError::PermissionDenied {
        path: Some(path.to_string_lossy().to_string()),
        detail: e.to_string(),
    })?;
    let mut hasher = Sha256::new();
    hasher.update(&content);
    Ok(hex::encode(hasher.finalize()))
}

fn local_hostname() -> Result<String, std::io::Error> {
    let text = std::fs::read_to_string("/etc/hostname")?;
    Ok(text.trim().to_string())
}

fn required_string(input: &Value, field: &str) -> Result<String, CapabilityError> {
    input
        .get(field)
        .and_then(|v| v.as_str())
        .filter(|value| !value.trim().is_empty())
        .map(|value| value.to_string())
        .ok_or_else(|| CapabilityError::InvalidInput {
            field: field.into(),
            message: format!("{field} is required"),
        })
}
