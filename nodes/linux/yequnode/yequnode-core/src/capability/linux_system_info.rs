use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxSystemInfo;

#[async_trait]
impl Capability for LinuxSystemInfo {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.system.info".into(),
            description: "Return basic Linux host information.".into(),
            agent_description: Some(
                "Inspect Linux host OS, kernel, uptime, CPU and memory summary.".into(),
            ),
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
        let hostname =
            read_first_line("/proc/sys/kernel/hostname").unwrap_or_else(|_| "unknown".into());
        let os_release = read_file("/etc/os-release").unwrap_or_default();
        let kernel = read_first_line("/proc/version").unwrap_or_else(|_| "unknown".into());
        let uptime_sec = read_first_line("/proc/uptime")
            .ok()
            .and_then(|s| s.split_whitespace().next().map(|v| v.to_string()))
            .and_then(|v| v.parse::<f64>().ok())
            .map(|v| v as u64);

        let cpu_count = count_matching_lines("/proc/cpuinfo", "processor");
        let cpu_model = read_cpu_model("/proc/cpuinfo");

        let mem_total_kb = parse_proc_meminfo_key("MemTotal");
        let mem_available_kb = parse_proc_meminfo_key("MemAvailable");

        // Identity — tells the Agent exactly who is executing these capabilities.
        // Prevents wrong inferences about permission failures.
        let current_user = std::env::var("USER").unwrap_or_else(|_| "unknown".into());
        let uid = unsafe { libc::getuid() };
        let gid = unsafe { libc::getgid() };
        let groups = current_groups();
        let has_sudo = check_sudo().unwrap_or(false);

        Ok(json!({
            "hostname": hostname.trim(),
            "os": parse_os_pretty_name(&os_release),
            "kernel": kernel.trim(),
            "arch": std::env::consts::ARCH,
            "uptime_sec": uptime_sec,
            "identity": {
                "user": current_user,
                "uid": uid,
                "gid": gid,
                "groups": groups,
                "sudo_available": has_sudo,
            },
            "cpu": {
                "count": cpu_count,
                "model": cpu_model,
            },
            "memory": {
                "total_kb": mem_total_kb,
                "available_kb": mem_available_kb,
            },
        }))
    }
}

// /proc helpers — these are private to this module

fn read_file(path: &str) -> Result<String, CapabilityError> {
    std::fs::read_to_string(path)
        .map_err(|e| CapabilityError::Internal(format!("read {} failed: {}", path, e)))
}

fn read_first_line(path: &str) -> Result<String, CapabilityError> {
    let content = read_file(path)?;
    Ok(content.lines().next().unwrap_or("").to_string())
}

fn count_matching_lines(path: &str, prefix: &str) -> u32 {
    read_file(path)
        .map(|c| c.lines().filter(|l| l.starts_with(prefix)).count() as u32)
        .unwrap_or(0)
}

fn parse_proc_meminfo_key(key: &str) -> Option<u64> {
    read_file("/proc/meminfo").ok().and_then(|content| {
        content.lines().find_map(|line| {
            if line.starts_with(key) {
                line.split_whitespace()
                    .nth(1)
                    .and_then(|v| v.parse::<u64>().ok())
            } else {
                None
            }
        })
    })
}

fn read_cpu_model(path: &str) -> String {
    read_file(path)
        .ok()
        .and_then(|content| {
            content
                .lines()
                .find(|l| l.starts_with("model name"))
                .and_then(|l| l.split(':').nth(1))
                .map(|s| s.trim().to_string())
        })
        .unwrap_or_else(|| "unknown".into())
}

fn parse_os_pretty_name(os_release: &str) -> String {
    os_release
        .lines()
        .find(|l| l.starts_with("PRETTY_NAME="))
        .and_then(|l| l.split('=').nth(1))
        .map(|v| v.trim_matches('"').to_string())
        .unwrap_or_else(|| "Linux".into())
}

fn current_groups() -> Vec<String> {
    let gid = unsafe { libc::getgid() };
    let mut groups = Vec::new();
    if let Ok(content) = std::fs::read_to_string("/etc/group") {
        for line in content.lines() {
            let parts: Vec<&str> = line.split(':').collect();
            if parts.len() >= 4 {
                let name = parts[0];
                let gid_str = parts[2];
                let members = parts[3];
                if let Ok(g) = gid_str.parse::<u32>() {
                    if g == gid {
                        groups.push(name.to_string());
                        continue;
                    }
                }
                let user = std::env::var("USER").unwrap_or_default();
                if members.split(',').any(|m| m == user) {
                    groups.push(name.to_string());
                }
            }
        }
    }
    groups
}

fn check_sudo() -> Result<bool, std::io::Error> {
    std::process::Command::new("sudo")
        .arg("-ln")
        .output()
        .map(|o| o.status.success())
}
