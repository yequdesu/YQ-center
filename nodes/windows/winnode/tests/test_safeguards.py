from __future__ import annotations


def test_safeguards_default():
    from node_win_client.safeguards import Safeguards

    safeguards = Safeguards(allow_write_actions=True, allowed_services=["Spooler", "wuauserv"])
    result = safeguards.check_write_action("windows.artifact.download_file")
    assert result.allowed is True


def test_safeguards_write_denied():
    from node_win_client.safeguards import Safeguards

    safeguards = Safeguards(allow_write_actions=False, allowed_services=[])
    result = safeguards.check_write_action("windows.artifact.download_file")
    assert result.allowed is False
    assert result.error_code == "WRITE_ACTION_DENIED"


def test_safeguards_service_allowed():
    from node_win_client.safeguards import Safeguards

    safeguards = Safeguards(allow_write_actions=True, allowed_services=["Spooler"])
    result = safeguards.check_service("Spooler")
    assert result.allowed is True


def test_safeguards_service_not_allowed():
    from node_win_client.safeguards import Safeguards

    safeguards = Safeguards(allow_write_actions=True, allowed_services=["Spooler"])
    result = safeguards.check_service("EventLog")
    assert result.allowed is False
    assert result.error_code == "SERVICE_NOT_ALLOWED"


def test_load_safeguards():
    from node_win_client.safeguards import load_safeguards

    sg = load_safeguards(allow_write_actions=True, allowed_services=["Spooler", "wuauserv"])
    assert sg.allow_write_actions is True
    assert len(sg.allowed_services) == 2
    assert sg.check_service("Spooler").allowed is True

