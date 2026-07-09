from yequ.agent.deepseek_provider import _system_prompt_text_enhanced
from yequ.agent.prompt_policy import render_agent_system_prompt


def test_shared_agent_system_prompt_contains_phase5i_tool_selection_rules() -> None:
    prompt = render_agent_system_prompt("No nodes currently online.")

    assert "never guess shortened aliases" in prompt
    assert "do not guess the source node" in prompt
    assert "resume_mode is user intent" in prompt
    assert "Provider-visible tools are only capability.groups" in prompt
    assert "If YCR provides a working set or capability_candidates" in prompt
    assert "Open groups only when the current working set lacks" in prompt
    assert "target is binding" in prompt
    assert "source_id returned for another node is not usable" in prompt
    assert "Before transfer.create, use transfer.preflight" in prompt
    assert "Before artifact.deploy, use artifact.deploy.preflight" in prompt
    assert "Do not invoke a node-local artifact.download_file source directly" in prompt
    assert "dispatchable=false" in prompt
    assert "unavailable_reasons" in prompt
    assert "always provide an exact execution profile" in prompt
    assert "Never use bare values such as admin" in prompt
    assert "retry with admin.readonly instead of asking the user" in prompt
    assert "Do not end with suggested next steps while a schema error" in prompt


def test_deepseek_system_prompt_uses_shared_policy() -> None:
    assert _system_prompt_text_enhanced("No nodes currently online.") == (
        render_agent_system_prompt("No nodes currently online.")
    )
