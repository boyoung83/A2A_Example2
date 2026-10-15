"""자재대응 Agent 데모 앱 (tkinter): 왼쪽 대화, 오른쪽 도구 실행 기록, 답변 아래 판정 카드.

디자인은 직전 프로젝트(교대 인수인계 앱)와 같은 체계: 어두운 상단 헤더, 흰 카드, 슬레이트 계열 글자색, 상태별 강조색.
"""
import json
import queue
import re
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from material_agent.agent import MODEL, AgentError, MaterialAgent, ToolCall, TurnResult
from material_agent.config import Settings, load_api_key, load_settings
from material_agent.data import load_dataset
from material_agent.tools import ToolBox
from material_agent.verdict import Verdict, build_verdict

BG = "#F3F5F9"
SURFACE = "#FFFFFF"
SUBTLE = "#F8FAFC"
BORDER = "#E2E8F0"
TEXT = "#0F172A"
MUTED = "#64748B"
FAINT = "#94A3B8"
PRIMARY = "#2563EB"
PRIMARY_HOVER = "#1D4ED8"
PRIMARY_SOFT = "#EFF6FF"
USER_BUBBLE = "#DBEAFE"
HEADER = "#0F172A"
HEADER_MUTED = "#94A3B8"
HEADER_RAISED = "#1E293B"
DANGER = "#DC2626"
DANGER_SOFT = "#FEF2F2"
STATUS_COLORS = {  # 판정: (강조색, 옅은 배경)
    "가능": ("#059669", "#F0FDF4"),
    "조건부 가능": ("#D97706", "#FFFBEB"),
    "불가": ("#DC2626", "#FEF2F2"),
    "확인 불가": ("#475569", "#F1F5F9"),
}
LIMIT_CHOICES = ("16", "8")
BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")


class Fonts:
    def __init__(self, root: tk.Misc):
        families = set(tkfont.families(root))
        base = next((f for f in ("Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans KR")
                     if f in families), "TkDefaultFont")
        mono = next((f for f in ("D2Coding", "Consolas", "Menlo") if f in families), "TkFixedFont")
        self.brand = (base, 15, "bold")
        self.title = (base, 12, "bold")
        self.verdict = (base, 13, "bold")
        self.body = (base, 10)
        self.body_bold = (base, 10, "bold")
        self.chat = (base, 11)
        self.chat_bold = (base, 11, "bold")
        self.small = (base, 9)
        self.small_bold = (base, 9, "bold")
        self.mono = (mono, 9)


class Button(tk.Label):
    """평평한 버튼 (hover·비활성 상태 포함). 직전 프로젝트와 같은 모양."""

    STYLES = {
        "primary": (PRIMARY, "#FFFFFF", PRIMARY_HOVER, PRIMARY),
        "secondary": (SURFACE, TEXT, SUBTLE, BORDER),
    }

    def __init__(self, master, text, command, kind="secondary", font=None, **kw):
        bg, fg, hover, border = self.STYLES[kind]
        super().__init__(master, text=text, bg=bg, fg=fg, font=font, cursor="hand2",
                         padx=16, pady=7, highlightthickness=1, highlightbackground=border, **kw)
        self._colors = (bg, fg, hover)
        self._command = command
        self.bind("<Enter>", lambda e: self["state"] != "disabled" and self.configure(bg=hover))
        self.bind("<Leave>", lambda e: self.configure(bg=bg))
        self.bind("<Button-1>", lambda e: self["state"] != "disabled" and self._command())

    def set_enabled(self, enabled: bool):
        bg, fg, _ = self._colors
        self.configure(state="normal" if enabled else "disabled", bg=bg if enabled else BORDER,
                       fg=fg, disabledforeground=FAINT, cursor="hand2" if enabled else "arrow")


class Card(tk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER, **kw)


def card_header(card, fonts: Fonts, title: str, subtitle: str) -> tk.Frame:
    head = tk.Frame(card, bg=SURFACE)
    head.pack(fill="x", padx=18, pady=(14, 10))
    actions = tk.Frame(head, bg=SURFACE)  # 버튼 영역을 먼저 배치해야 좁은 창에서도 잘리지 않는다
    actions.pack(side="right", anchor="n")
    text = tk.Frame(head, bg=SURFACE)
    text.pack(side="left", fill="x", expand=True)
    tk.Label(text, text=title, bg=SURFACE, fg=TEXT, font=fonts.title).pack(anchor="w")
    tk.Label(text, text=subtitle, bg=SURFACE, fg=MUTED, font=fonts.small, anchor="w").pack(anchor="w", pady=(2, 0))
    return actions


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


class MaterialApp(tk.Frame):
    def __init__(self, master, agent: MaterialAgent | None, settings: Settings,
                 startup_error: str | None = None, runner=None):
        super().__init__(master, bg=BG)
        self.agent = agent
        self.settings = settings
        self.runner = runner or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.busy = False
        self.turn_no = 0
        self._queue: queue.Queue = queue.Queue()
        self._groups: dict[int, dict] = {}
        self._details: dict[str, ToolCall] = {}
        self.fonts = Fonts(self)
        self._setup_style()
        self._build_header(startup_error)
        self._build_toolbar()
        self._build_body()
        if agent is None:
            self._append_meta(f"⚠ {startup_error or '에이전트를 시작하지 못했습니다'}", "error")
            self.send_button.set_enabled(False)
            self.entry.configure(state="disabled")
        else:
            self._append_meta("생산 가능 여부를 물어보세요. 예: 내일 P-100을 20개 만들 수 있을까?", "meta")
        self.after(100, self._poll)

    # ---------- 화면 구성 ----------
    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        f = self.fonts
        style.configure("Tool.Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                        bordercolor=SURFACE, rowheight=28, font=f.body)
        style.map("Tool.Treeview", background=[("selected", PRIMARY_SOFT)], foreground=[("selected", TEXT)])
        style.layout("Slim.Vertical.TScrollbar",
                     [("Vertical.Scrollbar.trough", {"sticky": "ns",
                       "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Slim.Vertical.TScrollbar", troughcolor=SURFACE, background="#CBD5E1",
                        bordercolor=SURFACE, lightcolor="#CBD5E1", darkcolor="#CBD5E1", width=8)
        style.map("Slim.Vertical.TScrollbar", background=[("active", FAINT)])

    def _build_header(self, startup_error):
        f = self.fonts
        header = tk.Frame(self, bg=HEADER, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=HEADER)
        brand.pack(side="left", padx=24)
        tk.Label(brand, text="자", bg=PRIMARY, fg="#FFFFFF", font=f.title, width=2, pady=2).pack(side="left", pady=16)
        titles = tk.Frame(brand, bg=HEADER)
        titles.pack(side="left", padx=12)
        tk.Label(titles, text="자재대응 Agent", bg=HEADER, fg="#FFFFFF", font=f.brand).pack(anchor="w")
        tk.Label(titles, text="생산 가능 여부를 자재 기준으로 확인합니다", bg=HEADER, fg=HEADER_MUTED,
                 font=f.small).pack(anchor="w")

        status = tk.Frame(header, bg=HEADER)
        status.pack(side="right", padx=24)
        dot, label = ("#34D399", "Claude API 연결 준비됨") if self.agent else ("#F87171", "API 키 없음 · .env 확인")
        pill = tk.Frame(status, bg=HEADER_RAISED, padx=12, pady=5)
        pill.pack(side="right")
        tk.Label(pill, text="●", bg=HEADER_RAISED, fg=dot, font=f.small).pack(side="left")
        self.status_label = tk.Label(pill, text=f" {label}", bg=HEADER_RAISED, fg="#E2E8F0", font=f.small_bold)
        self.status_label.pack(side="left")
        tk.Label(pill, text=f"  ·  {MODEL}", bg=HEADER_RAISED, fg=HEADER_MUTED, font=f.small).pack(side="left")
        tk.Label(status, text=f"기준시각 {self.settings.base_time:%Y-%m-%d %H:%M}", bg=HEADER, fg=HEADER_MUTED,
                 font=f.small).pack(side="right", padx=16)

    def _build_toolbar(self):
        f = self.fonts
        bar = Card(self)
        bar.pack(fill="x", padx=20, pady=(16, 0))
        inner = tk.Frame(bar, bg=SURFACE)
        inner.pack(fill="x", padx=18, pady=10)
        Button(inner, "새 대화", self.new_conversation, font=f.body).pack(side="left")

        tk.Label(inner, text="실행 한도", bg=SURFACE, fg=MUTED, font=f.small_bold).pack(side="left", padx=(24, 8))
        default = str(self.settings.max_tool_calls)
        self.limit_var = tk.StringVar(value=default)
        seg = tk.Frame(inner, bg=BORDER, padx=3, pady=3)
        seg.pack(side="left")
        self._limit_tabs = {}
        for value in dict.fromkeys((default, *LIMIT_CHOICES)):
            tab = tk.Label(seg, text=f"{value}회", font=f.body_bold, padx=14, pady=4, cursor="hand2")
            tab.pack(side="left")
            tab.bind("<Button-1>", lambda e, v=value: self.limit_var.set(v))
            self._limit_tabs[value] = tab
        self.limit_var.trace_add("write", lambda *_: self._paint_limit())
        self._paint_limit()
        tk.Label(inner, text="질문 한 건당", bg=SURFACE, fg=FAINT, font=f.small).pack(side="left", padx=8)

        self.fail_var = tk.BooleanVar(value=False)
        self.fail_toggle = tk.Label(inner, font=f.body_bold, padx=12, pady=5, cursor="hand2", highlightthickness=1)
        self.fail_toggle.pack(side="left", padx=(24, 0))
        self.fail_toggle.bind("<Button-1>", lambda e: self.fail_var.set(not self.fail_var.get()))
        self.fail_var.trace_add("write", lambda *_: self._apply_fail_mode())
        self._paint_fail()
        tk.Label(inner, text="켜면 입고 예정 조회가 오류를 돌려줍니다 (대화 5 시연용)", bg=SURFACE, fg=FAINT,
                 font=f.small).pack(side="left", padx=8)

    def _build_body(self):
        f = self.fonts
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=16)
        body.columnconfigure(0, weight=3, uniform="col")
        body.columnconfigure(1, weight=2, uniform="col")
        body.rowconfigure(0, weight=1)

        chat_card = Card(body)
        chat_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        card_header(chat_card, f, "대화", "질문하면 Claude가 필요한 도구를 골라 조회하고 답합니다")
        input_row = tk.Frame(chat_card, bg=SURFACE)
        input_row.pack(side="bottom", fill="x", padx=18, pady=(10, 16))
        self.send_button = Button(input_row, "보내기", self.send, kind="primary", font=f.body_bold)
        self.send_button.pack(side="right", padx=(8, 0))
        box = tk.Frame(input_row, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER,
                       highlightcolor=PRIMARY)
        box.pack(side="left", fill="x", expand=True)
        self.entry = tk.Entry(box, font=f.chat, relief="flat", bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                              disabledbackground=SUBTLE)
        self.entry.pack(fill="x", padx=10, pady=7)
        self.entry.bind("<Return>", lambda _e: self.send())

        chat_box = tk.Frame(chat_card, bg=SUBTLE)
        chat_box.pack(fill="both", expand=True, padx=18)
        self.chat = tk.Text(chat_box, wrap="word", font=f.chat, state="disabled", padx=14, pady=10,
                            bg=SUBTLE, fg=TEXT, relief="flat", highlightthickness=0, cursor="arrow",
                            spacing2=3)
        scroll = ttk.Scrollbar(chat_box, command=self.chat.yview, style="Slim.Vertical.TScrollbar")
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self._configure_chat_tags()

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)
        right.columnconfigure(0, weight=1)

        tool_card = Card(right)
        tool_card.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        card_header(tool_card, f, "도구 실행 기록", "Claude가 고른 도구와 결과 · 줄을 누르면 아래에 원문이 보입니다")
        tree_box = tk.Frame(tool_card, bg=SURFACE)
        tree_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tool_tree = ttk.Treeview(tree_box, show="tree", selectmode="browse", style="Tool.Treeview")
        tree_scroll = ttk.Scrollbar(tree_box, command=self.tool_tree.yview, style="Slim.Vertical.TScrollbar")
        self.tool_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.tool_tree.pack(side="left", fill="both", expand=True)
        self.tool_tree.tag_configure("error", foreground=DANGER, background=DANGER_SOFT)
        self.tool_tree.tag_configure("group", font=f.body_bold, foreground=PRIMARY)
        self.tool_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())

        detail_card = Card(right)
        detail_card.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        card_header(detail_card, f, "입력·결과 원문", "선택한 도구에 전달된 입력과 돌려받은 결과(JSON)")
        self.detail = tk.Text(detail_card, wrap="none", font=f.mono, height=8, state="disabled", bg=SUBTLE,
                              fg=TEXT, relief="flat", padx=12, pady=8)
        self.detail.pack(fill="both", expand=True, padx=18, pady=(0, 16))

    def _configure_chat_tags(self):
        f = self.fonts
        c = self.chat
        c.tag_configure("who_user", foreground=MUTED, font=f.small_bold, justify="right", spacing1=10)
        c.tag_configure("who_agent", foreground=MUTED, font=f.small_bold, spacing1=10)
        c.tag_configure("user", background=USER_BUBBLE, lmargin1=160, lmargin2=160, rmargin=4,
                        spacing1=6, spacing3=6)
        c.tag_configure("agent", background=SURFACE, lmargin1=4, lmargin2=4, rmargin=90, spacing1=6, spacing3=6)
        c.tag_configure("bold", font=f.chat_bold)
        c.tag_configure("meta", foreground=MUTED, font=f.small, spacing1=6)
        c.tag_configure("waiting", foreground=PRIMARY, font=f.small_bold, spacing1=8)
        c.tag_configure("error", foreground=DANGER, font=f.body_bold, spacing1=8)
        c.tag_configure("note", foreground=MUTED, font=f.small, lmargin1=16, rmargin=90)
        for status, (accent, tint) in STATUS_COLORS.items():
            c.tag_configure(f"badge:{status}", background=accent, foreground="#FFFFFF", font=f.verdict)
            c.tag_configure(f"card:{status}", background=tint, lmargin1=16, lmargin2=16, rmargin=90,
                            spacing1=4, spacing3=4)
            c.tag_configure(f"head:{status}", background=tint, foreground=accent, font=f.verdict,
                            lmargin1=16, rmargin=90, spacing1=12, spacing3=6)

    def _paint_limit(self):
        for value, tab in self._limit_tabs.items():
            active = value == self.limit_var.get()
            tab.configure(bg=SURFACE if active else BORDER, fg=TEXT if active else MUTED)

    def _paint_fail(self):
        on = bool(self.fail_var.get())
        self.fail_toggle.configure(text=("● 입고 조회 실패 모드 켜짐" if on else "○ 입고 조회 실패 모드"),
                                   bg=DANGER_SOFT if on else SURFACE, fg=DANGER if on else MUTED,
                                   highlightbackground="#FECACA" if on else BORDER)

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
        self._append_message("나", text, "user")
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
        self._append_meta("새 대화를 시작합니다.", "meta")

    def _apply_fail_mode(self):
        self._paint_fail()
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
        self._append_message("에이전트", result.text or "(답변 없음)", "agent")
        verdict = build_verdict(result.tool_calls, getattr(self.agent, "last_bom", None))
        if verdict:
            self._render_verdict(verdict)
        if result.limit_reached:
            self._append_meta("※ 도구 실행 한도에 도달해 일부 조회를 실행하지 않았습니다", "note")
            group = self._groups.get(n)
            if group:
                self.tool_tree.item(group["id"], text=self.tool_tree.item(group["id"], "text") + " (한도 도달)")
        self._set_busy(False)

    def _on_turn_error(self, message: str) -> None:
        self._clear_waiting()
        self._append_meta(f"⚠ {message}", "error")
        self._set_busy(False)

    def _render_verdict(self, verdict: Verdict) -> None:
        s = verdict.status
        self._write([(f" {s} ", f"badge:{s}"), ("  자재 기준 판정\n", f"head:{s}")])
        for r in verdict.rows:
            line = (f"{r.part_name} ({r.part_code})   필요 {r.required} · 재고 {r.current_stock} · "
                    f"반영 입고 {r.inbound_before_start} · 현재부족 {r.shortage_now} · 시작기준부족 {r.shortage_at_start}")
            if r.inbound_error:
                line += " · 입고 조회 실패"
            self._write([(line + "\n", f"card:{s}")])
        self._write([("※ 자재 기준 판단 · 설비·인력은 확인하지 않음\n", f"card:{s}")])

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
        self.entry.configure(state="disabled" if busy else "normal")
        self.send_button.set_enabled(not busy)
        if busy:
            self._append_meta("● Claude가 도구를 골라 확인하고 있습니다…", "waiting")
        else:
            self.entry.focus_set()

    def _clear_waiting(self) -> None:
        ranges = self.chat.tag_ranges("waiting")
        if ranges:
            self.chat.configure(state="normal")
            self.chat.delete(ranges[0], ranges[-1])
            self.chat.configure(state="disabled")

    def _append_message(self, who: str, text: str, kind: str) -> None:
        """말풍선 하나. **굵게**는 굵은 글씨로 바꾸고 별표는 지운다."""
        parts = [(f"{who}\n", f"who_{kind}")]
        pos = 0
        for m in BOLD_PATTERN.finditer(text):
            parts.append((text[pos:m.start()], kind))
            parts.append((m.group(1), (kind, "bold")))
            pos = m.end()
        parts.append((text[pos:].rstrip() + "\n", kind))
        self._write(parts)

    def _append_meta(self, text: str, tag: str) -> None:
        self._write([(text + "\n", tag)])

    def _write(self, parts) -> None:
        self.chat.configure(state="normal")
        for text, tags in parts:
            if text:
                self.chat.insert("end", text, tags)
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
    root.configure(bg=BG)
    root.geometry("1400x880")
    root.minsize(1100, 700)
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
