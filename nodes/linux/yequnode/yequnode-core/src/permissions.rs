use std::process::Command;

use crate::capability::manifest::CapabilityError;

#[derive(Debug)]
pub struct ProbeResult {
    pub function_name: String,
    pub passed: bool,
    pub detail: String,
}

impl ProbeResult {
    pub fn passed(name: &str) -> Self {
        Self { function_name: name.into(), passed: true, detail: "ok".into() }
    }

    pub fn failed(name: &str, detail: &str) -> Self {
        Self { function_name: name.into(), passed: false, detail: detail.into() }
    }
}

// -- Individual probes --

fn probe_proc() -> Result<(), CapabilityError> {
    std::fs::read_to_string("/proc/version").map_err(|e| {
        CapabilityError::PermissionDenied {
            path: Some("/proc/version".into()),
            detail: format!("cannot read /proc: {}", e),
        }
    })?;
    Ok(())
}

fn probe_command(binary: &str) -> bool {
    Command::new("which").arg(binary).output().map(|o| o.status.success()).unwrap_or(false)
}

fn probe_sudo() -> bool {
    Command::new("sudo").arg("-ln").output().map(|o| o.status.success()).unwrap_or(false)
}

fn probe_framebuffer() -> bool {
    std::fs::metadata("/dev/fb0").is_ok()
}

fn probe_filesystem() -> bool {
    std::fs::metadata("/").is_ok()
}

/// Run all probes. Returns results for each declared capability.
/// Capabilities whose probes fail are excluded from registration.
pub fn run_probes() -> Vec<ProbeResult> {
    let mut results = Vec::new();
    let proc_ok = probe_proc().is_ok();
    let has_journalctl = probe_command("journalctl");
    let has_systemctl = probe_command("systemctl");
    let has_dmesg = probe_command("dmesg");
    let has_ip = probe_command("ip");
    let has_ss = probe_command("ss");
    let has_sudo = probe_sudo();
    let has_fb = probe_framebuffer();
    let fs_ok = probe_filesystem();

    // -- user runtime capabilities --
    add(&mut results, "linux.system.info", proc_ok, "/proc required");
    add(&mut results, "linux.metrics.snapshot", proc_ok, "/proc required");
    add(&mut results, "linux.process.list", proc_ok, "/proc required");
    add(&mut results, "linux.disk.detail", proc_ok, "/proc/mounts required");
    add(&mut results, "linux.network.interfaces", has_ip, "ip command not found");
    add(&mut results, "linux.network.routes", has_ip, "ip command not found");
    add(&mut results, "linux.network.connections", has_ss, "ss command not found");
    add(&mut results, "linux.service.list", has_systemctl, "systemctl not found");
    add(&mut results, "linux.service.status", has_systemctl, "systemctl not found");
    add(&mut results, "linux.log.journal", has_journalctl, "journalctl not found");
    add(&mut results, "linux.user.list", true, "always available");
    add(&mut results, "linux.package.list", true, "always available");
    add(&mut results, "linux.dmesg", has_dmesg, "dmesg not found");
    add(&mut results, "linux.filesystem.stat", fs_ok, "/ not accessible");
    add(&mut results, "linux.filesystem.read_text", fs_ok, "/ not accessible");
    add(&mut results, "linux.filesystem.find", true, "always available");
    add(&mut results, "linux.filesystem.list_dir", fs_ok, "/ not accessible");

    // -- sudo runtime capabilities --
    add(&mut results, "linux.service.restart", has_systemctl && has_sudo,
        "systemctl + sudo required");
    add(&mut results, "linux.artifact.upload_file", has_sudo,
        "sudo required for arbitrary file access");
    add(&mut results, "linux.artifact.upload_log", has_journalctl && has_sudo,
        "journalctl + sudo required");
    add(&mut results, "linux.artifact.diagnostics", has_sudo,
        "sudo required for diagnostics collection");
    add(&mut results, "linux.artifact.upload_proc", proc_ok && has_sudo,
        "/proc + sudo required");

    // -- optional --
    add(&mut results, "linux.artifact.screenshot_via_fb", has_fb,
        "/dev/fb0 not available");

    results
}

fn add(results: &mut Vec<ProbeResult>, name: &str, ok: bool, fail_reason: &str) {
    if ok {
        results.push(ProbeResult::passed(name));
    } else {
        results.push(ProbeResult::failed(name, fail_reason));
    }
}

/// Given probe results, filter a list of function names to only those that passed.
pub fn filter_passed_functions(
    probes: &[ProbeResult],
    all_functions: &[String],
) -> Vec<String> {
    let failed: std::collections::HashSet<&str> = probes
        .iter()
        .filter(|r| !r.passed)
        .map(|r| r.function_name.as_str())
        .collect();
    all_functions
        .iter()
        .filter(|name| !failed.contains(name.as_str()))
        .cloned()
        .collect()
}

/// Check if sudo runtime is available.
pub fn sudo_available() -> bool { probe_sudo() }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_probes_run() {
        let results = run_probes();
        assert!(!results.is_empty());
        for r in &results {
            println!("{}: passed={} detail={}", r.function_name, r.passed, r.detail);
        }
    }

    #[test]
    fn filter_excludes_failed() {
        let probes = vec![
            ProbeResult::passed("func_a"),
            ProbeResult::failed("func_b", "no access"),
            ProbeResult::passed("func_c"),
        ];
        let all = vec!["func_a".into(), "func_b".into(), "func_c".into()];
        let filtered = filter_passed_functions(&probes, &all);
        assert_eq!(filtered, vec!["func_a", "func_c"]);
    }
}
