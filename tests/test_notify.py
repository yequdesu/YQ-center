import os
import pytest
from yequ.notify.base import (
    NotifyAdapter,
    LogNotifyAdapter,
    NotifyRouter,
    Severity,
    Notification,
)


class TestLogNotifyAdapter:
    def test_send_writes_to_log(self, tmp_path):
        log_path = str(tmp_path / "gateway.log")
        adapter = LogNotifyAdapter(log_path)

        notif = Notification(
            severity=Severity.WARNING,
            title="设备离线",
            body="pixel-8a 已离线 5 分钟",
            device_id="pixel-8a",
        )
        adapter.send(notif)

        assert os.path.exists(log_path)
        content = open(log_path).read()
        assert "[WARNING]" in content
        assert "设备离线" in content
        assert "pixel-8a" in content

    def test_send_creates_directory(self, tmp_path):
        log_path = str(tmp_path / "subdir" / "gateway.log")
        adapter = LogNotifyAdapter(log_path)
        notif = Notification(severity=Severity.INFO, title="测试", body="内容")
        adapter.send(notif)

        assert os.path.exists(log_path)


class TestNotifyRouter:
    @pytest.fixture
    def router(self, tmp_path):
        log_path = str(tmp_path / "gateway.log")
        return NotifyRouter(log_path=log_path)

    def test_route_critical(self, router, tmp_path):
        notif = Notification(
            severity=Severity.CRITICAL,
            title="磁盘告警",
            body="磁盘使用率 92%",
            device_id="localhost",
        )
        router.send(notif)

        log_content = open(str(tmp_path / "gateway.log")).read()
        assert "[CRITICAL]" in log_content

    def test_route_info_to_log_only(self, router, tmp_path):
        notif = Notification(
            severity=Severity.INFO,
            title="设备上线",
            body="pixel-8a 已连接",
            device_id="pixel-8a",
        )
        router.send(notif)

        log_content = open(str(tmp_path / "gateway.log")).read()
        assert "[INFO]" in log_content
