use async_trait::async_trait;
use serde_json::{json, Value};
use tokio::process::Command;

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;
use crate::config::Config;

pub struct LinuxTransferRcloneStatus;

#[async_trait]
impl Capability for LinuxTransferRcloneStatus {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.rclone.status".into(),
            description: "Return rclone SFTP transfer availability and configuration.".into(),
            agent_description: Some(
                "Check whether rclone is installed and configured for managed SFTP transfer."
                    .into(),
            ),
            input_schema: json!({"type": "object", "properties": {}, "additionalProperties": false}),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 10,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "transfer"]
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
        let config = Config::load().map_err(|e| CapabilityError::Internal(e.to_string()))?;
        let rclone = &config.transfer.rclone;
        let binary_path = &rclone.binary_path;
        let exists = std::path::Path::new(binary_path).exists();
        let version_output = if exists {
            Command::new(binary_path).arg("version").output().await.ok()
        } else {
            None
        };
        let executable = version_output
            .as_ref()
            .map(|output| output.status.success())
            .unwrap_or(false);
        let version = version_output.as_ref().and_then(|output| {
            String::from_utf8(output.stdout.clone())
                .ok()
                .and_then(|text| text.lines().next().map(str::to_string))
        });
        let temp_dir = &rclone.temp_dir;
        let temp_dir_exists = temp_dir.exists();
        let advertise_host = rclone
            .advertise_host
            .clone()
            .or_else(|| local_hostname().ok());
        let ready = rclone.enabled && exists && executable && advertise_host.is_some();
        Ok(json!({
            "transport": "rclone_sftp",
            "installed": exists,
            "executable": executable,
            "version": version,
            "binary_path": binary_path,
            "enabled": rclone.enabled,
            "allow_send": rclone.allow_send,
            "allow_receive": rclone.allow_receive,
            "advertise_host": advertise_host,
            "bind_host": rclone.bind_host,
            "listen_port": rclone.listen_port,
            "temp_dir": temp_dir.to_string_lossy(),
            "temp_dir_exists": temp_dir_exists,
            "max_concurrent_transfers": rclone.max_concurrent_transfers,
            "ready": ready,
        }))
    }
}

fn local_hostname() -> Result<String, std::io::Error> {
    let text = std::fs::read_to_string("/etc/hostname")?;
    Ok(text.trim().to_string())
}
