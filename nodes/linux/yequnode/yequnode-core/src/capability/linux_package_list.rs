use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxPackageList;

#[async_trait]
impl Capability for LinuxPackageList {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.package.list".into(),
            description: "List installed system packages.".into(),
            agent_description: Some(
                "List all installed system packages with name and version. Supports dpkg and rpm."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "array"})),
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
        // Try dpkg first
        let packages =
            tokio::task::spawn_blocking(|| list_packages_dpkg().or_else(|_| list_packages_rpm()))
                .await
                .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))?
                .map_err(|e| {
                    CapabilityError::Internal(format!("package listing failed: {:?}", e))
                })?;

        Ok(json!(packages))
    }
}

#[derive(serde::Serialize)]
struct PackageEntry {
    name: String,
    version: String,
}

fn list_packages_dpkg() -> Result<Vec<PackageEntry>, CapabilityError> {
    let output = std::process::Command::new("dpkg-query")
        .args(["-W", "-f", "${Package}\t${Version}\t${Status}\n"])
        .output()
        .map_err(|e| CapabilityError::Internal(format!("dpkg-query execution failed: {}", e)))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(CapabilityError::Internal(format!(
            "dpkg-query exited with {}: {}",
            output.status,
            stderr.trim()
        )));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut packages = Vec::new();

    for line in stdout.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let parts: Vec<&str> = line.splitn(3, '\t').collect();
        if parts.len() >= 3 && parts[2].contains("installed") {
            packages.push(PackageEntry {
                name: parts[0].to_string(),
                version: parts[1].to_string(),
            });
        }
    }

    Ok(packages)
}

fn list_packages_rpm() -> Result<Vec<PackageEntry>, CapabilityError> {
    let output = std::process::Command::new("rpm")
        .args(["-qa", "--queryformat", "%{NAME}\t%{VERSION}\n"])
        .output()
        .map_err(|e| CapabilityError::Internal(format!("rpm execution failed: {}", e)))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(CapabilityError::Internal(format!(
            "rpm exited with {}: {}",
            output.status,
            stderr.trim()
        )));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut packages = Vec::new();

    for line in stdout.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let parts: Vec<&str> = line.splitn(2, '\t').collect();
        if parts.len() >= 2 {
            packages.push(PackageEntry {
                name: parts[0].to_string(),
                version: parts[1].to_string(),
            });
        }
    }

    Ok(packages)
}
