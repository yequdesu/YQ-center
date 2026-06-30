use serde_json::json;
use wiremock::matchers::{header_exists, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};
use yequnode_core::config::TransferConfig;
use yequnode_core::yqp::types::RuntimePrivilege;

/// Test 1: Hello handshake returns node.accepted with configuration.
#[tokio::test]
async fn test_hello_handshake() {
    let mock = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/yqp/"))
        .and(header_exists("Authorization"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "yqp_version": "0.1",
            "message_id": "msg_resp",
            "message_type": "node.accepted",
            "trace_id": "tr_resp",
            "node_id": "test-node",
            "timestamp": "2026-06-28T12:00:00Z",
            "payload": {
                "status": "ok",
                "heartbeat_interval_sec": 10,
                "heartbeat_timeout_multiplier": 3,
                "signal_report_interval_sec": 5,
                "signal_stale_multiplier": 3,
                "job_delivery_mode": "poll",
                "job_poll_interval_sec": 3,
                "server_time": "2026-06-28T12:00:00Z"
            }
        })))
        .mount(&mock)
        .await;

    let config = yequnode_core::config::Config {
        node_id: "test-node".into(),
        node_token: "test-token".into(),
        center_base_url: mock.uri(),
        yqp_path: "/yqp/".into(),
        log_level: "debug".into(),
        db_path: std::env::temp_dir().join("test-jobs.db"),
        transfer: TransferConfig::default(),
    };

    let client = yequnode_core::yqp::client::YqpClient::new(&config);
    let runtimes = vec![yequnode_core::yqp::types::RuntimeSnapshot {
        runtime_id: "user".into(),
        kind: "privileged".into(),
        status: "online".into(),
        interactive: false,
        privilege: Some(RuntimePrivilege::User),
        labels: Some(vec!["linux".into()]),
        owner: None,
        metadata: None,
    }];

    let resp = client
        .hello("0.1.0", "linux", "x86_64", &runtimes)
        .await
        .unwrap();

    assert_eq!(resp.message_type, "node.accepted");
    assert_eq!(resp.payload["heartbeat_interval_sec"], 10);
    assert_eq!(resp.payload["job_poll_interval_sec"], 3);
    assert_eq!(resp.payload["job_delivery_mode"], "poll");
}

/// Test 2: Job poll returns empty job list.
#[tokio::test]
async fn test_job_poll_empty() {
    let mock = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/yqp/"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "yqp_version": "0.1",
            "message_id": "msg_resp",
            "message_type": "job.empty",
            "trace_id": "tr_resp",
            "node_id": "test-node",
            "timestamp": "2026-06-28T12:00:00Z",
            "payload": {
                "jobs": []
            }
        })))
        .mount(&mock)
        .await;

    let config = yequnode_core::config::Config {
        node_id: "test-node".into(),
        node_token: "test-token".into(),
        center_base_url: mock.uri(),
        yqp_path: "/yqp/".into(),
        log_level: "debug".into(),
        db_path: std::env::temp_dir().join("test-jobs.db"),
        transfer: TransferConfig::default(),
    };

    let client = yequnode_core::yqp::client::YqpClient::new(&config);
    let resp = client.job_poll(4, &[]).await.unwrap();

    assert_eq!(resp.message_type, "job.empty");
    let jobs = resp.payload["jobs"].as_array().unwrap();
    assert!(jobs.is_empty());
}

/// Test 3: HTTP 401 with non-retryable error detail.
#[tokio::test]
async fn test_http_401_no_retry() {
    let mock = MockServer::start().await;

    Mock::given(method("POST"))
        .and(path("/yqp/"))
        .respond_with(ResponseTemplate::new(401).set_body_json(json!({
            "code": "auth_failed",
            "message": "invalid token",
            "retryable": false,
            "details": {}
        })))
        .mount(&mock)
        .await;

    let config = yequnode_core::config::Config {
        node_id: "test-node".into(),
        node_token: "bad-token".into(),
        center_base_url: mock.uri(),
        yqp_path: "/yqp/".into(),
        log_level: "debug".into(),
        db_path: std::env::temp_dir().join("test-jobs.db"),
        transfer: TransferConfig::default(),
    };

    let client = yequnode_core::yqp::client::YqpClient::new(&config);
    let result = client.job_poll(4, &[]).await;

    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(!err.is_retryable());
    assert_eq!(err.error_code(), "auth_failed");
}
