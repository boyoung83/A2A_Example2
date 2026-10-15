"""자재대응 Agent 데모 앱 (tkinter): 왼쪽 대화, 오른쪽 도구 실행 기록, 답변 아래 판정 카드.

디자인은 직전 프로젝트(교대 인수인계 앱)와 같은 체계: 어두운 상단 헤더, 흰 카드, 슬레이트 계열 글자색, 상태별 강조색.
"""
import json
import queue
import threading
from datetime import datetime
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
CHAT_BG = "#B2C7DA"       # 카카오톡 대화방 배경
USER_BUBBLE = "#FEE500"   # 내 말풍선 (카카오톡 노랑)
AGENT_BUBBLE = "#FFFFFF"  # 상대 말풍선
CHAT_MUTED = "#4B5563"
BUBBLE_WRAP = 460
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


class Bubble(tk.Canvas):
    """모서리가 둥근 말풍선. 줄마다 (글자, 굵게 여부)를 받아 캔버스에 그린다."""

    RADIUS = 16

    def __init__(self, master, lines: list[tuple[str, bool]], fill: str, fonts: "Fonts",
                 wrap: int = 460, padx: int = 14, pady: int = 9, bg: str = "#B2C7DA"):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self.lines = lines
        self.fill = fill
        self.text = "\n".join(t for t, _ in lines)
        y, width = pady, 0
        for text, bold in lines:
            item = self.create_text(padx, y, text=text, anchor="nw", width=wrap, fill=TEXT,
                                    font=fonts.chat_bold if bold else fonts.chat)
            x1, y1, x2, y2 = self.bbox(item)
            width = max(width, x2 - padx)
            y = y2 + 3
        w, h = width + 2 * padx, y - 3 + pady
        self.configure(width=w, height=h)
        shape = self._round_rect(1, 1, w - 1, h - 1, min(self.RADIUS, h // 2), fill=fill, outline=fill)
        self.tag_lower(shape)
        self.is_rounded = True

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)


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

        chat_box = tk.Frame(chat_card, bg=CHAT_BG)
        chat_box.pack(fill="both", expand=True, padx=18)
        self.chat = tk.Text(chat_box, wrap="word", font=f.chat, state="disabled", padx=12, pady=12,
                            bg=CHAT_BG, fg=TEXT, relief="flat", highlightthickness=0, cursor="arrow")
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
        """말풍선 줄 정렬과 가운데 알림 스타일. 말풍선 자체는 위젯으로 그린다."""
        f = self.fonts
        c = self.chat
        c.tag_configure("row_user", justify="right", rmargin=4, spacing1=10)
        c.tag_configure("row_agent", justify="left", lmargin1=4, spacing1=10)
        c.tag_configure("meta", justify="center", foreground=CHAT_MUTED, font=f.small, spacing1=10)
        c.tag_configure("note", justify="center", foreground=CHAT_MUTED, font=f.small, spacing1=6)
        c.tag_configure("waiting", justify="center", foreground=TEXT, font=f.small_bold, spacing1=12)
        c.tag_configure("error", justify="center", foreground=DANGER, font=f.body_bold, spacing1=10)
        self.user_bubbles: list[Bubble] = []
        self.agent_bubbles: list[Bubble] = []
        self.verdict_badges: list[tk.Label] = []
        self._log: list[str] = []

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
        for child in self.chat.winfo_children():
            child.destroy()
        self.user_bubbles.clear()
        self.agent_bubbles.clear()
        self.verdict_badges.clear()
        self._log.clear()
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
        verdict = build_verdict(result.tool_calls, getattr(self.agent, "last_bom", None),
                                getattr(self.agent, "conversation_calls", None))
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
        """판정 카드: 에이전트 말풍선에 이어 붙는 카드 메시지 (카톡의 연속 메시지처럼 프로필 생략)."""
        f = self.fonts
        s = verdict.status
        accent, tint = STATUS_COLORS[s]
        row = tk.Frame(self.chat, bg=CHAT_BG)
        tk.Frame(row, bg=CHAT_BG, width=48).pack(side="left")  # 프로필 자리만큼 들여쓰기
        card = tk.Frame(row, bg=AGENT_BUBBLE, highlightthickness=1, highlightbackground=accent)
        card.pack(side="left")
        head = tk.Frame(card, bg=tint)
        head.pack(fill="x")
        badge = tk.Label(head, text=f" {s} ", bg=accent, fg="#FFFFFF", font=f.verdict, padx=6)
        badge.pack(side="left", padx=(12, 8), pady=8)
        tk.Label(head, text="자재 기준 판정", bg=tint, fg=accent, font=f.title).pack(side="left", pady=8, padx=(0, 12))
        self.verdict_badges.append(badge)
        lines = [f"[판정 {s}] 자재 기준 판정"]
        for r in verdict.rows:
            item = tk.Frame(card, bg=AGENT_BUBBLE)
            item.pack(fill="x", padx=14, pady=(8, 0))
            short = r.shortage_at_start > 0 or r.inbound_error
            tk.Label(item, text=f"{r.part_name} ({r.part_code})", bg=AGENT_BUBBLE,
                     fg=accent if short else TEXT, font=f.body_bold).pack(anchor="w")
            detail = (f"필요 {r.required} · 재고 {r.current_stock} · 반영 입고 {r.inbound_before_start} · "
                      f"현재부족 {r.shortage_now} · 시작기준부족 {r.shortage_at_start}")
            if r.inbound_error:
                detail += " · 입고 조회 실패"
            tk.Label(item, text=detail, bg=AGENT_BUBBLE, fg=MUTED, font=f.small).pack(anchor="w")
            lines.append(f"{r.part_name}({r.part_code}) {detail}")
        tk.Label(card, text="※ 자재 기준 판단 · 설비·인력은 확인하지 않음", bg=AGENT_BUBBLE, fg=FAINT,
                 font=f.small).pack(anchor="w", padx=14, pady=(8, 10))
        lines.append("※ 자재 기준 판단 · 설비·인력은 확인하지 않음")
        self._insert_row(row, "row_agent")
        self._log.append("\n".join(lines))

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

    def transcript(self) -> str:
        """대화창에 보이는 내용을 글자로 (테스트·확인용)."""
        return "\n".join(self._log)

    def _append_message(self, who: str, text: str, kind: str) -> None:
        """카톡처럼: 내 메시지는 오른쪽 노란 말풍선, 에이전트는 왼쪽 프로필+이름+흰 말풍선, 옆에 시각."""
        f = self.fonts
        now = datetime.now()
        stamp = f"{'오전' if now.hour < 12 else '오후'} {now.hour % 12 or 12}:{now:%M}"
        row = tk.Frame(self.chat, bg=CHAT_BG)
        if kind == "user":
            tk.Label(row, text=stamp, bg=CHAT_BG, fg=CHAT_MUTED, font=f.small).pack(side="left", anchor="s", padx=6)
            bubble = Bubble(row, [(text, False)], USER_BUBBLE, f, wrap=BUBBLE_WRAP, bg=CHAT_BG)
            bubble.pack(side="left")
            self.user_bubbles.append(bubble)
            self._insert_row(row, "row_user")
            self._log.append(f"{who}: {text}")
            return
        # 빈 줄은 없애고, **로 시작하는 줄(결론)은 굵게, 별표는 지운다
        lines = [line.rstrip() for line in text.splitlines() if line.strip()] or ["(답변 없음)"]
        tk.Label(row, text="자", bg=PRIMARY, fg="#FFFFFF", font=f.title, width=3, pady=6).pack(
            side="left", anchor="n", padx=(0, 8))
        col = tk.Frame(row, bg=CHAT_BG)
        col.pack(side="left")
        tk.Label(col, text="자재 에이전트", bg=CHAT_BG, fg=TEXT, font=f.small).pack(anchor="w", pady=(0, 3))
        line_row = tk.Frame(col, bg=CHAT_BG)
        line_row.pack(anchor="w")
        styled = [(line.replace("**", ""), line.lstrip().startswith("**")) for line in lines]
        bubble = Bubble(line_row, styled, AGENT_BUBBLE, f, wrap=BUBBLE_WRAP, bg=CHAT_BG)
        bubble.pack(side="left")
        tk.Label(line_row, text=stamp, bg=CHAT_BG, fg=CHAT_MUTED, font=f.small).pack(side="left", anchor="s", padx=6)
        self.agent_bubbles.append(bubble)
        self._insert_row(row, "row_agent")
        self._log.append(f"{who}: {bubble.text}")

    def _append_meta(self, text: str, tag: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text + "\n", tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")
        if tag != "waiting":
            self._log.append(text)

    def _insert_row(self, widget: tk.Widget, tag: str) -> None:
        """말풍선 위젯 한 줄을 대화창 끝에 넣는다. 위젯 위에서도 마우스 휠로 스크롤되게 한다."""
        self._bind_wheel(widget)
        self.chat.configure(state="normal")
        start = self.chat.index("end-1c")
        self.chat.window_create("end", window=widget)
        self.chat.insert("end", "\n")
        self.chat.tag_add(tag, start, "end-1c")
        self.chat.configure(state="disabled")
        # 말풍선 크기가 정해진 뒤에 맨 아래로 (바로 see 하면 위젯 높이 계산 전이라 덜 내려간다)
        self.after_idle(lambda: self.chat.yview_moveto(1.0))
        self.after(80, lambda: self.chat.yview_moveto(1.0))

    def _bind_wheel(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", lambda e: self.chat.yview_scroll(int(-e.delta / 120), "units"))
        for child in widget.winfo_children():
            self._bind_wheel(child)

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
