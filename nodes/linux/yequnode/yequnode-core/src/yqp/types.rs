use serde::{Deserialize, Serialize};
use serde_json::Value;

// ---------------------------------------------------------------------------
// MessageType -- all YQP message identifiers per contract Section 4
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageType {
    // node lifecycle
    NodeHello,
    NodeAccepted,
    NodeHeartbeat,
    NodeRegisterCapabilities,
    RegistryAccepted,
    // signal
    SignalReport,
    // job
    JobPoll,
    JobAvailable,
    JobEmpty,
    JobAccepted,
    JobFinished,
    JobLeaseRenew,
    JobLeaseAccepted,
    JobEvent,
    JobCancel,
    // reconcile
    NodeReconcileJobs,
    JobReconciliation,
    // artifact
    ArtifactUpload,
    ArtifactAccepted,
}

impl MessageType {
    pub fn as_str(&self) -> &'static str {
        match self {
            MessageType::NodeHello => "node.hello",
            MessageType::NodeAccepted => "node.accepted",
            MessageType::NodeHeartbeat => "node.heartbeat",
            MessageType::NodeRegisterCapabilities => "node.register_capabilities",
            MessageType::RegistryAccepted => "registry.accepted",
            MessageType::SignalReport => "signal.report",
            MessageType::JobPoll => "job.poll",
            MessageType::JobAvailable => "job.available",
            MessageType::JobEmpty => "job.empty",
            MessageType::JobAccepted => "job.accepted",
            MessageType::JobFinished => "job.finished",
            MessageType::JobLeaseRenew => "job.lease_renew",
            MessageType::JobLeaseAccepted => "job.lease_accepted",
            MessageType::JobEvent => "job.event",
            MessageType::JobCancel => "job.cancel",
            MessageType::NodeReconcileJobs => "node.reconcile_jobs",
            MessageType::JobReconciliation => "job.reconciliation",
            MessageType::ArtifactUpload => "artifact.upload",
            MessageType::ArtifactAccepted => "artifact.accepted",
        }
    }
}

impl std::fmt::Display for MessageType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

// ---------------------------------------------------------------------------
// Runtime (contract Section 7)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeSnapshot {
    pub runtime_id: String,
    pub kind: String,                        // privileged | interactive | wasm | docker
    pub status: String,                      // online | degraded | offline
    #[serde(default)]
    pub interactive: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub privilege: Option<RuntimePrivilege>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub labels: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub owner: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimePrivilege {
    User,
    Admin,
    Root,
    #[serde(untagged)]
    Custom(String),
}

// ---------------------------------------------------------------------------
// node.hello (contract Section 6)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HelloPayload {
    pub daemon_version: String,
    pub platform: PlatformInfo,
    pub runtimes: Vec<RuntimeSnapshot>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlatformInfo {
    pub os: String,
    pub arch: String,
}

// ---------------------------------------------------------------------------
// node.accepted (contract Section 6)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcceptedPayload {
    pub heartbeat_interval_sec: u64,
    #[serde(default)]
    pub heartbeat_timeout_multiplier: u64,
    #[serde(default)]
    pub signal_report_interval_sec: u64,
    #[serde(default)]
    pub signal_stale_multiplier: u64,
    #[serde(default)]
    pub job_delivery_mode: String,
    pub job_poll_interval_sec: u64,
    #[serde(default)]
    pub server_time: String,
}

// ---------------------------------------------------------------------------
// node.heartbeat (contract Section 9)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HeartbeatPayload {
    pub daemon_uptime_sec: u64,
    pub running_jobs: Vec<String>,
    pub plugin_count: u32,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtimes: Option<Vec<RuntimeSnapshot>>,
}

// ---------------------------------------------------------------------------
// node.register_capabilities (contract Section 8)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegisterCapabilitiesPayload {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtimes: Option<Vec<RuntimeSnapshot>>,
    pub plugins: Vec<PluginManifest>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginManifest {
    pub plugin_id: String,
    pub plugin_version: String,
    pub functions: Vec<FunctionManifest>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub signals: Vec<SignalManifest>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionManifest {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_visible_name: Option<String>,
    pub input_schema: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_schema: Option<Value>,
    pub risk: String,                        // safe | maintenance | destructive | catastrophic
    pub effect: String,                      // read | write | destructive | external
    #[serde(default = "default_timeout_sec")]
    pub timeout_sec: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idempotency: Option<String>,         // idempotent | non_idempotent | transactional
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_context: Option<String>,   // system | user | hybrid
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_requirements: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resource_keys: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conflict_policy: Option<String>,     // allow_parallel | serialize | reject_if_running
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hidden_input_fields: Option<Vec<String>>,
    #[serde(default)]
    pub preflight_supported: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalManifest {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,               // node | plugin | resource
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ttl_sec: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value_schema: Option<Value>,
}

// ---------------------------------------------------------------------------
// signal.report (contract Section 10)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalReportPayload {
    pub signals: Vec<SignalValue>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalValue {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,
    pub value: Value,
    pub collected_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ttl_sec: Option<u32>,
}

// ---------------------------------------------------------------------------
// job.poll (contract Section 11)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobPollPayload {
    pub capacity: u32,
    pub running_jobs: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobAvailablePayload {
    pub jobs: Vec<JobDescriptor>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobDescriptor {
    pub job_id: String,
    pub invocation_id: String,
    pub function: String,
    pub input: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_requirements: Option<Value>,
    #[serde(default = "default_timeout_sec")]
    pub timeout_sec: u32,
    #[serde(default = "default_lease_sec")]
    pub lease_sec: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub approval_id: Option<String>,
    #[serde(default)]
    pub resource_keys: Vec<String>,
    #[serde(default)]
    pub dry_run: bool,
}

// ---------------------------------------------------------------------------
// job.accepted (contract Section 12)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobAcceptedPayload {
    pub job_id: String,
}

// ---------------------------------------------------------------------------
// job.finished (contract Section 14)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobFinishedPayload {
    pub job_id: String,
    pub status: String,                      // succeeded | failed | cancelled | timeout
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JobErrorDetail>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobErrorDetail {
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<Value>,
}

// ---------------------------------------------------------------------------
// job.event (contract Section 13)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobEventPayload {
    pub job_id: String,
    pub event_type: String,
    pub sequence: u32,
    pub data: Value,
}

// ---------------------------------------------------------------------------
// job.lease_renew (contract Section 15)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobLeaseRenewPayload {
    pub job_id: String,
    #[serde(default = "default_lease_sec")]
    pub lease_extend_sec: u32,
}

// ---------------------------------------------------------------------------
// node.reconcile_jobs / job.reconciliation (contract Section 17)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReconcilePayload {
    pub known_jobs: Vec<KnownJob>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnownJob {
    pub job_id: String,
    pub local_status: String,                // running | succeeded | failed | cancelled | timeout
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started_at: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReconciliationPayload {
    pub actions: Vec<ReconciliationAction>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReconciliationAction {
    pub job_id: String,
    pub action: String,                      // continue | cancel | accept_result | discard_result | forget
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lease_sec: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reconciled: Option<bool>,
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const fn default_timeout_sec() -> u32 {
    30
}

const fn default_lease_sec() -> u32 {
    30
}

// ---------------------------------------------------------------------------
// artifact.upload / artifact.accepted
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactUploadPayload {
    pub artifact_type: String,
    pub content_type: String,
    pub title: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub summary: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub job_id: Option<String>,
    pub data_base64: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactAcceptedPayload {
    pub artifact: ArtifactDetail,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactDetail {
    pub artifact_id: String,
    pub node_id: String,
    pub artifact_type: String,
    pub content_type: String,
    pub title: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub download_url: String,
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_message_type_as_str() {
        assert_eq!(MessageType::NodeHello.as_str(), "node.hello");
        assert_eq!(MessageType::NodeHeartbeat.as_str(), "node.heartbeat");
        assert_eq!(MessageType::JobPoll.as_str(), "job.poll");
        assert_eq!(MessageType::JobEmpty.as_str(), "job.empty");
        assert_eq!(MessageType::RegistryAccepted.as_str(), "registry.accepted");
        assert_eq!(MessageType::NodeReconcileJobs.as_str(), "node.reconcile_jobs");
        assert_eq!(MessageType::JobReconciliation.as_str(), "job.reconciliation");
    }

    #[test]
    fn test_runtime_snapshot_minimal() {
        let rt = RuntimeSnapshot {
            runtime_id: "user".into(),
            kind: "privileged".into(),
            status: "online".into(),
            interactive: false,
            privilege: None,
            labels: Some(vec!["linux".into(), "filesystem:limited".into()]),
            owner: None,
            metadata: None,
        };
        let v = serde_json::to_value(&rt).unwrap();
        assert_eq!(v["runtime_id"], "user");
        assert_eq!(v["labels"], json!(["linux", "filesystem:limited"]));
        // owner absent
        assert!(v.get("owner").is_none());
    }

    #[test]
    fn test_hello_payload_roundtrip() {
        let hello = HelloPayload {
            daemon_version: "0.1.0".into(),
            platform: PlatformInfo {
                os: "linux".into(),
                arch: "x86_64".into(),
            },
            runtimes: vec![RuntimeSnapshot {
                runtime_id: "user".into(),
                kind: "privileged".into(),
                status: "online".into(),
                interactive: false,
                privilege: Some(RuntimePrivilege::User),
                labels: Some(vec!["linux".into()]),
                owner: None,
                metadata: None,
            }],
        };
        let json = serde_json::to_string(&hello).unwrap();
        let parsed: HelloPayload = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.daemon_version, "0.1.0");
        assert_eq!(parsed.platform.os, "linux");
        assert_eq!(parsed.runtimes.len(), 1);
        assert_eq!(parsed.runtimes[0].status, "online");
    }

    #[test]
    fn test_platform_info_no_extras() {
        let p = PlatformInfo { os: "linux".into(), arch: "x86_64".into() };
        let v = serde_json::to_value(&p).unwrap();
        assert_eq!(v.as_object().unwrap().len(), 2);
    }

    #[test]
    fn test_accepted_payload_deser() {
        let json = json!({
            "heartbeat_interval_sec": 10,
            "heartbeat_timeout_multiplier": 3,
            "signal_report_interval_sec": 5,
            "signal_stale_multiplier": 3,
            "job_delivery_mode": "poll",
            "job_poll_interval_sec": 3,
            "server_time": "2026-06-28T12:00:00Z"
        });
        let a: AcceptedPayload = serde_json::from_value(json).unwrap();
        assert_eq!(a.heartbeat_interval_sec, 10);
        assert_eq!(a.job_poll_interval_sec, 3);
    }

    #[test]
    fn test_heartbeat_payload_contract_fields() {
        let hb = HeartbeatPayload {
            daemon_uptime_sec: 3600,
            running_jobs: vec!["job_1".into()],
            plugin_count: 2,
            status: "online".into(),
            runtimes: Some(vec![RuntimeSnapshot {
                runtime_id: "default".into(),
                kind: "privileged".into(),
                status: "online".into(),
                interactive: false,
                privilege: None,
                labels: Some(vec!["linux".into()]),
                owner: None,
                metadata: None,
            }]),
        };
        let v = serde_json::to_value(&hb).unwrap();
        assert_eq!(v["daemon_uptime_sec"], 3600);
        assert_eq!(v["running_jobs"], json!(["job_1"]));
        assert_eq!(v["plugin_count"], 2);
        assert_eq!(v["status"], "online");
    }

    #[test]
    fn test_plugin_manifest_functions_always_array() {
        let p = PluginManifest {
            plugin_id: "linux.system".into(),
            plugin_version: "0.1.0".into(),
            functions: vec![],
            signals: vec![],
        };
        let v = serde_json::to_value(&p).unwrap();
        assert!(v["functions"].is_array());
    }

    #[test]
    fn test_function_manifest_contract_fields() {
        let fm = FunctionManifest {
            name: "linux.system.info".into(),
            description: "desc".into(),
            agent_description: Some("agent desc".into()),
            user_visible_name: None,
            input_schema: json!({"type": "object"}),
            output_schema: None,
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 5,
            idempotency: Some("idempotent".into()),
            execution_context: None,
            execution_requirements: Some(json!({"runtime_kind": "privileged", "labels": ["linux"]})),
            resource_keys: None,
            conflict_policy: None,
            hidden_input_fields: None,
            preflight_supported: false,
        };
        let v = serde_json::to_value(&fm).unwrap();
        assert_eq!(v["risk"], "safe");
        assert_eq!(v["execution_requirements"]["labels"], json!(["linux"]));
    }

    #[test]
    fn test_signal_manifest_contract_fields() {
        let sm = SignalManifest {
            name: "cpu.load".into(),
            scope: Some("node".into()),
            ttl_sec: Some(30),
            value_schema: Some(json!({"type": "object"})),
        };
        let v = serde_json::to_value(&sm).unwrap();
        assert_eq!(v["name"], "cpu.load");
        assert_eq!(v["scope"], "node");
        assert_eq!(v["ttl_sec"], 30);
    }

    #[test]
    fn test_job_poll_payload() {
        let p = JobPollPayload {
            capacity: 2,
            running_jobs: vec!["job_1".into()],
        };
        let v = serde_json::to_value(&p).unwrap();
        assert_eq!(v["capacity"], 2);
        assert_eq!(v["running_jobs"], json!(["job_1"]));
    }

    #[test]
    fn test_job_descriptor_defaults() {
        let desc = JobDescriptor {
            job_id: "j1".into(),
            invocation_id: "i1".into(),
            function: "test.fn".into(),
            input: json!({"x": 1}),
            runtime_id: None,
            execution_requirements: None,
            timeout_sec: 30,
            lease_sec: 30,
            approval_id: None,
            resource_keys: vec![],
            dry_run: false,
        };
        let json = serde_json::to_string(&desc).unwrap();
        let parsed: JobDescriptor = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.timeout_sec, 30);
        assert_eq!(parsed.lease_sec, 30);
        assert!(!parsed.dry_run);
    }

    #[test]
    fn test_job_finished_payload() {
        let jf = JobFinishedPayload {
            job_id: "j1".into(),
            status: "succeeded".into(),
            output: Some(json!({"ok": true})),
            error_code: None,
            error_message: None,
            error: None,
        };
        let v = serde_json::to_value(&jf).unwrap();
        assert_eq!(v["job_id"], "j1");
        assert_eq!(v["status"], "succeeded");
    }

    #[test]
    fn test_job_error_detail() {
        let err = JobErrorDetail {
            code: "function_execution_failed".into(),
            message: "exit code 1".into(),
            details: Some(json!({"exit_code": 1, "stderr": "error"})),
        };
        let json = serde_json::to_string(&err).unwrap();
        let parsed: JobErrorDetail = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.code, "function_execution_failed");
    }

    #[test]
    fn test_reconcile_payload() {
        let p = ReconcilePayload {
            known_jobs: vec![KnownJob {
                job_id: "j1".into(),
                local_status: "running".into(),
                started_at: Some("2026-06-28T12:00:00Z".into()),
                updated_at: Some("2026-06-28T12:00:10Z".into()),
                output: None,
            }],
        };
        let v = serde_json::to_value(&p).unwrap();
        let jobs = &v["known_jobs"].as_array().unwrap();
        assert_eq!(jobs[0]["job_id"], "j1");
        assert_eq!(jobs[0]["local_status"], "running");
    }

    #[test]
    fn test_reconciliation_action() {
        let a = ReconciliationAction {
            job_id: "j1".into(),
            action: "continue".into(),
            lease_sec: Some(30),
            reconciled: None,
        };
        let v = serde_json::to_value(&a).unwrap();
        assert_eq!(v["action"], "continue");
        assert_eq!(v["lease_sec"], 30);
    }

    #[test]
    fn test_runtime_privilege_serde() {
        assert_eq!(
            serde_json::to_value(RuntimePrivilege::User).unwrap(),
            json!("user")
        );
        assert_eq!(
            serde_json::to_value(RuntimePrivilege::Root).unwrap(),
            json!("root")
        );
    }
}
