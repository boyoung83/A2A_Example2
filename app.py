"""자재대응 Agent 데모 앱 (tkinter): 왼쪽 대화, 오른쪽 도구 실행 기록, 답변 아래 판정 카드."""
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk

from material_agent.agent import AgentError, MaterialAgent, ToolCall, TurnResult
from material_agent.config import Settings, load_api_key, load_settings
from material_agent.data import load_dataset
from material_agent.tools import ToolBox
from material_agent.verdict import Verdict, build_verdict

FONT = ("맑은 고딕", 10)
FONT_BOLD = ("맑은 고딕", 10, "bold")
STATUS_COLORS = {"가능": "#15803d", "조건부 가능": "#c2410c", "불가": "#b91c1c", "확인 불가": "#4b5563"}
LIMIT_CHOICES = ("16", "8")


def _fmt_args(args: dict) -> str:
    return ", ".join(json.dumps(v, ensure_ascii=False) for v in args.values() if v is not None)


def summarize(call: ToolCall) -> str:
    """도구 기록 한 줄에 붙일 결과 요약."""
    p = call.payload
    if not call.ok:
        return f"오류: {p.get('error', '')}"
    if call.name == "get_bom":
        if not p.get("found"):
            return "등록되지 않은 제품"
        return ", ".join(f"{x['part_code']}×{x['qty_per_unit']}" for x in p["parts"])
    if call.name == "get_inventory":
        return f"{p['current_stock']}{p['unit']}" if p.get("found") else "등록되지 않은 부품"
    if call.name == "get_inbound_schedule":
        rows = " / ".join(f"{r['arrival'][5:10].replace('-', '/')} {r['arrival'][11:16]} {r['qty']}개"
                          for r in p.get("schedule", [])) or "입고 예정 없음"
        if "inbound_before_start_total" in p:
            rows += f" → 시작 전 합계 {p['inbound_before_start_total']}개"
        return rows
    if call.name == "calculate_shortage":
        return f"필요 {p['required']} · 현재부족 {p['shortage_now']} · 시작기준부족 {p['shortage_at_start']}"
    return json.dumps(p, ensure_ascii=False)[:60]


class MaterialApp(ttk.Frame):
    def __init__(self, master, agent: MaterialAgent | None, settings: Settings,
                 startup_error: str | None = None, runner=None):
        super().__init__(master)
        self.agent = agent
        self.settings = settings
        self.runner = runner or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.busy = False
        self.turn_no = 0
        self._queue: queue.Queue = queue.Queue()
        self._groups: dict[int, dict] = {}
        self._details: dict[str, ToolCall] = {}
        self._build()
        if agent is None:
            self._append(f"⚠ {startup_error or '에이전트를 시작하지 못했습니다'}\n", "error")
            self.send_button.configure(state="disabled")
            self.entry.configure(state="disabled")
        else:
            self._append("생산 가능 여부를 물어보세요. 예: 내일 P-100을 20개 만들 수 있을까?\n", "meta")
        self.after(100, self._poll)

    # ---------- 화면 구성 ----------
    def _build(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="새 대화", command=self.new_conversation).pack(side="left")
        ttk.Label(bar, text="  실행 한도").pack(side="left")
        default = str(self.settings.max_tool_calls)
        self.limit_var = tk.StringVar(value=default)
        values = LIMIT_CHOICES if default in LIMIT_CHOICES else (default, *LIMIT_CHOICES)
        ttk.Combobox(bar, textvariable=self.limit_var, values=values, width=4, state="readonly").pack(side="left", padx=4)
        ttk.Label(bar, text="회/질문").pack(side="left")
        self.fail_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="입고 조회 실패 모드", variable=self.fail_var).pack(side="left", padx=16)
        self.fail_var.trace_add("write", lambda *_: self._apply_fail_mode())
        ttk.Label(bar, text=f"기준시각 {self.settings.base_time:%Y-%m-%d %H:%M}", foreground="#6b7280").pack(side="right")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(panes)
        panes.add(left, weight=3)
        ttk.Label(left, text="대화", font=FONT_BOLD).pack(anchor="w")
        chat_box = ttk.Frame(left)
        chat_box.pack(fill="both", expand=True)
        self.chat = tk.Text(chat_box, wrap="word", font=FONT, state="disabled", padx=8, pady=6,
                            background="#ffffff", relief="solid", borderwidth=1)
        scroll = ttk.Scrollbar(chat_box, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("user", background="#dbeafe", lmargin1=120, lmargin2=120, spacing1=6, spacing3=6)
        self.chat.tag_configure("agent", background="#f3f4f6", rmargin=80, spacing1=6, spacing3=6)
        self.chat.tag_configure("meta", foreground="#6b7280")
        self.chat.tag_configure("waiting", foreground="#6b7280", font=(FONT[0], 10, "italic"))
        self.chat.tag_configure("error", foreground="#b91c1c", font=FONT_BOLD)
        self.chat.tag_configure("card", lmargin1=12, lmargin2=12, rmargin=80, background="#fafafa")
        self.chat.tag_configure("note", foreground="#6b7280", lmargin1=12, background="#fafafa")
        for status, color in STATUS_COLORS.items():
            self.chat.tag_configure(f"status:{status}", foreground=color, font=("맑은 고딕", 11, "bold"),
                                    lmargin1=12, background="#fafafa", spacing1=4)

        entry_row = ttk.Frame(left)
        entry_row.pack(fill="x", pady=(6, 0))
        self.entry = ttk.Entry(entry_row, font=FONT)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _e: self.send())
        self.send_button = ttk.Button(entry_row, text="보내기", command=self.send)
        self.send_button.pack(side="left", padx=(6, 0))

        right = ttk.PanedWindow(panes, orient="vertical")
        panes.add(right, weight=2)
        tree_box = ttk.Frame(right)
        right.add(tree_box, weight=3)
        ttk.Label(tree_box, text="도구 실행 기록", font=FONT_BOLD).pack(anchor="w")
        self.tool_tree = ttk.Treeview(tree_box, show="tree", selectmode="browse")
        self.tool_tree.pack(fill="both", expand=True)
        self.tool_tree.tag_configure("error", foreground="#b91c1c")
        self.tool_tree.tag_configure("group", font=FONT_BOLD)
        self.tool_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())
        detail_box = ttk.Frame(right)
        right.add(detail_box, weight=2)
        ttk.Label(detail_box, text="선택한 도구의 입력·결과 원문", font=FONT_BOLD).pack(anchor="w")
        self.detail = tk.Text(detail_box, wrap="none", font=("Consolas", 9), height=10, state="disabled",
                              relief="solid", borderwidth=1)
        self.detail.pack(fill="both", expand=True)

    # ---------- 동작 ----------
    def send(self) -> None:
        if self.busy or self.agent is None:
            return
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.turn_no += 1
        n, limit = self.turn_no, int(self.limit_var.get())
        self._append(f"{text}\n", "user")
        self._start_turn_group(n)
        self._set_busy(True)

        def work():
            try:
                result = self.agent.ask(text, limit, on_tool_call=lambda c: self._post(self._on_tool_call, n, c))
                self._post(self._on_turn_done, n, result)
            except AgentError as e:
                self._post(self._on_turn_error, str(e))
            except Exception as e:  # 예상하지 못한 오류도 화면에 알린다
                self._post(self._on_turn_error, f"처리 중 오류가 발생했습니다: {e}")

        self.runner(work)

    def new_conversation(self) -> None:
        if self.busy:
            return
        if self.agent is not None:
            self.agent.reset()
        self.turn_no = 0
        self._groups.clear()
        self._details.clear()
        self.tool_tree.delete(*self.tool_tree.get_children())
        self._set_text(self.detail, "")
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self._append("새 대화를 시작합니다.\n", "meta")

    def _apply_fail_mode(self):
        if self.agent is not None:
            self.agent.toolbox.inbound_fails = bool(self.fail_var.get())

    # ---------- 백그라운드 결과 전달 ----------
    def _post(self, fn, *args):
        self._queue.put((fn, args))

    def _drain(self):
        while True:
            try:
                fn, args = self._queue.get_nowait()
            except queue.Empty:
                return
            fn(*args)

    def _poll(self):
        self._drain()
        self.after(100, self._poll)

    # ---------- 화면 갱신 ----------
    def _start_turn_group(self, n: int) -> None:
        limit = int(self.limit_var.get())
        parent = self.tool_tree.insert("", "end", text=f"질문 {n} · 도구 0/{limit}회", open=True, tags=("group",))
        self._groups[n] = {"id": parent, "limit": limit, "count": 0}

    def _on_tool_call(self, n: int, call: ToolCall) -> None:
        group = self._groups[n]
        group["count"] += 1
        text = f"{group['count']}. {call.name}({_fmt_args(call.args)}) → {summarize(call)}"
        row = self.tool_tree.insert(group["id"], "end", text=text, tags=() if call.ok else ("error",))
        self._details[row] = call
        self.tool_tree.item(group["id"], text=f"질문 {n} · 도구 {group['count']}/{group['limit']}회")
        self.tool_tree.see(row)

    def _on_turn_done(self, n: int, result: TurnResult) -> None:
        self._clear_waiting()
        self._append(f"{result.text or '(답변 없음)'}\n", "agent")
        verdict = build_verdict(result.tool_calls, getattr(self.agent, "last_bom", None))
        if verdict:
            self._render_verdict(verdict)
        if result.limit_reached:
            self._append("※ 도구 실행 한도에 도달해 일부 조회를 실행하지 않았습니다\n", "note")
            group = self._groups.get(n)
            if group:
                self.tool_tree.item(group["id"], text=self.tool_tree.item(group["id"], "text") + " (한도 도달)")
        self._set_busy(False)

    def _on_turn_error(self, message: str) -> None:
        self._clear_waiting()
        self._append(f"⚠ {message}\n", "error")
        self._set_busy(False)

    def _render_verdict(self, verdict: Verdict) -> None:
        self._append(f"판정: {verdict.status} (자재 기준)\n", f"status:{verdict.status}")
        for r in verdict.rows:
            line = (f"{r.part_name}({r.part_code})  필요 {r.required} · 재고 {r.current_stock} · "
                    f"반영 입고 {r.inbound_before_start} · 현재부족 {r.shortage_now} · 시작기준부족 {r.shortage_at_start}")
            if r.inbound_error:
                line += " · 입고 조회 실패"
            self._append(line + "\n", "card")
        self._append("※ 자재 기준 판단 · 설비·인력은 확인하지 않음\n", "note")

    def _show_detail(self) -> None:
        selected = self.tool_tree.selection()
        call = self._details.get(selected[0]) if selected else None
        if call is None:
            self._set_text(self.detail, "")
            return
        body = (f"{call.name} ({'성공' if call.ok else '오류'})\n\n입력:\n"
                f"{json.dumps(call.args, ensure_ascii=False, indent=2)}\n\n결과:\n"
                f"{json.dumps(call.payload, ensure_ascii=False, indent=2)}")
        self._set_text(self.detail, body)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.entry.configure(state=state)
        self.send_button.configure(state=state)
        if busy:
            self._append("확인 중…\n", "waiting")
        else:
            self.entry.focus_set()

    def _clear_waiting(self) -> None:
        ranges = self.chat.tag_ranges("waiting")
        if ranges:
            self.chat.configure(state="normal")
            self.chat.delete(ranges[0], f"{ranges[-1]}+1c")  # 뒤따르는 빈 줄까지
            self.chat.configure(state="disabled")

    def _append(self, text: str, tag: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.insert("end", "\n")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    root.title("자재대응 Agent — 생산 가능 여부 확인")
    root.geometry("1280x800")
    try:
        root.state("zoomed")
    except tk.TclError:
        pass
    settings = load_settings()
    key = load_api_key()
    agent, error = None, None
    if key:
        import anthropic
        toolbox = ToolBox(load_dataset(), settings.base_time)
        agent = MaterialAgent(anthropic.Anthropic(api_key=key), toolbox, settings.base_time)
    else:
        error = ".env에 ANTHROPIC_API_KEY가 없습니다 (.env.example 참고)"
    MaterialApp(root, agent, settings, error).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
