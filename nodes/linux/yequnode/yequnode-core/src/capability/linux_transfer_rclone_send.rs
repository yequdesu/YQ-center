use std::path::{Path, PathBuf};
use std::time::Duration;

use async_trait::async_trait;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::process::Command;

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;
use crate::config::Config;

pub struct LinuxTransferRcloneSend;

#[async_trait]
impl Capability for LinuxTransferRcloneSend {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.rclone.send".into(),
            description: "Push a local file to a managed rclone SFTP receiver.".into(),
            agent_description: Some(
                "Send one local file to the receiver endpoint created by transfer.rclone.receive."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "transfer_id": {"type": "string"},
                    "source_path": {"type": "string"},
                    "target_endpoint": {"type": "object"},
                    "target_payload_path": {"type": "string"},
                    "timeout_sec": {"type": "integer", "default": 3600},
                    "expected_size_bytes": {"type": "integer"},
                    "expected_sha256": {"type": "string"}
                },
                "required": ["transfer_id", "source_path", "target_endpoint", "target_payload_path"],
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
            progress_contract: Some("phase, bytes_transferred, total_bytes".into()),
            preconditions: vec![json!({"fact": "source.readable", "required": true})],
            required_intent_slots: vec![
                "transfer_id".into(),
                "source_path".into(),
                "target_endpoint".into(),
            ],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let config = Config::load().map_err(|e| CapabilityError::Internal(e.to_string()))?;
        let rclone = &config.transfer.rclone;
        if !rclone.enabled || !rclone.allow_send {
            return Err(CapabilityError::PermissionDenied {
                path: None,
                detail: "rclone send is disabled by node config".into(),
            });
        }

        let transfer_id = required_string(&input, "transfer_id")?;
        let source_path = PathBuf::from(required_string(&input, "source_path")?);
        let target_payload_path = required_string(&input, "target_payload_path")?;
        let endpoint =
            input
                .get("target_endpoint")
                .ok_or_else(|| CapabilityError::InvalidInput {
                    field: "target_endpoint".into(),
                    message: "target_endpoint is required".into(),
                })?;
        let timeout_sec = input
            .get("timeout_sec")
            .and_then(|v| v.as_u64())
            .unwrap_or(3600);

        if !source_path.is_file() {
            return Err(CapabilityError::NotFound {
                path: source_path.to_string_lossy().to_string(),
            });
        }

        let size_bytes = source_path
            .metadata()
            .map_err(|e| CapabilityError::Internal(format!("stat failed: {e}")))?
            .len();
        if let Some(expected) = input.get("expected_size_bytes").and_then(|v| v.as_u64()) {
            if expected != size_bytes {
                return Err(CapabilityError::IntegrityMismatch {
                    detail: format!("source size {size_bytes} does not match expected {expected}"),
                });
            }
        }

        let sha256 = compute_file_sha256(&source_path)?;
        if let Some(expected) = input.get("expected_sha256").and_then(|v| v.as_str()) {
            if !expected.eq_ignore_ascii_case(&sha256) {
                return Err(CapabilityError::IntegrityMismatch {
                    detail: "source sha256 does not match expected_sha256".into(),
                });
            }
        }

        report_progress(&transfer_id, "preparing", 5.0, "preparing rclone sender").await;
        let workspace = rclone.temp_dir.join(&transfer_id).join("sender");
        std::fs::create_dir_all(&workspace)
            .map_err(|e| CapabilityError::Internal(format!("workspace create failed: {e}")))?;
        let config_path = write_rclone_config(&rclone.binary_path, &workspace, endpoint).await?;

        report_progress(&transfer_id, "transferring", 15.0, "uploading payload").await;
        run_rclone_copyto(
            &rclone.binary_path,
            &config_path,
            &source_path,
            &format!("target:{}", target_payload_path),
            timeout_sec,
        )
        .await?;

        let marker_dir = workspace.join(".yequ-transfer").join(&transfer_id);
        std::fs::create_dir_all(&marker_dir)
            .map_err(|e| CapabilityError::Internal(format!("marker create failed: {e}")))?;
        let marker_path = marker_dir.join("done.json");
        std::fs::write(
            &marker_path,
            json!({
                "transfer_id": transfer_id,
                "target_payload_path": target_payload_path,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "completed_at": chrono::Utc::now().to_rfc3339(),
            })
            .to_string(),
        )
        .map_err(|e| CapabilityError::Internal(format!("marker write failed: {e}")))?;

        report_progress(
            &transfer_id,
            "finalizing",
            90.0,
            "uploading completion marker",
        )
        .await;
        run_rclone_copyto(
            &rclone.binary_path,
            &config_path,
            &marker_path,
            &format!("target:.yequ-transfer/{transfer_id}/done.json"),
            timeout_sec,
        )
        .await?;

        report_progress(&transfer_id, "succeeded", 100.0, "send complete").await;
        Ok(json!({
            "transfer_id": transfer_id,
            "source_path": source_path.to_string_lossy(),
            "target_payload_path": target_payload_path,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }))
    }
}

async fn write_rclone_config(
    binary_path: &str,
    workspace: &Path,
    endpoint: &Value,
) -> Result<PathBuf, CapabilityError> {
    let host = endpoint_string(endpoint, "host")?;
    let port = endpoint.get("port").and_then(|v| v.as_u64()).unwrap_or(22);
    let username = endpoint_string(endpoint, "username")?;
    let password = endpoint_string(endpoint, "password")?;
    let obscured = obscure_password(binary_path, &password).await?;
    let path = workspace.join("rclone.conf");
    std::fs::write(
        &path,
        format!(
            "[target]\ntype = sftp\nhost = {host}\nport = {port}\nuser = {username}\npass = {obscured}\n"
        ),
    )
    .map_err(|e| CapabilityError::Internal(format!("rclone config write failed: {e}")))?;
    Ok(path)
}

async fn obscure_password(binary_path: &str, password: &str) -> Result<String, CapabilityError> {
    let output = Command::new(binary_path)
        .arg("obscure")
        .arg(password)
        .output()
        .await
        .map_err(|e| CapabilityError::FunctionExecutionFailed {
            message: format!("failed to run rclone obscure: {e}"),
            exit_code: None,
            stderr: None,
        })?;
    if !output.status.success() {
        return Err(CapabilityError::FunctionExecutionFailed {
            message: "rclone obscure failed".into(),
            exit_code: output.status.code(),
            stderr: Some(String::from_utf8_lossy(&output.stderr).to_string()),
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

async fn run_rclone_copyto(
    binary_path: &str,
    config_path: &Path,
    source: &Path,
    target: &str,
    timeout_sec: u64,
) -> Result<(), CapabilityError> {
    let output = tokio::time::timeout(
        Duration::from_secs(timeout_sec),
        Command::new(binary_path)
            .arg("--config")
            .arg(config_path)
            .arg("copyto")
            .arg(source)
            .arg(target)
            .output(),
    )
    .await
    .map_err(|_| CapabilityError::Timeout {
        timeout_sec: timeout_sec as u32,
    })?
    .map_err(|e| CapabilityError::FunctionExecutionFailed {
        message: format!("failed to spawn rclone copyto: {e}"),
        exit_code: None,
        stderr: None,
    })?;
    if output.status.success() {
        Ok(())
    } else {
        Err(CapabilityError::FunctionExecutionFailed {
            message: "rclone copyto failed".into(),
            exit_code: output.status.code(),
            stderr: Some(String::from_utf8_lossy(&output.stderr).to_string()),
        })
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

async fn report_progress(transfer_id: &str, phase: &str, pct: f64, message: &str) {
    if let Some(ctx) = crate::execution_context::try_current() {
        ctx.report_progress(
            "job.progress",
            json!({
                "transfer_id": transfer_id,
                "phase": phase,
                "progress_pct": pct,
                "progress_message": message,
                "progress_source": "rclone_sender"
            }),
        )
        .await;
    }
}

fn endpoint_string(endpoint: &Value, field: &str) -> Result<String, CapabilityError> {
    endpoint
        .get(field)
        .and_then(|v| v.as_str())
        .filter(|value| !value.trim().is_empty())
        .map(|value| value.to_string())
        .ok_or_else(|| CapabilityError::InvalidInput {
            field: format!("target_endpoint.{field}"),
            message: format!("{field} is required"),
        })
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
