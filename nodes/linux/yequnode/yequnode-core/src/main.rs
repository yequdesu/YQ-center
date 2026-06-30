use tracing::info;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();

    let config = match yequnode_core::config::Config::load() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("config error: {}", e);
            std::process::exit(1);
        }
    };

    if args.len() > 1 && args[1] == "artifact-test" {
        if let Err(e) = run_artifact_smoke_test(&config).await {
            eprintln!("artifact smoke test failed: {e}");
            std::process::exit(1);
        }
        return;
    }

    yequnode_core::audit::init_tracing(&config.log_level);

    info!(
        version = env!("CARGO_PKG_VERSION"),
        node_id = %config.node_id,
        "yequnode daemon starting"
    );

    if let Err(e) = yequnode_core::daemon::Daemon::run(config).await {
        tracing::error!(error = %e, "daemon fatal error");
        std::process::exit(1);
    }
}

async fn run_artifact_smoke_test(config: &yequnode_core::config::Config) -> Result<(), Box<dyn std::error::Error>> {
    use yequnode_core::yqp::client::YqpClient;

    let client = YqpClient::new(config);
    let test_content = format!(
        "Artifact upload smoke test from node {}\ntimestamp: {}\n",
        config.node_id,
        chrono::Utc::now().to_rfc3339()
    );
    let data = test_content.as_bytes().to_vec();
    let title = "artifact-upload-test.txt";

    println!("Uploading artifact '{}' ({} bytes)...", title, data.len());

    let result = client
        .send_artifact_upload(
            "file",
            "text/plain",
            title,
            &data,
            Some(serde_json::json!({"purpose": "artifact upload smoke test"})),
            Some(serde_json::json!({
                "runtime": "privileged",
                "source": "node smoke test",
            })),
            None,
        )
        .await?;

    let a = &result.artifact;
    println!("artifact_id:  {}", a.artifact_id);
    println!("node_id:      {}", a.node_id);
    println!("type:         {}", a.artifact_type);
    println!("content_type: {}", a.content_type);
    println!("size_bytes:   {}", a.size_bytes);
    println!("sha256:       {}", a.sha256);
    println!("download_url: {}", a.download_url);

    // Verify via Admin API (requires admin token)
    let verify_url = format!(
        "{}{}",
        config.center_base_url.trim_end_matches('/'),
        a.download_url
    );
    println!(
        "verify: curl -H 'Authorization: Bearer <admin-token>' {} | wc -c  # expect {}",
        verify_url, data.len()
    );

    Ok(())
}
