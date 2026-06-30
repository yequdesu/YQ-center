use async_trait::async_trait;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, BufReader};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

/// Maximum characters to keep from stdout/stderr tail.
const MAX_OUTPUT_TAIL: usize = 4000;

pub struct LinuxTransferCrocReceive;

#[async_trait]
impl Capability for LinuxTransferCrocReceive {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.croc.receive".into(),
            description: "Receive a file or directory from another node via croc.".into(),
            agent_description: Some(
                "Receive a file or directory using croc. \
                 Supports resume_mode: resume (default), overwrite, fail_if_exists. \
                 Reports progress via job.event and supports cancellation. \
                 Returns transfer_id, received path, size, and sha256 on completion."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Croc transfer code from sender."
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Directory to receive files into."
                    },
                    "relay_url": {
                        "type": ["string", "null"],
                        "description": "Optional relay URL override."
                    },
                    "timeout_sec": {
                        "type": "integer",
                        "minimum": 60,
                        "maximum": 86400,
                        "default": 3600
                    },
                    "resume_mode": {
                        "type": "string",
                        "enum": ["resume", "overwrite", "fail_if_exists"],
                        "default": "resume",
                        "description": "How to handle existing/partial files."
                    },
                    "expected_sha256": {
                        "type": ["string", "null"],
                        "description": "Optional expected SHA256 for verification."
                    },
                    "transfer_id": {
                        "type": ["string", "null"],
                        "description": "Optional transfer ID for idempotency."
                    }
                },
                "required": ["code", "output_dir"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "maintenance".into(),
            effect: "external".into(),
            timeout_sec: 3600,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "transfer"]
            })),
            resource_keys: Some(vec!["node.transfer".into()]),
            conflict_policy: Some("serialize".into()),
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let config = crate::config::Config::load()
            .map_err(|e| CapabilityError::Internal(format!("config load failed: {}", e)))?;

        let croc_config = &config.transfer.croc;

        if !croc_config.enabled {
            return Err(CapabilityError::FunctionExecutionFailed {
                message: "croc transfer is disabled in config".into(),
                exit_code: None,
                stderr: None,
            });
        }

        if !croc_config.allow_receive {
            return Err(CapabilityError::PermissionDenied {
                path: None,
                detail: "croc receive is not allowed on this node".into(),
            });
        }

        let code = input.get("code").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "code".into(),
                message: "code is required".into(),
            }
        })?;

        let output_dir = input
            .get("output_dir")
            .and_then(|v| v.as_str())
            .ok_or_else(|| CapabilityError::InvalidInput {
                field: "output_dir".into(),
                message: "output_dir is required".into(),
            })?;

        let relay_url = input
            .get("relay_url")
            .and_then(|v| v.as_str())
            .or(croc_config.relay_url.as_deref());

        let timeout_sec = input
            .get("timeout_sec")
            .and_then(|v| v.as_u64())
            .unwrap_or(3600);

        let resume_mode_str = input
            .get("resume_mode")
            .and_then(|v| v.as_str())
            .unwrap_or("resume");

        let expected_sha256 = input.get("expected_sha256").and_then(|v| v.as_str());

        let transfer_id = input
            .get("transfer_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        // Parse resume mode
        let resume_mode = match resume_mode_str {
            "resume" => crate::transfer_ledger::ResumeMode::Resume,
            "overwrite" => crate::transfer_ledger::ResumeMode::Overwrite,
            "fail_if_exists" => crate::transfer_ledger::ResumeMode::FailIfExists,
            _ => {
                return Err(CapabilityError::InvalidInput {
                    field: "resume_mode".into(),
                    message: format!("unknown resume_mode: {}", resume_mode_str),
                })
            }
        };

        // Validate output directory
        let out_path = std::path::Path::new(output_dir);
        if !out_path.exists() {
            std::fs::create_dir_all(out_path).map_err(|e| {
                CapabilityError::FunctionExecutionFailed {
                    message: format!("failed to create output directory: {}", e),
                    exit_code: None,
                    stderr: Some(e.to_string()),
                }
            })?;
        }

        // Check for existing files based on resume_mode
        match resume_mode {
            crate::transfer_ledger::ResumeMode::FailIfExists => {
                let entries: Vec<_> = std::fs::read_dir(out_path)
                    .map_err(|e| CapabilityError::Internal(format!("read_dir failed: {}", e)))?
                    .filter_map(|e| e.ok())
                    .collect();
                if !entries.is_empty() {
                    return Err(CapabilityError::FunctionExecutionFailed {
                        message: format!(
                            "output directory {} is not empty and resume_mode is fail_if_exists",
                            output_dir
                        ),
                        exit_code: None,
                        stderr: None,
                    });
                }
            }
            crate::transfer_ledger::ResumeMode::Overwrite => {
                // Allow overwriting - croc will handle it
            }
            crate::transfer_ledger::ResumeMode::Resume => {
                // Default: try to resume if partial files exist
            }
        }

        // Code hash for ledger
        let code_hash = compute_code_hash(code);

        // Initialize transfer ledger
        let ledger_path = config
            .db_path
            .parent()
            .unwrap_or(std::path::Path::new("."))
            .join("transfers.db");
        let ledger = crate::transfer_ledger::TransferLedger::open(&ledger_path).map_err(|e| {
            CapabilityError::Internal(format!("failed to open transfer ledger: {}", e))
        })?;

        let now = chrono::Utc::now().to_rfc3339();

        // Detect existing partial file before starting
        let partial_path = find_partial_file(out_path);

        // Check idempotency
        if let Some(existing) = ledger
            .get(&transfer_id)
            .map_err(|e| CapabilityError::Internal(format!("ledger lookup failed: {}", e)))?
        {
            match existing.status {
                crate::transfer_ledger::TransferStatus::Succeeded => {
                    return Ok(json!({
                        "transfer_id": existing.transfer_id,
                        "status": "succeeded",
                        "received_path": existing.target_path,
                        "size_bytes": existing.source_size_bytes,
                        "sha256": existing.source_sha256,
                        "started_at": existing.started_at,
                        "completed_at": existing.completed_at,
                        "cached": true,
                    }));
                }
                crate::transfer_ledger::TransferStatus::Failed
                | crate::transfer_ledger::TransferStatus::Cancelled => {
                    ledger.increment_attempt(&transfer_id).map_err(|e| {
                        CapabilityError::Internal(format!("ledger update failed: {}", e))
                    })?;
                }
                _ => {
                    return Err(CapabilityError::FunctionExecutionFailed {
                        message: format!(
                            "transfer {} is already in state {:?}",
                            transfer_id, existing.status
                        ),
                        exit_code: None,
                        stderr: None,
                    });
                }
            }
        } else {
            let entry = crate::transfer_ledger::TransferLedgerEntry {
                transfer_id: transfer_id.clone(),
                role: crate::transfer_ledger::TransferRole::Receiver,
                status: crate::transfer_ledger::TransferStatus::Created,
                code_hash: code_hash.clone(),
                relay_url: relay_url.map(|s| s.to_string()),
                source_path: None,
                target_path: None,
                output_dir: Some(output_dir.to_string()),
                source_size_bytes: None,
                source_mtime: None,
                source_sha256: None,
                partial_path: partial_path.clone(),
                resume_mode,
                attempt_count: 1,
                pid: None,
                started_at: None,
                last_progress_at: None,
                completed_at: None,
                last_error_code: None,
                last_error_message: None,
                created_at: now.clone(),
                updated_at: now.clone(),
            };
            ledger
                .insert(&entry)
                .map_err(|e| CapabilityError::Internal(format!("ledger insert failed: {}", e)))?;
        }

        // Get execution context
        let ctx = crate::execution_context::try_current();

        // Update status to running
        ledger
            .update_status(
                &transfer_id,
                crate::transfer_ledger::TransferStatus::Running,
                None,
                None,
            )
            .map_err(|e| CapabilityError::Internal(format!("ledger update failed: {}", e)))?;

        // Report initial progress
        if let Some(ref ctx) = ctx {
            ctx.report_progress(
                "transfer_started",
                json!({
                    "transfer_id": transfer_id,
                    "role": "receiver",
                    "output_dir": output_dir,
                    "resume_mode": resume_mode_str,
                    "partial_path": partial_path,
                }),
            )
            .await;
        }

        // Build croc command
        // croc v10.4.4 requires CROC_SECRET env var for receive mode
        // Passing code as positional arg doesn't work for receiving
        let binary_path = &croc_config.binary_path;
        ensure_croc_executable(binary_path).await?;
        let mut cmd = tokio::process::Command::new(binary_path);
        cmd.arg("--yes").arg("--quiet"); // reduce output noise

        if let Some(relay) = relay_url {
            cmd.arg("--relay").arg(relay);
        }

        // Set the receive code via environment variable
        cmd.env("CROC_SECRET", code);

        // Set output directory
        cmd.current_dir(output_dir);

        // Spawn as background process
        cmd.stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());

        let mut child = cmd
            .spawn()
            .map_err(|e| CapabilityError::FunctionExecutionFailed {
                message: format!("failed to spawn croc: {}", e),
                exit_code: None,
                stderr: Some(e.to_string()),
            })?;

        let pid = child.id().unwrap_or(0);

        // Update ledger with PID
        ledger
            .update_status(
                &transfer_id,
                crate::transfer_ledger::TransferStatus::Running,
                Some(pid),
                None,
            )
            .map_err(|e| CapabilityError::Internal(format!("ledger update failed: {}", e)))?;

        let started_at = chrono::Utc::now().to_rfc3339();

        // Spawn stdout/stderr readers
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();

        let stdout_handle = tokio::spawn(async move {
            let mut lines = Vec::new();
            if let Some(stdout) = stdout {
                let reader = BufReader::new(stdout);
                let mut line_stream = reader.lines();
                while let Ok(Some(line)) = line_stream.next_line().await {
                    lines.push(redact_sensitive(&line));
                }
            }
            lines
        });

        let stderr_handle = tokio::spawn(async move {
            let mut lines = Vec::new();
            if let Some(stderr) = stderr {
                let reader = BufReader::new(stderr);
                let mut line_stream = reader.lines();
                while let Ok(Some(line)) = line_stream.next_line().await {
                    lines.push(redact_sensitive(&line));
                }
            }
            lines
        });

        // Monitor loop
        let lease_interval = Duration::from_secs(60);
        let progress_interval = Duration::from_secs(30);
        let mut last_lease = tokio::time::Instant::now();
        let mut last_progress = tokio::time::Instant::now();
        let deadline = tokio::time::Instant::now() + Duration::from_secs(timeout_sec);

        let result = loop {
            // Check for cancellation
            if let Some(ref ctx) = ctx {
                if ctx.is_cancelled() {
                    // Collect stdout/stderr before killing
                    let _ = child.kill().await;
                    let _ = child.wait().await;
                    let stdout_lines = stdout_handle.await.unwrap_or_default();
                    let stderr_lines = stderr_handle.await.unwrap_or_default();
                    let stdout_text = truncate_tail(&stdout_lines.join("\n"));
                    let stderr_text = truncate_tail(&stderr_lines.join("\n"));

                    ledger
                        .update_status(
                            &transfer_id,
                            crate::transfer_ledger::TransferStatus::Cancelled,
                            None,
                            Some(("cancelled", "job cancelled by center")),
                        )
                        .map_err(|e| {
                            CapabilityError::Internal(format!("ledger update failed: {}", e))
                        })?;

                    ctx.report_progress(
                        "transfer_cancelled",
                        json!({
                            "transfer_id": transfer_id,
                            "stdout": stdout_text,
                            "stderr": stderr_text,
                        }),
                    )
                    .await;

                    return Err(CapabilityError::Cancelled {
                        message: format!("transfer cancelled: {}", stderr_text),
                    });
                }
            }

            match child.try_wait() {
                Ok(Some(status)) => {
                    let exit_code = status.code();
                    let stdout_lines = stdout_handle.await.unwrap_or_default();
                    let stderr_lines = stderr_handle.await.unwrap_or_default();

                    if status.success() {
                        break Ok((exit_code, stdout_lines, stderr_lines));
                    } else {
                        break Err((exit_code, stdout_lines, stderr_lines));
                    }
                }
                Ok(None) => {
                    let now = tokio::time::Instant::now();

                    if now >= deadline {
                        let _ = child.kill().await;
                        let _ = child.wait().await;
                        let stdout_lines = stdout_handle.await.unwrap_or_default();
                        let stderr_lines = stderr_handle.await.unwrap_or_default();
                        let stdout_text = truncate_tail(&stdout_lines.join("\n"));
                        let stderr_text = truncate_tail(&stderr_lines.join("\n"));

                        ledger
                            .update_status(
                                &transfer_id,
                                crate::transfer_ledger::TransferStatus::Failed,
                                None,
                                Some(("timeout", "transfer timed out")),
                            )
                            .map_err(|e| {
                                CapabilityError::Internal(format!("ledger update failed: {}", e))
                            })?;

                        if let Some(ref ctx) = ctx {
                            ctx.report_progress(
                                "transfer_timeout",
                                json!({
                                    "transfer_id": transfer_id,
                                    "timeout_sec": timeout_sec,
                                    "stdout": stdout_text,
                                    "stderr": stderr_text,
                                }),
                            )
                            .await;
                        }

                        return Err(CapabilityError::Timeout {
                            timeout_sec: timeout_sec as u32,
                        });
                    }

                    if now.duration_since(last_lease) >= lease_interval {
                        if let Some(ref ctx) = ctx {
                            ctx.renew_lease(120).await;
                        }
                        last_lease = now;
                    }

                    if now.duration_since(last_progress) >= progress_interval {
                        ledger.record_progress(&transfer_id).map_err(|e| {
                            CapabilityError::Internal(format!("ledger update failed: {}", e))
                        })?;

                        if let Some(ref ctx) = ctx {
                            ctx.report_progress(
                                "transfer_progress",
                                json!({
                                    "transfer_id": transfer_id,
                                    "pid": pid,
                                    "status": "running",
                                }),
                            )
                            .await;
                        }
                        last_progress = now;
                    }

                    tokio::time::sleep(Duration::from_millis(500)).await;
                }
                Err(e) => {
                    break Err((None, vec![], vec![format!("wait error: {}", e)]));
                }
            }
        };

        match result {
            Ok((exit_code, stdout_lines, stderr_lines)) => {
                // Find received file in output_dir (only files, not directories)
                let received_path = match find_newest_file(out_path) {
                    Some(p) => p,
                    None => {
                        // croc returned success but no file was received
                        let _stdout_text = truncate_tail(&stdout_lines.join("\n"));
                        let stderr_text = truncate_tail(&stderr_lines.join("\n"));
                        let error_message =
                            "croc returned success but no file found in output directory";

                        ledger
                            .update_status(
                                &transfer_id,
                                crate::transfer_ledger::TransferStatus::Failed,
                                None,
                                Some(("received_file_missing", error_message)),
                            )
                            .map_err(|e| {
                                CapabilityError::Internal(format!("ledger update failed: {}", e))
                            })?;

                        return Err(CapabilityError::FunctionExecutionFailed {
                            message: error_message.into(),
                            exit_code,
                            stderr: Some(stderr_text),
                        });
                    }
                };

                let received_path_obj = std::path::Path::new(&received_path);

                // Determine received kind
                let received_kind = if received_path_obj.is_dir() {
                    "directory"
                } else {
                    "file"
                };

                // Compute size and sha256 of received file
                let received_meta = compute_received_metadata(received_path_obj);

                // For directories, skip file-level sha256 verification
                if received_kind == "file" {
                    // Verify expected sha256 if provided
                    if let Some(expected) = expected_sha256 {
                        match received_meta.sha256.as_ref() {
                            Some(actual) if actual != expected => {
                                let _stdout_text = truncate_tail(&stdout_lines.join("\n"));
                                let stderr_text = truncate_tail(&stderr_lines.join("\n"));

                                ledger
                                    .update_status(
                                        &transfer_id,
                                        crate::transfer_ledger::TransferStatus::Failed,
                                        None,
                                        Some((
                                            "checksum_mismatch",
                                            &format!("expected {}, got {}", expected, actual),
                                        )),
                                    )
                                    .map_err(|e| {
                                        CapabilityError::Internal(format!(
                                            "ledger update failed: {}",
                                            e
                                        ))
                                    })?;

                                if let Some(ref ctx) = ctx {
                                    ctx.report_progress(
                                        "transfer_failed",
                                        json!({
                                            "transfer_id": transfer_id,
                                            "error_code": "checksum_mismatch",
                                        }),
                                    )
                                    .await;
                                }

                                return Err(CapabilityError::FunctionExecutionFailed {
                                    message: format!(
                                        "checksum mismatch: expected {}, got {}",
                                        expected, actual
                                    ),
                                    exit_code: None,
                                    stderr: Some(stderr_text),
                                });
                            }
                            _ => {}
                        }
                    }
                }

                // Update ledger to succeeded
                ledger
                    .update_status(
                        &transfer_id,
                        crate::transfer_ledger::TransferStatus::Succeeded,
                        None,
                        None,
                    )
                    .map_err(|e| {
                        CapabilityError::Internal(format!("ledger update failed: {}", e))
                    })?;

                let completed_at = chrono::Utc::now().to_rfc3339();

                if let Some(ref ctx) = ctx {
                    ctx.report_progress(
                        "transfer_completed",
                        json!({
                            "transfer_id": transfer_id,
                            "status": "succeeded",
                            "received_path": received_path,
                            "received_kind": received_kind,
                            "size_bytes": received_meta.size_bytes,
                            "sha256": received_meta.sha256,
                        }),
                    )
                    .await;
                }

                Ok(json!({
                    "transfer_id": transfer_id,
                    "status": "succeeded",
                    "received_path": received_path,
                    "received_kind": received_kind,
                    "size_bytes": received_meta.size_bytes,
                    "sha256": received_meta.sha256,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "exit_code": exit_code,
                    "stdout": truncate_tail(&stdout_lines.join("\n")),
                    "stderr": truncate_tail(&stderr_lines.join("\n")),
                }))
            }
            Err((exit_code, stdout_lines, stderr_lines)) => {
                let stdout_text = truncate_tail(&stdout_lines.join("\n"));
                let stderr_text = truncate_tail(&stderr_lines.join("\n"));
                let error_message = format!("croc receive failed with exit code {:?}", exit_code);

                ledger
                    .update_status(
                        &transfer_id,
                        crate::transfer_ledger::TransferStatus::Failed,
                        None,
                        Some(("croc_failed", &error_message)),
                    )
                    .map_err(|e| {
                        CapabilityError::Internal(format!("ledger update failed: {}", e))
                    })?;

                if let Some(ref ctx) = ctx {
                    ctx.report_progress(
                        "transfer_failed",
                        json!({
                            "transfer_id": transfer_id,
                            "error_code": "croc_failed",
                            "error_message": error_message,
                        }),
                    )
                    .await;
                }

                // Build structured error details per contract
                let error_details = json!({
                    "returncode": exit_code,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "binary_path": binary_path,
                    "relay_url": relay_url,
                    "output_dir": output_dir,
                    "resume_mode": resume_mode_str,
                });

                Err(CapabilityError::FunctionExecutionFailed {
                    message: error_message,
                    exit_code,
                    stderr: Some(serde_json::to_string(&error_details).unwrap_or_default()),
                })
            }
        }
    }
}

/// Redact sensitive data from output lines.
/// Masks croc codes, relay passwords, and tokens.
fn redact_sensitive(line: &str) -> String {
    let lower = line.to_lowercase();

    // Redact lines that contain croc codes
    if lower.contains("receiving") && lower.contains("code") {
        // Mask the code part
        if let Some(pos) = line.find(':') {
            let prefix = &line[..pos + 1];
            return format!("{} [REDACTED]", prefix);
        }
    }
    if lower.contains("code is") || lower.contains("code:") {
        if let Some(pos) = line.find(':') {
            let prefix = &line[..pos + 1];
            return format!("{} [REDACTED]", prefix);
        }
    }

    // Redact relay passwords
    if lower.contains("relay") && (lower.contains("pass") || lower.contains("password")) {
        if let Some(pos) = line.find(':') {
            let prefix = &line[..pos + 1];
            return format!("{} [REDACTED]", prefix);
        }
    }

    line.to_string()
}

/// Truncate output to tail summary (last MAX_OUTPUT_TAIL characters).
fn truncate_tail(text: &str) -> String {
    if text.len() <= MAX_OUTPUT_TAIL {
        text.to_string()
    } else {
        let start = text.len() - MAX_OUTPUT_TAIL;
        // Find a valid char boundary
        let start = text[start..]
            .char_indices()
            .nth(1)
            .map(|(i, _)| start + i)
            .unwrap_or(start);
        format!("...{}", &text[start..])
    }
}

/// Find a partial file in the output directory (croc creates .partial files).
fn find_partial_file(dir: &std::path::Path) -> Option<String> {
    for entry in std::fs::read_dir(dir).ok()?.filter_map(|e| e.ok()) {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.ends_with(".partial") || name.ends_with(".croc") {
            return Some(entry.path().to_string_lossy().to_string());
        }
    }
    None
}

struct ReceivedMetadata {
    size_bytes: Option<u64>,
    sha256: Option<String>,
}

fn compute_received_metadata(path: &std::path::Path) -> ReceivedMetadata {
    if !path.exists() {
        return ReceivedMetadata {
            size_bytes: None,
            sha256: None,
        };
    }

    let meta = std::fs::metadata(path).ok();
    let size_bytes = meta.map(|m| m.len());
    let sha256 = if path.is_file() {
        compute_file_sha256(path).ok()
    } else {
        None
    };

    ReceivedMetadata { size_bytes, sha256 }
}

fn compute_file_sha256(path: &std::path::Path) -> Result<String, std::io::Error> {
    let content = std::fs::read(path)?;
    let mut hasher = Sha256::new();
    hasher.update(&content);
    Ok(hex::encode(hasher.finalize()))
}

fn find_newest_file(dir: &std::path::Path) -> Option<String> {
    let mut newest: Option<(std::time::SystemTime, String)> = None;
    for entry in std::fs::read_dir(dir).ok()?.filter_map(|e| e.ok()) {
        let meta = entry.metadata().ok()?;
        // Only consider files, not directories
        if !meta.is_file() {
            continue;
        }
        let modified = meta.modified().ok()?;
        let path = entry.path().to_string_lossy().to_string();
        match &newest {
            Some((t, _)) if modified > *t => {
                newest = Some((modified, path));
            }
            None => {
                newest = Some((modified, path));
            }
            _ => {}
        }
    }
    newest.map(|(_, p)| p)
}

fn compute_code_hash(code: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(code.as_bytes());
    hex::encode(hasher.finalize())
}

async fn ensure_croc_executable(binary_path: &str) -> Result<(), CapabilityError> {
    if !std::path::Path::new(binary_path).exists() {
        return Err(CapabilityError::InvalidInput {
            field: "binary_path".into(),
            message: format!("croc binary not found at {}", binary_path),
        });
    }

    match tokio::process::Command::new(binary_path)
        .arg("--version")
        .output()
        .await
    {
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => Err(CapabilityError::FunctionExecutionFailed {
            message: format!("croc --version failed with {:?}", output.status.code()),
            exit_code: output.status.code(),
            stderr: Some(String::from_utf8_lossy(&output.stderr).trim().to_string()),
        }),
        Err(e) => Err(CapabilityError::PermissionDenied {
            path: Some(binary_path.into()),
            detail: format!("croc is not executable by daemon user: {}", e),
        }),
    }
}
