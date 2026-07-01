#[macro_export]
macro_rules! register {
    ($($cap:ty),* $(,)?) => {
        pub fn collect_manifests() -> Vec<$crate::capability::manifest::CapabilityManifest> {
            vec![
                $(<$cap as $crate::capability::Capability>::manifest()),*
            ]
        }

        pub async fn dispatch(
            name: &str,
            input: serde_json::Value,
        ) -> Result<serde_json::Value, $crate::capability::manifest::CapabilityError> {
            $(
                if name == <$cap as $crate::capability::Capability>::manifest().name.as_str() {
                    return <$cap as $crate::capability::Capability>::execute(input).await;
                }
            )*
            Err($crate::capability::manifest::CapabilityError::UnknownFunction(name.into()))
        }

        pub fn registered_functions() -> Vec<String> {
            collect_manifests().into_iter().map(|m| m.name).collect()
        }
    };
}

#[cfg(test)]
mod tests {
    use crate::capability::manifest::{CapabilityError, CapabilityManifest};
    use crate::capability::Capability;
    use async_trait::async_trait;
    use serde_json::Value;

    struct TestCapA;
    struct TestCapB;

    #[async_trait]
    impl Capability for TestCapA {
        fn manifest() -> CapabilityManifest {
            CapabilityManifest {
                name: "test.a".into(),
                description: "test A".into(),
                agent_description: None,
                input_schema: serde_json::json!({"type": "object"}),
                output_schema: None,
                risk: "safe".into(),
                effect: "read".into(),
                timeout_sec: 5,
                idempotency: None,
                execution_requirements: None,
                resource_keys: None,
                conflict_policy: None,
                supports_progress: false,
                supports_cancel: false,
                supports_resume: false,
                progress_contract: None,
                preconditions: vec![],
                required_intent_slots: vec![],
            }
        }

        async fn execute(_input: Value) -> Result<Value, CapabilityError> {
            Ok(serde_json::json!({"a": true}))
        }
    }

    #[async_trait]
    impl Capability for TestCapB {
        fn manifest() -> CapabilityManifest {
            CapabilityManifest {
                name: "test.b".into(),
                description: "test B".into(),
                agent_description: None,
                input_schema: serde_json::json!({"type": "object"}),
                output_schema: None,
                risk: "safe".into(),
                effect: "read".into(),
                timeout_sec: 5,
                idempotency: None,
                execution_requirements: None,
                resource_keys: None,
                conflict_policy: None,
                supports_progress: false,
                supports_cancel: false,
                supports_resume: false,
                progress_contract: None,
                preconditions: vec![],
                required_intent_slots: vec![],
            }
        }

        async fn execute(_input: Value) -> Result<Value, CapabilityError> {
            Ok(serde_json::json!({"b": true}))
        }
    }

    register!(TestCapA, TestCapB);

    #[test]
    fn test_collect_manifests() {
        let manifests = collect_manifests();
        assert_eq!(manifests.len(), 2);
        assert_eq!(manifests[0].name, "test.a");
        assert_eq!(manifests[1].name, "test.b");
    }

    #[test]
    fn test_registered_functions() {
        let names = registered_functions();
        assert_eq!(names, vec!["test.a", "test.b"]);
    }

    #[tokio::test]
    async fn test_dispatch_known() {
        let result = dispatch("test.a", serde_json::json!({})).await.unwrap();
        assert_eq!(result, serde_json::json!({"a": true}));
    }

    #[tokio::test]
    async fn test_dispatch_unknown() {
        let result = dispatch("test.c", serde_json::json!({})).await;
        assert!(result.is_err());
        match result.unwrap_err() {
            CapabilityError::UnknownFunction(name) => assert_eq!(name, "test.c"),
            _ => panic!("expected UnknownFunction"),
        }
    }
}

// Real capability registration -- add new capabilities here.
pub mod production {
    use crate::capability::linux_artifact_diagnostics::LinuxArtifactDiagnostics;
    use crate::capability::linux_artifact_download_file::LinuxArtifactDownloadFile;
    use crate::capability::linux_artifact_screenshot_via_fb::LinuxArtifactScreenshotViaFb;
    use crate::capability::linux_artifact_upload_file::LinuxArtifactUploadFile;
    use crate::capability::linux_artifact_upload_log::LinuxArtifactUploadLog;
    use crate::capability::linux_artifact_upload_proc::LinuxArtifactUploadProc;
    use crate::capability::linux_disk_detail::LinuxDiskDetail;
    use crate::capability::linux_dmesg::LinuxDmesg;
    use crate::capability::linux_filesystem_disk_usage::LinuxFilesystemDiskUsage;
    use crate::capability::linux_filesystem_find::LinuxFilesystemFind;
    use crate::capability::linux_filesystem_hash::LinuxFilesystemHash;
    use crate::capability::linux_filesystem_list_dir::LinuxFilesystemListDir;
    use crate::capability::linux_filesystem_mkdir::LinuxFilesystemMkdir;
    use crate::capability::linux_filesystem_read_text::LinuxFilesystemReadText;
    use crate::capability::linux_filesystem_stat::LinuxFilesystemStat;
    use crate::capability::linux_log_journal::LinuxLogJournal;
    use crate::capability::linux_metrics_snapshot::LinuxMetricsSnapshot;
    use crate::capability::linux_network_connections::LinuxNetworkConnections;
    use crate::capability::linux_network_dns_lookup::LinuxNetworkDnsLookup;
    use crate::capability::linux_network_interfaces::LinuxNetworkInterfaces;
    use crate::capability::linux_network_port_check::LinuxNetworkPortCheck;
    use crate::capability::linux_network_routes::LinuxNetworkRoutes;
    use crate::capability::linux_package_list::LinuxPackageList;
    use crate::capability::linux_process_list::LinuxProcessList;
    use crate::capability::linux_service_list::LinuxServiceList;
    use crate::capability::linux_service_restart::LinuxServiceRestart;
    use crate::capability::linux_service_status::LinuxServiceStatus;
    use crate::capability::linux_system_info::LinuxSystemInfo;
    use crate::capability::linux_transfer_croc_receive::LinuxTransferCrocReceive;
    use crate::capability::linux_transfer_croc_reconcile::LinuxTransferCrocReconcile;
    use crate::capability::linux_transfer_croc_send::LinuxTransferCrocSend;
    use crate::capability::linux_transfer_croc_status::LinuxTransferCrocStatus;
    use crate::capability::linux_transfer_local_stat::LinuxTransferLocalStat;
    use crate::capability::linux_user_list::LinuxUserList;

    register!(
        LinuxSystemInfo,
        LinuxMetricsSnapshot,
        LinuxProcessList,
        LinuxFilesystemStat,
        LinuxFilesystemHash,
        LinuxFilesystemDiskUsage,
        LinuxDiskDetail,
        LinuxNetworkInterfaces,
        LinuxNetworkRoutes,
        LinuxNetworkConnections,
        LinuxNetworkDnsLookup,
        LinuxNetworkPortCheck,
        LinuxServiceList,
        LinuxServiceStatus,
        LinuxLogJournal,
        LinuxUserList,
        LinuxPackageList,
        LinuxDmesg,
        LinuxFilesystemReadText,
        LinuxFilesystemFind,
        LinuxFilesystemListDir,
        LinuxFilesystemMkdir,
        LinuxServiceRestart,
        LinuxArtifactUploadFile,
        LinuxArtifactDownloadFile,
        LinuxArtifactUploadLog,
        LinuxArtifactDiagnostics,
        LinuxArtifactUploadProc,
        LinuxArtifactScreenshotViaFb,
        LinuxTransferCrocStatus,
        LinuxTransferCrocSend,
        LinuxTransferCrocReceive,
        LinuxTransferLocalStat,
        LinuxTransferCrocReconcile,
    );

    #[cfg(test)]
    mod tests {
        use super::collect_manifests;

        #[test]
        fn production_manifests_use_protocol_idempotency_values() {
            let allowed = ["idempotent", "non_idempotent", "transactional"];
            for manifest in collect_manifests() {
                let Some(idempotency) = manifest.idempotency.as_deref() else {
                    continue;
                };
                assert!(
                    allowed.contains(&idempotency),
                    "{} has invalid idempotency value {:?}",
                    manifest.name,
                    idempotency
                );
            }
        }
    }
}
