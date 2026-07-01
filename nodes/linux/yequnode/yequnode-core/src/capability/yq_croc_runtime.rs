use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};

use crate::config::YqCrocConfig;
use crate::execution_context::ExecutionContext;

use super::manifest::CapabilityError;

const MAX_OUTPUT_TAIL: usize = 4000;
const TAIL_LINE_LIMIT: usize = 256;

#[derive(Debug, Clone, Copy)]
pub enum YqCrocRole {
    Sender,
    Receiver,
}

impl YqCrocRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Sender => "sender",
            Self::Receiver => "receiver",
        }
    }

    fn command(self) -> &'static str {
        match self {
            Self::Sender => "send",
            Self::Receiver => "receive",
        }
    }
}

#[derive(Debug, Clone)]
pub struct YqCrocRun {
    pub role: YqCrocRole,
    pub transfer_id: String,
    pub attempt: u32,
    pub code: String,
    pub source_path: Option<String>,
    pub output_dir: Option<String>,
    pub target_path: Option<String>,
    pub relay_url: Option<String>,
    pub resume_mode: String,
    pub expected_size_bytes: Option<u64>,
    pub expected_sha256: Option<String>,
    pub timeout_sec: u64,
    pub cleanup_on_failure: bool,
}

#[derive(Debug, Clone)]
pub struct YqCrocRunResult {
    pub exit_code: Option<i32>,
    pub started_at: String,
    pub completed_at: String,
    pub stdout_tail: String,
    pub stderr_tail: String,
}

#[derive(Debug, Serialize)]
struct RequestFile<'a> {
    transfer_id: &'a str,
    attempt: u32,
    role: &'a str,
    code: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    relay_url: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    relay_password: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    source_path: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    output_dir: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    target_path: Option<&'a str>,
    resume_mode: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    expected_size_bytes: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    expected_sha256: Option<&'a str>,
    timeout_sec: u64,
    cleanup_on_failure: bool,
}

pub async fn ensure_yq_croc_executable(binary_path: &str) -> Result<(), CapabilityError> {
    if !Path::new(binary_path).exists() {
        return Err(CapabilityError::InvalidInput {
            field: "binary_path".into(),
            message: format!("yq-croc binary not found at {}", binary_path),
        });
    }

    match Command::new(binary_path).arg("version").output().await {
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => Err(CapabilityError::FunctionExecutionFailed {
            message: format!("yq-croc version failed with {:?}", output.status.code()),
            exit_code: output.status.code(),
            stderr: Some(String::from_utf8_lossy(&output.stderr).trim().to_string()),
        }),
        Err(e) => Err(CapabilityError::PermissionDenied {
            path: Some(binary_path.into()),
            detail: format!("yq-croc is not executable by daemon user: {}", e),
        }),
    }
}

pub async fn run_yq_croc(
    config: &YqCrocConfig,
    run: YqCrocRun,
    ctx: Option<Arc<ExecutionContext>>,
) -> Result<YqCrocRunResult, CapabilityError> {
    ensure_yq_croc_executable(&config.binary_path).await?;

    let relay_password = relay_password(config)?;
    let request_path = write_request_file(config, &run, relay_password.as_deref())?;
    let started_at = chrono::Utc::now().to_rfc3339();

    let mut child = Command::new(&config.binary_path);
    child
        .arg(run.role.command())
        .arg("--request")
        .arg(&request_path)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = child.spawn().map_err(|e| {
        cleanup_request_file(&request_path);
        CapabilityError::FunctionExecutionFailed {
            message: format!("failed to spawn yq-croc: {}", e),
            exit_code: None,
            stderr: Some(e.to_string()),
        }
    })?;

    let pid = child.id().unwrap_or(0);
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();

    let stdout_base = ProgressBase {
        transfer_id: run.transfer_id.clone(),
        role: run.role.as_str().into(),
        attempt: run.attempt,
        pid,
        total_bytes: run.expected_size_bytes,
    };
    let stdout_ctx = ctx.clone();
    let stdout_handle =
        tokio::spawn(async move { collect_stdout_events(stdout, stdout_ctx, stdout_base).await });
    let stderr_handle = tokio::spawn(async move { collect_output_tail(stderr).await });

    if let Some(ref ctx) = ctx {
        ctx.report_progress(
            "transfer_progress",
            json!({
                "transfer_id": run.transfer_id,
                "role": run.role.as_str(),
                "attempt": run.attempt,
                "pid": pid,
                "status": "running",
                "phase": "process_started",
                "progress_source": "process_keepalive",
            }),
        )
        .await;
    }

    let result = monitor_child(
        &mut child,
        &run,
        ctx.as_ref(),
        stdout_handle,
        stderr_handle,
        &request_path,
        pid,
        started_at,
    )
    .await;
    cleanup_request_file(&request_path);
    result
}

#[allow(clippy::too_many_arguments)]
async fn monitor_child(
    child: &mut Child,
    run: &YqCrocRun,
    ctx: Option<&Arc<ExecutionContext>>,
    stdout_handle: tokio::task::JoinHandle<CollectedStdout>,
    stderr_handle: tokio::task::JoinHandle<Vec<String>>,
    request_path: &Path,
    pid: u32,
    started_at: String,
) -> Result<YqCrocRunResult, CapabilityError> {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(run.timeout_sec);
    let lease_interval = Duration::from_secs(60);
    let progress_interval = Duration::from_secs(30);
    let mut last_lease = tokio::time::Instant::now();
    let mut last_progress = tokio::time::Instant::now();

    loop {
        if let Some(ctx) = ctx {
            if ctx.is_cancelled() {
                let _ = child.kill().await;
                let _ = child.wait().await;
                let collected = collect_joined(stdout_handle, stderr_handle).await;
                return Err(cancelled_error(
                    request_path,
                    &run.transfer_id,
                    &collected.stderr_tail,
                ));
            }
        }

        match child.try_wait() {
            Ok(Some(status)) => {
                let exit_code = status.code();
                let collected = collect_joined(stdout_handle, stderr_handle).await;
                let completed_at = chrono::Utc::now().to_rfc3339();
                if status.success() {
                    return Ok(YqCrocRunResult {
                        exit_code,
                        started_at,
                        completed_at,
                        stdout_tail: collected.stdout_tail,
                        stderr_tail: collected.stderr_tail,
                    });
                }
                let message = format!(
                    "yq-croc {} failed with exit code {:?}",
                    run.role.command(),
                    exit_code
                );
                return Err(CapabilityError::FunctionExecutionFailed {
                    message,
                    exit_code,
                    stderr: Some(
                        json!({
                            "returncode": exit_code,
                            "stdout": collected.stdout_tail,
                            "stderr": collected.stderr_tail,
                            "binary_path": null,
                            "role": run.role.as_str(),
                            "transfer_id": run.transfer_id,
                            "attempt": run.attempt,
                        })
                        .to_string(),
                    ),
                });
            }
            Ok(None) => {
                let now = tokio::time::Instant::now();
                if now >= deadline {
                    let _ = child.kill().await;
                    let _ = child.wait().await;
                    let _ = collect_joined(stdout_handle, stderr_handle).await;
                    return Err(CapabilityError::Timeout {
                        timeout_sec: run.timeout_sec as u32,
                    });
                }

                if let Some(ctx) = ctx {
                    if now.duration_since(last_lease) >= lease_interval {
                        ctx.renew_lease(120).await;
                        last_lease = now;
                    }
                    if now.duration_since(last_progress) >= progress_interval {
                        ctx.report_progress(
                            "transfer_progress",
                            json!({
                                "transfer_id": run.transfer_id,
                                "role": run.role.as_str(),
                                "attempt": run.attempt,
                                "pid": pid,
                                "status": "running",
                                "phase": "process_keepalive",
                                "progress_source": "process_keepalive",
                            }),
                        )
                        .await;
                        last_progress = now;
                    }
                }

                tokio::time::sleep(Duration::from_millis(500)).await;
            }
            Err(e) => {
                return Err(CapabilityError::FunctionExecutionFailed {
                    message: format!("failed to wait yq-croc child process: {}", e),
                    exit_code: None,
                    stderr: Some(e.to_string()),
                });
            }
        }
    }
}

fn relay_password(config: &YqCrocConfig) -> Result<Option<String>, CapabilityError> {
    let Some(env_name) = config.relay_password_env.as_deref() else {
        return Ok(None);
    };
    std::env::var(env_name)
        .map(Some)
        .map_err(|_| CapabilityError::InvalidInput {
            field: "relay_password_env".into(),
            message: format!("configured relay password env {} is not set", env_name),
        })
}

fn write_request_file(
    config: &YqCrocConfig,
    run: &YqCrocRun,
    relay_password: Option<&str>,
) -> Result<PathBuf, CapabilityError> {
    let attempt_dir = config
        .temp_dir
        .join(&run.transfer_id)
        .join(format!("attempt-{}", run.attempt));
    std::fs::create_dir_all(&attempt_dir).map_err(|e| {
        CapabilityError::FunctionExecutionFailed {
            message: format!("failed to create yq-croc request directory: {}", e),
            exit_code: None,
            stderr: Some(e.to_string()),
        }
    })?;

    let request = RequestFile {
        transfer_id: &run.transfer_id,
        attempt: run.attempt,
        role: run.role.as_str(),
        code: &run.code,
        relay_url: run.relay_url.as_deref().or(config.relay_url.as_deref()),
        relay_password,
        source_path: run.source_path.as_deref(),
        output_dir: run.output_dir.as_deref(),
        target_path: run.target_path.as_deref(),
        resume_mode: &run.resume_mode,
        expected_size_bytes: run.expected_size_bytes,
        expected_sha256: run.expected_sha256.as_deref(),
        timeout_sec: run.timeout_sec,
        cleanup_on_failure: run.cleanup_on_failure,
    };
    let request_path = attempt_dir.join("request.json");
    let payload = serde_json::to_vec_pretty(&request).map_err(|e| {
        CapabilityError::Internal(format!("failed to encode yq-croc request: {}", e))
    })?;
    std::fs::write(&request_path, payload).map_err(|e| {
        CapabilityError::FunctionExecutionFailed {
            message: format!("failed to write yq-croc request: {}", e),
            exit_code: None,
            stderr: Some(e.to_string()),
        }
    })?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&request_path, std::fs::Permissions::from_mode(0o600));
    }

    Ok(request_path)
}

fn cleanup_request_file(path: &Path) {
    let _ = std::fs::remove_file(path);
}

#[derive(Debug, Clone)]
struct ProgressBase {
    transfer_id: String,
    role: String,
    attempt: u32,
    pid: u32,
    total_bytes: Option<u64>,
}

#[derive(Debug, Clone, Default)]
struct CollectedStdout {
    stdout_tail: String,
}

#[derive(Debug, Clone, Default)]
struct CollectedProcessOutput {
    stdout_tail: String,
    stderr_tail: String,
}

async fn collect_stdout_events(
    stdout: Option<tokio::process::ChildStdout>,
    ctx: Option<Arc<ExecutionContext>>,
    base: ProgressBase,
) -> CollectedStdout {
    let mut tail = Tail::default();
    let Some(stdout) = stdout else {
        return CollectedStdout::default();
    };
    let mut lines = BufReader::new(stdout).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let redacted = redact_sensitive(&line);
        tail.push(redacted.clone());
        let Ok(event) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let Some(event_name) = event.get("event").and_then(|v| v.as_str()) else {
            continue;
        };
        if let Some(ref ctx) = ctx {
            ctx.report_progress(
                "transfer_progress",
                progress_payload(&base, event_name, &event),
            )
            .await;
        }
    }
    CollectedStdout {
        stdout_tail: tail.text(),
    }
}

async fn collect_output_tail(output: Option<tokio::process::ChildStderr>) -> Vec<String> {
    let mut lines = Vec::new();
    let Some(output) = output else {
        return lines;
    };
    let mut reader = BufReader::new(output).lines();
    while let Ok(Some(line)) = reader.next_line().await {
        lines.push(redact_sensitive(&line));
    }
    lines
}

async fn collect_joined(
    stdout_handle: tokio::task::JoinHandle<CollectedStdout>,
    stderr_handle: tokio::task::JoinHandle<Vec<String>>,
) -> CollectedProcessOutput {
    let stdout = stdout_handle.await.unwrap_or_default();
    let stderr_lines = stderr_handle.await.unwrap_or_default();
    CollectedProcessOutput {
        stdout_tail: stdout.stdout_tail,
        stderr_tail: truncate_tail(&stderr_lines.join("\n")),
    }
}

fn progress_payload(base: &ProgressBase, event_name: &str, event: &Value) -> Value {
    let data = event.get("data").cloned().unwrap_or_else(|| json!({}));
    let mut payload = json!({
        "transfer_id": base.transfer_id,
        "role": base.role,
        "attempt": base.attempt,
        "pid": base.pid,
        "status": "running",
        "phase": phase_for_event(event_name),
        "progress_source": "yq_croc_event",
        "runtime": "yq-croc",
        "event": event_name,
        "event_data": data,
    });

    if let Some(total) = base.total_bytes {
        payload["total_bytes"] = json!(total);
    }
    if let Some(bytes) = event
        .get("data")
        .and_then(|v| v.get("bytes_transferred"))
        .and_then(|v| v.as_u64())
    {
        payload["bytes_transferred"] = json!(bytes);
        if let Some(total) = event
            .get("data")
            .and_then(|v| v.get("total_bytes"))
            .and_then(|v| v.as_u64())
            .or(base.total_bytes)
        {
            payload["total_bytes"] = json!(total);
            if total > 0 {
                payload["progress_pct"] = json!((bytes as f64 / total as f64) * 100.0);
            }
        }
    }
    if event_name == "sender_ready" {
        payload["sender_ready"] = json!(true);
    }
    if event_name == "transfer_error" {
        if let Some(code) = event
            .get("data")
            .and_then(|v| v.get("error_code"))
            .and_then(|v| v.as_str())
        {
            payload["error_code"] = json!(code);
        }
        if let Some(message) = event
            .get("data")
            .and_then(|v| v.get("error_message"))
            .and_then(|v| v.as_str())
        {
            payload["error_message"] = json!(message);
        }
    }
    payload
}

fn phase_for_event(event_name: &str) -> &'static str {
    match event_name {
        "runtime_ready" => "preparing",
        "source_scanned" | "file_info" => "scanning",
        "sender_ready" | "receiver_connected" => "ready",
        "channel_secured" => "handshake",
        "bytes_progress" => "transferring",
        "integrity_verified" => "verifying",
        "transfer_done" => "completed",
        "transfer_error" => "failed",
        "transfer_cancelled" => "cancelled",
        _ => "running",
    }
}

fn cancelled_error(request_path: &Path, transfer_id: &str, stderr_tail: &str) -> CapabilityError {
    cleanup_request_file(request_path);
    CapabilityError::Cancelled {
        message: format!("transfer {} cancelled: {}", transfer_id, stderr_tail),
    }
}

fn redact_sensitive(line: &str) -> String {
    let lower = line.to_lowercase();
    if lower.contains("code") || lower.contains("password") || lower.contains("passphrase") {
        return "[REDACTED]".into();
    }
    line.to_string()
}

#[derive(Default)]
struct Tail {
    lines: VecDeque<String>,
}

impl Tail {
    fn push(&mut self, line: String) {
        self.lines.push_back(line);
        while self.lines.len() > TAIL_LINE_LIMIT {
            self.lines.pop_front();
        }
    }

    fn text(&self) -> String {
        truncate_tail(&self.lines.iter().cloned().collect::<Vec<_>>().join("\n"))
    }
}

fn truncate_tail(text: &str) -> String {
    if text.len() <= MAX_OUTPUT_TAIL {
        return text.to_string();
    }
    let mut start = text.len() - MAX_OUTPUT_TAIL;
    while !text.is_char_boundary(start) {
        start += 1;
    }
    format!("...{}", &text[start..])
}
