"""tool use 반복 처리(MaterialAgent) 테스트 — 가짜 클라이언트 사용."""
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from conftest import text_reply, tool_reply
from material_agent.agent import AgentError


def test_runs_tools_and_returns_answer(agent_with):
    agent, client = agent_with(tool_reply(("get_inventory", {"part_code": "SNS-B"})), text_reply("센서 B는 25개입니다."))
    r = agent.ask("센서 B 재고만 알려줘", max_tool_calls=16)
    assert r.text == "센서 B는 25개입니다." and [c.name for c in r.tool_calls] == ["get_inventory"]
    assert r.tool_calls[0].payload["current_stock"] == 25 and not r.limit_reached
    sent = client.calls[1]["messages"][-1]["content"][0]
    assert sent["type"] == "tool_result" and sent["is_error"] is False and '"current_stock": 25' in sent["content"]


def test_request_params(agent_with):
    agent, client = agent_with(text_reply("네"))
    agent.ask("안녕", 16)
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["tool_choice"] == {"type": "auto"} and "2026-10-15" in kw["system"] and "thinking" not in kw


def test_parallel_results_in_one_message(agent_with):
    agent, client = agent_with(tool_reply(("get_inventory", {"part_code": "BRG-A"}),
                                          ("get_inventory", {"part_code": "SNS-B"})), text_reply("끝"))
    agent.ask("q", 16)
    assert len(client.calls[1]["messages"][-1]["content"]) == 2


def test_limit_stops_execution_and_forces_answer(agent_with):
    agent, client = agent_with(
        tool_reply(("get_inventory", {"part_code": "BRG-A"}), ("get_inventory", {"part_code": "SNS-B"})),
        text_reply("한도 때문에 일부만 확인했습니다"))
    r = agent.ask("q", max_tool_calls=1)
    assert len(r.tool_calls) == 1 and r.limit_reached
    results = client.calls[1]["messages"][-1]["content"]
    assert results[1]["is_error"] and "도구 실행 한도(1회)" in results[1]["content"]
    assert client.calls[1]["tool_choice"] == {"type": "none"}


def test_limit_resets_per_utterance(agent_with):
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), text_reply("a"),
                          tool_reply(("get_bom", {"product_code": "P-100"})), text_reply("b"))
    assert len(agent.ask("q1", 1).tool_calls) == 1
    assert len(agent.ask("q2", 1).tool_calls) == 1


def test_tool_error_marked(agent_with):
    agent, client = agent_with(tool_reply(("get_inbound_schedule", {"part_code": "BRG-A", "production_start": None})),
                               text_reply("확인 불가"))
    agent.toolbox.inbound_fails = True
    r = agent.ask("q", 16)
    assert r.tool_calls[0].ok is False and client.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_on_tool_call_callback(agent_with):
    seen = []
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), text_reply("x"))
    agent.ask("q", 16, on_tool_call=seen.append)
    assert [c.name for c in seen] == ["get_bom"]


def test_last_bom_kept_across_turns(agent_with):
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), text_reply("시작 시각은?"), text_reply("x"))
    agent.ask("q1", 16)
    agent.ask("오전 9시야", 16)
    assert [p["part_code"] for p in agent.last_bom] == ["BRG-A", "SNS-B"]


def test_history_kept_between_turns(agent_with):
    agent, client = agent_with(text_reply("시작 시각은?"), text_reply("x"))
    agent.ask("q1", 16)
    agent.ask("오전 9시야", 16)
    roles = [m["role"] for m in client.calls[1]["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_refusal_raises_and_rolls_back(agent_with):
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), SimpleNamespace(stop_reason="refusal", content=[]))
    with pytest.raises(AgentError, match="거절"):
        agent.ask("q", 16)
    assert agent.messages == []


def test_api_error_rolls_back_then_next_works(agent_with):
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), err, text_reply("ok"))
    with pytest.raises(AgentError, match="연결"):
        agent.ask("q", 16)
    assert agent.messages == [] and agent.ask("다시", 16).text == "ok"


def test_reset_clears(agent_with):
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), text_reply("a"))
    agent.ask("q", 16)
    agent.reset()
    assert agent.messages == [] and agent.last_bom is None


def test_system_prompt_asks_for_plain_text(agent_with):
    agent, _ = agent_with()
    assert "마크다운" in agent.system and "표" in agent.system


def test_conversation_calls_accumulate_and_reset(agent_with):
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), text_reply("a"),
                          tool_reply(("get_inventory", {"part_code": "BRG-A"})), text_reply("b"))
    agent.ask("q1", 16)
    agent.ask("q2", 16)
    assert [c.name for c in agent.conversation_calls] == ["get_bom", "get_inventory"]
    agent.reset()
    assert agent.conversation_calls == []
