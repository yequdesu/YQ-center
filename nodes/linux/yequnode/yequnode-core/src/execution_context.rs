//! Execution context for long-running capabilities.
//!
//! Provides job-scoped context (job_id, yqp client, cancel token) to capabilities
//! that need to report progress, renew leases, and support cancellation during execution.

use std::sync::Arc;
use tokio_util::sync::CancellationToken;

use crate::yqp::client::YqpClient;

/// Context passed to long-running capability executions.
///
/// Set by the daemon before dispatching a job, and cleared after execution completes.
/// Capabilities access it via [`ExecutionContext::current()`].
pub struct ExecutionContext {
    /// The job ID this execution is associated with.
    pub job_id: String,
    /// YQP client for sending progress events and lease renewals.
    pub yqp: Arc<YqpClient>,
    /// Cancellation token — signaled when the job should be cancelled.
    pub cancel: CancellationToken,
    /// Event sequence counter for job.event messages.
    sequence: std::sync::atomic::AtomicU32,
}

impl ExecutionContext {
    /// Create a new execution context.
    pub fn new(job_id: String, yqp: Arc<YqpClient>) -> Self {
        Self {
            job_id,
            yqp,
            cancel: CancellationToken::new(),
            sequence: std::sync::atomic::AtomicU32::new(1),
        }
    }

    /// Get the next event sequence number.
    pub fn next_sequence(&self) -> u32 {
        self.sequence.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
    }

    /// Send a progress event to Center.
    pub async fn report_progress(&self, event_type: &str, data: serde_json::Value) {
        let seq = self.next_sequence();
        if let Err(e) = self.yqp.job_event(&self.job_id, event_type, seq, data).await {
            tracing::warn!(job_id = %self.job_id, error = ?e, "job.event failed");
        }
    }

    /// Renew the job lease to prevent timeout.
    pub async fn renew_lease(&self, lease_extend_sec: u32) {
        if let Err(e) = self.yqp.job_lease_renew(&self.job_id, lease_extend_sec).await {
            tracing::warn!(job_id = %self.job_id, error = ?e, "job.lease_renew failed");
        }
    }

    /// Check if the job has been cancelled.
    pub fn is_cancelled(&self) -> bool {
        self.cancel.is_cancelled()
    }
}

// ---------------------------------------------------------------------------
// Thread-local context storage
// ---------------------------------------------------------------------------

tokio::task_local! {
    static CURRENT_CONTEXT: Arc<ExecutionContext>;
}

/// Set the execution context for the current task scope.
pub async fn with_context<F, R>(ctx: Arc<ExecutionContext>, f: F) -> R
where
    F: std::future::Future<Output = R>,
{
    CURRENT_CONTEXT.scope(ctx, f).await
}

/// Get the current execution context, if set.
///
/// Returns `None` if called outside a task-local context (e.g., from a non-long-running capability).
pub fn try_current() -> Option<Arc<ExecutionContext>> {
    CURRENT_CONTEXT.try_with(|ctx| ctx.clone()).ok()
}
