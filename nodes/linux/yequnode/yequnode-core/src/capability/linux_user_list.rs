use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxUserList;

#[async_trait]
impl Capability for LinuxUserList {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.user.list".into(),
            description: "List local users and current login sessions.".into(),
            agent_description: Some(
                "List local user accounts from /etc/passwd and currently logged-in sessions from who(1).".into()
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
        }
    }

    async fn execute(_input: Value) -> Result<Value, CapabilityError> {
        let users = tokio::task::spawn_blocking(|| parse_passwd())
            .await
            .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))?
            .unwrap_or_default();

        let sessions = tokio::task::spawn_blocking(|| run_who())
            .await
            .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))?
            .unwrap_or_default();

        Ok(json!({
            "users": users,
            "sessions": sessions,
        }))
    }
}

fn parse_passwd() -> Result<Vec<Value>, CapabilityError> {
    let content = std::fs::read_to_string("/etc/passwd")
        .map_err(|e| CapabilityError::Internal(format!("read /etc/passwd failed: {}", e)))?;

    let mut users = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }

        let parts: Vec<&str> = line.split(':').collect();
        if parts.len() >= 7 {
            users.push(json!({
                "username": parts[0],
                "uid": parts[2].parse::<u32>().ok(),
                "gid": parts[3].parse::<u32>().ok(),
                "home": parts[5],
                "shell": parts[6],
            }));
        }
    }

    Ok(users)
}

fn run_who() -> Result<Vec<Value>, CapabilityError> {
    let output = std::process::Command::new("who")
        .output()
        .map_err(|e| CapabilityError::Internal(format!("who execution failed: {}", e)))?;

    if !output.status.success() {
        return Ok(Vec::new());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut sessions = Vec::new();

    for line in stdout.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 2 {
            sessions.push(json!({
                "user": parts[0],
                "tty": parts[1],
                "date": if parts.len() >= 3 { parts[2..].join(" ") } else { String::new() },
            }));
        }
    }

    Ok(sessions)
}
