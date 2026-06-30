use async_trait::async_trait;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxFilesystemFind;

const MAX_RESULTS: usize = 200;

#[async_trait]
impl Capability for LinuxFilesystemFind {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.filesystem.find".into(),
            description: "Find files by shell glob pattern (*.log, *config*, etc.) under a directory. Supports wildcard and fuzzy name matching.".into(),
            agent_description: Some(
                "Search for files matching a shell glob pattern like *.log, *.rs, *config*, or report*.txt. \
                The pattern is passed to find -name which supports *, ?, and [...] wildcards. \
                Use this for any file search — it handles wildcards natively. \
                Optionally filter by minimum file size and maximum directory depth."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "default": "/",
                        "description": "Root directory to search from"
                    },
                    "name": {
                        "type": "string",
                        "description": "File name glob pattern: *.log, *config*, report*.txt, etc. Supports shell wildcards."
                    },
                    "min_size_kb": {
                        "type": "integer",
                        "description": "Minimum file size in kilobytes"
                    },
                    "max_depth": {
                        "type": "integer",
                        "default": 4,
                        "description": "Maximum directory traversal depth"
                    }
                },
                "required": ["name"],
                "additionalProperties": false
            }),
            output_schema: Some(json!({"type": "array"})),
            risk: "safe".into(),
            effect: "read".into(),
            timeout_sec: 5,
            idempotency: Some("idempotent".into()),
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "labels": ["linux"]
            })),
            resource_keys: None,
            conflict_policy: None,
        }
    }

    async fn execute(input: Value) -> Result<Value, CapabilityError> {
        let name = input.get("name").and_then(|v| v.as_str()).ok_or_else(|| {
            CapabilityError::InvalidInput {
                field: "name".into(),
                message: "missing required field: name".into(),
            }
        })?;

        let path = input.get("path").and_then(|v| v.as_str()).unwrap_or("/");

        let max_depth = input
            .get("max_depth")
            .and_then(|v| v.as_u64())
            .unwrap_or(4)
            .min(20) as u64;

        let min_size_kb = input.get("min_size_kb").and_then(|v| v.as_u64());

        let path = path.to_string();
        let name = name.to_string();

        let results = tokio::task::spawn_blocking(move || {
            let mut args: Vec<String> = Vec::new();
            args.push(path);
            args.push("-maxdepth".into());
            args.push(max_depth.to_string());
            args.push("-name".into());
            args.push(name);
            args.push("-type".into());
            args.push("f".into());

            // Use -size if min_size_kb is specified
            if let Some(min_kb) = min_size_kb {
                args.push("-size".into());
                args.push(format!("+{}k", min_kb.saturating_sub(1)));
            }

            args.push("-printf".into());
            args.push("%s\t%p\n".into());

            let output = std::process::Command::new("find")
                .args(&args)
                .output()
                .map_err(|e| CapabilityError::Internal(format!("find execution failed: {}", e)))?;

            if !output.status.success() && output.stdout.is_empty() {
                return Err(CapabilityError::FunctionExecutionFailed {
                    message: format!("find exited with {:?}", output.status.code()),
                    exit_code: output.status.code(),
                    stderr: Some(String::from_utf8_lossy(&output.stderr).to_string()),
                });
            }

            let stdout = String::from_utf8_lossy(&output.stdout);
            let mut files = Vec::new();
            let mut count = 0;

            for line in stdout.lines() {
                if count >= MAX_RESULTS {
                    break;
                }

                let line = line.trim();
                if line.is_empty() {
                    continue;
                }

                if let Some((size_str, file_path)) = line.split_once('\t') {
                    if let Ok(size_bytes) = size_str.parse::<u64>() {
                        files.push(json!({
                            "path": file_path,
                            "size_bytes": size_bytes,
                        }));
                        count += 1;
                    }
                }
            }

            Ok::<_, CapabilityError>(files)
        })
        .await
        .map_err(|e| CapabilityError::Internal(format!("spawn blocking failed: {}", e)))??;

        Ok(
            json!({ "files": results, "count": results.len(), "truncated": results.len() >= MAX_RESULTS }),
        )
    }
}
