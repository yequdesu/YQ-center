mod croc_command;
mod croc_progress;
pub mod linux_artifact_diagnostics;
pub mod linux_artifact_download_file;
pub mod linux_artifact_screenshot_via_fb;
pub mod linux_artifact_upload_file;
pub mod linux_artifact_upload_log;
pub mod linux_artifact_upload_proc;
pub mod linux_disk_detail;
pub mod linux_dmesg;
pub mod linux_filesystem_disk_usage;
pub mod linux_filesystem_find;
pub mod linux_filesystem_hash;
pub mod linux_filesystem_list_dir;
pub mod linux_filesystem_mkdir;
pub mod linux_filesystem_read_text;
pub mod linux_filesystem_stat;
pub mod linux_log_journal;
pub mod linux_metrics_snapshot;
pub mod linux_network_connections;
pub mod linux_network_dns_lookup;
pub mod linux_network_interfaces;
pub mod linux_network_port_check;
pub mod linux_network_routes;
pub mod linux_package_list;
pub mod linux_process_list;
pub mod linux_service_list;
pub mod linux_service_restart;
pub mod linux_service_status;
pub mod linux_system_info;
pub mod linux_transfer_croc_receive;
pub mod linux_transfer_croc_reconcile;
pub mod linux_transfer_croc_send;
pub mod linux_transfer_croc_status;
pub mod linux_transfer_local_stat;
pub mod linux_user_list;
pub mod manifest;

use std::sync::OnceLock;

use async_trait::async_trait;
use manifest::{CapabilityError, CapabilityManifest};
use serde_json::Value;

use crate::yqp::client::YqpClient;

/// Global YqpClient reference for artifact-upload capabilities.
///
/// Set once at daemon startup via [`set_yqp_client`]. Artifact capabilities
/// retrieve it through [`YQP_CLIENT.get().unwrap()`].
pub static YQP_CLIENT: OnceLock<YqpClient> = OnceLock::new();

/// Install the YqpClient into the global OnceLock so artifact-upload
/// capabilities can upload data without direct access to the daemon state.
pub fn set_yqp_client(client: YqpClient) {
    let _ = YQP_CLIENT.set(client);
}

#[async_trait]
pub trait Capability: Send + Sync {
    fn manifest() -> CapabilityManifest;
    async fn execute(input: Value) -> Result<Value, CapabilityError>;
}

// Permission probe -- compile-time check, not execution-time
pub trait PermissionProbe {
    fn probe() -> Result<(), CapabilityError>;
}
