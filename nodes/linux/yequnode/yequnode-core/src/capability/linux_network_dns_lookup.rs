use std::collections::BTreeSet;
use std::net::ToSocketAddrs;

use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxNetworkDnsLookup;

#[async_trait]
impl Capability for LinuxNetworkDnsLookup {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.network.dns_lookup".into(),
            description: "Resolve a hostname using the Linux node's system DNS resolver.".into(),
            agent_description: Some(
                "Resolve DNS for a hostname from the Linux node's network perspective.".into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "hostname": {
                        "type": "string",
                        "description": "Hostname to resolve, without scheme or path."
                    },
                    "port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                        "default": 80,
                        "description": "Port used only to satisfy the system resolver API."
                    }
                },
                "required": ["hostname"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "hostname": {"type": "string"},
                    "port": {"type": "integer"},
                    "addresses": {"type": "array", "items": {"type": "string"}},
                    "count": {"type": "integer"}
                },
                "required": ["hostname", "port", "addresses", "count"],
                "additionalProperties": false
            })),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 10,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux", "network"]
            })),
            resource_keys: None,
            conflict_policy: None,
            supports_progress: false,
            supports_cancel: false,
            supports_resume: false,
            progress_contract: None,
            preconditions: vec![json!({"fact": "hostname.explicit", "source": "intent"})],
            required_intent_slots: vec!["hostname".into()],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let hostname = input
            .get("hostname")
            .and_then(|v| v.as_str())
            .ok_or_else(|| CapabilityError::InvalidInput {
                field: "hostname".into(),
                message: "hostname is required".into(),
            })?;
        if hostname.trim().is_empty() || hostname.contains('/') || hostname.contains("://") {
            return Err(CapabilityError::InvalidInput {
                field: "hostname".into(),
                message: "hostname must not include a URL scheme, slash, or path".into(),
            });
        }
        let port = input.get("port").and_then(|v| v.as_u64()).unwrap_or(80);
        if port == 0 || port > 65535 {
            return Err(CapabilityError::InvalidInput {
                field: "port".into(),
                message: "port must be between 1 and 65535".into(),
            });
        }
        let hostname_owned = hostname.to_string();

        tokio::task::spawn_blocking(move || resolve_hostname(&hostname_owned, port as u16))
            .await
            .map_err(|e| CapabilityError::Internal(format!("dns lookup worker failed: {}", e)))?
    }
}

fn resolve_hostname(hostname: &str, port: u16) -> Result<Value, CapabilityError> {
    let addresses =
        (hostname, port)
            .to_socket_addrs()
            .map_err(|e| CapabilityError::ExternalServiceFailed {
                detail: format!("dns lookup failed for {}: {}", hostname, e),
            })?;

    let mut unique = BTreeSet::new();
    for address in addresses {
        unique.insert(address.ip().to_string());
    }
    let addresses: Vec<String> = unique.into_iter().collect();

    Ok(json!({
        "hostname": hostname,
        "port": port,
        "addresses": addresses,
        "count": addresses.len(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn dns_lookup_resolves_localhost() {
        let output = LinuxNetworkDnsLookup::execute(json!({"hostname": "localhost"}))
            .await
            .unwrap();

        assert_eq!(output["hostname"], "localhost");
        assert!(output["count"].as_u64().unwrap() >= 1);
    }

    #[tokio::test]
    async fn dns_lookup_rejects_url_input() {
        let result =
            LinuxNetworkDnsLookup::execute(json!({"hostname": "https://example.com"})).await;

        assert!(matches!(result, Err(CapabilityError::InvalidInput { .. })));
    }
}
