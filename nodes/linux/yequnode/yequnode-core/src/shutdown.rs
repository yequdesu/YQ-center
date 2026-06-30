/// Returns a future that resolves when a shutdown signal is received.
pub async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {
            tracing::info!("received SIGINT, initiating shutdown");
        }
        _ = terminate => {
            tracing::info!("received SIGTERM, initiating shutdown");
        }
    }
}

/// Drain pending jobs: log unconfirmed records before exit.
pub async fn drain_jobs(store: &crate::job::JobStore) {
    let unconfirmed = store.get_unconfirmed_jobs().unwrap_or_default();
    if !unconfirmed.is_empty() {
        tracing::warn!(
            unconfirmed_count = unconfirmed.len(),
            "shutting down with unconfirmed jobs — will reconcile on restart"
        );
    }
    // All jobs are already persisted by the poll cycle.
    // This function exists as an explicit drain point for future extensions
    // (e.g., sending job.cancel for running jobs).
}
