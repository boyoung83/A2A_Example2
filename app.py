"""자재대응 Agent 데모 앱 (tkinter).

디자인 방향 (frontend-design 스킬로 정함): 생산 현장의 안돈(Andon) 신호등.
- 판정은 라인 경고등처럼 빨강·주황·초록 중 한 칸만 켠 카드로 보여준다 (화면에서 유일하게 강조하는 요소).
- 나머지는 강철 헤더, 작업장 바닥색 배경으로 조용하게. 대화창은 카카오톡형 (사용자 요청).
- 오른쪽 도구 기록은 실제 실행 순서이므로 번호(①②③)를 붙인 순서 목록으로 보여준다.
"""
import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont
from tkinter import ttk

from material_agent.agent import MODEL, AgentError, MaterialAgent, ToolCall, TurnResult
from material_agent.config import Settings, load_api_key, load_settings
from material_agent.data import load_dataset
from material_agent.tools import ToolBox
from material_agent.verdict import Verdict, build_verdict

# 색 — 강철 · 작업장 바닥 · 안돈 신호 · 카톡 대화창
STEEL = "#2B3440"
STEEL_SOFT = "#3A4553"
FLOOR = "#E9ECEF"
SURFACE = "#FFFFFF"
RULE = "#D5DAE0"
TEXT = "#1F2730"
MUTED = "#5F6B78"
FAINT = "#98A2AE"
ANDON = {"red": "#D63A2F", "amber": "#E8A317", "green": "#1E9E5A"}
LAMP_OFF = "#4A5563"
STATUS_STYLE = {  # 판정: (켜는 등, 글자색)
    "가능": ("green", ANDON["green"]),
    "조건부 가능": ("amber", "#B9790A"),
    "불가": ("red", ANDON["red"]),
    "확인 불가": (None, "#6B7785"),
}
CHAT_BG = "#B2C7DA"       # 카카오톡 대화방 배경
USER_BUBBLE = "#FEE500"   # 내 말풍선
AGENT_BUBBLE = "#FFFFFF"  # 상대 말풍선
CHAT_MUTED = "#4B5563"
BUBBLE_WRAP = 460
LIMIT_CHOICES = ("16", "8")
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def color_distance(a: str, b: str) -> float:
    """두 #RRGGBB 색의 RGB 거리 (스크롤바가 배경과 얼마나 가까운지 확인용)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return sum((x - y) ** 2 for x, y in zip(ca, cb)) ** 0.5


class Fonts:
    def __init__(self, root: tk.Misc):
        families = set(tkfont.families(root))
        base = next((f for f in ("Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "Noto Sans KR")
                     if f in families), "TkDefaultFont")
        # 수량·판정·도구 이름: 산업 표지판 계열 Bahnschrift (Windows 기본 글꼴), 없으면 본문 글꼴
        sign = "Bahnschrift" if "Bahnschrift" in families else base
        mono = next((f for f in ("Consolas", "Menlo") if f in families), "TkFixedFont")
        self.brand = (base, 14, "bold")
        self.title = (base, 11, "bold")
        self.body = (base, 10)
        self.body_bold = (base, 10, "bold")
        self.chat = (base, 11)
        self.chat_bold = (base, 11, "bold")
        self.small = (base, 9)
        self.small_bold = (base, 9, "bold")
        self.verdict = (base, 20, "bold")
        self.figure = (sign, 13, "bold")
        self.figure_small = (sign, 10)
        self.sign = (sign, 10, "bold")
        self.mono = (mono, 9)


class Button(tk.Label):
    """평평한 버튼 (hover·비활성 상태 포함)."""

    STYLES = {
        "primary": (STEEL, "#FFFFFF", STEEL_SOFT, STEEL),
        "secondary": (SURFACE, TEXT, FLOOR, RULE),
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
        self.configure(state="normal" if enabled else "disabled", bg=bg if enabled else RULE,
                       fg=fg, disabledforeground=FAINT, cursor="hand2" if enabled else "arrow")


class Bubble(tk.Canvas):
    """모서리가 둥근 말풍선. 줄마다 (글자, 굵게 여부)를 받아 캔버스에 그린다."""

    RADIUS = 16

    def __init__(self, master, lines: list[tuple[str, bool]], fill: str, fonts: Fonts,
                 wrap: int = BUBBLE_WRAP, padx: int = 14, pady: int = 9, bg: str = CHAT_BG,
                 line_gap: int = 3):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self.lines = lines
        self.fill = fill
        self.text = "\n".join(t for t, _ in lines)
        self.line_gap = line_gap  # 줄(문장·목록 항목) 사이 간격. 자동 줄바꿈된 부분에는 적용되지 않음
        y, width = pady, 0
        for text, bold in lines:
            item = self.create_text(padx, y, text=text, anchor="nw", width=wrap, fill=TEXT, tags=("line",),
                                    font=fonts.chat_bold if bold else fonts.chat)
            x1, y1, x2, y2 = self.bbox(item)
            width = max(width, x2 - padx)
            y = y2 + line_gap
        w, h = width + 2 * padx, y - line_gap + pady
        self.configure(width=w, height=h)
        shape = _round_rect(self, 1, 1, w - 1, h - 1, min(self.RADIUS, h // 2), fill=fill, outline=fill)
        self.tag_lower(shape)
        self.is_rounded = True


def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class AndonCard(tk.Frame):
    """판정 카드: 왼쪽 신호등 탑(위에서부터 빨강·주황·초록)에서 판정에 해당하는 등 하나만 켠다."""

    def __init__(self, master, verdict: Verdict, fonts: Fonts):
        super().__init__(master, bg=SURFACE, highlightthickness=1, highlightbackground=RULE)
        self.status = verdict.status
        self.lit, color = STATUS_STYLE[verdict.status]

        tower = tk.Canvas(self, width=40, height=110, bg=STEEL, highlightthickness=0, bd=0)
        tower.pack(side="left", fill="y")
        self.tower = tower
        for i, name in enumerate(("red", "amber", "green")):
            on = name == self.lit
            y = 16 + i * 30
            tower.create_oval(9, y, 31, y + 22, fill=ANDON[name] if on else LAMP_OFF,
                              outline=SURFACE if on else STEEL_SOFT, width=2 if on else 1)

        body = tk.Frame(self, bg=SURFACE)
        body.pack(side="left", fill="both", padx=(16, 18), pady=(10, 12))
        head = tk.Frame(body, bg=SURFACE)
        head.pack(anchor="w")
        self.status_label = tk.Label(head, text=verdict.status, bg=SURFACE, fg=color, font=fonts.verdict)
        self.status_label.pack(side="left")
        tk.Label(head, text="자재 기준 판정", bg=SURFACE, fg=MUTED, font=fonts.small).pack(
            side="left", anchor="s", padx=(10, 0), pady=(0, 6))

        table = tk.Frame(body, bg=SURFACE)
        table.pack(anchor="w", pady=(6, 0))
        headers = ("부품", "필요", "재고", "시작 전 입고", "시작 시 부족")
        for col, text in enumerate(headers):
            tk.Label(table, text=text, bg=SURFACE, fg=FAINT, font=fonts.small).grid(
                row=0, column=col, sticky="w" if col == 0 else "e", padx=(0, 18))
        for r_i, r in enumerate(verdict.rows, start=1):
            short = r.shortage_at_start > 0 or r.inbound_error
            tk.Label(table, text=f"{r.part_name} {r.part_code}", bg=SURFACE, fg=TEXT, font=fonts.body_bold).grid(
                row=r_i, column=0, sticky="w", padx=(0, 18), pady=1)
            inbound = "조회 실패" if r.inbound_error else str(r.inbound_before_start)
            values = (r.required, r.current_stock, inbound, r.shortage_at_start)
            for c_i, value in enumerate(values, start=1):
                hot = c_i == 4 and short
                tk.Label(table, text=str(value), bg=SURFACE, fg=color if hot else TEXT,
                         font=fonts.figure if hot else fonts.figure_small).grid(
                    row=r_i, column=c_i, sticky="e", padx=(0, 18))
        tk.Label(body, text="설비와 인력은 확인하지 않았습니다.", bg=SURFACE, fg=FAINT, font=fonts.small).pack(
            anchor="w", pady=(8, 0))


def _fmt_args(args: dict, name: str = "") -> str:
    """도구 기록용 짧은 입력 표시: 시각은 '10/16 09:00 시작', 부족량 계산은 부품과 생산 수량만."""
    if name == "calculate_shortage":
        return f"{args.get('part_code')}, {args.get('production_qty')}개"
    parts = []
    for key, value in args.items():
        if value is None:
            continue
        if key == "production_start" and isinstance(value, str) and len(value) >= 16:
            value = f"{value[5:10].replace('-', '/')} {value[11:16]} 시작"
        parts.append(str(value))
    return ", ".join(parts)


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
        rows = ", ".join(f"{r['arrival'][5:10].replace('-', '/')} {r['arrival'][11:16]} {r['qty']}개"
                         for r in p.get("schedule", [])) or "입고 예정 없음"
        if "inbound_before_start_total" in p:
            rows += f" (시작 전 {p['inbound_before_start_total']}개)"
        return rows
    if call.name == "calculate_shortage":
        return f"필요 {p['required']}, 시작 시 부족 {p['shortage_at_start']}"
    return json.dumps(p, ensure_ascii=False)[:60]


class MaterialApp(tk.Frame):
    def __init__(self, master, agent: MaterialAgent | None, settings: Settings,
                 startup_error: str | None = None, runner=None):
        super().__init__(master, bg=FLOOR)
        self.agent = agent
        self.settings = settings
        self.runner = runner or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.busy = False
        self.turn_no = 0
        self._queue: queue.Queue = queue.Queue()
        self._groups: dict[int, dict] = {}
        self._details: dict[str, ToolCall] = {}
        self.user_bubbles: list[Bubble] = []
        self.agent_bubbles: list[Bubble] = []
        self.verdict_cards: list[AndonCard] = []
        self._log: list[str] = []
        self.fonts = Fonts(self)
        self._setup_style()
        self._build_header()
        self._build_controls()
        self._build_body()
        if agent is None:
            self._append_meta(f"{startup_error or '에이전트를 시작하지 못했습니다'}", "error")
            self.send_button.set_enabled(False)
            self.entry.configure(state="disabled")
        else:
            self._append_meta("생산할 제품, 수량, 시작 시각을 물어보세요. 예: 내일 오후 4시에 P-100을 20개 만들 수 있을까?",
                              "meta")
        self.after(100, self._poll)

    # ---------- 화면 구성 ----------
    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        f = self.fonts
        style.configure("Trace.Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                        bordercolor=SURFACE, rowheight=30, font=f.body, indent=14)
        style.map("Trace.Treeview", background=[("selected", FLOOR)], foreground=[("selected", TEXT)])
        # 스크롤바: Tk는 투명도를 지원하지 않으므로 막대를 배경과 거의 같은 옅은 색·좁은 폭으로 그려
        # 투명해 보이게 하고, 마우스를 올렸을 때만 진하게 한다. (흰 영역용 Slim, 하늘색 대화창용 Chat)
        for name, trough, thumb, active in (("Slim.Vertical.TScrollbar", SURFACE, "#E8EBEF", "#B9C1CA"),
                                            ("Chat.Vertical.TScrollbar", CHAT_BG, "#C4D4E3", "#8FA9C2")):
            style.layout(name, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
            style.configure(name, troughcolor=trough, background=thumb, bordercolor=trough,
                            lightcolor=thumb, darkcolor=thumb, width=6)
            style.map(name, background=[("active", active)], lightcolor=[("active", active)],
                      darkcolor=[("active", active)])

    def _build_header(self):
        f = self.fonts
        header = tk.Frame(self, bg=STEEL, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="자재대응 Agent", bg=STEEL, fg="#FFFFFF", font=f.brand).pack(side="left", padx=(24, 12))
        tk.Label(header, text="생산 가능 여부를 자재 기준으로 확인합니다", bg=STEEL, fg="#AEB7C2",
                 font=f.small).pack(side="left", pady=(4, 0))

        right = tk.Frame(header, bg=STEEL)
        right.pack(side="right", padx=24)
        dot = ANDON["green"] if self.agent else ANDON["red"]
        label = f"Claude API 연결됨 ({MODEL})" if self.agent else "API 키 없음: .env를 확인하세요"
        tk.Label(right, text="●", bg=STEEL, fg=dot, font=f.small).pack(side="left")
        self.status_label = tk.Label(right, text=f" {label}", bg=STEEL, fg="#DDE2E8", font=f.small_bold)
        self.status_label.pack(side="left")
        base = self.settings.base_time
        noon = "오전" if base.hour < 12 else "오후"
        tk.Label(right, text=f"기준 {base.month}월 {base.day}일 {noon} {base.hour % 12 or 12}:{base:%M}", bg=STEEL,
                 fg="#AEB7C2", font=f.small).pack(side="left", padx=(24, 0))

    def _build_controls(self):
        f = self.fonts
        strip = tk.Frame(self, bg=SURFACE, highlightthickness=1, highlightbackground=RULE)
        strip.pack(fill="x")
        inner = tk.Frame(strip, bg=SURFACE)
        inner.pack(fill="x", padx=24, pady=9)
        Button(inner, "새 대화", self.new_conversation, font=f.body).pack(side="left")

        tk.Label(inner, text="질문당 실행 한도", bg=SURFACE, fg=MUTED, font=f.small_bold).pack(side="left", padx=(28, 8))
        default = str(self.settings.max_tool_calls)
        self.limit_var = tk.StringVar(value=default)
        seg = tk.Frame(inner, bg=RULE, padx=2, pady=2)
        seg.pack(side="left")
        self._limit_tabs = {}
        for value in dict.fromkeys((default, *LIMIT_CHOICES)):
            tab = tk.Label(seg, text=f"{value}회", font=f.sign, padx=14, pady=3, cursor="hand2")
            tab.pack(side="left")
            tab.bind("<Button-1>", lambda e, v=value: self.limit_var.set(v))
            self._limit_tabs[value] = tab
        self.limit_var.trace_add("write", lambda *_: self._paint_limit())
        self._paint_limit()

        self.fail_var = tk.BooleanVar(value=False)
        self.fail_toggle = tk.Label(inner, font=f.body_bold, padx=12, pady=4, cursor="hand2", highlightthickness=1)
        self.fail_toggle.pack(side="left", padx=(28, 8))
        self.fail_toggle.bind("<Button-1>", lambda e: self.fail_var.set(not self.fail_var.get()))
        self.fail_var.trace_add("write", lambda *_: self._apply_fail_mode())
        self._paint_fail()
        tk.Label(inner, text="켜면 입고 예정 조회가 실패합니다", bg=SURFACE, fg=FAINT, font=f.small).pack(side="left")

    def _build_body(self):
        f = self.fonts
        body = tk.Frame(self, bg=FLOOR)
        body.pack(fill="both", expand=True, padx=24, pady=18)
        body.columnconfigure(0, weight=3, uniform="col")
        body.columnconfigure(1, weight=2, uniform="col")
        body.rowconfigure(0, weight=1)

        # 왼쪽: 카톡형 대화창 + 입력줄
        left = tk.Frame(body, bg=CHAT_BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        input_row = tk.Frame(left, bg=SURFACE)
        input_row.pack(side="bottom", fill="x")
        self.send_button = Button(input_row, "보내기", self.send, kind="primary", font=f.body_bold)
        self.send_button.pack(side="right", padx=10, pady=10)
        box = tk.Frame(input_row, bg=SURFACE, highlightthickness=1, highlightbackground=RULE, highlightcolor=STEEL)
        box.pack(side="left", fill="x", expand=True, padx=(12, 0), pady=10)
        self.entry = tk.Entry(box, font=f.chat, relief="flat", bg=SURFACE, fg=TEXT, insertbackground=TEXT,
                              disabledbackground=FLOOR)
        self.entry.pack(fill="x", padx=10, pady=7)
        self.entry.bind("<Return>", lambda _e: self.send())
        # 비어 있을 때만 보이는 안내 문구 (누르면 입력칸으로 초점 이동)
        self.placeholder = tk.Label(box, text="질문을 입력하세요. 예: 내일 오후 4시에 P-100을 20개 만들 수 있을까?",
                                    bg=SURFACE, fg=FAINT, font=f.chat, cursor="xterm")
        self.placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry.bind("<KeyRelease>", lambda _e: self._update_placeholder(), add="+")
        self._update_placeholder()

        self.chat = tk.Text(left, wrap="word", font=f.chat, state="disabled", padx=14, pady=14,
                            bg=CHAT_BG, fg=TEXT, relief="flat", highlightthickness=0, cursor="arrow")
        scroll = ttk.Scrollbar(left, command=self.chat.yview, style="Chat.Vertical.TScrollbar")
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self._configure_chat_tags()

        # 오른쪽: 조회 순서(도구 실행 기록) + 선택한 단계의 원문
        right = tk.Frame(body, bg=SURFACE, highlightthickness=1, highlightbackground=RULE)
        right.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        tk.Label(right, text="조회 순서", bg=SURFACE, fg=TEXT, font=f.title).pack(anchor="w", padx=18, pady=(14, 0))
        tk.Label(right, text="Claude가 고른 도구가 실행된 순서입니다. 줄을 누르면 아래에 입력과 결과가 보입니다.",
                 bg=SURFACE, fg=MUTED, font=f.small, wraplength=420, justify="left").pack(anchor="w", padx=18,
                                                                                         pady=(2, 8))
        # 조회 순서(위)와 원문(아래)은 가운데 경계선을 끌어 높이를 조절한다
        self.right_panes = tk.PanedWindow(right, orient="vertical", bg=RULE, bd=0, sashwidth=6,
                                          sashrelief="flat", sashcursor="sb_v_double_arrow", showhandle=False)
        self.right_panes.pack(fill="both", expand=True)
        tree_box = tk.Frame(self.right_panes, bg=SURFACE, padx=0)  # 스크롤바를 오른쪽 끝에 붙여 아래 원문 칸과 맞춘다
        self.right_panes.add(tree_box, minsize=120, stretch="always", height=380)
        self.tool_tree = ttk.Treeview(tree_box, show="tree", selectmode="browse", style="Trace.Treeview")
        tree_scroll = ttk.Scrollbar(tree_box, command=self.tool_tree.yview, style="Slim.Vertical.TScrollbar")
        self.tool_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.tool_tree.pack(side="left", fill="both", expand=True)
        self.tool_tree.tag_configure("error", foreground=ANDON["red"])
        self.tool_tree.tag_configure("group", font=f.small_bold, foreground=MUTED)
        self.tool_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())

        detail_box = tk.Frame(self.right_panes, bg=SURFACE)
        self.right_panes.add(detail_box, minsize=100, stretch="always")
        self.detail = tk.Text(detail_box, wrap="none", font=f.mono, height=9, state="disabled", bg=SURFACE,
                              fg=TEXT, relief="flat", padx=18, pady=10)
        self.detail_scroll = ttk.Scrollbar(detail_box, orient="vertical", command=self.detail.yview,
                                           style="Slim.Vertical.TScrollbar")
        self.detail.configure(yscrollcommand=self.detail_scroll.set)
        self.detail_scroll.pack(side="right", fill="y")
        self.detail.pack(side="left", fill="both", expand=True)
        self._set_text(self.detail, "선택한 단계의 입력과 결과가 여기에 표시됩니다.")

    def _configure_chat_tags(self):
        """말풍선 줄 정렬과 가운데 알림 스타일. 말풍선 자체는 위젯으로 그린다."""
        f = self.fonts
        c = self.chat
        c.tag_configure("row_user", justify="right", rmargin=4, spacing1=10)
        c.tag_configure("row_agent", justify="left", lmargin1=4, spacing1=10)
        c.tag_configure("meta", justify="center", foreground=CHAT_MUTED, font=f.small, spacing1=10)
        c.tag_configure("note", justify="center", foreground=CHAT_MUTED, font=f.small, spacing1=6)
        c.tag_configure("waiting", justify="center", foreground=TEXT, font=f.small_bold, spacing1=12)
        c.tag_configure("error", justify="center", foreground=ANDON["red"], font=f.body_bold, spacing1=10)

    def _update_placeholder(self):
        if self.entry.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=11, rely=0.5, anchor="w")

    def _paint_limit(self):
        for value, tab in self._limit_tabs.items():
            active = value == self.limit_var.get()
            tab.configure(bg=STEEL if active else RULE, fg="#FFFFFF" if active else MUTED)

    def _paint_fail(self):
        on = bool(self.fail_var.get())
        self.fail_toggle.configure(text=("입고 조회 실패 모드 켜짐" if on else "입고 조회 실패 모드"),
                                   bg=ANDON["red"] if on else SURFACE, fg="#FFFFFF" if on else MUTED,
                                   highlightbackground=ANDON["red"] if on else RULE)

    # ---------- 동작 ----------
    def send(self) -> None:
        if self.busy or self.agent is None:
            return
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._update_placeholder()
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
        self._set_text(self.detail, "선택한 단계의 입력과 결과가 여기에 표시됩니다.")
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        for child in self.chat.winfo_children():
            child.destroy()
        self.user_bubbles.clear()
        self.agent_bubbles.clear()
        self.verdict_cards.clear()
        self._log.clear()
        self._append_meta("새 대화를 시작했습니다.", "meta")

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
        parent = self.tool_tree.insert("", "end", text=f"질문 {n}   도구 0/{limit}회", open=True, tags=("group",))
        self._groups[n] = {"id": parent, "limit": limit, "count": 0}

    def _on_tool_call(self, n: int, call: ToolCall) -> None:
        group = self._groups[n]
        group["count"] += 1
        k = group["count"]
        mark = CIRCLED[k - 1] if k <= len(CIRCLED) else f"({k})"
        text = f"{mark} {call.name}({_fmt_args(call.args, call.name)})  →  {summarize(call)}"
        row = self.tool_tree.insert(group["id"], "end", text=text, tags=() if call.ok else ("error",))
        self._details[row] = call
        self.tool_tree.item(group["id"], text=f"질문 {n}   도구 {k}/{group['limit']}회")
        self.tool_tree.see(row)

    def _on_turn_done(self, n: int, result: TurnResult) -> None:
        self._clear_waiting()
        self._append_message("에이전트", result.text or "(답변 없음)", "agent")
        verdict = build_verdict(result.tool_calls, getattr(self.agent, "last_bom", None),
                                getattr(self.agent, "conversation_calls", None))
        if verdict:
            self._render_verdict(verdict)
        if result.limit_reached:
            self._append_meta("도구 실행 한도에 도달해 일부 조회를 실행하지 않았습니다.", "note")
            group = self._groups.get(n)
            if group:
                self.tool_tree.item(group["id"], text=self.tool_tree.item(group["id"], "text") + "  한도 도달")
        self._set_busy(False)

    def _on_turn_error(self, message: str) -> None:
        self._clear_waiting()
        self._append_meta(message, "error")
        self._set_busy(False)

    def _render_verdict(self, verdict: Verdict) -> None:
        """안돈 판정 카드: 에이전트 말풍선 아래에 이어 붙는다 (프로필 자리만큼 들여쓰기)."""
        row = tk.Frame(self.chat, bg=CHAT_BG)
        tk.Frame(row, bg=CHAT_BG, width=48).pack(side="left")
        card = AndonCard(row, verdict, self.fonts)
        card.pack(side="left")
        self.verdict_cards.append(card)
        self._insert_row(row, "row_agent")
        lines = [f"[판정 {verdict.status}] 자재 기준 판정"]
        for r in verdict.rows:
            lines.append(f"{r.part_name}({r.part_code}) 필요 {r.required}, 재고 {r.current_stock}, "
                         f"시작 전 입고 {'조회 실패' if r.inbound_error else r.inbound_before_start}, "
                         f"시작 시 부족 {r.shortage_at_start}")
        lines.append("설비와 인력은 확인하지 않았습니다. (자재 기준 판단)")
        self._log.append("\n".join(lines))

    def _show_detail(self) -> None:
        selected = self.tool_tree.selection()
        call = self._details.get(selected[0]) if selected else None
        if call is None:
            self._set_text(self.detail, "선택한 단계의 입력과 결과가 여기에 표시됩니다.")
            return
        body = (f"{call.name} ({'성공' if call.ok else '오류'})\n\n입력\n"
                f"{json.dumps(call.args, ensure_ascii=False, indent=2)}\n\n결과\n"
                f"{json.dumps(call.payload, ensure_ascii=False, indent=2)}")
        self._set_text(self.detail, body)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.entry.configure(state="disabled" if busy else "normal")
        self.send_button.set_enabled(not busy)
        if busy:
            self._append_meta("Claude가 필요한 자료를 조회하고 있습니다…", "waiting")
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
            bubble = Bubble(row, [(text, False)], USER_BUBBLE, f)
            bubble.pack(side="left")
            self.user_bubbles.append(bubble)
            self._insert_row(row, "row_user")
            self._log.append(f"{who}: {text}")
            return
        # 빈 줄은 없애고, **로 시작하는 줄(결론)은 굵게, 별표는 지운다
        lines = [line.rstrip() for line in text.splitlines() if line.strip()] or ["(답변 없음)"]
        tk.Label(row, text="자", bg=STEEL, fg="#FFFFFF", font=f.title, width=3, pady=6).pack(
            side="left", anchor="n", padx=(0, 8))
        col = tk.Frame(row, bg=CHAT_BG)
        col.pack(side="left")
        tk.Label(col, text="자재 에이전트", bg=CHAT_BG, fg=TEXT, font=f.small).pack(anchor="w", pady=(0, 3))
        line_row = tk.Frame(col, bg=CHAT_BG)
        line_row.pack(anchor="w")
        styled = [(line.replace("**", ""), line.lstrip().startswith("**")) for line in lines]
        # 에이전트 답변은 줄마다 빈 줄 하나만큼 띄워 읽기 쉽게 한다
        blank_line = tkfont.Font(font=f.chat).metrics("linespace")
        bubble = Bubble(line_row, styled, AGENT_BUBBLE, f, line_gap=blank_line)
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
    root.title("자재대응 Agent")
    root.configure(bg=FLOOR)
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
        error = ".env에 ANTHROPIC_API_KEY가 없습니다. .env.example을 복사해 키를 넣어주세요."
    MaterialApp(root, agent, settings, error).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
