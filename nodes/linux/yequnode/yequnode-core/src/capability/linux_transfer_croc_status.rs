use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxTransferCrocStatus;

#[async_trait]
impl Capability for LinuxTransferCrocStatus {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.transfer.croc.status".into(),
            description: "Return yq-croc transfer runtime availability and configuration.".into(),
            agent_description: Some(
                "Check whether yq-croc is installed and configured for file transfer on this Linux node. \
                 Returns runtime version, upstream croc version, relay mode, temp directory, and send/receive switches.".into()
            ),
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "object"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 15,
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
        let yq_croc_config = &config.transfer.yq_croc;
        let binary_path = &yq_croc_config.binary_path;
        let installed = std::path::Path::new(binary_path).exists();

        let version_probe = if installed {
            run_json(binary_path, &["version"]).await
        } else {
            Err(format!("yq-croc binary not found at {}", binary_path))
        };
        let local_probe = if installed {
            run_json(binary_path, &["probe", "--json"]).await
        } else {
            Err(format!("yq-croc binary not found at {}", binary_path))
        };
        let relay_probe = if installed {
            let mut args = vec!["relay-probe"];
            if let Some(relay) = yq_croc_config.relay_url.as_deref() {
                args.push("--relay");
                args.push(relay);
            }
            if let Some(env_name) = yq_croc_config.relay_password_env.as_deref() {
                args.push("--pass-env");
                args.push(env_name);
            }
            args.push("--timeout");
            args.push("5s");
            run_json(binary_path, &args).await
        } else {
            Err(format!("yq-croc binary not found at {}", binary_path))
        };

        let executable = version_probe.is_ok() && local_probe.is_ok();
        let relay_reachable = relay_probe
            .as_ref()
            .ok()
            .and_then(|v| v.get("relay_reachable"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let error = if !installed {
            Some(format!("yq-croc binary not found at {}", binary_path))
        } else if let Err(err) = &version_probe {
            Some(err.clone())
        } else if let Err(err) = &local_probe {
            Some(err.clone())
        } else if !yq_croc_config.enabled {
            Some("yq-croc transfer is disabled in config".to_string())
        } else if !relay_reachable {
            relay_probe.as_ref().err().cloned().or_else(|| {
                relay_probe
                    .as_ref()
                    .ok()
                    .and_then(|v| v.get("error"))
                    .and_then(|v| v.as_str())
                    .map(|v| v.to_string())
            })
        } else {
            None
        };

        let daemon_user = std::env::var("USER").unwrap_or_else(|_| "unknown".into());
        let version_json = version_probe.as_ref().ok();
        let local_json = local_probe.as_ref().ok();
        let relay_json = relay_probe.as_ref().ok();

        Ok(json!({
            "transport": "croc",
            "runtime": "yq-croc",
            "installed": installed,
            "executable": executable,
            "ready": installed && executable && yq_croc_config.enabled && relay_reachable,
            "binary_path": binary_path,
            "runtime_version": version_json
                .and_then(|v| v.get("runtime_version"))
                .or_else(|| local_json.and_then(|v| v.get("runtime_version"))),
            "upstream_croc_version": version_json
                .and_then(|v| v.get("upstream_croc_version"))
                .or_else(|| local_json.and_then(|v| v.get("upstream_croc_version"))),
            "relay_mode": if yq_croc_config.relay_url.is_some() { "configured" } else { "public_default" },
            "relay_url": relay_json
                .and_then(|v| v.get("relay_url"))
                .cloned()
                .or_else(|| yq_croc_config.relay_url.as_ref().map(|v| json!(v))),
            "relay_reachable": relay_reachable,
            "temp_dir": yq_croc_config.temp_dir.to_string_lossy(),
            "temp_dir_exists": yq_croc_config.temp_dir.exists(),
            "daemon_user": daemon_user,
            "allow_send": yq_croc_config.allow_send,
            "allow_receive": yq_croc_config.allow_receive,
            "enabled": yq_croc_config.enabled,
            "max_concurrent": yq_croc_config.max_concurrent,
            "limits": {
                "max_concurrent": yq_croc_config.max_concurrent,
            },
            "probe": local_probe.ok(),
            "relay_probe": relay_probe.ok(),
            "error": error,
        }))
    }
}

async fn run_json(binary_path: &str, args: &[&str]) -> Result<Value, String> {
    let output = tokio::process::Command::new(binary_path)
        .args(args)
        .output()
        .await
        .map_err(|e| format!("failed to execute yq-croc {:?}: {}", args, e))?;
    if !output.status.success() {
        return Err(format!(
            "yq-croc {:?} exited with {:?}: {}",
            args,
            output.status.code(),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    serde_json::from_slice::<Value>(&output.stdout)
        .map_err(|e| format!("invalid yq-croc JSON from {:?}: {}", args, e))
}

#[cfg(test)]
mod tests {
    use std::io::Write;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::Mutex;

    use serde_json::json;

    use crate::capability::Capability;

    use super::LinuxTransferCrocStatus;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[tokio::test]
    async fn status_reports_yq_croc_runtime_facts() {
        let _guard = ENV_LOCK.lock().unwrap();
        let temp = tempfile::tempdir().unwrap();
        let bin_path = temp.path().join("yq-croc");
        let home = temp.path().join("home");
        let config_dir = home.join(".yequnode");
        std::fs::create_dir_all(&config_dir).unwrap();

        let mut script = std::fs::File::create(&bin_path).unwrap();
        writeln!(
            script,
            r#"#!/bin/sh
case "$1" in
  version)
    printf '%s\n' '{{"runtime":"yq-croc","runtime_version":"test-runtime","upstream_croc_version":"v10.4.6"}}'
    ;;
  probe)
    printf '%s\n' '{{"runtime":"yq-croc","runtime_version":"test-runtime","upstream_croc_version":"v10.4.6","goos":"linux","goarch":"amd64"}}'
    ;;
  relay-probe)
    printf '%s\n' '{{"runtime":"yq-croc","relay_url":"relay.example:9009","relay_reachable":true}}'
    ;;
  *)
    exit 64
    ;;
esac
"#
        )
        .unwrap();
        drop(script);
        std::fs::set_permissions(&bin_path, std::fs::Permissions::from_mode(0o755)).unwrap();

        std::fs::write(
            config_dir.join("config.yaml"),
            format!(
                r#"node_token: test-token
transfer:
  yq_croc:
    binary_path: "{}"
    temp_dir: "{}"
"#,
                bin_path.display(),
                temp.path().join("transfers").display()
            ),
        )
        .unwrap();

        let old_home = std::env::var("HOME").ok();
        let old_token = std::env::var("YEQU_NODE_TOKEN").ok();
        std::env::set_var("HOME", &home);
        std::env::remove_var("YEQU_NODE_TOKEN");

        let output = LinuxTransferCrocStatus::execute(json!({})).await.unwrap();

        match old_home {
            Some(value) => std::env::set_var("HOME", value),
            None => std::env::remove_var("HOME"),
        }
        match old_token {
            Some(value) => std::env::set_var("YEQU_NODE_TOKEN", value),
            None => std::env::remove_var("YEQU_NODE_TOKEN"),
        }

        assert_eq!(output["runtime"], "yq-croc", "status output: {output}");
        assert_eq!(output["installed"], true, "status output: {output}");
        assert_eq!(output["executable"], true, "status output: {output}");
        assert_eq!(output["ready"], true, "status output: {output}");
        assert_eq!(output["relay_reachable"], true, "status output: {output}");
        assert_eq!(
            output["runtime_version"], "test-runtime",
            "status output: {output}"
        );
        assert_eq!(
            output["upstream_croc_version"], "v10.4.6",
            "status output: {output}"
        );
        assert_eq!(
            output["error"],
            serde_json::Value::Null,
            "status output: {output}"
        );
    }
}
