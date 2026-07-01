use async_trait::async_trait;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::Read;

use super::manifest::{CapabilityError, CapabilityManifest};
use super::yq_croc_runtime::{run_yq_croc, YqCrocRole, YqCrocRun};
use super::Capability;

pub struct LinuxTransferCrocSend;

#[async_trait]
impl Capability for LinuxTransferCrocSend {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.croc.send".into(),
            description: "Send a file or directory through the yq-croc transfer runtime.".into(),
            agent_description: Some(
                "Send a local Linux path through yq-croc. Center supplies transfer_id, attempt, \
                 code, source_path, relay, and timeout. Progress comes from yq-croc NDJSON events."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "transfer_id": {"type": "string"},
                    "attempt": {"type": "integer", "minimum": 1},
                    "source_path": {"type": "string"},
                    "code": {"type": "string"},
                    "relay_url": {"type": ["string", "null"]},
                    "timeout_sec": {"type": "integer", "minimum": 60, "maximum": 86400, "default": 3600},
                    "resume_mode": {
                        "type": "string",
                        "enum": ["resume", "overwrite", "fail_if_exists"],
                        "default": "resume"
                    }
                },
                "required": ["transfer_id", "attempt", "source_path", "code"],
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
                json!({"fact": "source.exists", "capability": "linux.transfer.local.stat"}),
                json!({"fact": "source.readable", "capability": "linux.transfer.local.stat"}),
            ],
            required_intent_slots: vec![
                "transfer_id".into(),
                "attempt".into(),
                "source_path".into(),
                "code".into(),
            ],
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
        if !yq_croc_config.allow_send {
            return Err(CapabilityError::PermissionDenied {
                path: None,
                detail: "yq-croc send is not allowed on this node".into(),
            });
        }

        let transfer_id = required_str(&input, "transfer_id")?.to_string();
        let attempt = required_u32(&input, "attempt")?;
        let source_path = required_str(&input, "source_path")?.to_string();
        let code = required_str(&input, "code")?.to_string();
        let relay_url = optional_str(&input, "relay_url");
        let timeout_sec = input
            .get("timeout_sec")
            .and_then(|v| v.as_u64())
            .unwrap_or(3600);
        let resume_mode = input
            .get("resume_mode")
            .and_then(|v| v.as_str())
            .unwrap_or("resume")
            .to_string();
        validate_resume_mode(&resume_mode)?;

        let source = std::path::Path::new(&source_path);
        if !source.exists() {
            return Err(CapabilityError::NotFound { path: source_path });
        }
        let source_metadata = compute_source_metadata(source)
            .map_err(|e| CapabilityError::Internal(format!("failed to stat source: {}", e)))?;

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
                role: crate::transfer_ledger::TransferRole::Sender,
                status: crate::transfer_ledger::TransferStatus::Created,
                code_hash: compute_code_hash(&code),
                relay_url: relay_url
                    .clone()
                    .or_else(|| yq_croc_config.relay_url.clone()),
                source_path: Some(source_path.clone()),
                target_path: None,
                output_dir: None,
                source_size_bytes: source_metadata.size_bytes,
                source_mtime: source_metadata.mtime.clone(),
                source_sha256: source_metadata.sha256.clone(),
                partial_path: None,
                resume_mode: crate::transfer_ledger::ResumeMode::Resume,
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
                role: YqCrocRole::Sender,
                transfer_id: transfer_id.clone(),
                attempt,
                code,
                source_path: Some(source_path.clone()),
                output_dir: None,
                target_path: None,
                relay_url,
                resume_mode,
                expected_size_bytes: source_metadata.size_bytes,
                expected_sha256: source_metadata.sha256.clone(),
                timeout_sec,
                cleanup_on_failure: false,
            },
            ctx,
        )
        .await;

        match run_result {
            Ok(result) => {
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
                    "role": "sender",
                    "size_bytes": source_metadata.size_bytes,
                    "sha256": source_metadata.sha256,
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

fn prepare_ledger_entry(
    ledger: &crate::transfer_ledger::TransferLedger,
    entry: crate::transfer_ledger::TransferLedgerEntry,
) -> Result<(), CapabilityError> {
    if let Some(existing) = ledger
        .get(&entry.transfer_id)
        .map_err(|e| CapabilityError::Internal(format!("ledger lookup failed: {}", e)))?
    {
        if existing.attempt == entry.attempt {
            if existing.status == crate::transfer_ledger::TransferStatus::Succeeded {
                return Err(CapabilityError::FunctionExecutionFailed {
                    message: format!(
                        "transfer {} attempt {} already succeeded",
                        entry.transfer_id, entry.attempt
                    ),
                    exit_code: None,
                    stderr: None,
                });
            }
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

fn validate_resume_mode(value: &str) -> Result<(), CapabilityError> {
    match value {
        "resume" | "overwrite" | "fail_if_exists" => Ok(()),
        _ => Err(CapabilityError::InvalidInput {
            field: "resume_mode".into(),
            message: format!("unknown resume_mode: {}", value),
        }),
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
    let mtime = meta.modified().ok().map(|t| {
        let dt: chrono::DateTime<chrono::Utc> = t.into();
        dt.to_rfc3339()
    });
    let sha256 = if meta.is_file() {
        Some(compute_file_sha256(path)?)
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
