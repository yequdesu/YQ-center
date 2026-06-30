use async_trait::async_trait;
use chrono::Utc;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxArtifactDiagnostics;

#[async_trait]
impl Capability for LinuxArtifactDiagnostics {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.artifact.diagnostics".into(),
            description: "Collect and upload a system diagnostics snapshot as an artifact.".into(),
            agent_description: Some(
                "Gather dmesg, recent journalctl entries, load average, and memory info into a single diagnostic artifact."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "sha256": {"type": "string"},
                    "download_url": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            })),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 15,
            idempotency: None,
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "privilege": "root",
                "labels": ["linux"]
            })),
            resource_keys: None,
            conflict_policy: None,
        }
    }

    async fn execute(_input: Value) -> Result<Value, CapabilityError> {
        let mut sections: Vec<String> = Vec::new();
        let mut body = String::new();

        // --- Section 1: dmesg ---
        sections.push("dmesg".into());
        body.push_str("========================================\n");
        body.push_str("SECTION: dmesg\n");
        body.push_str("========================================\n");
        let dmesg_output = tokio::process::Command::new("dmesg")
            .arg("--level")
            .arg("err,warn")
            .output()
            .await;
        match dmesg_output {
            Ok(output) if output.status.success() => {
                body.push_str(&String::from_utf8_lossy(&output.stdout));
            }
            Ok(output) => {
                body.push_str(&format!(
                    "[dmesg failed: code {:?}]\n",
                    output.status.code()
                ));
            }
            Err(e) => {
                body.push_str(&format!("[dmesg error: {}]\n", e));
            }
        }
        body.push('\n');

        // --- Section 2: journalctl (last 200 lines, priority err+) ---
        sections.push("journalctl".into());
        body.push_str("========================================\n");
        body.push_str("SECTION: journalctl --lines 200 --priority 3\n");
        body.push_str("========================================\n");
        let journal_output = tokio::process::Command::new("journalctl")
            .args(["--no-pager", "--lines", "200", "--priority", "3"])
            .output()
            .await;
        match journal_output {
            Ok(output) if output.status.success() => {
                body.push_str(&String::from_utf8_lossy(&output.stdout));
            }
            Ok(output) => {
                body.push_str(&format!(
                    "[journalctl failed: code {:?}]\n",
                    output.status.code()
                ));
            }
            Err(e) => {
                body.push_str(&format!("[journalctl error: {}]\n", e));
            }
        }
        body.push('\n');

        // --- Section 3: /proc/loadavg ---
        sections.push("loadavg".into());
        body.push_str("========================================\n");
        body.push_str("SECTION: /proc/loadavg\n");
        body.push_str("========================================\n");
        match std::fs::read_to_string("/proc/loadavg") {
            Ok(content) => body.push_str(&content),
            Err(e) => body.push_str(&format!("[error: {}]\n", e)),
        }
        body.push('\n');

        // --- Section 4: /proc/meminfo ---
        sections.push("meminfo".into());
        body.push_str("========================================\n");
        body.push_str("SECTION: /proc/meminfo\n");
        body.push_str("========================================\n");
        match std::fs::read_to_string("/proc/meminfo") {
            Ok(content) => body.push_str(&content),
            Err(e) => body.push_str(&format!("[error: {}]\n", e)),
        }
        body.push('\n');

        let data = body.into_bytes();

        let timestamp = Utc::now().format("%Y%m%d%H%M%S");
        let title = format!("diagnostics-{}.txt", timestamp);

        // Upload via global YQP client
        let client = super::YQP_CLIENT.get().ok_or_else(|| {
            CapabilityError::Internal(
                "YQP client not initialized; set_yqp_client() must be called at daemon startup".into(),
            )
        })?;

        let response = client
            .send_artifact_upload(
                "diagnostics",
                "text/plain",
                &title,
                &data,
                None, // summary
                None, // metadata
                None, // job_id
            )
            .await
            .map_err(|e| CapabilityError::Internal(format!("artifact upload failed: {}", e)))?;

        let detail = response.artifact;

        Ok(json!({
            "artifact_id": detail.artifact_id,
            "size_bytes": detail.size_bytes,
            "sha256": detail.sha256,
            "download_url": detail.download_url,
            "sections": sections,
        }))
    }
}
