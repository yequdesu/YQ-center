use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::Value;
use tokio::sync::RwLock;
use tokio_util::sync::CancellationToken;
use tracing::{error, info, warn};

use crate::capability::manifest::CapabilityManifest;
use crate::config::Config;
use crate::execution_context::ExecutionContext;
use crate::job::{JobRecord, JobStore};
use crate::permissions;
use crate::registry;
use crate::shutdown;
use crate::yqp::client::YqpClient;
use crate::yqp::types::{
    AcceptedPayload, JobDescriptor, JobErrorDetail, JobFinishedPayload,
    PluginManifest, ReconciliationAction, RuntimePrivilege, RuntimeSnapshot,
};

/// Long-running capabilities that need progress reporting and cancellation.
const LONG_RUNNING_CAPABILITIES: &[&str] = &[
    "linux.transfer.croc.send",
    "linux.transfer.croc.receive",
];

// ---------------------------------------------------------------------------
// Daemon
// ---------------------------------------------------------------------------

pub struct Daemon {
    #[allow(dead_code)]
    config: Config,
    yqp: Arc<YqpClient>,
    store: Arc<JobStore>,
    started_at: Instant,
    heartbeat_interval_sec: u64,
    poll_interval_sec: u64,
    cancel_tokens: Arc<RwLock<HashMap<String, CancellationToken>>>,
}

impl Daemon {
    /// Full startup sequence. Returns only on fatal error.
    pub async fn run(config: Config) -> Result<(), Box<dyn std::error::Error>> {
        // Open local job store
        info!(path = %config.db_path.display(), "opening job store");
        let store = JobStore::open(&config.db_path)?;
        let store = Arc::new(store);

        // Create YQP client
        let yqp = Arc::new(YqpClient::new(&config));
        crate::capability::set_yqp_client(YqpClient::new(&config));

        // Run permission probes
        let probe_results = permissions::run_probes();
        for result in &probe_results {
            if result.passed {
                info!(
                    function = %result.function_name,
                    detail = %result.detail,
                    "permission probe passed"
                );
            } else {
                warn!(
                    function = %result.function_name,
                    detail = %result.detail,
                    "permission probe failed — capability will be excluded"
                );
            }
        }

        // Build runtime snapshots — user + optional sudo + optional transfer
        let mut runtimes = vec![build_user_runtime()];
        if permissions::sudo_available() {
            runtimes.push(build_sudo_runtime());
            info!("sudo runtime available — registering privileged capabilities");
        }
        if let Some(transfer_runtime) = build_transfer_runtime(&config) {
            runtimes.push(transfer_runtime);
            info!("croc transfer runtime available — registering transfer capabilities");
        }

        // Handshake: node.hello
        info!("sending node.hello");
        let hello_resp = yqp
            .hello(
                env!("CARGO_PKG_VERSION"),
                std::env::consts::OS,
                std::env::consts::ARCH,
                &runtimes,
            )
            .await
            .map_err(|e| format!("hello handshake failed: {e:?}"))?;

        // Extract configuration from hello response
        let accepted: AcceptedPayload = serde_json::from_value(hello_resp.payload.clone())
            .map_err(|e| format!("invalid node.accepted payload: {e}"))?;

        let heartbeat_interval_sec = accepted.heartbeat_interval_sec;
        let poll_interval_sec = accepted.job_poll_interval_sec;

        info!(
            heartbeat_sec = heartbeat_interval_sec,
            poll_sec = poll_interval_sec,
            "node accepted by center"
        );

        // Collect capability manifests and filter by probe results
        let all_manifests = registry::production::collect_manifests();
        let all_function_names: Vec<String> = all_manifests.iter().map(|m| m.name.clone()).collect();
        let passed_functions = permissions::filter_passed_functions(&probe_results, &all_function_names);

        let filtered_manifests: Vec<&CapabilityManifest> = all_manifests
            .iter()
            .filter(|m| passed_functions.contains(&m.name))
            .collect();

        // Convert CapabilityManifests to PluginManifests for the protocol
        let plugins = vec![PluginManifest {
            plugin_id: "linux.system".into(),
            plugin_version: "0.1.0".into(),
            functions: filtered_manifests
                .iter()
                .map(|cm| crate::yqp::types::FunctionManifest {
                    name: cm.name.clone(),
                    description: cm.description.clone(),
                    agent_description: cm.agent_description.clone(),
                    user_visible_name: None,
                    input_schema: cm.input_schema.clone(),
                    output_schema: cm.output_schema.clone(),
                    risk: cm.risk.clone(),
                    effect: cm.effect.clone(),
                    timeout_sec: cm.timeout_sec,
                    idempotency: cm.idempotency.clone(),
                    execution_context: None,
                    execution_requirements: cm.execution_requirements.clone(),
                    resource_keys: cm.resource_keys.clone(),
                    conflict_policy: cm.conflict_policy.clone(),
                    hidden_input_fields: None,
                    preflight_supported: false,
                })
                .collect(),
            signals: vec![],
        }];

        // Register capabilities
        info!(
            plugin_count = 1,
            function_count = filtered_manifests.len(),
            "registering capabilities"
        );
        yqp.register_capabilities(&plugins, &runtimes)
            .await
            .map_err(|e| format!("capability registration failed: {e:?}"))?;

        // Reconcile unconfirmed jobs
        let unconfirmed = store.get_unconfirmed_jobs()?;
        if !unconfirmed.is_empty() {
            info!(count = unconfirmed.len(), "reconciling unconfirmed jobs");
            let known_jobs: Vec<crate::yqp::types::KnownJob> = unconfirmed
                .iter()
                .map(|jr| crate::yqp::types::KnownJob {
                    job_id: jr.job_id.clone(),
                    local_status: jr.status.clone(),
                    started_at: Some(jr.created_at.clone()),
                    updated_at: Some(jr.updated_at.clone()),
                    output: jr.output.clone(),
                })
                .collect();

            let reconcile_resp = yqp
                .reconcile_jobs(&known_jobs)
                .await
                .map_err(|e| format!("reconciliation failed: {e:?}"))?;

            let actions: Vec<ReconciliationAction> = reconcile_resp
                .payload
                .get("actions")
                .and_then(|a| serde_json::from_value(a.clone()).ok())
                .unwrap_or_default();

            for action in &actions {
                match action.action.as_str() {
                    "accept_result" | "discard_result" => {
                        // Center confirms the result — mark as confirmed
                        store.update_job_status(
                            &action.job_id,
                            &unconfirmed.iter().find(|j| j.job_id == action.job_id).map(|j| j.status.clone()).unwrap_or_default(),
                            None,
                            None,
                            true,
                        )?;
                        info!(job_id = %action.job_id, action = %action.action, "reconciliation: confirmed job");
                    }
                    "forget" => {
                        // Center doesn't know about this job — mark as cancelled
                        store.update_job_status(&action.job_id, "cancelled", None, None, true)?;
                        info!(job_id = %action.job_id, "reconciliation: forgot job");
                    }
                    other => {
                        warn!(job_id = %action.job_id, action = %other, "reconciliation: unknown action");
                    }
                }
            }
        }

        // Enter main loop
        let daemon = Self {
            config,
            yqp,
            store,
            started_at: Instant::now(),
            heartbeat_interval_sec,
            poll_interval_sec,
            cancel_tokens: Arc::new(RwLock::new(HashMap::new())),
        };

        daemon.main_loop().await;

        // Shutdown — drain unconfirmed jobs (just a warning log)
        shutdown::drain_jobs(&daemon.store).await;
        info!("daemon shutdown complete");

        Ok(())
    }

    // -----------------------------------------------------------------------
    // Main event loop
    // -----------------------------------------------------------------------

    async fn main_loop(&self) {
        let mut heartbeat_ticker = tokio::time::interval(Duration::from_secs(self.heartbeat_interval_sec));
        let mut poll_ticker = tokio::time::interval(Duration::from_secs(self.poll_interval_sec));

        // Tick immediately on first iteration
        heartbeat_ticker.reset_immediately();
        poll_ticker.reset_immediately();

        loop {
            tokio::select! {
                _ = heartbeat_ticker.tick() => {
                    self.do_heartbeat().await;
                }
                _ = poll_ticker.tick() => {
                    self.do_poll_cycle().await;
                }
                _ = shutdown::shutdown_signal() => {
                    info!("shutdown signal received, exiting main loop");
                    break;
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Job cancellation
    // -----------------------------------------------------------------------

    /// Cancel a running job by signaling its cancel token.
    pub async fn cancel_job(&self, job_id: &str) -> bool {
        let tokens = self.cancel_tokens.read().await;
        if let Some(token) = tokens.get(job_id) {
            token.cancel();
            info!(job_id = %job_id, "cancel signal sent");
            true
        } else {
            warn!(job_id = %job_id, "no cancel token found for job");
            false
        }
    }

    // -----------------------------------------------------------------------
    // Heartbeat
    // -----------------------------------------------------------------------

    async fn do_heartbeat(&self) {
        let uptime = self.started_at.elapsed().as_secs();
        let runtimes = build_all_runtimes();

        match self.yqp.heartbeat(uptime, &[], 1, &runtimes).await {
            Ok(resp) => {
                info!(
                    uptime_sec = uptime,
                    status = ?resp.payload.get("status").and_then(|v| v.as_str()),
                    "heartbeat sent"
                );
            }
            Err(e) => {
                warn!(error = ?e, "heartbeat failed");
            }
        }
    }

    // -----------------------------------------------------------------------
    // Poll cycle
    // -----------------------------------------------------------------------

    async fn do_poll_cycle(&self) {
        match self.yqp.job_poll(4, &[]).await {
            Ok(resp) => {
                let jobs: Vec<JobDescriptor> = resp
                    .payload
                    .get("jobs")
                    .and_then(|j| serde_json::from_value(j.clone()).ok())
                    .unwrap_or_default();

                if !jobs.is_empty() {
                    info!(count = jobs.len(), "jobs received from poll");
                }

                for job in &jobs {
                    self.execute_job(job).await;
                }
            }
            Err(e) => {
                // Poll failures are logged but non-fatal
                warn!(error = ?e, "job poll failed");
            }
        }
    }

    // -----------------------------------------------------------------------
    // Job execution pipeline
    // -----------------------------------------------------------------------

    async fn execute_job(&self, job: &JobDescriptor) {
        let job_id = &job.job_id;
        let function_name = &job.function;
        info!(job_id = %job_id, function = %function_name, "executing job");

        // 1. Insert job record (status=claimed)
        let record = JobRecord {
            job_id: job_id.clone(),
            function_name: function_name.clone(),
            status: "claimed".into(),
            input: Some(job.input.clone()),
            output: None,
            error_code: None,
            error_message: None,
            error_details: None,
            created_at: chrono::Utc::now().to_rfc3339(),
            updated_at: chrono::Utc::now().to_rfc3339(),
            confirmed: false,
        };

        if let Err(e) = self.store.insert_job(&record) {
            error!(job_id = %job_id, error = %e, "failed to insert job record");
            return;
        }
        info!(job_id = %job_id, "job record inserted (claimed)");

        // 2. Report job.accepted
        if let Err(e) = self.yqp.job_accepted(job_id).await {
            error!(job_id = %job_id, error = ?e, "job_accepted failed");
            // Continue anyway — the center will reconcile on reconnect
        }

        // 3. Update status to running
        if let Err(e) = self.store.update_job_status(
            job_id,
            "running",
            None,
            None,
            false,
        ) {
            error!(job_id = %job_id, error = %e, "failed to update job status to running");
        }
        info!(job_id = %job_id, "job status set to running");

        // 4. Execute function — long-running vs standard
        let is_long_running = LONG_RUNNING_CAPABILITIES.contains(&function_name.as_str());

        let result: Result<Value, crate::capability::manifest::CapabilityError> = if is_long_running {
            // Create execution context with cancel token
            let ctx = Arc::new(ExecutionContext::new(job_id.clone(), self.yqp.clone()));

            // Register cancel token for this job
            {
                let mut tokens: tokio::sync::RwLockWriteGuard<'_, HashMap<String, CancellationToken>> = self.cancel_tokens.write().await;
                tokens.insert(job_id.clone(), ctx.cancel.clone());
            }

            // Execute with context
            let result = crate::execution_context::with_context(
                ctx.clone(),
                registry::production::dispatch(function_name, job.input.clone()),
            ).await;

            // Remove cancel token
            {
                let mut tokens = self.cancel_tokens.write().await;
                tokens.remove(job_id);
            }

            result
        } else {
            // Standard execution without context
            registry::production::dispatch(function_name, job.input.clone()).await
        };

        match result {
            Ok(output) => {
                info!(job_id = %job_id, "job execution succeeded");

                // Build finished payload with status="succeeded"
                let job_result = JobFinishedPayload {
                    job_id: job_id.clone(),
                    status: "succeeded".into(),
                    output: Some(output.clone()),
                    error_code: None,
                    error_message: None,
                    error: None,
                };

                // Send job.result
                if let Err(e) = self.yqp.job_finished(&job_result).await {
                    error!(job_id = %job_id, error = ?e, "job_finished report failed");
                    // Store will be reconciled on restart
                }

                // Update store: succeeded, confirmed
                if let Err(e) = self.store.update_job_status(
                    job_id,
                    "succeeded",
                    Some(&output),
                    None,
                    true,
                ) {
                    error!(job_id = %job_id, error = %e, "failed to update job record on success");
                }

                info!(job_id = %job_id, "job completed successfully");
            }
            Err(cap_err) => {
                let error_code = cap_err.error_code();
                let error_message = cap_err.error_message();
                let error_details = Some(cap_err.to_job_error());

                info!(
                    job_id = %job_id,
                    error_code = %error_code,
                    "job execution failed"
                );

                // Build error detail for protocol
                let job_error = JobErrorDetail {
                    code: error_code.into(),
                    message: error_message.clone(),
                    details: Some(cap_err.to_job_error()),
                };

                // Build finished payload with status="failed"
                let job_result = JobFinishedPayload {
                    job_id: job_id.clone(),
                    status: "failed".into(),
                    output: None,
                    error_code: Some(error_code.into()),
                    error_message: Some(error_message.clone()),
                    error: Some(job_error),
                };

                // Send job.finished
                if let Err(e) = self.yqp.job_finished(&job_result).await {
                    error!(job_id = %job_id, error = ?e, "job_finished report failed");
                }

                // Update store: failed, confirmed, with error details
                if let Err(e) = self.store.update_job_status(
                    job_id,
                    "failed",
                    None,
                    Some((error_code, &error_message, error_details.as_ref())),
                    true,
                ) {
                    error!(job_id = %job_id, error = %e, "failed to update job record on failure");
                }

                info!(job_id = %job_id, "job completed with failure");
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build all available runtime snapshots.
fn build_all_runtimes() -> Vec<RuntimeSnapshot> {
    let mut runtimes = vec![build_user_runtime()];
    if crate::permissions::sudo_available() {
        runtimes.push(build_sudo_runtime());
    }
    if let Ok(config) = crate::config::Config::load() {
        if let Some(transfer_runtime) = build_transfer_runtime(&config) {
            runtimes.push(transfer_runtime);
        }
    }
    runtimes
}

fn build_user_runtime() -> RuntimeSnapshot {
    let user = std::env::var("USER").unwrap_or_else(|_| "unknown".into());
    RuntimeSnapshot {
        runtime_id: "user".into(),
        kind: "privileged".into(),
        status: "online".into(),
        interactive: false,
        privilege: Some(RuntimePrivilege::User),
        labels: Some(vec!["linux".into()]),
        owner: None,
        metadata: Some(serde_json::json!({"user": user})),
    }
}

fn build_sudo_runtime() -> RuntimeSnapshot {
    RuntimeSnapshot {
        runtime_id: "sudo-limited".into(),
        kind: "privileged".into(),
        status: "online".into(),
        interactive: false,
        privilege: Some(RuntimePrivilege::Root),
        labels: Some(vec!["linux".into(), "sudoers:yequnode".into(), "filesystem:host".into()]),
        owner: None,
        metadata: Some(serde_json::json!({"sudoers_file": "/etc/sudoers.d/yequnode"})),
    }
}

/// Build a RuntimeSnapshot for the transfer runtime (croc enabled).
/// Returns None if croc is disabled in config or the binary is not available.
fn build_transfer_runtime(config: &Config) -> Option<RuntimeSnapshot> {
    let croc_config = &config.transfer.croc;

    if !croc_config.enabled {
        return None;
    }

    // Probe croc binary existence
    let binary_path = &croc_config.binary_path;
    if !std::path::Path::new(binary_path).exists() {
        return None;
    }

    Some(RuntimeSnapshot {
        runtime_id: "linux-transfer".into(),
        kind: "privileged".into(),
        status: "online".into(),
        interactive: false,
        privilege: Some(RuntimePrivilege::User),
        labels: Some(vec!["linux".into(), "transfer".into()]),
        owner: None,
        metadata: Some(serde_json::json!({
            "croc_binary_path": binary_path,
            "temp_dir": croc_config.temp_dir.to_string_lossy(),
            "allow_send": croc_config.allow_send,
            "allow_receive": croc_config.allow_receive,
            "relay_url": croc_config.relay_url,
        })),
    })
}
