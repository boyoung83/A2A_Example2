"""테스트 공용 도구: API 없이 정해진 응답을 돌려주는 가짜 Claude 클라이언트."""
from types import SimpleNamespace

import pytest

from material_agent.agent import MaterialAgent
from material_agent.config import load_settings
from material_agent.data import load_dataset
from material_agent.tools import ToolBox


def text_reply(text: str):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def tool_reply(*uses):
    blocks = [SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input=args)
              for i, (name, args) in enumerate(uses)]
    return SimpleNamespace(stop_reason="tool_use", content=blocks)


class FakeClient:
    """client.beta.messages.create(**kw)를 흉내 낸다. 응답 목록을 차례로 돌려주고, 예외면 던진다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        # messages는 이후에 변경되므로 호출 시점 상태를 복사해 둔다
        self.calls.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def agent_with():
    def make(*responses):
        client = FakeClient(responses)
        settings = load_settings()
        return MaterialAgent(client, ToolBox(load_dataset(), settings.base_time), settings.base_time), client
    return make
