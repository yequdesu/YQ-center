use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxNetworkConnections;

#[async_trait]
impl Capability for LinuxNetworkConnections {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.network.connections".into(),
            description: "List current TCP and UDP connections with process info.".into(),
            agent_description: Some("List current TCP and UDP connections with process info.".into()),
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
        let output = std::process::Command::new("ss")
            .args(["-tunap"])
            .output()
            .map_err(|e| CapabilityError::Internal(format!("failed to execute ss: {}", e)))?;

        let raw = String::from_utf8_lossy(&output.stdout).to_string();

        let connections = parse_ss_output(&raw);

        Ok(json!({
            "count": connections.len(),
            "connections": connections,
        }))
    }
}

fn parse_ss_output(raw: &str) -> Vec<Value> {
    let mut entries = Vec::new();

    for line in raw.lines().skip(1) {
        // Skip header line
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() < 5 {
            continue;
        }

        // ss -tunap output format (columns vary by protocol):
        // Netid  State   Recv-Q   Send-Q   Local Address:Port    Peer Address:Port    Process
        let protocol = parts[0].to_string();
        let state = parts[1].to_string();
        let local_full = parts[4].to_string();
        let peer_full = parts[5].to_string();

        let (local_address, local_port) = split_address_port(&local_full);
        let (peer_address, peer_port) = split_address_port(&peer_full);

        let process = if parts.len() > 6 {
            // Process field looks like: "users:((\"nginx\",pid=1234,fd=6))"
            let proc_raw = parts[6..].join(" ");
            extract_process_name(&proc_raw)
        } else {
            String::new()
        };

        entries.push(json!({
            "protocol": protocol,
            "state": state,
            "local_address": local_address,
            "local_port": local_port,
            "peer_address": peer_address,
            "peer_port": peer_port,
            "process": process,
        }));
    }

    entries
}

/// Split "192.168.1.1:443" or "[::1]:22" into (address, port).
fn split_address_port(input: &str) -> (String, String) {
    // IPv6 is wrapped in brackets: [addr]:port
    if input.starts_with('[') {
        if let Some(close_bracket) = input.rfind("]:") {
            let addr = &input[1..close_bracket];
            let port = &input[close_bracket + 2..];
            return (addr.to_string(), port.to_string());
        }
        // Fallback: whole string as address
        return (input.to_string(), String::new());
    }

    // IPv4: addr:port
    if let Some(colon) = input.rfind(':') {
        let addr = &input[..colon];
        let port = &input[colon + 1..];
        (addr.to_string(), port.to_string())
    } else {
        (input.to_string(), String::new())
    }
}

/// Extract process name from ss process field like `users:((\"nginx\",pid=1234,fd=6))`
fn extract_process_name(raw: &str) -> String {
    // Look for quoted process name between \" and \"
    if let Some(start) = raw.find("\\\"") {
        let rest = &raw[start + 2..];
        if let Some(end) = rest.find("\\\"") {
            return rest[..end].to_string();
        }
    }
    // Alternative: try pid= extraction for raw output without escapes
    if let Some(pid_start) = raw.find("pid=") {
        let after_pid = &raw[pid_start + 4..];
        let pid: String = after_pid.chars().take_while(|c| c.is_ascii_digit()).collect();
        if !pid.is_empty() {
            return format!("pid={}", pid);
        }
    }
    String::new()
}
