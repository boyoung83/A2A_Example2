"""tkinter 데모 앱 테스트 — 가짜 에이전트 사용."""
import tkinter as tk
from tkinter import ttk

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
    return app.transcript()


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
    assert app.tool_tree.item(parent, "text") == "질문 1   도구 4/16회"
    assert len(app.tool_tree.get_children(parent)) == 4
    text = chat_text(app)
    assert [b.text for b in app.user_bubbles] == ["오전 9시야"] and "생산 불가입니다" in text
    assert "[판정 불가] 자재 기준 판정" in text
    card = app.verdict_cards[-1]
    assert card.status == "불가" and card.lit == "red"
    assert card.status_label["text"] == "불가" and card.status_label["fg"] == "#D63A2F"
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
    assert "[판정" not in chat_text(app) and app.verdict_cards == []


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


def test_markdown_bold_rendered_without_stars(root):
    agent = FakeAgent(TurnResult("**결론: 불가**입니다", []))
    app = make_app(root, agent)
    app.entry.insert(0, "q")
    app.send()
    app._drain()
    text = chat_text(app)
    assert "결론: 불가입니다" in text and "**" not in text
    bubble = app.agent_bubbles[-1]
    assert bubble.lines[0] == ("결론: 불가입니다", True) and "**" not in bubble.text


def test_api_status_shown_in_header(root):
    app = make_app(root, FakeAgent())
    assert "API 연결됨" in app.status_label["text"]


def test_user_bubble_right_aligned_and_distinct(root):
    app = make_app(root, FakeAgent())
    c = app.chat
    assert c.tag_cget("row_user", "justify") == "right" and c.tag_cget("row_agent", "justify") in ("", "left")
    app._append_message("나", "오전 9시야", "user")
    app._append_message("에이전트", "확인했습니다", "agent")
    user, agent_bubble = app.user_bubbles[-1], app.agent_bubbles[-1]
    assert user.text == "오전 9시야" and user.fill != agent_bubble.fill
    assert user.is_rounded and agent_bubble.is_rounded
    assert "row_user" in c.tag_names(c.index(str(user.master)))


def test_agent_blank_lines_collapsed(root):
    app = make_app(root, FakeAgent())
    app._append_message("에이전트", "첫 줄\n\n\n둘째 줄", "agent")
    assert "첫 줄\n둘째 줄" in chat_text(app)


@pytest.mark.parametrize("status,lit", [("가능", "green"), ("조건부 가능", "amber"), ("불가", "red"), ("확인 불가", None)])
def test_andon_lamp_matches_status(root, status, lit):
    from material_agent.verdict import PartRow, Verdict
    app = make_app(root, FakeAgent())
    row = PartRow("BRG-A", "베어링 A", 40, 30, 0, 10, 10, status == "확인 불가")
    app._render_verdict(Verdict(status, [row]))
    assert app.verdict_cards[-1].lit == lit


def test_tool_rows_numbered_in_order(root):
    app = make_app(root, FakeAgent())
    app._start_turn_group(1)
    for part in ("BRG-A", "SNS-B"):
        app._on_tool_call(1, ToolCall("get_inventory", {"part_code": part}, True,
                                      {"found": True, "current_stock": 1, "unit": "개"}))
    rows = app.tool_tree.get_children(app.tool_tree.get_children()[0])
    assert [app.tool_tree.item(r, "text")[0] for r in rows] == ["①", "②"]


def test_tool_row_args_are_short():
    from app import _fmt_args
    assert _fmt_args({"part_code": "BRG-A", "production_start": "2026-10-16T09:00:00+09:00"}) == "BRG-A, 10/16 09:00 시작"
    assert _fmt_args({"part_code": "SNS-B", "production_qty": 20, "qty_per_unit": 1,
                      "current_stock": 25, "inbound_before_start": 0}, "calculate_shortage") == "SNS-B, 20개"


def test_andon_tower_is_compact(root):
    from material_agent.verdict import PartRow, Verdict
    app = make_app(root, FakeAgent())
    app._render_verdict(Verdict("불가", [PartRow("BRG-A", "베어링 A", 40, 30, 0, 10, 10, False)]))
    assert int(app.verdict_cards[-1].tower["height"]) <= 120


def test_entry_has_placeholder_until_focus(root):
    app = make_app(root, FakeAgent(TurnResult("x", [])))
    assert app.entry.get() == "" and app.placeholder.winfo_manager() != ""


def test_agent_lines_separated_by_blank_line(root):
    from tkinter import font as tkfont
    app = make_app(root, FakeAgent())
    app._append_message("에이전트", "첫 문장입니다.\n- 둘째 줄", "agent")
    bubble = app.agent_bubbles[-1]
    first, second = bubble.find_withtag("line")
    gap = bubble.coords(second)[1] - bubble.bbox(first)[3]
    assert gap >= tkfont.Font(font=app.fonts.chat).metrics("linespace") * 0.8
    app._append_message("나", "오전 9시야", "user")
    assert app.user_bubbles[-1].line_gap < app.agent_bubbles[-1].line_gap


def test_trace_and_detail_split_is_resizable(root):
    app = make_app(root, FakeAgent())
    panes = app.right_panes
    assert str(panes["orient"]) == "vertical" and len(panes.panes()) == 2


def test_detail_has_vertical_scrollbar(root):
    app = make_app(root, FakeAgent())
    assert str(app.detail_scroll["orient"]) == "vertical"
    assert app.detail["yscrollcommand"] != ""
    assert app.detail.pack_info()["expand"] in (1, True, "1")


def test_right_scrollbars_aligned(root):
    app = make_app(root, FakeAgent())
    app.pack(fill="both", expand=True)
    root.deiconify()
    root.geometry("1400x880")
    root.update()
    # 스크롤바는 내용이 넘칠 때만 보이므로, 양쪽 모두 넘치게 채운다
    app._start_turn_group(1)
    for i in range(30):
        app._on_tool_call(1, ToolCall("get_inventory", {"part_code": f"P{i}"}, True, {"found": True,
                                      "current_stock": i, "unit": "개"}))
    app._set_text(app.detail, "\n".join(f"line {i}" for i in range(80)))
    root.update()
    assert app.detail_scroll.winfo_ismapped() and app.detail_scroll.winfo_width() > 1
    tree_sb = [w for w in app.tool_tree.master.winfo_children() if isinstance(w, ttk.Scrollbar)][0]
    right_edge = lambda w: w.winfo_rootx() + w.winfo_width()
    assert right_edge(tree_sb) == right_edge(app.detail_scroll)
    assert tree_sb.winfo_rootx() == app.detail_scroll.winfo_rootx()
    root.withdraw()


def test_scrollbars_are_faint(root):
    import app as app_module
    app = make_app(root, FakeAgent())
    style = ttk.Style(app)
    for name, trough in (("Slim.Vertical.TScrollbar", app_module.SURFACE),
                                          ("Chat.Vertical.TScrollbar", app_module.CHAT_BG)):
        thumb = style.lookup(name, "background")
        assert style.lookup(name, "troughcolor") == trough
        assert app_module.color_distance(thumb, trough) < 40   # 배경과 거의 같은 옅은 막대
        assert int(style.lookup(name, "width")) <= 6



def test_chat_uses_chat_scrollbar_style(root):
    app = make_app(root, FakeAgent())
    chat_sb = [w for w in app.chat.master.winfo_children() if isinstance(w, ttk.Scrollbar)][0]
    assert str(chat_sb["style"]) == "Chat.Vertical.TScrollbar"


def test_buttons_are_rounded(root):
    app = make_app(root, FakeAgent())
    assert app.send_button.is_rounded and app.new_button.is_rounded
    app.send_button.set_enabled(False)
    assert str(app.send_button["state"]) == "disabled"


def test_limit_segmented_control(root):
    app = make_app(root, FakeAgent())
    assert app.limit_control.is_rounded
    app.limit_control.select("8")
    assert app.limit_var.get() == "8" and app.limit_control.selected == "8"


def test_fail_switch_toggles(root):
    agent = FakeAgent()
    app = make_app(root, agent)
    assert app.fail_switch.on is False
    app.fail_switch.toggle()
    assert app.fail_var.get() is True and app.fail_switch.on is True and agent.toolbox.inbound_fails is True


def test_panels_rounded_and_tree_borderless(root):
    app = make_app(root, FakeAgent())
    assert app.chat_panel.is_rounded and app.trace_panel.is_rounded and app.entry_pill.is_rounded
    assert "field" not in str(ttk.Style(app).layout("Trace.Treeview"))


def test_andon_card_rounded(root):
    from material_agent.verdict import PartRow, Verdict
    app = make_app(root, FakeAgent())
    app._render_verdict(Verdict("가능", [PartRow("BRG-A", "베어링 A", 20, 30, 0, 0, 0, False)]))
    assert app.verdict_cards[-1].is_rounded



def test_scrollbars_hide_when_content_fits(root):
    app = make_app(root, FakeAgent())
    app.pack(fill="both", expand=True)
    root.deiconify()
    root.geometry("1400x880")
    root.update()
    assert not app.detail_scroll.winfo_ismapped()
    app._set_text(app.detail, "\n".join(f"line {i}" for i in range(80)))
    root.update()
    assert app.detail_scroll.winfo_ismapped()
    app._set_text(app.detail, "short")
    root.update()
    assert not app.detail_scroll.winfo_ismapped()
    root.withdraw()


def test_detail_hint_explains_clicking_a_step(root):
    import app as app_module
    app = make_app(root, FakeAgent())
    hint = app.detail.get("1.0", "end").strip()
    assert hint == app_module.DETAIL_HINT and "단계" in hint and "누르면" in hint
    assert "hint" in app.detail.tag_names("1.0")  # 안내문은 본문 글꼴(①②③이 깨지지 않게)
    app.new_conversation()
    assert app.detail.get("1.0", "end").strip() == app_module.DETAIL_HINT
