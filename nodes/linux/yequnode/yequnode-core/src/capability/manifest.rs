use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CapabilityManifest {
    pub name: String,
    pub description: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_description: Option<String>,
    pub input_schema: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_schema: Option<Value>,
    pub risk: String,
    pub effect: String,
    pub timeout_sec: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub idempotency: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub execution_requirements: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resource_keys: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conflict_policy: Option<String>,
}

#[derive(Debug, Clone)]
pub enum CapabilityError {
    PermissionDenied {
        path: Option<String>,
        detail: String,
    },
    FunctionExecutionFailed {
        message: String,
        exit_code: Option<i32>,
        stderr: Option<String>,
    },
    Cancelled {
        message: String,
    },
    InvalidInput {
        field: String,
        message: String,
    },
    Timeout {
        timeout_sec: u32,
    },
    UnknownFunction(String),
    Internal(String),
}

impl CapabilityError {
    pub fn error_code(&self) -> &'static str {
        match self {
            CapabilityError::PermissionDenied { .. } => "permission_denied",
            CapabilityError::FunctionExecutionFailed { .. } => "function_execution_failed",
            CapabilityError::Cancelled { .. } => "cancelled",
            CapabilityError::InvalidInput { .. } => "invalid_input",
            CapabilityError::Timeout { .. } => "timeout",
            CapabilityError::UnknownFunction(_) => "unknown_function",
            CapabilityError::Internal(_) => "internal_error",
        }
    }

    pub fn error_message(&self) -> String {
        match self {
            CapabilityError::PermissionDenied { path, detail } => {
                if let Some(p) = path {
                    format!("permission denied for {}: {}", p, detail)
                } else {
                    format!("permission denied: {}", detail)
                }
            }
            CapabilityError::FunctionExecutionFailed { message, .. } => message.clone(),
            CapabilityError::Cancelled { message } => message.clone(),
            CapabilityError::InvalidInput { field, message } => {
                format!("invalid input for {}: {}", field, message)
            }
            CapabilityError::Timeout { timeout_sec } => {
                format!("execution timed out after {}s", timeout_sec)
            }
            CapabilityError::UnknownFunction(name) => format!("unknown function: {}", name),
            CapabilityError::Internal(msg) => msg.clone(),
        }
    }

    pub fn to_job_error(&self) -> serde_json::Value {
        serde_json::json!({
            "code": self.error_code(),
            "message": self.error_message(),
            "details": self.error_details(),
        })
    }

    fn error_details(&self) -> Value {
        match self {
            CapabilityError::PermissionDenied { path, .. } => {
                serde_json::json!({ "path": path })
            }
            CapabilityError::FunctionExecutionFailed {
                exit_code, stderr, ..
            } => {
                serde_json::json!({ "exit_code": exit_code, "stderr": stderr })
            }
            CapabilityError::Cancelled { .. } => serde_json::json!({ "cancelled": true }),
            _ => Value::Null,
        }
    }
}
