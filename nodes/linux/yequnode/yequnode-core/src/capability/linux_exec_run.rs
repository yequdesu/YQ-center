use std::process::Stdio;
use std::time::Instant;

use async_trait::async_trait;
use serde_json::{json, Value};
use tokio::process::Command;
use tokio::time::{timeout, Duration};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxExecRun;

const MAX_STDOUT_BYTES: usize = 65_536;
const MAX_STDERR_BYTES: usize = 8_192;
const MAX_TIMEOUT_SEC: u64 = 30;

#[async_trait]
impl Capability for LinuxExecRun {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.exec.run".into(),
            description: "Run one Linux command string under a declared execution profile.".into(),
            agent_description: Some(
                "Use for simple Linux command-line diagnostics or controlled write tasks. Prefer Product/Core capabilities for files, logs, transfer, artifacts, services, and structured system facts."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "string",
                        "enum": ["user.readonly", "user.write", "admin.readonly", "admin.write"]
                    },
                    "command": { "type": "string", "minLength": 1 },
                    "reason": { "type": "string", "minLength": 1 },
                    "cwd": { "type": "string" },
                    "timeout_sec": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_TIMEOUT_SEC,
                        "default": 10
                    }
                },
                "required": ["profile", "command", "reason"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "maintenance".into(),
            effect: "write".into(),
            timeout_sec: MAX_TIMEOUT_SEC as u32,
            idempotency: Some("non_idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "exec"],
                "execution_profiles": [
                    "user.readonly",
                    "user.write",
                    "admin.readonly",
                    "admin.write"
                ]
            })),
            resource_keys: Some(vec!["node.exec".into()]),
            conflict_policy: Some("serialize".into()),
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![],
            required_intent_slots: vec!["profile".into(), "command".into(), "reason".into()],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let profile = required_str(&input, "profile")?;
        if !matches!(
            profile,
            "user.readonly" | "user.write" | "admin.readonly" | "admin.write"
        ) {
            return Err(CapabilityError::InvalidInput {
                field: "profile".into(),
                message: format!("unsupported exec profile: {profile}"),
            });
        }
        let command = required_str(&input, "command")?;
        let reason = required_str(&input, "reason")?;
        let timeout_sec = input
            .get("timeout_sec")
            .and_then(|v| v.as_u64())
            .unwrap_or(10)
            .clamp(1, MAX_TIMEOUT_SEC);
        let cwd = input
            .get("cwd")
            .and_then(|v| v.as_str())
            .filter(|v| !v.is_empty());

        let mut cmd = if profile.starts_with("admin.") {
            let mut c = Command::new("sudo");
            c.arg("-n").arg("sh").arg("-c").arg(command);
            c
        } else {
            let mut c = Command::new("sh");
            c.arg("-c").arg(command);
            c
        };
        if let Some(cwd) = cwd {
            cmd.current_dir(cwd);
        }
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        let started = Instant::now();
        let child = cmd.output();
        let output = match timeout(Duration::from_secs(timeout_sec), child).await {
            Ok(result) => result.map_err(|e| CapabilityError::FunctionExecutionFailed {
                message: format!("command spawn failed: {e}"),
                exit_code: None,
                stderr: None,
            })?,
            Err(_) => {
                return Ok(json!({
                    "status": "failed",
                    "profile": profile,
                    "reason": reason,
                    "exit_code": null,
                    "error_code": "timeout",
                    "error_message": format!("command timed out after {timeout_sec}s"),
                    "stdout_preview": "",
                    "stderr_tail": "",
                    "truncated": false,
                    "duration_ms": started.elapsed().as_millis() as u64,
                }));
            }
        };

        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let (stdout_preview, stdout_truncated) = truncate_prefix(&stdout, MAX_STDOUT_BYTES);
        let (stderr_tail, stderr_truncated) = truncate_suffix(&stderr, MAX_STDERR_BYTES);

        Ok(json!({
            "status": if output.status.success() { "succeeded" } else { "failed" },
            "profile": profile,
            "reason": reason,
            "command": command,
            "cwd": cwd.unwrap_or(""),
            "exit_code": output.status.code(),
            "stdout_preview": stdout_preview,
            "stderr_tail": stderr_tail,
            "truncated": stdout_truncated || stderr_truncated,
            "duration_ms": started.elapsed().as_millis() as u64,
        }))
    }
}

fn required_str<'a>(input: &'a Value, field: &str) -> Result<&'a str, CapabilityError> {
    input
        .get(field)
        .and_then(|v| v.as_str())
        .filter(|v| !v.trim().is_empty())
        .map(str::trim)
        .ok_or_else(|| CapabilityError::InvalidInput {
            field: field.into(),
            message: format!("{field} is required"),
        })
}

fn truncate_prefix(value: &str, max_bytes: usize) -> (String, bool) {
    if value.len() <= max_bytes {
        return (value.to_string(), false);
    }
    let mut end = max_bytes;
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    (value[..end].to_string(), true)
}

fn truncate_suffix(value: &str, max_bytes: usize) -> (String, bool) {
    if value.len() <= max_bytes {
        return (value.to_string(), false);
    }
    let mut start = value.len() - max_bytes;
    while !value.is_char_boundary(start) {
        start += 1;
    }
    (value[start..].to_string(), true)
}
