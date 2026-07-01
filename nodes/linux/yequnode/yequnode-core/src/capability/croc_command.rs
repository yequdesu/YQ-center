use super::manifest::CapabilityError;

pub async fn croc_supports_flag(binary_path: &str, flag: &str) -> bool {
    let output = match tokio::process::Command::new(binary_path)
        .arg("--help")
        .output()
        .await
    {
        Ok(output) => output,
        Err(_) => return false,
    };
    let mut help_text = String::new();
    help_text.push_str(&String::from_utf8_lossy(&output.stdout));
    help_text.push_str(&String::from_utf8_lossy(&output.stderr));
    let help_text = help_text.to_lowercase();
    let flag = flag.to_lowercase();
    help_text.contains(&flag)
}

pub async fn require_croc_flag(binary_path: &str, flag: &str) -> Result<(), CapabilityError> {
    if croc_supports_flag(binary_path, flag).await {
        return Ok(());
    }
    Err(CapabilityError::FunctionExecutionFailed {
        message: format!("croc binary does not support required flag {}", flag),
        exit_code: None,
        stderr: None,
    })
}
