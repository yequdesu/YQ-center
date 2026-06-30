use serde::{Deserialize, Serialize};
use serde_json::Value;

// ---------------------------------------------------------------------------
// YqpEnvelope -- wire-level message container
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct YqpEnvelope {
    pub yqp_version: String,
    pub message_id: String,
    pub message_type: String,
    pub trace_id: String,
    pub node_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    pub timestamp: String,
    pub payload: Value,
}

impl YqpEnvelope {
    pub fn new(
        message_type: impl Into<String>,
        node_id: impl Into<String>,
        payload: Value,
    ) -> Self {
        Self {
            yqp_version: "0.1".into(),
            message_id: format!("msg_{}", uuid::Uuid::new_v4().simple()),
            message_type: message_type.into(),
            trace_id: format!("tr_{}", uuid::Uuid::new_v4().simple()),
            node_id: node_id.into(),
            session_id: None,
            timestamp: chrono::Utc::now().to_rfc3339(),
            payload,
        }
    }
}

// ---------------------------------------------------------------------------
// YqpResponse -- parsed reply envelope
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct YqpResponse {
    pub yqp_version: String,
    pub message_id: String,
    pub message_type: String,
    pub trace_id: String,
    pub node_id: String,
    pub timestamp: String,
    pub payload: Value,
}

// ---------------------------------------------------------------------------
// YqpErrorDetail -- structured error carried inside a response payload
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct YqpErrorDetail {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: Value,
}

// ---------------------------------------------------------------------------
// YqpError -- unified error type for the YQP client
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum YqpError {
    HttpError { status: u16, detail: YqpErrorDetail },
    NetworkError { message: String, retryable: bool },
    JsonError(String),
}

impl YqpError {
    pub fn is_retryable(&self) -> bool {
        match self {
            YqpError::HttpError { detail, .. } => detail.retryable,
            YqpError::NetworkError { retryable, .. } => *retryable,
            YqpError::JsonError(_) => false,
        }
    }

    pub fn error_code(&self) -> &str {
        match self {
            YqpError::HttpError { detail, .. } => &detail.code,
            YqpError::NetworkError { .. } => "network_error",
            YqpError::JsonError(_) => "json_error",
        }
    }

    pub fn error_message(&self) -> &str {
        match self {
            YqpError::HttpError { detail, .. } => &detail.message,
            YqpError::NetworkError { message, .. } => message,
            YqpError::JsonError(m) => m,
        }
    }
}

impl std::fmt::Display for YqpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.error_code(), self.error_message())
    }
}

impl std::error::Error for YqpError {}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_envelope_roundtrip() {
        let env = YqpEnvelope::new(
            "node.hello",
            "test-node",
            json!({"daemon_version": "0.1.0"}),
        );
        let json_str = serde_json::to_string(&env).unwrap();
        let parsed: YqpEnvelope = serde_json::from_str(&json_str).unwrap();
        assert_eq!(parsed.yqp_version, "0.1");
        assert_eq!(parsed.message_type, "node.hello");
        assert_eq!(parsed.node_id, "test-node");
        assert!(!parsed.message_id.is_empty());
        assert!(!parsed.trace_id.is_empty());
    }

    #[test]
    fn test_message_id_unique() {
        let env1 = YqpEnvelope::new("node.heartbeat", "node1", json!({}));
        let env2 = YqpEnvelope::new("node.heartbeat", "node1", json!({}));
        assert_ne!(env1.message_id, env2.message_id);
    }
}
