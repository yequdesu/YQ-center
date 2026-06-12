import os
import tempfile
import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as d:
        old_cwd = os.getcwd()
        os.chdir(d)
        yield d
        os.chdir(old_cwd)


@pytest.fixture
def test_config_dict():
    """Return a minimal valid config dict for testing."""
    return {
        "gateway": {"host": "127.0.0.1", "port": 9800, "unix_socket": "/tmp/gateway.sock"},
        "data": {"dir": "/tmp/yequ-test"},
        "monitor": {"scan_interval_seconds": 30},
        "collector": {"interval_seconds": 60},
        "agent": {"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": ""},
        "notify": {"log_file": "/tmp/yequ-test/gateway.log"},
    }


@pytest.fixture
def db_path(tmp_path):
    """Return an isolated SQLite database path."""
    return str(tmp_path / "test.db")
