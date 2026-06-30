use std::time::Duration;
use crate::yqp::envelope::YqpError;

pub struct RetryConfig {
    pub max_attempts: u32,
    pub base_backoff: Duration,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            base_backoff: Duration::from_secs(1),
        }
    }
}

pub fn should_retry(error: &YqpError, attempt: u32, config: &RetryConfig) -> bool {
    if attempt >= config.max_attempts {
        return false;
    }
    if !error.is_retryable() {
        return false;
    }
    true
}

pub fn backoff_duration(attempt: u32, config: &RetryConfig) -> Duration {
    config.base_backoff * 2u32.pow(attempt)
}

pub fn is_network_timeout(error: &reqwest::Error) -> bool {
    error.is_timeout() || error.is_connect()
}

pub fn classify_reqwest_error(error: reqwest::Error) -> YqpError {
    if is_network_timeout(&error) {
        YqpError::NetworkError {
            message: error.to_string(),
            retryable: true,
        }
    } else {
        YqpError::NetworkError {
            message: error.to_string(),
            retryable: false,
        }
    }
}
