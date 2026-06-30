use async_trait::async_trait;
use serde_json::{json, Value};
use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxDiskDetail;

#[async_trait]
impl Capability for LinuxDiskDetail {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.disk.detail".into(),
            description: "Return disk usage for all mounted filesystems.".into(),
            agent_description: Some("Inspect disk capacity, used, available, and usage percent for each mount.".into()),
            input_schema: json!({"type": "object", "properties": {}, "additionalProperties": false}),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(), effect: "read".into(), timeout_sec: 5,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({"runtime_kind": "privileged", "labels": ["linux"]})),
            resource_keys: None, conflict_policy: None,
        }
    }

    async fn execute(_input: Value) -> Result<Value, CapabilityError> {
        let mounts = std::fs::read_to_string("/proc/mounts")
            .map_err(|e| CapabilityError::Internal(format!("read /proc/mounts: {}", e)))?;
        let mut disks = Vec::new();
        for line in mounts.lines() {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 2 { continue; }
            let mount = parts[1];
            if mount.starts_with("/proc") || mount.starts_with("/sys") || mount.starts_with("/dev") { continue; }
            match nix::sys::statfs::statfs(mount) {
                Ok(stat) => {
                    let total = stat.blocks() as u64 * stat.block_size() as u64;
                    let avail = stat.blocks_available() as u64 * stat.block_size() as u64;
                    let used = total.saturating_sub(avail);
                    let pct = if total > 0 { (used as f64 / total as f64) * 100.0 } else { 0.0 };
                    disks.push(json!({
                        "mount": mount, "filesystem": parts.get(0).unwrap_or(&"?"),
                        "total_bytes": total, "available_bytes": avail, "used_bytes": used,
                        "used_percent": (pct * 100.0).round() / 100.0,
                    }));
                }
                Err(_) => continue,
            }
        }
        Ok(json!({"mounts": disks, "count": disks.len()}))
    }
}
