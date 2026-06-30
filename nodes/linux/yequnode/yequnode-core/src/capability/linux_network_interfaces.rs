use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxNetworkInterfaces;

#[async_trait]
impl Capability for LinuxNetworkInterfaces {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.network.interfaces".into(),
            description: "List network interfaces with IP addresses, MAC, and traffic counters.".into(),
            agent_description: Some("List network interfaces with IP addresses, MAC, and traffic counters.".into()),
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
        // Try JSON output first: ip -j addr
        let output = std::process::Command::new("ip")
            .args(["-j", "addr"])
            .output()
            .map_err(|e| CapabilityError::Internal(format!("failed to execute ip: {}", e)))?;

        if output.status.success() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            match serde_json::from_str::<Value>(&stdout) {
                Ok(parsed) => {
                    let interfaces = parse_json_interfaces(&parsed);
                    return Ok(json!({
                        "count": interfaces.len(),
                        "interfaces": interfaces,
                        "format": "json"
                    }));
                }
                Err(_) => {
                    // JSON parse failed, fall through to text fallback
                }
            }
        }

        // Fallback: ip addr (text output)
        let fallback_output = std::process::Command::new("ip")
            .args(["addr"])
            .output()
            .map_err(|e| CapabilityError::Internal(format!("failed to execute ip addr: {}", e)))?;

        let raw = String::from_utf8_lossy(&fallback_output.stdout).to_string();

        Ok(json!({
            "count": count_interfaces_text(&raw),
            "raw": raw,
            "format": "text"
        }))
    }
}

fn parse_json_interfaces(data: &Value) -> Vec<Value> {
    let arr = match data.as_array() {
        Some(a) => a,
        None => return Vec::new(),
    };

    arr.iter().map(|iface| {
        let name = iface.get("ifname").and_then(|v| v.as_str()).unwrap_or("unknown");
        let mac = iface.get("address").and_then(|v| v.as_str()).unwrap_or("");
        let operstate = iface.get("operstate").and_then(|v| v.as_str()).unwrap_or("unknown");
        let mtu = iface.get("mtu").and_then(|v| v.as_u64()).unwrap_or(0);

        let mut ipv4 = Vec::new();
        let mut ipv6 = Vec::new();

        if let Some(addr_info) = iface.get("addr_info").and_then(|v| v.as_array()) {
            for addr in addr_info {
                let local = addr.get("local").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let prefixlen = addr.get("prefixlen").and_then(|v| v.as_u64()).unwrap_or(0);
                let family = addr.get("family").and_then(|v| v.as_str()).unwrap_or("");
                if family == "inet" {
                    ipv4.push(json!({
                        "address": local,
                        "prefixlen": prefixlen
                    }));
                } else if family == "inet6" {
                    ipv6.push(json!({
                        "address": local,
                        "prefixlen": prefixlen
                    }));
                }
            }
        }

        // Traffic counters
        let stats64 = iface.get("stats64");
        let rx_bytes = stats64.and_then(|s| s.get("rx").and_then(|r| r.get("bytes").and_then(|v| v.as_u64()))).unwrap_or(0);
        let rx_packets = stats64.and_then(|s| s.get("rx").and_then(|r| r.get("packets").and_then(|v| v.as_u64()))).unwrap_or(0);
        let tx_bytes = stats64.and_then(|s| s.get("tx").and_then(|t| t.get("bytes").and_then(|v| v.as_u64()))).unwrap_or(0);
        let tx_packets = stats64.and_then(|s| s.get("tx").and_then(|t| t.get("packets").and_then(|v| v.as_u64()))).unwrap_or(0);

        json!({
            "name": name,
            "mac": mac,
            "operstate": operstate,
            "mtu": mtu,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "traffic": {
                "rx_bytes": rx_bytes,
                "rx_packets": rx_packets,
                "tx_bytes": tx_bytes,
                "tx_packets": tx_packets,
            }
        })
    }).collect()
}

fn count_interfaces_text(raw: &str) -> usize {
    raw.lines().filter(|l| l.starts_with(|c: char| c.is_ascii_digit() || c == ' ')).count()
}
