use async_trait::async_trait;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::yq_croc_runtime::{run_yq_croc, YqCrocRole, YqCrocRun};
use super::Capability;

pub struct LinuxTransferCrocReceive;

#[async_trait]
impl Capability for LinuxTransferCrocReceive {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.croc.receive".into(),
            description: "Receive a file or directory through the yq-croc transfer runtime.".into(),
            agent_description: Some(
                "Receive a file or directory through yq-croc. Center supplies transfer_id, attempt, \
                 code, target/output path, relay, expected facts, and timeout. Progress comes from \
                 yq-croc NDJSON events."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "transfer_id": {"type": "string"},
                    "attempt": {"type": "integer", "minimum": 1},
                    "code": {"type": "string"},
                    "output_dir": {"type": ["string", "null"]},
                    "target_path": {"type": ["string", "null"]},
                    "relay_url": {"type": ["string", "null"]},
                    "route_policy": {
                        "type": ["string", "null"],
                        "enum": ["auto", "relay_only", "relay_pool", "local_first", "local_only", "direct_ip", null],
                        "default": "auto"
                    },
                    "direct_ip": {"type": ["string", "null"]},
                    "multicast_address": {"type": ["string", "null"]},
                    "timeout_sec": {"type": "integer", "minimum": 60, "maximum": 86400, "default": 3600},
                    "resume_mode": {
                        "type": "string",
                        "enum": ["resume", "overwrite", "fail_if_exists"],
                        "default": "resume"
                    },
                    "expected_sha256": {"type": ["string", "null"]},
                    "expected_size_bytes": {"type": ["integer", "null"], "minimum": 0},
                    "cleanup_on_failure": {"type": "boolean", "default": false}
                },
                "required": ["transfer_id", "attempt", "code"],
                "anyOf": [
                    {"required": ["output_dir"]},
                    {"required": ["target_path"]}
                ],
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "maintenance".into(),
            effect: "external".into(),
            timeout_sec: 3600,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "transfer", "yq-croc"]
            })),
            resource_keys: Some(vec!["node.transfer".into()]),
            conflict_policy: Some("serialize".into()),
            supports_progress: true,
            supports_cancel: true,
            supports_resume: true,
            progress_contract: Some("transfer_progress_v1".into()),
            preconditions: vec![
                json!({"fact": "target.parent_exists", "capability": "linux.transfer.local.stat"}),
                json!({"fact": "target.writable", "capability": "linux.transfer.local.stat"}),
            ],
            required_intent_slots: vec!["transfer_id".into(), "attempt".into(), "code".into(), "target_output_dir_or_target_path".into()],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let config = crate::config::Config::load()
            .map_err(|e| CapabilityError::Internal(format!("config load failed: {}", e)))?;
        let yq_croc_config = &config.transfer.yq_croc;
        if !yq_croc_config.enabled {
            return Err(CapabilityError::FunctionExecutionFailed {
                message: "yq-croc transfer is disabled in config".into(),
                exit_code: None,
                stderr: None,
            });
        }
        if !yq_croc_config.allow_receive {
            return Err(CapabilityError::PermissionDenied {
                path: None,
                detail: "yq-croc receive is not allowed on this node".into(),
            });
        }

        let transfer_id = required_str(&input, "transfer_id")?.to_string();
        let attempt = required_u32(&input, "attempt")?;
        let code = required_str(&input, "code")?.to_string();
        let target_path = optional_str(&input, "target_path");
        let output_dir = optional_str(&input, "output_dir").or_else(|| {
            target_path
                .as_deref()
                .and_then(|p| Path::new(p).parent())
                .map(|p| p.to_string_lossy().to_string())
        });
        let output_dir = output_dir.ok_or_else(|| CapabilityError::InvalidInput {
            field: "output_dir".into(),
            message: "output_dir or target_path is required".into(),
        })?;
        let relay_url = optional_str(&input, "relay_url");
        let route_policy = optional_str(&input, "route_policy");
        let direct_ip = optional_str(&input, "direct_ip");
        let multicast_address = optional_str(&input, "multicast_address");
        let timeout_sec = input
            .get("timeout_sec")
            .and_then(|v| v.as_u64())
            .unwrap_or(3600);
        let resume_mode = input
            .get("resume_mode")
            .and_then(|v| v.as_str())
            .unwrap_or("resume")
            .to_string();
        let resume_mode_ledger = parse_resume_mode(&resume_mode)?;
        let expected_sha256 = optional_str(&input, "expected_sha256");
        let expected_size_bytes = input.get("expected_size_bytes").and_then(|v| v.as_u64());
        let cleanup_on_failure = input
            .get("cleanup_on_failure")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        validate_target(&output_dir, target_path.as_deref(), &resume_mode)?;

        let ledger_path = config
            .db_path
            .parent()
            .unwrap_or(std::path::Path::new("."))
            .join("transfers.db");
        let ledger = crate::transfer_ledger::TransferLedger::open(&ledger_path).map_err(|e| {
            CapabilityError::Internal(format!("failed to open transfer ledger: {}", e))
        })?;
        prepare_ledger_entry(
            &ledger,
            crate::transfer_ledger::TransferLedgerEntry {
                transfer_id: transfer_id.clone(),
                role: crate::transfer_ledger::TransferRole::Receiver,
                status: crate::transfer_ledger::TransferStatus::Created,
                code_hash: compute_code_hash(&code),
                relay_url: relay_url
                    .clone()
                    .or_else(|| yq_croc_config.relay_url.clone()),
                source_path: None,
                target_path: target_path.clone(),
                output_dir: Some(output_dir.clone()),
                source_size_bytes: expected_size_bytes,
                source_mtime: None,
                source_sha256: expected_sha256.clone(),
                partial_path: find_partial_file(Path::new(&output_dir)),
                resume_mode: resume_mode_ledger,
                attempt,
                pid: None,
                started_at: None,
                last_progress_at: None,
                completed_at: None,
                last_error_code: None,
                last_error_message: None,
                created_at: chrono::Utc::now().to_rfc3339(),
                updated_at: chrono::Utc::now().to_rfc3339(),
            },
        )?;

        let ctx = crate::execution_context::try_current();
        ledger
            .update_status(
                &transfer_id,
                crate::transfer_ledger::TransferStatus::Running,
                None,
                None,
            )
            .map_err(|e| CapabilityError::Internal(format!("ledger update failed: {}", e)))?;

        let run_result = run_yq_croc(
            yq_croc_config,
            YqCrocRun {
                role: YqCrocRole::Receiver,
                transfer_id: transfer_id.clone(),
                attempt,
                code,
                source_path: None,
                output_dir: Some(output_dir.clone()),
                target_path: target_path.clone(),
                relay_url,
                route_policy,
                direct_ip,
                multicast_address,
                resume_mode: resume_mode.clone(),
                expected_size_bytes,
                expected_sha256: expected_sha256.clone(),
                timeout_sec,
                cleanup_on_failure,
            },
            ctx,
        )
        .await;

        match run_result {
            Ok(result) => {
                let received_path =
                    finalize_received_path(&output_dir, target_path.as_deref(), &resume_mode)?;
                let received_meta = compute_received_metadata(Path::new(&received_path))?;
                if let Some(expected) = expected_sha256.as_deref() {
                    if received_meta.sha256.as_deref() != Some(expected) {
                        ledger
                            .update_status(
                                &transfer_id,
                                crate::transfer_ledger::TransferStatus::Failed,
                                None,
                                Some((
                                    "integrity_mismatch",
                                    "received file sha256 does not match expected_sha256",
                                )),
                            )
                            .map_err(|e| {
                                CapabilityError::Internal(format!("ledger update failed: {}", e))
                            })?;
                        return Err(CapabilityError::IntegrityMismatch {
                            detail: format!(
                                "expected {}, got {:?}",
                                expected, received_meta.sha256
                            ),
                        });
                    }
                }
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
                Ok(json!({
                    "runtime": "yq-croc",
                    "transfer_id": transfer_id,
                    "attempt": attempt,
                    "status": "succeeded",
                    "role": "receiver",
                    "received_path": received_path,
                    "received_kind": received_meta.kind,
                    "size_bytes": received_meta.size_bytes,
                    "sha256": received_meta.sha256,
                    "started_at": result.started_at,
                    "completed_at": result.completed_at,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout_tail,
                    "stderr": result.stderr_tail,
                }))
            }
            Err(err) => {
                let error_code = err.error_code().to_string();
                let error_message = err.error_message();
                ledger
                    .update_status(
                        &transfer_id,
                        crate::transfer_ledger::TransferStatus::Failed,
                        None,
                        Some((&error_code, &error_message)),
                    )
                    .map_err(|e| {
                        CapabilityError::Internal(format!("ledger update failed: {}", e))
                    })?;
                Err(err)
            }
        }
    }
}

fn validate_target(
    output_dir: &str,
    target_path: Option<&str>,
    resume_mode: &str,
) -> Result<(), CapabilityError> {
    let out = Path::new(output_dir);
    std::fs::create_dir_all(out).map_err(|e| CapabilityError::FunctionExecutionFailed {
        message: format!("failed to create output directory: {}", e),
        exit_code: None,
        stderr: Some(e.to_string()),
    })?;
    if resume_mode == "fail_if_exists" {
        if let Some(target) = target_path {
            if Path::new(target).exists() {
                return Err(CapabilityError::TargetExists {
                    path: target.to_string(),
                });
            }
        } else if dir_has_content(out)? {
            return Err(CapabilityError::TargetExists {
                path: output_dir.to_string(),
            });
        }
    }
    Ok(())
}

fn finalize_received_path(
    output_dir: &str,
    target_path: Option<&str>,
    resume_mode: &str,
) -> Result<String, CapabilityError> {
    if let Some(target) = target_path {
        let target = PathBuf::from(target);
        if target.exists() {
            return Ok(target.to_string_lossy().to_string());
        }
        let received = find_newest_entry(Path::new(output_dir)).ok_or_else(|| {
            CapabilityError::FunctionExecutionFailed {
                message: "yq-croc returned success but no received file was found".into(),
                exit_code: None,
                stderr: None,
            }
        })?;
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(|e| {
                CapabilityError::FunctionExecutionFailed {
                    message: format!("failed to create target parent directory: {}", e),
                    exit_code: None,
                    stderr: Some(e.to_string()),
                }
            })?;
        }
        if target.exists() && resume_mode == "overwrite" {
            remove_existing(&target)?;
        }
        if received != target {
            std::fs::rename(&received, &target).map_err(|e| {
                CapabilityError::FunctionExecutionFailed {
                    message: format!("failed to move received path to target_path: {}", e),
                    exit_code: None,
                    stderr: Some(e.to_string()),
                }
            })?;
        }
        return Ok(target.to_string_lossy().to_string());
    }
    find_newest_entry(Path::new(output_dir))
        .map(|p| p.to_string_lossy().to_string())
        .ok_or_else(|| CapabilityError::FunctionExecutionFailed {
            message: "yq-croc returned success but no received file was found".into(),
            exit_code: None,
            stderr: None,
        })
}

fn prepare_ledger_entry(
    ledger: &crate::transfer_ledger::TransferLedger,
    entry: crate::transfer_ledger::TransferLedgerEntry,
) -> Result<(), CapabilityError> {
    if let Some(existing) = ledger
        .get(&entry.transfer_id)
        .map_err(|e| CapabilityError::Internal(format!("ledger lookup failed: {}", e)))?
    {
        if existing.attempt == entry.attempt {
            return Err(CapabilityError::FunctionExecutionFailed {
                message: format!(
                    "transfer {} attempt {} already exists in state {:?}",
                    entry.transfer_id, entry.attempt, existing.status
                ),
                exit_code: None,
                stderr: None,
            });
        }
        if existing.attempt > entry.attempt {
            return Err(CapabilityError::InvalidInput {
                field: "attempt".into(),
                message: format!(
                    "stale attempt {}; latest local attempt is {}",
                    entry.attempt, existing.attempt
                ),
            });
        }
        if !matches!(
            existing.status,
            crate::transfer_ledger::TransferStatus::Succeeded
                | crate::transfer_ledger::TransferStatus::Failed
                | crate::transfer_ledger::TransferStatus::Cancelled
                | crate::transfer_ledger::TransferStatus::Interrupted
        ) {
            return Err(CapabilityError::FunctionExecutionFailed {
                message: format!(
                    "transfer {} has non-terminal attempt {} in state {:?}",
                    entry.transfer_id, existing.attempt, existing.status
                ),
                exit_code: None,
                stderr: None,
            });
        }
        ledger
            .delete(&entry.transfer_id)
            .map_err(|e| CapabilityError::Internal(format!("ledger delete failed: {}", e)))?;
    }
    ledger
        .insert(&entry)
        .map_err(|e| CapabilityError::Internal(format!("ledger insert failed: {}", e)))?;
    Ok(())
}

fn required_str<'a>(input: &'a Value, field: &str) -> Result<&'a str, CapabilityError> {
    input
        .get(field)
        .and_then(|v| v.as_str())
        .filter(|v| !v.is_empty())
        .ok_or_else(|| CapabilityError::InvalidInput {
            field: field.into(),
            message: format!("{} is required", field),
        })
}

fn required_u32(input: &Value, field: &str) -> Result<u32, CapabilityError> {
    let value =
        input
            .get(field)
            .and_then(|v| v.as_u64())
            .ok_or_else(|| CapabilityError::InvalidInput {
                field: field.into(),
                message: format!("{} is required", field),
            })?;
    if value == 0 || value > u32::MAX as u64 {
        return Err(CapabilityError::InvalidInput {
            field: field.into(),
            message: format!("{} must be between 1 and {}", field, u32::MAX),
        });
    }
    Ok(value as u32)
}

fn optional_str(input: &Value, field: &str) -> Option<String> {
    input
        .get(field)
        .and_then(|v| v.as_str())
        .filter(|v| !v.is_empty())
        .map(|v| v.to_string())
}

fn parse_resume_mode(value: &str) -> Result<crate::transfer_ledger::ResumeMode, CapabilityError> {
    match value {
        "resume" => Ok(crate::transfer_ledger::ResumeMode::Resume),
        "overwrite" => Ok(crate::transfer_ledger::ResumeMode::Overwrite),
        "fail_if_exists" => Ok(crate::transfer_ledger::ResumeMode::FailIfExists),
        _ => Err(CapabilityError::InvalidInput {
            field: "resume_mode".into(),
            message: format!("unknown resume_mode: {}", value),
        }),
    }
}

fn dir_has_content(path: &Path) -> Result<bool, CapabilityError> {
    let entries = std::fs::read_dir(path)
        .map_err(|e| CapabilityError::Internal(format!("read_dir failed: {}", e)))?;
    Ok(entries.filter_map(|e| e.ok()).next().is_some())
}

fn find_partial_file(dir: &Path) -> Option<String> {
    for entry in std::fs::read_dir(dir).ok()?.filter_map(|e| e.ok()) {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.ends_with(".partial") || name.ends_with(".croc") {
            return Some(entry.path().to_string_lossy().to_string());
        }
    }
    None
}

fn find_newest_entry(dir: &Path) -> Option<PathBuf> {
    let mut newest: Option<(std::time::SystemTime, PathBuf)> = None;
    for entry in std::fs::read_dir(dir).ok()?.filter_map(|e| e.ok()) {
        let meta = entry.metadata().ok()?;
        let modified = meta.modified().ok()?;
        let path = entry.path();
        match &newest {
            Some((time, _)) if modified <= *time => {}
            _ => newest = Some((modified, path)),
        }
    }
    newest.map(|(_, p)| p)
}

fn remove_existing(path: &Path) -> Result<(), CapabilityError> {
    if path.is_dir() {
        std::fs::remove_dir_all(path)
    } else {
        std::fs::remove_file(path)
    }
    .map_err(|e| CapabilityError::FunctionExecutionFailed {
        message: format!("failed to remove existing target: {}", e),
        exit_code: None,
        stderr: Some(e.to_string()),
    })
}

struct ReceivedMetadata {
    kind: &'static str,
    size_bytes: Option<u64>,
    sha256: Option<String>,
}

fn compute_received_metadata(path: &Path) -> Result<ReceivedMetadata, CapabilityError> {
    if !path.exists() {
        return Err(CapabilityError::NotFound {
            path: path.to_string_lossy().to_string(),
        });
    }
    let meta = std::fs::metadata(path)
        .map_err(|e| CapabilityError::Internal(format!("failed to stat received path: {}", e)))?;
    let kind = if meta.is_dir() { "directory" } else { "file" };
    let size_bytes = if meta.is_file() {
        Some(meta.len())
    } else {
        None
    };
    let sha256 = if meta.is_file() {
        Some(
            compute_file_sha256(path)
                .map_err(|e| CapabilityError::Internal(format!("sha256 failed: {}", e)))?,
        )
    } else {
        None
    };
    Ok(ReceivedMetadata {
        kind,
        size_bytes,
        sha256,
    })
}

fn compute_file_sha256(path: &Path) -> Result<String, std::io::Error> {
    let mut file = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = [0_u8; 1024 * 1024];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hex::encode(hasher.finalize()))
}

fn compute_code_hash(code: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(code.as_bytes());
    hex::encode(hasher.finalize())
}
