use serde::Deserialize;
use std::path::PathBuf;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    #[serde(default = "default_node_id")]
    pub node_id: String,

    #[serde(default = "default_center_url")]
    pub center_base_url: String,

    #[serde(default = "default_yqp_path")]
    pub yqp_path: String,

    #[serde(default = "default_log_level")]
    pub log_level: String,

    #[serde(default = "default_db_path")]
    pub db_path: PathBuf,

    pub node_token: String,

    #[serde(default)]
    pub transfer: TransferConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TransferConfig {
    #[serde(default = "default_rclone_config")]
    pub rclone: RcloneConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RcloneConfig {
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default = "default_rclone_binary_path")]
    pub binary_path: String,
    #[serde(default)]
    pub advertise_host: Option<String>,
    #[serde(default)]
    pub bind_host: Option<String>,
    #[serde(default = "default_temp_dir")]
    pub temp_dir: PathBuf,
    #[serde(default = "default_true")]
    pub allow_send: bool,
    #[serde(default = "default_true")]
    pub allow_receive: bool,
    #[serde(default = "default_max_concurrent_transfers")]
    pub max_concurrent_transfers: u32,
    #[serde(default = "default_listen_port")]
    pub listen_port: u16,
}

fn default_rclone_config() -> RcloneConfig {
    RcloneConfig {
        enabled: true,
        binary_path: default_rclone_binary_path(),
        advertise_host: None,
        bind_host: None,
        temp_dir: default_temp_dir(),
        allow_send: true,
        allow_receive: true,
        max_concurrent_transfers: default_max_concurrent_transfers(),
        listen_port: default_listen_port(),
    }
}

fn default_true() -> bool {
    true
}

fn default_rclone_binary_path() -> String {
    "/usr/bin/rclone".into()
}

fn default_temp_dir() -> PathBuf {
    PathBuf::from("/tmp/yequ-transfer")
}

fn default_max_concurrent_transfers() -> u32 {
    1
}

fn default_listen_port() -> u16 {
    42981
}

impl Default for TransferConfig {
    fn default() -> Self {
        Self {
            rclone: default_rclone_config(),
        }
    }
}

fn default_node_id() -> String {
    "linuxServer".into()
}
fn default_center_url() -> String {
    "https://gtw.yequdesu.top".into()
}
fn default_yqp_path() -> String {
    "/yqp/".into()
}
fn default_log_level() -> String {
    "info".into()
}
fn default_db_path() -> PathBuf {
    dirs_next()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".yequnode")
        .join("jobs.db")
}

fn dirs_next() -> Option<PathBuf> {
    std::env::var("HOME").ok().map(PathBuf::from)
}

impl Config {
    pub fn load() -> Result<Self, ConfigError> {
        let config_path = Self::config_path();
        let mut config = if config_path.exists() {
            let content = std::fs::read_to_string(&config_path)
                .map_err(|e| ConfigError::ReadError(config_path.clone(), e))?;
            serde_yaml::from_str::<Self>(&content)
                .map_err(|e| ConfigError::ParseError(config_path.clone(), e))?
        } else {
            Self::default_config()
        };

        // Environment variable overrides
        if let Ok(token) = std::env::var("YEQU_NODE_TOKEN") {
            config.node_token = token;
        }
        if let Ok(node_id) = std::env::var("YEQU_NODE_ID") {
            config.node_id = node_id;
        }
        if let Ok(url) = std::env::var("YEQU_CENTER_URL") {
            config.center_base_url = url;
        }

        if config.node_token.is_empty() {
            return Err(ConfigError::MissingToken);
        }

        Ok(config)
    }

    fn config_path() -> PathBuf {
        dirs_next()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".yequnode")
            .join("config.yaml")
    }

    fn default_config() -> Self {
        Self {
            node_id: default_node_id(),
            center_base_url: default_center_url(),
            yqp_path: default_yqp_path(),
            log_level: default_log_level(),
            db_path: default_db_path(),
            node_token: String::new(),
            transfer: TransferConfig::default(),
        }
    }

    pub fn center_yqp_url(&self) -> String {
        format!(
            "{}{}",
            self.center_base_url.trim_end_matches('/'),
            self.yqp_path
        )
    }
}

#[derive(Debug)]
pub enum ConfigError {
    ReadError(PathBuf, std::io::Error),
    ParseError(PathBuf, serde_yaml::Error),
    MissingToken,
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::ReadError(path, e) => write!(f, "cannot read {:?}: {}", path, e),
            ConfigError::ParseError(path, e) => write!(f, "invalid YAML in {:?}: {}", path, e),
            ConfigError::MissingToken => write!(f, "YEQU_NODE_TOKEN is required"),
        }
    }
}

impl std::error::Error for ConfigError {}
