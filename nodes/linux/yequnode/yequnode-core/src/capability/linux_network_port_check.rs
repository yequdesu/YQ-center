use std::net::{IpAddr, SocketAddr};
use std::time::Instant;

use async_trait::async_trait;
use serde_json::{json, Value};
use tokio::net::TcpStream;
use tokio::time::{timeout, Duration};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxNetworkPortCheck;

#[async_trait]
impl Capability for LinuxNetworkPortCheck {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.network.port_check".into(),
            description: "Check TCP connectivity from the Linux node to one host and port.".into(),
            agent_description: Some(
                "Check whether a TCP host:port is reachable from the Linux node.".into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Hostname or IP address to connect to."
                    },
                    "port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 30000,
                        "default": 3000
                    }
                },
                "required": ["host", "port"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                    "reachable": {"type": "boolean"},
                    "latency_ms": {"type": ["number", "null"]},
                    "error_code": {"type": ["string", "null"]},
                    "error_message": {"type": ["string", "null"]}
                },
                "required": ["host", "port", "reachable"],
                "additionalProperties": false
            })),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 35,
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
            preconditions: vec![
                json!({"fact": "host.explicit", "source": "intent"}),
                json!({"fact": "port.explicit", "source": "intent"}),
            ],
            required_intent_slots: vec!["host".into(), "port".into()],
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let host = input.get("host").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "host".into(),
                message: "host is required".into(),
            }
        })?;
        if host.trim().is_empty() || host.contains('/') || host.contains("://") {
            return Err(CapabilityError::InvalidInput {
                field: "host".into(),
                message: "host must not include a URL scheme, slash, or path".into(),
            });
        }
        let port = input.get("port").and_then(|v| v.as_u64()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "port".into(),
                message: "port is required".into(),
            }
        })?;
        if port == 0 || port > 65535 {
            return Err(CapabilityError::InvalidInput {
                field: "port".into(),
                message: "port must be between 1 and 65535".into(),
            });
        }
        let timeout_ms = input
            .get("timeout_ms")
            .and_then(|v| v.as_u64())
            .unwrap_or(3000)
            .clamp(100, 30_000);

        check_port(host, port as u16, timeout_ms).await
    }
}

async fn check_port(host: &str, port: u16, timeout_ms: u64) -> Result<Value, CapabilityError> {
    let start = Instant::now();
    let connect_future = async {
        if let Ok(ip) = host.parse::<IpAddr>() {
            TcpStream::connect(SocketAddr::new(ip, port)).await
        } else {
            TcpStream::connect((host, port)).await
        }
    };

    match timeout(Duration::from_millis(timeout_ms), connect_future).await {
        Ok(Ok(_stream)) => {
            let latency_ms = start.elapsed().as_secs_f64() * 1000.0;
            Ok(json!({
                "host": host,
                "port": port,
                "reachable": true,
                "latency_ms": latency_ms,
                "error_code": null,
                "error_message": null,
            }))
        }
        Ok(Err(error)) => Ok(json!({
            "host": host,
            "port": port,
            "reachable": false,
            "latency_ms": null,
            "error_code": "connection_failed",
            "error_message": error.to_string(),
        })),
        Err(_) => Ok(json!({
            "host": host,
            "port": port,
            "reachable": false,
            "latency_ms": null,
            "error_code": "timeout",
            "error_message": format!("connect timed out after {}ms", timeout_ms),
        })),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::net::TcpListener;

    #[tokio::test]
    async fn port_check_reports_reachable_listener() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let _accept_task = tokio::spawn(async move {
            let _ = listener.accept().await;
        });

        let output = LinuxNetworkPortCheck::execute(json!({
            "host": "127.0.0.1",
            "port": port,
            "timeout_ms": 1000
        }))
        .await
        .unwrap();

        assert_eq!(output["reachable"], true);
        assert_eq!(output["port"].as_u64().unwrap(), u64::from(port));
    }

    #[tokio::test]
    async fn port_check_rejects_url_input() {
        let result = LinuxNetworkPortCheck::execute(json!({
            "host": "http://127.0.0.1",
            "port": 80
        }))
        .await;

        assert!(matches!(result, Err(CapabilityError::InvalidInput { .. })));
    }
}
