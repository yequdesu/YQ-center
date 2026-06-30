use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxNetworkRoutes;

#[async_trait]
impl Capability for LinuxNetworkRoutes {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.network.routes".into(),
            description: "Return the IP routing table.".into(),
            agent_description: Some("Return the IP routing table.".into()),
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
        // Try JSON output first: ip -j route
        let output = std::process::Command::new("ip")
            .args(["-j", "route"])
            .output()
            .map_err(|e| CapabilityError::Internal(format!("failed to execute ip: {}", e)))?;

        if output.status.success() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            match serde_json::from_str::<Value>(&stdout) {
                Ok(parsed) => {
                    let routes = parse_json_routes(&parsed);
                    return Ok(json!({
                        "count": routes.len(),
                        "routes": routes,
                        "format": "json"
                    }));
                }
                Err(_) => {
                    // JSON parse failed, fall through to text fallback
                }
            }
        }

        // Fallback: ip route (text output)
        let fallback_output = std::process::Command::new("ip")
            .args(["route"])
            .output()
            .map_err(|e| CapabilityError::Internal(format!("failed to execute ip route: {}", e)))?;

        let raw = String::from_utf8_lossy(&fallback_output.stdout).to_string();

        Ok(json!({
            "count": raw.lines().count(),
            "raw": raw,
            "format": "text"
        }))
    }
}

fn parse_json_routes(data: &Value) -> Vec<Value> {
    let arr = match data.as_array() {
        Some(a) => a,
        None => return Vec::new(),
    };

    arr.iter()
        .map(|route| {
            let dst = route
                .get("dst")
                .and_then(|v| v.as_str())
                .unwrap_or("default");
            let dev = route.get("dev").and_then(|v| v.as_str()).unwrap_or("");
            let proto = route.get("protocol").and_then(|v| v.as_str()).unwrap_or("");
            let scope = route.get("scope").and_then(|v| v.as_str()).unwrap_or("");
            let table = route
                .get("table")
                .and_then(|v| v.as_str())
                .unwrap_or("main");
            let prefsrc = route.get("prefsrc").and_then(|v| v.as_str()).unwrap_or("");
            let metric = route.get("metric").and_then(|v| v.as_u64());
            let gateway = route.get("gateway").and_then(|v| v.as_str()).unwrap_or("");

            let mut entry = json!({
                "destination": dst,
                "device": dev,
                "protocol": proto,
                "scope": scope,
                "table": table,
            });

            if !prefsrc.is_empty() {
                entry["prefsrc"] = json!(prefsrc);
            }
            if let Some(m) = metric {
                entry["metric"] = json!(m);
            }
            if !gateway.is_empty() {
                entry["gateway"] = json!(gateway);
            }

            entry
        })
        .collect()
}
