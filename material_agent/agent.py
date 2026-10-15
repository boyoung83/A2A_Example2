"""Claude tool use 반복 처리: 질문 → 도구 선택·실행 → 결과 전달 → 답변."""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import anthropic

from material_agent.prompts import build_system_prompt
from material_agent.tools import TOOL_SCHEMAS, ToolBox

MODEL = "claude-opus-5-5"
LIMIT_MESSAGE = "도구 실행 한도({limit}회)에 도달해 실행하지 않았습니다"


class AgentError(Exception):
    """질문 처리에 실패했을 때 사용자에게 보여줄 오류."""


@dataclass
class ToolCall:
    name: str
    args: dict
    ok: bool
    payload: dict


@dataclass
class TurnResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    limit_reached: bool = False


class MaterialAgent:
    def __init__(self, client, toolbox: ToolBox, base_time: datetime):
        self.client = client
        self.toolbox = toolbox
        self.system = build_system_prompt(base_time)
        self.messages: list = []
        self.last_bom: list[dict] | None = None

    def reset(self) -> None:
        self.messages = []
        self.last_bom = None

    def ask(self, user_text: str, max_tool_calls: int,
            on_tool_call: Callable[[ToolCall], None] | None = None) -> TurnResult:
        start = len(self.messages)
        saved_bom = self.last_bom
        try:
            return self._run_turn(user_text, max_tool_calls, on_tool_call)
        except Exception as e:
            # 실패한 발화는 기록에서 통째로 빼서 다음 질문이 정상적으로 이어지게 한다
            del self.messages[start:]
            self.last_bom = saved_bom
            raise e if isinstance(e, AgentError) else AgentError(_error_message(e)) from e

    def _run_turn(self, user_text, max_tool_calls, on_tool_call) -> TurnResult:
        self.messages.append({"role": "user", "content": user_text})
        calls: list[ToolCall] = []
        limit_reached = False
        for _ in range(max_tool_calls + 5):  # 응답이 끝나지 않을 때를 대비한 안전장치
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "medium"},
                system=self.system,
                tools=TOOL_SCHEMAS,
                tool_choice={"type": "none"} if limit_reached else {"type": "auto"},
                messages=self.messages,
            )
            if response.stop_reason == "refusal":
                raise AgentError("Claude가 요청을 거절했습니다")
            if response.stop_reason == "max_tokens":
                raise AgentError("응답이 잘렸습니다")
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                return TurnResult(text, calls, limit_reached)

            results = []
            for block in (b for b in response.content if b.type == "tool_use"):
                if len(calls) < max_tool_calls:
                    outcome = self.toolbox.run(block.name, dict(block.input))
                    call = ToolCall(block.name, dict(block.input), outcome.ok, outcome.payload)
                    calls.append(call)
                    if call.name == "get_bom" and call.ok and call.payload.get("found"):
                        self.last_bom = call.payload["parts"]
                    if on_tool_call:
                        on_tool_call(call)
                    ok, payload = outcome.ok, outcome.payload
                else:
                    limit_reached = True
                    ok, payload = False, {"error": LIMIT_MESSAGE.format(limit=max_tool_calls)}
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(payload, ensure_ascii=False), "is_error": not ok})
            self.messages.append({"role": "user", "content": results})
        raise AgentError("답변을 마치지 못했습니다 (반복 횟수 초과)")


def _error_message(e: Exception) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "API 키가 올바르지 않습니다"
    if isinstance(e, anthropic.RateLimitError):
        return "요청이 많아 잠시 후 다시 시도해주세요"
    if isinstance(e, anthropic.APIConnectionError):
        return "API 서버에 연결하지 못했습니다"
    if isinstance(e, anthropic.APIStatusError):
        return f"API 오류 ({e.status_code})"
    return f"처리 중 오류가 발생했습니다: {e}"
