use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

/// Known flags to probe in croc --help output.
const PROBE_FLAGS: &[&str] = &[
    "--yes",
    "--quiet",
    "--disable-clipboard",
    "--overwrite",
    "--out",
    "--relay",
    "--pass",
];

/// Probe which flags are supported by the installed croc binary.
async fn probe_supported_flags(binary_path: &str) -> Vec<String> {
    let output = match tokio::process::Command::new(binary_path)
        .arg("--help")
        .output()
        .await
    {
        Ok(o) => o,
        Err(_) => return vec![],
    };

    let help_text = String::from_utf8_lossy(&output.stdout).to_lowercase();
    let mut supported = Vec::new();

    for flag in PROBE_FLAGS {
        // Check if the flag appears in the help text
        if help_text.contains(flag) || help_text.contains(&flag.replace("--", "")) {
            supported.push(flag.to_string());
        }
    }

    supported
}

async fn probe_version(binary_path: &str) -> (bool, Option<String>, Option<String>) {
    if !std::path::Path::new(binary_path).exists() {
        return (
            false,
            None,
            Some(format!("croc binary not found at {}", binary_path)),
        );
    }

    match tokio::process::Command::new(binary_path)
        .arg("--version")
        .output()
        .await
    {
        Ok(output) if output.status.success() => (
            true,
            Some(String::from_utf8_lossy(&output.stdout).trim().to_string()),
            None,
        ),
        Ok(output) => (
            false,
            None,
            Some(format!(
                "croc --version exited with {:?}: {}",
                output.status.code(),
                String::from_utf8_lossy(&output.stderr).trim()
            )),
        ),
        Err(e) => (
            false,
            None,
            Some(format!("croc is not executable by daemon user: {}", e)),
        ),
    }
}

pub struct LinuxTransferCrocStatus;

#[async_trait]
impl Capability for LinuxTransferCrocStatus {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.croc.status".into(),
            description: "Return croc transfer tool availability and configuration.".into(),
            agent_description: Some(
                "Check whether croc is installed and configured for file transfer on this Linux node. \
                 Returns binary path, version, relay configuration, temp directory, and transfer limits.".into()
            ),
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 10,
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
        let config = crate::config::Config::load()
            .map_err(|e| CapabilityError::Internal(format!("config load failed: {}", e)))?;

        let croc_config = &config.transfer.croc;

        let binary_path = &croc_config.binary_path;
        let installed = std::path::Path::new(binary_path).exists();
        let (executable, version, executable_error) = probe_version(binary_path).await;

        // Probe supported flags via --help
        let supported_flags = if executable {
            probe_supported_flags(binary_path).await
        } else {
            vec![]
        };

        // Check temp dir
        let temp_dir = &croc_config.temp_dir;
        let temp_dir_exists = temp_dir.exists();

        // Current user
        let daemon_user = std::env::var("USER").unwrap_or_else(|_| "unknown".into());

        Ok(json!({
            "installed": installed,
            "executable": executable,
            "binary_path": binary_path,
            "version": version,
            "supported_flags": supported_flags,
            "relay_url": croc_config.relay_url,
            "temp_dir": temp_dir.to_string_lossy(),
            "temp_dir_exists": temp_dir_exists,
            "daemon_user": daemon_user,
            "allow_send": croc_config.allow_send,
            "allow_receive": croc_config.allow_receive,
            "enabled": croc_config.enabled,
            "max_concurrent": croc_config.max_concurrent,
            "limits": {
                "max_concurrent": croc_config.max_concurrent,
            },
            "error": if !installed {
                Some(format!("croc binary not found at {}", binary_path))
            } else if !executable {
                executable_error
            } else if !croc_config.enabled {
                Some("croc transfer is disabled in config".to_string())
            } else {
                None
            },
        }))
    }
}
