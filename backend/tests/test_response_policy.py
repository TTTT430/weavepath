from api.llm import OpenAICompatibleLLM
from api.response_policy import COMMUNICATION_POLICY
from agent_runtime.context import build_system_policy


def test_chat_default_guidance_does_not_set_generation_limits():
    client = OpenAICompatibleLLM(base_url="https://example.test/v1", model="test")
    assert COMMUNICATION_POLICY in client.system_prompt
    assert client.request_options() == {}
    assert "requests detailed teaching" in client.system_prompt


def test_explicit_chat_prompt_is_preserved():
    client = OpenAICompatibleLLM(
        base_url="https://example.test/v1", model="test", system_prompt="Custom style"
    )
    assert client.system_prompt == "Custom style"


def test_agent_guidance_is_stable_and_not_duplicated():
    default = OpenAICompatibleLLM(base_url="https://example.test/v1", model="test").system_prompt
    for configured in ("", default, "Custom agent instructions"):
        policy = build_system_policy(configured)
        assert policy == build_system_policy(configured)
        assert policy["content"].count(COMMUNICATION_POLICY) == 1
        assert "Never claim that a side effect happened" in policy["content"]
        if configured:
            assert configured in policy["content"]
