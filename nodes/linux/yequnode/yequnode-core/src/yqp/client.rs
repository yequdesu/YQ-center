use std::time::Duration;

use reqwest::header::CONTENT_TYPE;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;
use tracing::Span;

use crate::config::Config;
use crate::yqp::envelope::{YqpEnvelope, YqpError, YqpErrorDetail, YqpResponse};
use crate::yqp::retry::{backoff_duration, classify_reqwest_error, should_retry, RetryConfig};
use crate::yqp::types::{JobFinishedPayload, KnownJob, PluginManifest, RuntimeSnapshot};

// ---------------------------------------------------------------------------
// YqpClient
// ---------------------------------------------------------------------------

/// HTTP client for communicating with the YeQu Center over the YQP protocol.
///
/// Handles message envelope construction, retry logic, tracing audit spans,
/// and provides typed convenience methods for every YQP message type.
pub struct YqpClient {
    http: reqwest::Client,
    base_url: String,
    node_id: String,
    token: String,
    retry_config: RetryConfig,
}

impl YqpClient {
    /// Build a new client from application configuration.
    pub fn new(config: &Config) -> Self {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .expect("reqwest::Client::builder() should always succeed with default settings");

        Self {
            http,
            base_url: config.center_yqp_url(),
            node_id: config.node_id.clone(),
            token: config.node_token.clone(),
            retry_config: RetryConfig::default(),
        }
    }

    // -----------------------------------------------------------------------
    // Core send() method
    // -----------------------------------------------------------------------

    /// Send a YQP message to the center with retry logic and audit tracing.
    ///
    /// Span fields recorded by `#[tracing::instrument]`:
    ///   direction, message_type, message_id, trace_id, http_status,
    ///   duration_ms, retry_attempt, error.kind, error.message
    #[tracing::instrument(
        skip_all,
        fields(
            direction = "node_to_center",
            message_type,
            message_id,
            trace_id,
            http_status,
            duration_ms,
            retry_attempt,
            error.kind,
            error.message,
        )
    )]
    pub async fn send(&self, message_type: &str, payload: Value) -> Result<YqpResponse, YqpError> {
        let envelope = YqpEnvelope::new(message_type, &self.node_id, payload);

        let span = Span::current();
        span.record("message_type", envelope.message_type.as_str());
        span.record("message_id", envelope.message_id.as_str());
        span.record("trace_id", envelope.trace_id.as_str());

        let max_attempts = self.retry_config.max_attempts;
        let mut last_error: Option<YqpError> = None;

        for attempt in 0..=max_attempts {
            span.record("retry_attempt", attempt);

            let start = std::time::Instant::now();

            let request_body =
                serde_json::to_value(&envelope).map_err(|e| YqpError::JsonError(e.to_string()))?;

            let result = self
                .http
                .post(&self.base_url)
                .bearer_auth(&self.token)
                .header(CONTENT_TYPE, "application/json")
                .json(&request_body)
                .send()
                .await;

            match result {
                Ok(response) => {
                    let http_status = response.status().as_u16();
                    span.record("http_status", http_status);

                    if response.status().is_success() {
                        let yqp_response: YqpResponse = response
                            .json()
                            .await
                            .map_err(|e| YqpError::JsonError(e.to_string()))?;

                        let elapsed = start.elapsed();
                        span.record("duration_ms", elapsed.as_millis() as u64);

                        return Ok(yqp_response);
                    }

                    // HTTP error -- try to parse structured error detail
                    let detail: YqpErrorDetail = response
                        .json()
                        .await
                        .map_err(|e| YqpError::JsonError(e.to_string()))?;

                    let elapsed = start.elapsed();
                    span.record("duration_ms", elapsed.as_millis() as u64);

                    // 409 duplicate_message is treated as idempotent confirmation
                    if http_status == 409 && detail.code == "duplicate_message" {
                        // The message was already processed; return a synthetic Ok response
                        return Ok(YqpResponse {
                            yqp_version: envelope.yqp_version.clone(),
                            message_id: envelope.message_id.clone(),
                            message_type: envelope.message_type.clone(),
                            trace_id: envelope.trace_id.clone(),
                            node_id: envelope.node_id.clone(),
                            timestamp: envelope.timestamp.clone(),
                            payload: json!({"status": "already_processed"}),
                        });
                    }

                    let error = YqpError::HttpError {
                        status: http_status,
                        detail,
                    };

                    span.record("error.kind", error.error_code());
                    span.record("error.message", error.error_message());

                    if should_retry(&error, attempt, &self.retry_config) {
                        let backoff = backoff_duration(attempt, &self.retry_config);
                        tokio::time::sleep(backoff).await;
                        last_error = Some(error);
                        continue;
                    }

                    return Err(error);
                }
                Err(reqwest_err) => {
                    let elapsed = start.elapsed();
                    span.record("duration_ms", elapsed.as_millis() as u64);

                    let error = classify_reqwest_error(reqwest_err);

                    span.record("error.kind", error.error_code());
                    span.record("error.message", error.error_message());

                    if should_retry(&error, attempt, &self.retry_config) {
                        let backoff = backoff_duration(attempt, &self.retry_config);
                        tokio::time::sleep(backoff).await;
                        last_error = Some(error);
                        continue;
                    }

                    return Err(error);
                }
            }
        }

        Err(last_error.unwrap_or_else(|| YqpError::NetworkError {
            message: "all retry attempts exhausted".into(),
            retryable: false,
        }))
    }

    // -----------------------------------------------------------------------
    // Convenience methods
    // -----------------------------------------------------------------------

    /// Send a `node.hello` message to register the daemon with the center.
    pub async fn hello(
        &self,
        daemon_version: &str,
        platform_os: &str,
        platform_arch: &str,
        runtimes: &[RuntimeSnapshot],
    ) -> Result<YqpResponse, YqpError> {
        let payload = json!({
            "daemon_version": daemon_version,
            "platform": {
                "os": platform_os,
                "arch": platform_arch,
            },
            "runtimes": runtimes,
        });
        self.send("node.hello", payload).await
    }

    /// Send a `node.heartbeat` to signal liveness and current state.
    pub async fn heartbeat(
        &self,
        uptime_sec: u64,
        running_jobs: &[String],
        plugin_count: u32,
        runtimes: &[RuntimeSnapshot],
    ) -> Result<YqpResponse, YqpError> {
        let payload = json!({
            "daemon_uptime_sec": uptime_sec,
            "running_jobs": running_jobs,
            "plugin_count": plugin_count,
            "status": "online",
            "runtimes": runtimes,
        });
        self.send("node.heartbeat", payload).await
    }

    /// Register capabilities (functions and signals) from all plugins.
    pub async fn register_capabilities(
        &self,
        plugins: &[PluginManifest],
        runtimes: &[RuntimeSnapshot],
    ) -> Result<YqpResponse, YqpError> {
        let payload = json!({
            "runtimes": runtimes,
            "plugins": plugins,
        });
        self.send("node.register_capabilities", payload).await
    }

    /// Poll the center for available jobs.
    pub async fn job_poll(
        &self,
        capacity: u32,
        running_jobs: &[String],
    ) -> Result<YqpResponse, YqpError> {
        let payload = json!({
            "capacity": capacity,
            "running_jobs": running_jobs,
        });
        self.send("job.poll", payload).await
    }

    /// Report that a job has been accepted for execution.
    pub async fn job_accepted(&self, job_id: &str) -> Result<YqpResponse, YqpError> {
        let payload = json!({
            "job_id": job_id,
        });
        self.send("job.accepted", payload).await
    }

    /// Report a finished job result.
    pub async fn job_finished(&self, result: &JobFinishedPayload) -> Result<YqpResponse, YqpError> {
        let payload =
            serde_json::to_value(result).map_err(|e| YqpError::JsonError(e.to_string()))?;
        self.send("job.finished", payload).await
    }

    /// Emit a job lifecycle event.
    pub async fn job_event(
        &self,
        job_id: &str,
        event_type: &str,
        sequence: u32,
        data: Value,
    ) -> Result<YqpResponse, YqpError> {
        let payload = json!({
            "job_id": job_id,
            "event_type": event_type,
            "sequence": sequence,
            "data": data,
        });
        self.send("job.event", payload).await
    }

    /// Renew a job lease to prevent timeout.
    pub async fn job_lease_renew(
        &self,
        job_id: &str,
        lease_extend_sec: u32,
    ) -> Result<YqpResponse, YqpError> {
        let payload = json!({
            "job_id": job_id,
            "lease_extend_sec": lease_extend_sec,
        });
        self.send("job.lease_renew", payload).await
    }

    /// Reconcile known jobs with the center after reconnection.
    pub async fn reconcile_jobs(&self, known_jobs: &[KnownJob]) -> Result<YqpResponse, YqpError> {
        let payload = json!({
            "known_jobs": known_jobs,
        });
        self.send("node.reconcile_jobs", payload).await
    }

    /// Upload an artifact (file, screenshot, binary output) to the Center.
    pub async fn send_artifact_upload(
        &self,
        artifact_type: &str,
        content_type: &str,
        title: &str,
        data: &[u8],
        summary: Option<Value>,
        metadata: Option<Value>,
        job_id: Option<&str>,
    ) -> Result<crate::yqp::types::ArtifactAcceptedPayload, YqpError> {
        use base64::Engine;
        let data_base64 = base64::engine::general_purpose::STANDARD.encode(data);

        let payload = json!({
            "artifact_type": artifact_type,
            "content_type": content_type,
            "title": title,
            "summary": summary,
            "metadata": metadata,
            "job_id": job_id,
            "data_base64": data_base64,
        });

        let resp = self.send("artifact.upload", payload).await?;

        if resp.message_type != "artifact.accepted" {
            return Err(YqpError::NetworkError {
                message: format!("unexpected response type: {}", resp.message_type),
                retryable: false,
            });
        }

        serde_json::from_value(resp.payload).map_err(|e| YqpError::JsonError(e.to_string()))
    }

    /// Download a Center artifact to a local file using the Node bearer token.
    pub async fn download_artifact_to_file(
        &self,
        artifact_id: &str,
        output_path: &std::path::Path,
        overwrite: bool,
    ) -> Result<ArtifactDownloadResult, YqpError> {
        let url = format!(
            "{}/artifacts/{}/download",
            self.base_url.trim_end_matches('/'),
            artifact_id
        );
        if output_path.exists() && !overwrite {
            return Err(YqpError::NetworkError {
                message: format!("output path already exists: {}", output_path.display()),
                retryable: false,
            });
        }
        if let Some(parent) = output_path.parent() {
            tokio::fs::create_dir_all(parent)
                .await
                .map_err(|e| YqpError::NetworkError {
                    message: format!(
                        "failed to create output directory {}: {}",
                        parent.display(),
                        e
                    ),
                    retryable: false,
                })?;
        }

        let response = self
            .http
            .get(url)
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(classify_reqwest_error)?;
        let status = response.status();
        if !status.is_success() {
            let status_code = status.as_u16();
            let message = response
                .text()
                .await
                .unwrap_or_else(|_| "artifact download failed".into());
            return Err(YqpError::HttpError {
                status: status_code,
                detail: YqpErrorDetail {
                    code: if status_code == 404 {
                        "artifact_not_found".into()
                    } else {
                        "artifact_download_failed".into()
                    },
                    message: format!("artifact download HTTP {}: {}", status_code, message),
                    retryable: status.is_server_error(),
                    details: json!({"artifact_id": artifact_id}),
                },
            });
        }

        let expected_sha256 = response
            .headers()
            .get("X-YeQu-Artifact-Sha256")
            .and_then(|v| v.to_str().ok())
            .map(|s| s.to_string());
        let content_type = response
            .headers()
            .get(reqwest::header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok())
            .map(|s| s.to_string());

        let mut file =
            tokio::fs::File::create(output_path)
                .await
                .map_err(|e| YqpError::NetworkError {
                    message: format!("failed to create {}: {}", output_path.display(), e),
                    retryable: false,
                })?;
        let mut hasher = Sha256::new();
        let mut size_bytes: u64 = 0;
        let mut stream = response;
        while let Some(chunk) = stream.chunk().await.map_err(classify_reqwest_error)? {
            file.write_all(&chunk)
                .await
                .map_err(|e| YqpError::NetworkError {
                    message: format!("failed to write {}: {}", output_path.display(), e),
                    retryable: false,
                })?;
            hasher.update(&chunk);
            size_bytes += chunk.len() as u64;
        }
        file.flush().await.map_err(|e| YqpError::NetworkError {
            message: format!("failed to flush {}: {}", output_path.display(), e),
            retryable: false,
        })?;

        let sha256 = hex::encode(hasher.finalize());
        if let Some(expected) = &expected_sha256 {
            if !expected.eq_ignore_ascii_case(&sha256) {
                return Err(YqpError::NetworkError {
                    message: format!(
                        "artifact sha256 mismatch: expected {}, got {}",
                        expected, sha256
                    ),
                    retryable: false,
                });
            }
        }

        Ok(ArtifactDownloadResult {
            artifact_id: artifact_id.to_string(),
            output_path: output_path.display().to_string(),
            size_bytes,
            sha256,
            expected_sha256,
            content_type,
        })
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct ArtifactDownloadResult {
    pub artifact_id: String,
    pub output_path: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub expected_sha256: Option<String>,
    pub content_type: Option<String>,
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use wiremock::matchers::{bearer_token, method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    /// Helper to build a minimal YqpResponse from the server side.
    fn make_response(message_type: &str, node_id: &str, payload: Value) -> YqpResponse {
        YqpResponse {
            yqp_version: "0.1".into(),
            message_id: format!("rsp_{}", uuid::Uuid::new_v4().simple()),
            message_type: message_type.into(),
            trace_id: format!("tr_{}", uuid::Uuid::new_v4().simple()),
            node_id: node_id.into(),
            timestamp: chrono::Utc::now().to_rfc3339(),
            payload,
        }
    }

    fn test_config(server_url: &str) -> Config {
        Config {
            node_id: "test-node".into(),
            center_base_url: server_url.trim_end_matches("/yqp/").to_string(),
            yqp_path: "/yqp/".into(),
            log_level: "debug".into(),
            db_path: std::path::PathBuf::from("/tmp/test.db"),
            node_token: "test-token-123".into(),
            transfer: crate::config::TransferConfig::default(),
        }
    }

    #[tokio::test]
    async fn test_hello_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        let response_payload = make_response(
            "node.accepted",
            "test-node",
            json!({"status": "ok", "node_id": "test-node"}),
        );

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .and(bearer_token("test-token-123"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&response_payload))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client.hello("0.1.0", "linux", "x86_64", &[]).await;

        assert!(result.is_ok());
        let response = result.unwrap();
        assert_eq!(response.payload["status"], "ok");
    }

    #[tokio::test]
    async fn test_heartbeat_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        let response_payload =
            make_response("node.heartbeat", "test-node", json!({"status": "ok"}));

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .and(bearer_token("test-token-123"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&response_payload))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client.heartbeat(12345, &[], 3, &[]).await;

        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_register_capabilities_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        let response_payload = make_response("node.accepted", "test-node", json!({"status": "ok"}));

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .and(bearer_token("test-token-123"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&response_payload))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client.register_capabilities(&[], &[]).await;

        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_job_poll_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        let response_payload = make_response("job.available", "test-node", json!({"jobs": []}));

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .and(bearer_token("test-token-123"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&response_payload))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client.job_poll(5, &[]).await;

        assert!(result.is_ok());
        let response = result.unwrap();
        assert_eq!(response.payload["jobs"], json!([]));
    }

    #[tokio::test]
    async fn test_job_accepted_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        let response_payload = make_response("node.accepted", "test-node", json!({"status": "ok"}));

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .and(bearer_token("test-token-123"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&response_payload))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client.job_accepted("job-1").await;

        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_job_finished_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        let response_payload = make_response("node.accepted", "test-node", json!({"status": "ok"}));

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .and(bearer_token("test-token-123"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&response_payload))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = JobFinishedPayload {
            job_id: "j-1".into(),
            status: "succeeded".into(),
            output: Some(json!({"result": "done"})),
            error_code: None,
            error_message: None,
            error: None,
        };
        let result = client.job_finished(&result).await;

        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_job_event_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        let response_payload = make_response("job.event", "test-node", json!({"status": "ok"}));

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .and(bearer_token("test-token-123"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&response_payload))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client
            .job_event("j-1", "log", 1, json!({"line": "hello"}))
            .await;

        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_reconcile_jobs_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        let response_payload =
            make_response("node.reconciliation", "test-node", json!({"actions": []}));

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .and(bearer_token("test-token-123"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&response_payload))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let known = vec![KnownJob {
            job_id: "j-1".into(),
            local_status: "running".into(),
            started_at: None,
            updated_at: None,
            output: None,
        }];
        let result = client.reconcile_jobs(&known).await;

        assert!(result.is_ok());
        assert_eq!(result.unwrap().payload["actions"], json!([]));
    }

    #[tokio::test]
    async fn test_409_idempotent_success() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .respond_with(ResponseTemplate::new(409).set_body_json(json!({
                "code": "duplicate_message",
                "message": "message already processed",
                "retryable": false,
            })))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client.hello("0.1.0", "linux", "x86_64", &[]).await;

        assert!(result.is_ok());
        assert_eq!(result.unwrap().payload["status"], "already_processed");
    }

    #[tokio::test]
    async fn test_http_error_non_retryable() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .respond_with(ResponseTemplate::new(403).set_body_json(json!({
                "code": "forbidden",
                "message": "invalid token",
                "retryable": false,
            })))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client.hello("0.1.0", "linux", "x86_64", &[]).await;

        assert!(result.is_err());
        match result.unwrap_err() {
            YqpError::HttpError { status, detail } => {
                assert_eq!(status, 403);
                assert_eq!(detail.code, "forbidden");
            }
            other => panic!("expected HttpError, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_retry_then_failure() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        // Return 503 (retryable) for every request
        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .respond_with(ResponseTemplate::new(503).set_body_json(json!({
                "code": "service_unavailable",
                "message": "try again later",
                "retryable": true,
            })))
            .mount(&mock_server)
            .await;

        let client = YqpClient::new(&config);
        let result = client.hello("0.1.0", "linux", "x86_64", &[]).await;

        assert!(result.is_err());
        match result.unwrap_err() {
            YqpError::HttpError { status, detail } => {
                assert_eq!(status, 503);
                assert!(detail.retryable);
            }
            other => panic!("expected HttpError, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_network_timeout_retries() {
        let mock_server = MockServer::start().await;
        let config = test_config(&mock_server.uri());

        // Simulate a network timeout
        Mock::given(method("POST"))
            .and(path("/yqp/"))
            .respond_with(ResponseTemplate::new(200).set_delay(Duration::from_secs(60)))
            .mount(&mock_server)
            .await;

        // Use a very short client timeout so it fires quickly
        let http = reqwest::Client::builder()
            .timeout(Duration::from_millis(50))
            .build()
            .unwrap();

        let client = YqpClient {
            http,
            base_url: config.center_yqp_url(),
            node_id: config.node_id,
            token: config.node_token,
            retry_config: RetryConfig {
                max_attempts: 2,
                base_backoff: Duration::from_millis(10),
            },
        };

        let result = client.hello("0.1.0", "linux", "x86_64", &[]).await;

        assert!(result.is_err());
        match result.unwrap_err() {
            YqpError::NetworkError { retryable, .. } => {
                // After exhausting retries it should remain retryable
                assert!(retryable);
            }
            other => panic!("expected NetworkError, got {other:?}"),
        }
    }
}
