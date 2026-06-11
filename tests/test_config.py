import os
import pytest
from yequ.config import load_config, Config, GatewayConfig, DataConfig


class TestLoadConfig:
    def test_load_from_yaml(self, tmp_path):
        yaml_content = """
gateway:
  host: "0.0.0.0"
  port: 8080
  unix_socket: "/tmp/sock"
data:
  dir: "/tmp/data"
monitor:
  scan_interval_seconds: 10
collector:
  interval_seconds: 30
agent:
  anthropic_api_key: ""
  model: "claude-sonnet-4-6"
notify:
  log_file: "/tmp/log"
"""
        config_path = tmp_path / "gateway.yaml"
        config_path.write_text(yaml_content)

        config = load_config(str(config_path))

        assert config.gateway.host == "0.0.0.0"
        assert config.gateway.port == 8080
        assert config.data.dir == "/tmp/data"

    def test_expand_user_paths(self, tmp_path):
        yaml_content = """
gateway:
  host: "127.0.0.1"
  port: 9800
  unix_socket: "~/data/gateway.sock"
data:
  dir: "~/data"
monitor:
  scan_interval_seconds: 30
collector:
  interval_seconds: 60
agent:
  anthropic_api_key: ""
  model: "claude-sonnet-4-6"
notify:
  log_file: "~/data/gateway.log"
"""
        config_path = tmp_path / "gateway.yaml"
        config_path.write_text(yaml_content)

        config = load_config(str(config_path))

        assert not config.data.dir.startswith("~")
        assert config.data.dir.startswith("/")

    def test_default_values(self, tmp_path):
        yaml_content = """
gateway:
  host: "127.0.0.1"
  port: 9800
data:
  dir: "/tmp"
"""
        config_path = tmp_path / "gateway.yaml"
        config_path.write_text(yaml_content)

        config = load_config(str(config_path))

        assert config.monitor.scan_interval_seconds == 30
        assert config.collector.interval_seconds == 60
