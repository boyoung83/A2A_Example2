"""tkinter 데모 앱 테스트 — 가짜 에이전트 사용."""
import tkinter as tk

import pytest

from material_agent.agent import AgentError, ToolCall, TurnResult
from material_agent.config import load_settings
from material_agent.tools import INBOUND_FAIL_MESSAGE
from test_verdict import NINE, P100, calc, inb


@pytest.fixture(scope="session")
def tk_session():
    # Tk 인터프리터를 테스트마다 새로 만들면 Windows에서 가끔 초기화가 실패한다 → 세션당 하나만 만든다.
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("화면(Tk)을 사용할 수 없는 환경")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def root(tk_session):
    window = tk.Toplevel(tk_session)
    window.withdraw()
    yield window
    window.destroy()


class FakeToolbox:
    inbound_fails = False


class FakeAgent:
    def __init__(self, result=None, error=None, calls=()):
        self.toolbox = FakeToolbox()
        self.last_bom = P100
        self.asked = []
        self.limits = []
        self.reset_called = False
        self._result, self._error, self._calls = result, error, list(calls)

    def ask(self, text, max_tool_calls, on_tool_call=None):
        self.asked.append(text)
        self.limits.append(max_tool_calls)
        for c in self._calls:
            on_tool_call(c)
        if self._error:
            raise self._error
        return self._result

    def reset(self):
        self.reset_called = True


def make_app(root, agent):
    from app import MaterialApp
    return MaterialApp(root, agent, load_settings(), runner=lambda fn: fn())


def chat_text(app):
    return app.chat.get("1.0", "end")


NINE_CALLS = [ToolCall("get_bom", {"product_code": "P-100"}, True, {"product_code": "P-100", "found": True, "parts": P100}),
              inb("BRG-A", NINE), calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)]


def test_send_runs_turn_and_renders(root):
    agent = FakeAgent(TurnResult("생산 불가입니다", NINE_CALLS), calls=NINE_CALLS)
    app = make_app(root, agent)
    app.entry.insert(0, "오전 9시야")
    app.send()
    app._drain()
    assert agent.asked == ["오전 9시야"] and agent.limits == [16]
    parent = app.tool_tree.get_children()[0]
    assert app.tool_tree.item(parent, "text") == "질문 1 · 도구 4/16회"
    assert len(app.tool_tree.get_children(parent)) == 4
    text = chat_text(app)
    assert "오전 9시야" in text and "생산 불가입니다" in text and "판정: 불가" in text
    assert "자재 기준 판단" in text and not app.busy


def test_limit_choice_used(root):
    agent = FakeAgent(TurnResult("x", []))
    app = make_app(root, agent)
    app.limit_var.set("8")
    app.entry.insert(0, "q")
    app.send()
    app._drain()
    assert agent.limits == [8]


def test_no_card_for_simple_answer(root):
    calls = [ToolCall("get_inventory", {"part_code": "센서 B"}, True,
                      {"found": True, "part_code": "SNS-B", "part_name": "센서 B", "current_stock": 25, "unit": "개"})]
    agent = FakeAgent(TurnResult("센서 B는 25개입니다", calls), calls=calls)
    agent.last_bom = None
    app = make_app(root, agent)
    app.entry.insert(0, "센서 B 재고만 알려줘")
    app.send()
    app._drain()
    assert "판정:" not in chat_text(app)


def test_error_row_tagged(root):
    app = make_app(root, FakeAgent())
    app._start_turn_group(1)
    app._on_tool_call(1, ToolCall("get_inbound_schedule", {"part_code": "BRG-A"}, False, {"error": INBOUND_FAIL_MESSAGE}))
    row = app.tool_tree.get_children(app.tool_tree.get_children()[0])[0]
    assert "error" in app.tool_tree.item(row, "tags")
    assert "오류" in app.tool_tree.item(row, "text")


def test_selecting_row_shows_json(root):
    app = make_app(root, FakeAgent())
    app._start_turn_group(1)
    app._on_tool_call(1, ToolCall("get_inventory", {"part_code": "SNS-B"}, True, {"current_stock": 25}))
    row = app.tool_tree.get_children(app.tool_tree.get_children()[0])[0]
    app.tool_tree.selection_set(row)
    app._show_detail()
    assert '"current_stock": 25' in app.detail.get("1.0", "end")


def test_send_ignored_while_busy(root):
    agent = FakeAgent(TurnResult("x", []))
    app = make_app(root, agent)
    app.busy = True
    app.entry.insert(0, "q")
    app.send()
    assert agent.asked == []


def test_empty_input_ignored(root):
    agent = FakeAgent(TurnResult("x", []))
    app = make_app(root, agent)
    app.send()
    assert agent.asked == []


def test_agent_error_shown(root):
    app = make_app(root, FakeAgent(error=AgentError("API 서버에 연결하지 못했습니다")))
    app.entry.insert(0, "q")
    app.send()
    app._drain()
    assert "API 서버에 연결하지 못했습니다" in chat_text(app) and not app.busy


def test_new_conversation_clears(root):
    agent = FakeAgent(TurnResult("x", NINE_CALLS), calls=NINE_CALLS)
    app = make_app(root, agent)
    app.entry.insert(0, "q")
    app.send()
    app._drain()
    app.new_conversation()
    assert app.tool_tree.get_children() == () and agent.reset_called and app.turn_no == 0
    assert "q" not in chat_text(app)


def test_fail_switch_sets_toolbox(root):
    agent = FakeAgent()
    app = make_app(root, agent)
    app.fail_var.set(True)
    assert agent.toolbox.inbound_fails is True


def test_no_api_key_disables_send(root):
    from app import MaterialApp
    app = MaterialApp(root, None, load_settings(), startup_error=".env에 ANTHROPIC_API_KEY가 없습니다")
    assert "ANTHROPIC_API_KEY" in chat_text(app)
    assert str(app.send_button["state"]) == "disabled"
