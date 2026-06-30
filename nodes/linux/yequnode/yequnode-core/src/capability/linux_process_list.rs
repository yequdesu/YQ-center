use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;
use async_trait::async_trait;
use serde_json::{json, Value};

pub struct LinuxProcessList;

#[async_trait]
impl Capability for LinuxProcessList {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.process.list".into(),
            description: "List Linux processes with pid, user, cpu, memory and command.".into(),
            agent_description: Some("List running Linux processes.".into()),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "limit": { "type": "integer", "minimum": 1, "maximum": 200, "default": 50 }
                },
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

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let limit = input
            .get("limit")
            .and_then(|v| v.as_u64())
            .unwrap_or(50)
            .min(200) as usize;

        let processes = read_process_list(limit)?;

        Ok(json!({
            "count": processes.len(),
            "processes": processes,
            "partial": true,
            "note": "process list may be incomplete due to permission restrictions on /proc",
        }))
    }
}

#[derive(serde::Serialize)]
struct ProcessEntry {
    pid: u32,
    user: String,
    cpu_percent: f64,
    memory_kb: u64,
    command: String,
}

fn read_process_list(limit: usize) -> Result<Vec<ProcessEntry>, CapabilityError> {
    let mut entries = Vec::new();
    let proc_dir = std::fs::read_dir("/proc")
        .map_err(|e| CapabilityError::Internal(format!("cannot read /proc: {}", e)))?;

    for entry in proc_dir.take(limit * 3) {
        let entry = match entry {
            Ok(e) => e,
            Err(_) => continue,
        };
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        let pid: u32 = match name_str.parse() {
            Ok(p) => p,
            Err(_) => continue,
        };

        if entries.len() >= limit {
            break;
        }

        let comm = std::fs::read_to_string(entry.path().join("comm"))
            .unwrap_or_default()
            .trim()
            .to_string();

        let stat = match std::fs::read_to_string(entry.path().join("stat")) {
            Ok(s) => s,
            Err(_) => continue,
        };

        // Parse /proc/[pid]/stat — fields after comm (wrapped in parens)
        let stat_parts: Vec<&str> = stat.split(')').collect();
        let after_comm = stat_parts.get(1).unwrap_or(&"");
        let fields: Vec<&str> = after_comm.split_whitespace().collect();

        // Fields after comm (0-indexed from state char):
        //   field 11 -> utime, field 12 -> stime, field 21 -> rss
        let utime: u64 = fields.get(11).and_then(|v| v.parse().ok()).unwrap_or(0);
        let stime: u64 = fields.get(12).and_then(|v| v.parse().ok()).unwrap_or(0);
        let rss_pages: u64 = fields.get(21).and_then(|v| v.parse().ok()).unwrap_or(0);
        let page_size: u64 = 4096;

        // Get username from /proc/[pid]/status Uid field
        let status = std::fs::read_to_string(entry.path().join("status")).unwrap_or_default();
        let uid = status
            .lines()
            .find(|l| l.starts_with("Uid:"))
            .and_then(|l| l.split_whitespace().nth(1))
            .unwrap_or("?");
        let user = uid_to_name(uid).unwrap_or_else(|| uid.to_string());

        // CLK_TCK is 100 on Linux
        const CLK_TCK: u64 = 100;
        let total_cpu_ticks = utime + stime;
        let uptime_sec = std::fs::read_to_string("/proc/uptime")
            .ok()
            .and_then(|s| s.split_whitespace().next().map(|v| v.to_string()))
            .and_then(|v| v.parse::<f64>().ok())
            .unwrap_or(1.0);
        let cpu_percent = if uptime_sec > 0.0 {
            (total_cpu_ticks as f64 / CLK_TCK as f64 / uptime_sec) * 100.0
        } else {
            0.0
        };

        entries.push(ProcessEntry {
            pid,
            user,
            cpu_percent: (cpu_percent * 100.0).round() / 100.0,
            memory_kb: rss_pages * (page_size / 1024),
            command: comm,
        });
    }

    Ok(entries)
}

fn uid_to_name(uid: &str) -> Option<String> {
    let uid_num: u32 = uid.parse().ok()?;
    let passwd = std::fs::read_to_string("/etc/passwd").ok()?;
    passwd.lines().find_map(|line| {
        let parts: Vec<&str> = line.split(':').collect();
        if parts.len() >= 3 && parts[2].parse::<u32>().ok() == Some(uid_num) {
            Some(parts[0].to_string())
        } else {
            None
        }
    })
}
