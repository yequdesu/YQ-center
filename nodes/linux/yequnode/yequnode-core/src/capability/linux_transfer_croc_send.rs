use async_trait::async_trait;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, BufReader};

use super::croc_command::croc_supports_flag;
use super::croc_progress::collect_stderr_lines_with_progress;
use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

/// Maximum characters to keep from stdout/stderr tail.
const MAX_OUTPUT_TAIL: usize = 4000;

pub struct LinuxTransferCrocSend;

#[async_trait]
impl Capability for LinuxTransferCrocSend {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.croc.send".into(),
            description: "Send a file or directory to another node via croc.".into(),
            agent_description: Some(
                "Send a file or directory to another node using croc. \
                 Requires a croc code and the source path. \
                 Reports progress via job.event and supports cancellation. \
                 Returns transfer_id, process status, size, and sha256 on completion."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Source file or directory path to send."
                    },
                    "code": {
                        "type": "string",
                        "description": "Croc transfer code shared with receiver."
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
                    "transfer_id": {
                        "type": ["string", "null"],
                        "description": "Optional transfer ID for idempotency."
                    }
                },
                "required": ["path", "code"],
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
            supports_progress: true,
            supports_cancel: true,
            supports_resume: true,
            progress_contract: Some("transfer_progress_v1".into()),
            preconditions: vec![
                json!({"fact": "source.exists", "capability": "linux.transfer.local.stat"}),
                json!({"fact": "source.readable", "capability": "linux.transfer.local.stat"}),
            ],
            required_intent_slots: vec!["source_path".into(), "code".into()],
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

        if !croc_config.allow_send {
            return Err(CapabilityError::PermissionDenied {
                path: None,
                detail: "croc send is not allowed on this node".into(),
            });
        }

        let path_str = input.get("path").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "path".into(),
                message: "path is required".into(),
            }
        })?;

        let code = input.get("code").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "code".into(),
                message: "code is required".into(),
            }
        })?;

        let relay_url = input
            .get("relay_url")
            .and_then(|v| v.as_str())
            .or(croc_config.relay_url.as_deref());

        let timeout_sec = input
            .get("timeout_sec")
            .and_then(|v| v.as_u64())
            .unwrap_or(3600);

        let transfer_id = input
            .get("transfer_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        // Validate source path exists
        let source_path = std::path::Path::new(path_str);
        if !source_path.exists() {
            return Err(CapabilityError::InvalidInput {
                field: "path".into(),
                message: format!("path does not exist: {}", path_str),
            });
        }

        // Compute source metadata
        let source_metadata = compute_source_metadata(source_path)
            .map_err(|e| CapabilityError::Internal(format!("failed to stat source: {}", e)))?;

        // Code hash for ledger (don't store plaintext)
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

        // Check idempotency - if transfer_id exists and is terminal, return cached result
        if let Some(existing) = ledger
            .get(&transfer_id)
            .map_err(|e| CapabilityError::Internal(format!("ledger lookup failed: {}", e)))?
        {
            match existing.status {
                crate::transfer_ledger::TransferStatus::Succeeded => {
                    return Ok(json!({
                        "transfer_id": existing.transfer_id,
                        "status": "succeeded",
                        "size_bytes": existing.source_size_bytes,
                        "sha256": existing.source_sha256,
                        "started_at": existing.started_at,
                        "completed_at": existing.completed_at,
                        "cached": true,
                    }));
                }
                crate::transfer_ledger::TransferStatus::Failed
                | crate::transfer_ledger::TransferStatus::Cancelled => {
                    // Allow retry - increment attempt
                    ledger.increment_attempt(&transfer_id).map_err(|e| {
                        CapabilityError::Internal(format!("ledger update failed: {}", e))
                    })?;
                }
                _ => {
                    // Already running or created - return current state
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
            // Insert new entry
            let entry = crate::transfer_ledger::TransferLedgerEntry {
                transfer_id: transfer_id.clone(),
                role: crate::transfer_ledger::TransferRole::Sender,
                status: crate::transfer_ledger::TransferStatus::Created,
                code_hash: code_hash.clone(),
                relay_url: relay_url.map(|s| s.to_string()),
                source_path: Some(path_str.to_string()),
                target_path: None,
                output_dir: None,
                source_size_bytes: source_metadata.size_bytes,
                source_mtime: source_metadata.mtime.clone(),
                source_sha256: source_metadata.sha256.clone(),
                partial_path: None,
                resume_mode: crate::transfer_ledger::ResumeMode::Resume,
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

        // Get execution context for progress reporting and cancellation
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
                    "role": "sender",
                    "path": path_str,
                    "size_bytes": source_metadata.size_bytes,
                }),
            )
            .await;
        }

        // Build croc command
        // croc v10.4.4: use CROC_SECRET env var for custom code
        // CROC_SECRET=<code> croc --yes send <file>
        // Do not use --quiet: croc's byte-level progress is emitted on stderr.
        let binary_path = &croc_config.binary_path;
        ensure_croc_executable(binary_path).await?;
        let mut cmd = tokio::process::Command::new(binary_path);
        cmd.arg("--yes"); // auto-accept (global)

        if croc_supports_flag(binary_path, "--disable-clipboard").await {
            cmd.arg("--disable-clipboard");
        }

        cmd.arg("send").arg(path_str);

        if let Some(relay) = relay_url {
            cmd.arg("--relay").arg(relay);
        }

        // Set the transfer code via environment variable
        cmd.env("CROC_SECRET", code);

        // Spawn as background process
        cmd.stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
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

        // Spawn stdout/stderr readers for progress monitoring
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

        let stderr_ctx = ctx.clone();
        let stderr_progress_payload = json!({
            "transfer_id": transfer_id.clone(),
            "role": "sender",
            "phase": "transferring",
            "pid": pid,
            "total_bytes": source_metadata.size_bytes,
            "progress_source": "croc_stderr",
        });
        let stderr_handle = tokio::spawn(async move {
            if let Some(stderr) = stderr {
                collect_stderr_lines_with_progress(
                    stderr,
                    stderr_ctx,
                    stderr_progress_payload,
                    redact_sensitive,
                )
                .await
            } else {
                Vec::new()
            }
        });

        // Monitor loop: check for completion, cancellation, and send periodic updates
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

            // Check if process has exited
            match child.try_wait() {
                Ok(Some(status)) => {
                    // Process exited
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
                    // Still running - send periodic updates
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

                    // Sleep briefly before checking again
                    tokio::time::sleep(Duration::from_millis(500)).await;
                }
                Err(e) => {
                    break Err((None, vec![], vec![format!("wait error: {}", e)]));
                }
            }
        };

        match result {
            Ok((exit_code, stdout_lines, stderr_lines)) => {
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
                            "size_bytes": source_metadata.size_bytes,
                            "sha256": source_metadata.sha256,
                        }),
                    )
                    .await;
                }

                Ok(json!({
                    "transfer_id": transfer_id,
                    "status": "succeeded",
                    "size_bytes": source_metadata.size_bytes,
                    "sha256": source_metadata.sha256,
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
                let error_message = format!("croc send failed with exit code {:?}", exit_code);

                // Update ledger to failed
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
                    "output_dir": null,
                    "resume_mode": null,
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
/// Masks croc codes (typically 4-6 word phrases after "Sending" or similar),
/// relay passwords, and tokens.
fn redact_sensitive(line: &str) -> String {
    let lower = line.to_lowercase();

    // Redact lines that contain croc codes
    // croc typically outputs: "Sending (file) to (code)" or "Code is: (code)"
    if lower.contains("sending") && lower.contains("to") {
        // Mask the code part after "to"
        if let Some(pos) = line.rfind(" to ") {
            let prefix = &line[..pos + 4];
            return format!("{}[REDACTED]", prefix);
        }
    }
    if lower.contains("code is") || lower.contains("code:") {
        // Mask the entire code value
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

struct SourceMetadata {
    size_bytes: Option<u64>,
    mtime: Option<String>,
    sha256: Option<String>,
}

fn compute_source_metadata(path: &std::path::Path) -> Result<SourceMetadata, std::io::Error> {
    let meta = std::fs::metadata(path)?;
    let size_bytes = if meta.is_file() {
        Some(meta.len())
    } else {
        None
    };
    let mtime = meta.modified().ok().and_then(|t| {
        let dt: chrono::DateTime<chrono::Utc> = t.into();
        Some(dt.to_rfc3339())
    });

    // Only compute sha256 for files, not directories
    let sha256 = if meta.is_file() {
        compute_file_sha256(path).ok()
    } else {
        None
    };

    Ok(SourceMetadata {
        size_bytes,
        mtime,
        sha256,
    })
}

fn compute_file_sha256(path: &std::path::Path) -> Result<String, std::io::Error> {
    let content = std::fs::read(path)?;
    let mut hasher = Sha256::new();
    hasher.update(&content);
    Ok(hex::encode(hasher.finalize()))
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
