use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxMetricsSnapshot;

#[async_trait]
impl Capability for LinuxMetricsSnapshot {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.metrics.snapshot".into(),
            description: "Return CPU, memory, disk and load metrics.".into(),
            agent_description: Some("Read a Linux metrics snapshot.".into()),
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 5,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux"]
            })),
            resource_keys: None,
            conflict_policy: None,
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![],
            required_intent_slots: vec![],
        }
    }

    async fn execute(_input: Value) -> Result<Value, CapabilityError> {
        let cpu_usage = cpu_usage_percent().unwrap_or(0.0);
        let mem = memory_info();
        let load = load_average();
        let disk = disk_usage_root();

        Ok(json!({
            "cpu_percent": cpu_usage,
            "memory": mem,
            "load": load,
            "disk": disk,
        }))
    }
}

fn cpu_usage_percent() -> Result<f64, CapabilityError> {
    let content = std::fs::read_to_string("/proc/stat")
        .map_err(|e| CapabilityError::Internal(format!("read /proc/stat: {}", e)))?;
    let line = content.lines().next().unwrap_or("");
    let fields: Vec<u64> = line
        .split_whitespace()
        .skip(1)
        .filter_map(|s| s.parse::<u64>().ok())
        .collect();
    if fields.len() < 4 {
        return Ok(0.0);
    }
    let idle = fields[3];
    let total: u64 = fields.iter().sum();
    Ok(if total > 0 {
        ((total - idle) as f64 / total as f64) * 100.0
    } else {
        0.0
    })
}

fn memory_info() -> Value {
    let content = match std::fs::read_to_string("/proc/meminfo") {
        Ok(c) => c,
        Err(_) => return json!({}),
    };
    let get_kb = |key: &str| -> Option<u64> {
        content
            .lines()
            .find(|l| l.starts_with(key))
            .and_then(|l| l.split_whitespace().nth(1))
            .and_then(|v| v.parse::<u64>().ok())
    };
    let total = get_kb("MemTotal").unwrap_or(0);
    let available = get_kb("MemAvailable").unwrap_or(0);
    let used = total.saturating_sub(available);
    let percent = if total > 0 {
        (used as f64 / total as f64) * 100.0
    } else {
        0.0
    };
    json!({
        "total_kb": total,
        "available_kb": available,
        "used_kb": used,
        "used_percent": (percent * 100.0).round() / 100.0,
    })
}

fn load_average() -> Value {
    let line = match std::fs::read_to_string("/proc/loadavg") {
        Ok(c) => c,
        Err(_) => return json!({}),
    };
    let parts: Vec<&str> = line.split_whitespace().collect();
    json!({
        "load1": parts.first().and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0),
        "load5": parts.get(1).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0),
        "load15": parts.get(2).and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0),
    })
}

fn disk_usage_root() -> Value {
    let mounts = match std::fs::read_to_string("/proc/mounts") {
        Ok(c) => c,
        Err(_) => return json!({}),
    };
    let root_device = mounts
        .lines()
        .find(|l| l.split_whitespace().nth(1) == Some("/"))
        .and_then(|l| l.split_whitespace().next())
        .unwrap_or("/dev/root");

    json!({
        "mount": "/",
        "device": root_device,
        "note": "disk usage requires statvfs syscall; reporting mount info only in v0.1",
    })
}
