use async_trait::async_trait;
use chrono::Utc;
use serde_json::{json, Value};

use super::manifest::{CapabilityError, CapabilityManifest};
use super::Capability;

pub struct LinuxArtifactScreenshotViaFb;

#[async_trait]
impl Capability for LinuxArtifactScreenshotViaFb {
    fn manifest() -> CapabilityManifest {
        CapabilityManifest {
            name: "linux.artifact.screenshot_via_fb".into(),
            description: "Capture a screenshot by reading /dev/fb0 and upload as raw PPM artifact.".into(),
            agent_description: Some(
                "Read the Linux framebuffer (/dev/fb0), wrap it in a PPM header with the display's dimensions, and upload the result as an image artifact to the Center."
                    .into(),
            ),
            input_schema: json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }),
            output_schema: Some(json!({
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "sha256": {"type": "string"},
                    "download_url": {"type": "string"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"}
                }
            })),
            risk: "maintenance".into(),
            effect: "read".into(),
            timeout_sec: 10,
            idempotency: None,
            execution_requirements: Some(json!({
                "runtime_kind": "privileged",
                "privilege": "root",
                "labels": ["linux"]
            })),
            resource_keys: None,
            conflict_policy: None,
        }
    }

    async fn execute(_input: Value) -> Result<Value, CapabilityError> {
        // Check if /dev/fb0 exists
        if !std::path::Path::new("/dev/fb0").exists() {
            return Err(CapabilityError::FunctionExecutionFailed {
                message: "/dev/fb0 not found; no framebuffer device available".into(),
                exit_code: None,
                stderr: None,
            });
        }

        // Read virtual_size for dimensions
        let (width, height) = read_fb_dimensions().map_err(|e| {
            CapabilityError::Internal(format!("failed to read framebuffer dimensions: {}", e))
        })?;

        // Read the raw framebuffer data
        let fb_data = std::fs::read("/dev/fb0").map_err(|e| {
            if e.kind() == std::io::ErrorKind::PermissionDenied {
                CapabilityError::PermissionDenied {
                    path: Some("/dev/fb0".into()),
                    detail: e.to_string(),
                }
            } else {
                CapabilityError::Internal(format!("failed to read /dev/fb0: {}", e))
            }
        })?;

        // Build a PPM header: P6 format, max color value 255
        // Frame buffer is typically 32bpp (BGRA), PPM expects 24bpp RGB.
        // We write the raw data as-is; the consumer can interpret based on content type.
        // For now, store as raw binary with a PPM-style header.
        // PPM header: "P6\n<width> <height>\n255\n" followed by RGB data.
        // Since fb0 is 32bpp, we need to strip the alpha byte (every 4th byte).
        let header = format!("P6\n{} {}\n255\n", width, height);
        let ppm_data: Vec<u8> = header
            .bytes()
            .chain(fb_data.chunks(4).flat_map(|pixel| {
                // fb0 is typically BGRA (Linux console); reorder to RGB
                // pixel[0] = B, pixel[1] = G, pixel[2] = R, pixel[3] = A
                if pixel.len() >= 3 {
                    vec![pixel[2], pixel[1], pixel[0]]
                } else {
                    vec![0, 0, 0]
                }
            }))
            .collect();

        let timestamp = Utc::now().format("%Y%m%d%H%M%S");
        let title = format!("screenshot-{}.ppm", timestamp);

        // Upload via global YQP client
        let client = super::YQP_CLIENT.get().ok_or_else(|| {
            CapabilityError::Internal(
                "YQP client not initialized; set_yqp_client() must be called at daemon startup".into(),
            )
        })?;

        let response = client
            .send_artifact_upload(
                "screenshot",
                "image/x-portable-pixmap",
                &title,
                &ppm_data,
                None, // summary
                None, // metadata
                None, // job_id
            )
            .await
            .map_err(|e| CapabilityError::Internal(format!("artifact upload failed: {}", e)))?;

        let detail = response.artifact;

        Ok(json!({
            "artifact_id": detail.artifact_id,
            "size_bytes": detail.size_bytes,
            "sha256": detail.sha256,
            "download_url": detail.download_url,
            "width": width,
            "height": height,
        }))
    }
}

/// Read framebuffer dimensions from /sys/class/graphics/fb0/virtual_size.
/// Falls back to /sys/class/graphics/fb0/modes if virtual_size is unavailable.
fn read_fb_dimensions() -> Result<(u32, u32), String> {
    // Try virtual_size first (format: "W,H\n")
    if let Ok(content) = std::fs::read_to_string("/sys/class/graphics/fb0/virtual_size") {
        let trimmed = content.trim();
        if let Some((w, h)) = trimmed.split_once(',') {
            let width = w.trim().parse::<u32>().map_err(|e| format!("invalid width: {}", e))?;
            let height = h.trim().parse::<u32>().map_err(|e| format!("invalid height: {}", e))?;
            return Ok((width, height));
        }
    }

    // Fallback: try modes (format: "U:WxHp-...\n")
    if let Ok(content) = std::fs::read_to_string("/sys/class/graphics/fb0/modes") {
        let trimmed = content.trim();
        if let Some(dims) = trimmed.split(':').nth(1) {
            if let Some((w_str, rest)) = dims.split_once('x') {
                // rest may contain "p-" suffix; extract before any non-digit
                let h_str: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
                if !h_str.is_empty() {
                    let width = w_str.parse::<u32>().map_err(|e| format!("invalid width: {}", e))?;
                    let height = h_str.parse::<u32>().map_err(|e| format!("invalid height: {}", e))?;
                    return Ok((width, height));
                }
            }
        }
    }

    Err("could not determine framebuffer dimensions from /sys/class/graphics/fb0/virtual_size or modes".into())
}
