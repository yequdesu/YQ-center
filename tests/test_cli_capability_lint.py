from __future__ import annotations

from click.testing import CliRunner

from yequ import cli as yequ_cli


class _FakeResponse:
    def __init__(self, data: object) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._data


class _FakeClient:
    def __init__(self, data: object) -> None:
        self._data = data
        self.requests: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, path: str, params: dict[str, object] | None = None) -> _FakeResponse:
        self.requests.append((path, params or {}))
        return _FakeResponse(self._data)


def test_capability_contract_lint_report_counts_errors_and_warnings() -> None:
    report = yequ_cli._capability_contract_lint_report(
        [
            {
                "canonical_name": "artifact.download_file",
                "sources": [
                    {
                        "source_id": "src_1",
                        "node_id": "linux-node-01",
                        "registered_name": "linux.artifact.download_file",
                        "contract_issues": [
                            {
                                "severity": "error",
                                "code": "artifact_download_effect_must_be_write",
                                "message": "bad effect",
                            },
                            {
                                "severity": "warning",
                                "code": "missing_agent_description",
                                "message": "missing agent text",
                            },
                        ],
                    }
                ],
            }
        ]
    )

    assert report["status"] == "failed"
    assert report["checked_capability_count"] == 1
    assert report["error_count"] == 1
    assert report["warning_count"] == 1
    assert report["issues"][0]["node_id"] == "linux-node-01"


def test_capabilities_lint_cli_fails_on_error(monkeypatch) -> None:
    fake = _FakeClient(
        [
            {
                "canonical_name": "artifact.download_file",
                "sources": [
                    {
                        "source_id": "src_1",
                        "node_id": "winClient",
                        "registered_name": "windows.artifact.download_file",
                        "contract_issues": [
                            {
                                "severity": "error",
                                "code": "artifact_download_effect_must_be_write",
                                "message": "bad effect",
                            }
                        ],
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(yequ_cli, "_get_client", lambda: fake)

    result = CliRunner().invoke(yequ_cli.cli, ["capabilities", "lint", "--node", "winClient"])

    assert result.exit_code == 1
    assert "artifact_download_effect_must_be_write" in result.output
    assert fake.requests == [
        (
            "/admin/meta/capabilities/search",
            {
                "projection": "diagnostics",
                "capability_type": "function",
                "limit": 50,
                "include_inactive": False,
                "node_id": "winClient",
            },
        )
    ]


def test_capabilities_lint_cli_warnings_can_be_non_fatal(monkeypatch) -> None:
    fake = _FakeClient(
        [
            {
                "canonical_name": "system.info",
                "sources": [
                    {
                        "source_id": "src_1",
                        "node_id": "linux-node-01",
                        "registered_name": "linux.system.info",
                        "contract_issues": [
                            {
                                "severity": "warning",
                                "code": "missing_agent_description",
                                "message": "missing agent text",
                            }
                        ],
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(yequ_cli, "_get_client", lambda: fake)

    result = CliRunner().invoke(yequ_cli.cli, ["capabilities", "lint"])

    assert result.exit_code == 0
    assert "1 warnings" in result.output

    strict_result = CliRunner().invoke(
        yequ_cli.cli,
        ["capabilities", "lint", "--warnings-as-errors"],
    )
    assert strict_result.exit_code == 1
