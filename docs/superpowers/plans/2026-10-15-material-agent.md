# 자재대응 Agent 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude API tool use로 자재 기준 생산 가능 여부를 답하는 에이전트와, 대화·도구 실행 기록·판정 카드를 보여주는 tkinter 데모 앱을 만든다.

**Architecture:** `material_agent/` 패키지에 데이터 로딩(`data.py`)·설정(`config.py`)·도구 4개(`tools.py`)·tool use 루프(`agent.py`)·판정 카드 계산(`verdict.py`)을 두고, `app.py`(tkinter)는 이 패키지만 호출한다. 루프는 수동 tool use 루프로 직접 작성한다(발화당 실행 한도, 실행 즉시 화면 표시, 입고 조회 실패 주입이 필요하므로).

**Tech Stack:** Python 3.12, `anthropic` SDK, tkinter/ttk, pytest

**Spec:** `docs/superpowers/specs/2026-10-15-material-agent-design.md`

## Global Constraints

- 모델 `claude-opus-5-5`, 호출: `client.beta.messages.create(model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default", output_config={"effort": "medium"}, system=..., tools=..., tool_choice=..., messages=...)` — 직전 프로젝트(`01_인수인계_보고서/handover/llm.py`)와 같은 형태. `thinking` 파라미터는 보내지 않음(Opus 5.5는 끌 수 없음, 기본 adaptive)
- `tool_choice`는 `{"type": "auto"}` 또는 `{"type": "none"}`만 사용 (`any`/`tool`은 Opus 5.5에서 400)
- 도구 정의는 `strict: True` + `additionalProperties: False` + `required`
- 응답의 `response.content` 전체(thinking 블록 포함)를 그대로 `messages`에 덧붙인다. 이전 메시지를 수정·삭제하지 않는다(단, 실패한 발화 전체를 되돌리는 것은 허용 — Task 3)
- `stop_reason == "refusal"` / `"max_tokens"`는 content를 읽기 전에 처리
- API 키: 환경변수 `ANTHROPIC_API_KEY`, 없으면 프로젝트 폴더 `.env`의 `ANTHROPIC_API_KEY=...`
- CSV는 `encoding="utf-8-sig"`, 시각은 `+09:00` aware `datetime`으로 비교. 시간대 없는 입력 시각은 `+09:00`으로 간주
- 데이터 폴더 기본값: 프로젝트의 `참고/` (CSV 4개 + `실습설정.json`). 복사하지 않음
- 기준시각 `2026-10-15T09:00:00+09:00`, 실행 한도 기본 16(설정 파일), 앱에서 16/8 선택
- 입고 반영 범위: `기준시각 < 입고예정시각 <= 생산시작시각`
- 입고 조회 실패 문구(고정): `"입고 예정 조회 실패: 입고 시스템 응답 없음"`
- 한도 초과 문구(고정): `"도구 실행 한도({N}회)에 도달해 실행하지 않았습니다"`
- 판정 단계 문자열(고정): `"가능"`, `"조건부 가능"`, `"불가"`, `"확인 불가"`
- 화면·문서·주석은 한국어. 각 파일 첫 줄 docstring으로 역할 설명
- 커밋마다 `process.md` 표에 한 줄(일시 / 커밋 메시지 / 내용 + 테스트 수)을 같은 커밋에 추가
- git 원격: `https://github.com/boyoung83/A2A_Example2` (등록만, 푸시는 사용자 요청 시)

## Review Focus

1. Claude가 `"20"`(문자열)·`2.5`·`true` 같은 수량을 넘김 → `calculate_shortage`가 오류 결과로 돌려주고 프로그램은 멈추지 않음 (Task 2 테스트)
2. `production_start`가 `"2026-10-16 16:00"`(시간대 없음)·잘못된 문자열·기준시각 이전 → 시간대 없음은 +09:00 간주, 나머지는 오류 결과 (Task 2 테스트)
3. 도구 몇 개 실행 후 API 오류로 발화가 실패 → 대화 기록을 그 발화 이전으로 되돌려 다음 질문이 정상 동작 (Task 3 테스트)
4. 응답을 기다리는 중 사용자가 또 보냄 → 무시(입력 비활성), 중복 요청 없음 (Task 5 테스트)
5. `"p-100"`, `" SNS-B "`, `"센서B"`처럼 대소문자·공백이 다른 입력 → 같은 항목으로 조회 (Task 2 테스트)

---

### Task 1: 프로젝트 골격 · 데이터/설정 로딩

**Files:**
- Create: `.gitignore` (`.env`, `.superpowers/`, `__pycache__/`, `.pytest_cache/`), `.env.example` (`ANTHROPIC_API_KEY=sk-ant-...`), `requirements.txt` (`anthropic`, `pytest`), `pytest.ini` (`pythonpath = .`, `testpaths = tests`), `process.md` (직전 프로젝트와 같은 머리말+표)
- Create: `material_agent/__init__.py`, `material_agent/config.py`, `material_agent/data.py`
- Test: `tests/test_data.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `config.DATA_DIR: Path` (= 프로젝트/`참고`), `config.PROJECT_DIR: Path`
  - `config.Settings` dataclass: `base_time: datetime`, `max_tool_calls: int`
  - `config.load_settings(data_dir: Path = DATA_DIR) -> Settings`
  - `config.load_api_key(project_dir: Path = PROJECT_DIR) -> str | None`
  - `data.Dataset` dataclass: `bom: list[BomRow]`, `inventory: list[StockRow]`, `inbound: list[InboundRow]`
  - `data.BomRow(product_code, part_code, part_name, qty_per_unit: int, unit)`, `data.StockRow(part_code, part_name, current_stock: int, unit)`, `data.InboundRow(inbound_id, part_code, arrival: datetime, qty: int, unit)` (모두 frozen dataclass)
  - `data.load_dataset(data_dir: Path = DATA_DIR) -> Dataset`
  - `data.parse_time(text: str) -> datetime` (ISO, 시간대 없으면 +09:00; 잘못되면 `ValueError`)

- [ ] **Step 1: git 준비** — `git init`, `git remote add origin https://github.com/boyoung83/A2A_Example2`, 위 설정 파일 생성
- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/test_data.py
def test_load_dataset_counts():
    ds = load_dataset()
    assert len(ds.bom) == 29 and len(ds.inventory) == 20 and len(ds.inbound) == 25

def test_bom_p100_values():
    rows = [r for r in load_dataset().bom if r.product_code == "P-100"]
    assert [(r.part_code, r.qty_per_unit) for r in rows] == [("BRG-A", 2), ("SNS-B", 1)]

def test_inbound_time_is_aware():
    row = next(r for r in load_dataset().inbound if r.inbound_id == "IN-001")
    assert row.arrival == datetime(2026, 10, 16, 15, tzinfo=KST) and row.qty == 20

def test_parse_time_without_zone_assumes_kst():
    assert parse_time("2026-10-16T16:00:00") == datetime(2026, 10, 16, 16, tzinfo=KST)

def test_parse_time_invalid_raises():
    with pytest.raises(ValueError): parse_time("내일 오후")

# tests/test_config.py
def test_load_settings_defaults():
    s = load_settings()
    assert s.base_time == datetime(2026, 10, 15, 9, tzinfo=KST) and s.max_tool_calls == 16

def test_api_key_env_wins(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-file", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert load_api_key(tmp_path) == "sk-env"

def test_api_key_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-file\n", encoding="utf-8")
    assert load_api_key(tmp_path) == "sk-file"

def test_api_key_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert load_api_key(tmp_path) is None
```

`KST = timezone(timedelta(hours=9))` — `data.py`에 정의해 테스트에서 import.

- [ ] **Step 3: 실패 확인** — `pytest -q` → ImportError로 FAIL
- [ ] **Step 4: 구현** — `csv.DictReader`(utf-8-sig)로 읽어 한국어 열 이름(`제품코드`, `부품코드`, `부품명`, `제품1개당소요량`, `현재재고`, `입고번호`, `입고예정시각`, `입고예정수량`, `단위`)을 dataclass로 변환. `실습설정.json`의 `기준시각`, `최대도구실행횟수` 사용
- [ ] **Step 5: 통과 확인** — `pytest -q` → 9 passed
- [ ] **Step 6: 커밋** — process.md 행 추가 후, 설계 문서·계획 문서·`실습_2.docx`·`참고/`도 함께

```bash
git add -A && git commit -m "chore: project skeleton with data and settings loading"
```

---

### Task 2: 도구 4개

**Files:**
- Create: `material_agent/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `Dataset`, `parse_time`, `KST` (Task 1)
- Produces:
  - `INBOUND_FAIL_MESSAGE = "입고 예정 조회 실패: 입고 시스템 응답 없음"`
  - `ToolOutcome` dataclass: `ok: bool`, `payload: dict` (실패 시 `{"error": "<메시지>"}`)
  - `class ToolBox(dataset: Dataset, base_time: datetime)`, 속성 `inbound_fails: bool = False`(앱에서 바꿈)
  - `ToolBox.get_bom(product_code: str) -> dict`
    - 있음: `{"product_code": "P-100", "found": True, "parts": [{"part_code", "part_name", "qty_per_unit", "unit"}]}`
    - 없음: `{"product_code": "P-999", "found": False, "message": "등록되지 않은 제품입니다"}`
  - `ToolBox.get_inventory(part_code: str) -> dict` — 코드 또는 부품명. `{"found": True, "part_code", "part_name", "current_stock", "unit"}` / `{"found": False, "query": ..., "message": "등록되지 않은 부품입니다"}`
  - `ToolBox.get_inbound_schedule(part_code: str, production_start: str | None = None) -> dict` — `{"part_code", "schedule": [{"inbound_id", "arrival"(ISO 문자열), "qty"}]}`; `production_start`가 있으면 각 행에 `"before_start": bool`, 그리고 `"production_start"`(ISO), `"inbound_before_start_total": int` 추가. `inbound_fails`면 `InboundLookupError(INBOUND_FAIL_MESSAGE)`
  - `ToolBox.calculate_shortage(part_code: str, production_qty: int, qty_per_unit: int, current_stock: int, inbound_before_start: int) -> dict` — `{"part_code", "production_qty", "qty_per_unit", "required", "current_stock", "inbound_before_start", "shortage_now", "shortage_at_start"}`
  - `ToolBox.run(name: str, args: dict) -> ToolOutcome` — 위 메서드로 분배, 모든 예외(`ValueError`, `InboundLookupError`, 알 수 없는 도구, 잘못된 인자)를 `ok=False`로 변환. 절대 예외를 밖으로 던지지 않음
  - `TOOL_SCHEMAS: list[dict]` — 4개 도구의 `name`/`description`(한국어, 역할 + 언제 쓰는지)/`input_schema`/`strict: True`. `production_start`는 선택 인자(`required`에서 제외; strict 스키마에서 선택 인자는 `"type": ["string", "null"]`로 두고 `required`에 포함하는 방식이 필요하면 그렇게 함 — API가 400을 내면 그 형태로)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
@pytest.fixture
def tb(): return ToolBox(load_dataset(), load_settings().base_time)

def test_get_bom_found(tb):
    r = tb.get_bom("P-100")
    assert r["found"] and [p["part_code"] for p in r["parts"]] == ["BRG-A", "SNS-B"]

def test_get_bom_unknown_product(tb):
    r = tb.get_bom("P-999")
    assert r == {"product_code": "P-999", "found": False, "message": "등록되지 않은 제품입니다"}

def test_codes_normalized(tb):                       # Review Focus 5
    assert tb.get_bom(" p-100 ")["found"]
    assert tb.get_inventory(" sns-b ")["current_stock"] == 25

def test_inventory_by_part_name(tb):                 # "센서 B" → SNS-B
    assert tb.get_inventory("센서 B")["part_code"] == "SNS-B"
    assert tb.get_inventory("센서B")["current_stock"] == 25

def test_inbound_without_start_lists_rows(tb):
    r = tb.get_inbound_schedule("BRG-A")
    assert r["schedule"] == [{"inbound_id": "IN-001", "arrival": "2026-10-16T15:00:00+09:00", "qty": 20}]
    assert "inbound_before_start_total" not in r

def test_inbound_none_is_empty_list(tb):
    assert tb.get_inbound_schedule("SNS-B")["schedule"] == []

def test_inbound_9am_excludes_3pm(tb):
    r = tb.get_inbound_schedule("BRG-A", "2026-10-16T09:00:00+09:00")
    assert r["inbound_before_start_total"] == 0 and r["schedule"][0]["before_start"] is False

def test_inbound_4pm_includes_3pm(tb):
    assert tb.get_inbound_schedule("BRG-A", "2026-10-16T16:00:00+09:00")["inbound_before_start_total"] == 20

def test_inbound_same_time_included(tb):             # CABLE-K 16일 09:00 입고, 09:00 시작
    assert tb.get_inbound_schedule("CABLE-K", "2026-10-16T09:00:00+09:00")["inbound_before_start_total"] == 40

def test_inbound_start_without_zone(tb):             # Review Focus 2
    assert tb.get_inbound_schedule("BRG-A", "2026-10-16T16:00:00")["inbound_before_start_total"] == 20

def test_inbound_bad_or_past_start_is_error(tb):     # Review Focus 2
    assert not tb.run("get_inbound_schedule", {"part_code": "BRG-A", "production_start": "내일 오후"}).ok
    assert not tb.run("get_inbound_schedule", {"part_code": "BRG-A", "production_start": "2026-10-14T09:00:00+09:00"}).ok

def test_inbound_fail_mode(tb):
    tb.inbound_fails = True
    out = tb.run("get_inbound_schedule", {"part_code": "BRG-A"})
    assert out == ToolOutcome(False, {"error": "입고 예정 조회 실패: 입고 시스템 응답 없음"})

def test_shortage_formula(tb):
    r = tb.calculate_shortage("BRG-A", 20, 2, 30, 0)
    assert (r["required"], r["shortage_now"], r["shortage_at_start"]) == (40, 10, 10)
    r = tb.calculate_shortage("BRG-A", 30, 2, 30, 20)
    assert (r["required"], r["shortage_now"], r["shortage_at_start"]) == (60, 30, 10)

@pytest.mark.parametrize("qty", [0, -5, 2.5, "20", True])   # Review Focus 1
def test_shortage_rejects_bad_qty(tb, qty):
    out = tb.run("calculate_shortage", {"part_code": "BRG-A", "production_qty": qty,
                                        "qty_per_unit": 2, "current_stock": 30, "inbound_before_start": 0})
    assert not out.ok and "양의 정수" in out.payload["error"]

def test_run_unknown_tool(tb):
    assert not tb.run("delete_stock", {}).ok

def test_schemas_are_strict():
    assert {s["name"] for s in TOOL_SCHEMAS} == {"get_bom", "get_inventory", "get_inbound_schedule", "calculate_shortage"}
    assert all(s["strict"] and s["input_schema"]["additionalProperties"] is False for s in TOOL_SCHEMAS)
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_tools.py -q` → FAIL (ImportError)
- [ ] **Step 3: 구현** — 코드 정규화는 `strip().upper()`, 부품명 비교는 공백 제거 후 일치. 수량 검증: `type(x) is int`이고 생산수량·소요량은 > 0, 재고·입고는 >= 0. 오류 메시지에 "생산 수량은 양의 정수여야 합니다" 포함
- [ ] **Step 4: 통과 확인** — `pytest -q` → 전체 통과
- [ ] **Step 5: 커밋** — `feat: add four agent tools` (process.md 행 포함)

---

### Task 3: tool use 루프 (에이전트)

**Files:**
- Create: `material_agent/prompts.py`, `material_agent/agent.py`
- Test: `tests/test_agent.py`, `tests/conftest.py` (가짜 클라이언트)

**Interfaces:**
- Consumes: `ToolBox`, `TOOL_SCHEMAS`, `ToolOutcome` (Task 2)
- Produces:
  - `prompts.build_system_prompt(base_time: datetime) -> str`
  - `agent.MODEL = "claude-opus-5-5"`
  - `agent.LIMIT_MESSAGE = "도구 실행 한도({limit}회)에 도달해 실행하지 않았습니다"`
  - `agent.ToolCall` dataclass: `name: str`, `args: dict`, `ok: bool`, `payload: dict`
  - `agent.TurnResult` dataclass: `text: str`, `tool_calls: list[ToolCall]`, `limit_reached: bool`
  - `agent.AgentError(Exception)` — 사용자에게 보여줄 한국어 메시지
  - `class MaterialAgent(client, toolbox: ToolBox, base_time: datetime)` — 속성 `messages: list`, `last_bom: list[dict] | None`(가장 최근 성공한 `get_bom`의 `parts`, 대화 전체 기준)
  - `MaterialAgent.ask(user_text: str, max_tool_calls: int, on_tool_call: Callable[[ToolCall], None] | None = None) -> TurnResult`
  - `MaterialAgent.reset() -> None` — `messages`, `last_bom` 초기화

**시스템 프롬프트에 들어갈 원칙** (문장은 빌더가 작성): 기준시각과 "내일 = 2026-10-16"; 시각은 ISO(+09:00)로 바꿔 도구에 전달; 판단에 필요한 생산 수량·날짜/시작 시각이 없으면 임의로 정하지 말고 질문(현재 재고만으로 충분하면 시작 시각 없이 답해도 됨); 수량이 양의 정수가 아니면 다시 요청; 부족량 수치는 반드시 `calculate_shortage` 결과만 사용하고 판단할 때마다 **제품의 모든 부품**을 계산; 현재 재고 부족 부품은 `get_inbound_schedule`에 `production_start`를 넣어 조회; 재고로 충분한 부품은 입고 조회 생략 가능; 입고 반영 시 "예정대로 도착한다면" 조건부 표현, 입고 완료·일정 변경처럼 말하지 않음; 조회 오류는 "입고 예정 없음"과 구분해 확인하지 못한 내용을 밝힘; 등록되지 않은 제품은 제품코드 확인 요청; 자재 기준 판단이며 설비·인력은 미확인임을 밝힘; 조건이 바뀐 후속 질문은 새로 계산; 단순 재고 질문은 그 부품만 조회.

**루프 알고리즘:**

```
start = len(messages); messages.append(user)
used = 0; limit_reached = False; force_final = False
for _ in range(max_tool_calls + 5):                  # 안전장치
    resp = client.beta.messages.create(..., tool_choice={"type": "none"} if force_final else {"type": "auto"})
    refusal → AgentError("Claude가 요청을 거절했습니다"); max_tokens → AgentError("응답이 잘렸습니다")
    messages.append({"role": "assistant", "content": resp.content})
    if resp.stop_reason != "tool_use": return TurnResult(텍스트 블록 이어붙임, calls, limit_reached)
    results = []
    for block in tool_use 블록:
        if used < max_tool_calls: outcome = toolbox.run(...); used += 1; 기록·on_tool_call 호출; get_bom 성공이면 last_bom 갱신
        else: limit_reached = True; is_error 결과(LIMIT_MESSAGE)
        results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(payload, ensure_ascii=False), "is_error": not ok})
    messages.append({"role": "user", "content": results})     # 결과는 한 메시지로 모아서
    if limit_reached: force_final = True
어떤 예외(AgentError, anthropic 오류)든: del messages[start:] 후 AgentError로 다시 던짐
```

anthropic 예외 → `AgentError` 문구: `AuthenticationError` "API 키가 올바르지 않습니다", `APIConnectionError` "API 서버에 연결하지 못했습니다", `RateLimitError` "요청이 많아 잠시 후 다시 시도해주세요", `APIStatusError` "API 오류 ({status})".

- [ ] **Step 1: 가짜 클라이언트 작성 (`tests/conftest.py`)** — `FakeClient(responses: list)`; `client.beta.messages.create(**kw)`가 호출 인자를 `calls`에 저장하고 미리 준비한 응답을 차례로 반환(예외 객체면 raise). 응답은 `SimpleNamespace(stop_reason=..., content=[SimpleNamespace(type="text", text=...) | SimpleNamespace(type="tool_use", id=..., name=..., input=...)])`. 헬퍼 `text_reply(text)`, `tool_reply(*(name, input))`
- [ ] **Step 2: 실패하는 테스트 작성**

```python
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
    agent, client = agent_with(tool_reply(("get_inventory", {"part_code": "BRG-A"}), ("get_inventory", {"part_code": "SNS-B"})), text_reply("끝"))
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
    agent, client = agent_with(tool_reply(("get_inbound_schedule", {"part_code": "BRG-A", "production_start": None})), text_reply("확인 불가"))
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
    agent.ask("q1", 16); agent.ask("오전 9시야", 16)
    assert [p["part_code"] for p in agent.last_bom] == ["BRG-A", "SNS-B"]

def test_refusal_raises_and_rolls_back(agent_with):        # Review Focus 3
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), SimpleNamespace(stop_reason="refusal", content=[]))
    with pytest.raises(AgentError, match="거절"): agent.ask("q", 16)
    assert agent.messages == []

def test_api_error_rolls_back_then_next_works(agent_with): # Review Focus 3
    agent, _ = agent_with(tool_reply(("get_bom", {"product_code": "P-100"})), anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), text_reply("ok"))
    with pytest.raises(AgentError, match="연결"): agent.ask("q", 16)
    assert agent.messages == [] and agent.ask("다시", 16).text == "ok"

def test_reset_clears(agent_with):
    agent, _ = agent_with(text_reply("a"))
    agent.ask("q", 16); agent.reset()
    assert agent.messages == [] and agent.last_bom is None
```

- [ ] **Step 3: 실패 확인** — `pytest tests/test_agent.py -q` → FAIL
- [ ] **Step 4: 구현** — 위 알고리즘. 텍스트는 `type == "text"` 블록만 이어붙임(thinking 블록은 화면에 쓰지 않지만 `messages`에는 그대로 둠)
- [ ] **Step 5: 통과 확인** — `pytest -q` → 전체 통과
- [ ] **Step 6: 커밋** — `feat: add tool-use agent loop with per-utterance limit`

---

### Task 4: 판정 카드 계산

**Files:**
- Create: `material_agent/verdict.py`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Consumes: `ToolCall` (Task 3), `last_bom` 형식(Task 2 `get_bom`의 `parts`)
- Produces:
  - `PartRow` dataclass: `part_code`, `part_name`, `required`, `current_stock`, `inbound_before_start`, `shortage_now`, `shortage_at_start`, `inbound_error: bool`
  - `Verdict` dataclass: `status: str`, `rows: list[PartRow]`
  - `build_verdict(turn_calls: list[ToolCall], bom_parts: list[dict] | None) -> Verdict | None`

**규칙** (설계 문서 "Claude가 정한 것"):
- `bom_parts`가 없거나, 이번 발화에서 `bom_parts`의 **모든 부품**에 성공한 `calculate_shortage`가 없으면 `None` (부품별로 마지막 계산 사용)
- 현재부족 > 0인 각 부품에 대해, 이번 발화에 그 부품의 `get_inbound_schedule` 호출이 (`production_start` 있는 성공) 또는 (실패) 중 하나라도 없으면 `None`
- `inbound_error` = 이번 발화에서 그 부품의 입고 조회가 실패했는지
- 단계: 입고 조회가 성공한 부품 중 시작시각기준부족 > 0 → `"불가"`; 아니고 `inbound_error`인 부족 부품 있음 → `"확인 불가"`; 아니고 현재부족 > 0 부품 있음 → `"조건부 가능"`; 아니면 `"가능"`

- [ ] **Step 1: 실패하는 테스트 작성** — `calc(part, qty, per, stock, inbound)`/`inb(part, start, ok=True)` 헬퍼로 `ToolCall` 생성 (`calc`의 payload는 `ToolBox.calculate_shortage` 실제 결과 사용)

```python
P100 = [{"part_code": "BRG-A", "part_name": "베어링 A", "qty_per_unit": 2, "unit": "개"},
        {"part_code": "SNS-B", "part_name": "센서 B", "qty_per_unit": 1, "unit": "개"}]
NINE, FOUR = "2026-10-16T09:00:00+09:00", "2026-10-16T16:00:00+09:00"

def test_enough_stock_is_possible():          # 질문 2: 10개
    v = build_verdict([calc("BRG-A", 10, 2, 30, 0), calc("SNS-B", 10, 1, 25, 0)], P100)
    assert v.status == "가능" and [r.shortage_now for r in v.rows] == [0, 0]

def test_9am_is_not_possible():                # 대화 2 / 질문 3
    v = build_verdict([inb("BRG-A", NINE), calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)], P100)
    assert v.status == "불가" and v.rows[0].shortage_at_start == 10

def test_4pm_is_conditional():                 # 질문 4·5(20개), 대화 3
    v = build_verdict([inb("BRG-A", FOUR), calc("BRG-A", 20, 2, 30, 20), calc("SNS-B", 20, 1, 25, 0)], P100)
    assert v.status == "조건부 가능"

def test_30_units_not_possible():              # 질문 5 이어서
    v = build_verdict([inb("BRG-A", FOUR), inb("SNS-B", FOUR), calc("BRG-A", 30, 2, 30, 20), calc("SNS-B", 30, 1, 25, 0)], P100)
    assert v.status == "불가" and [r.shortage_at_start for r in v.rows] == [10, 5]

def test_inbound_failure_is_unconfirmed():     # 질문 9 / 대화 5
    v = build_verdict([inb("BRG-A", FOUR, ok=False), calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)], P100)
    assert v.status == "확인 불가" and v.rows[0].inbound_error

def test_no_card_when_asking_start_time():     # 시작 시각 없는 입고 조회만 있음
    assert build_verdict([inb("BRG-A", None), calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)], P100) is None

def test_no_card_when_part_missing():
    assert build_verdict([calc("BRG-A", 10, 2, 30, 0)], P100) is None

def test_no_card_without_bom():
    assert build_verdict([calc("SNS-B", 10, 1, 25, 0)], None) is None

def test_uses_last_calculation_per_part():
    v = build_verdict([calc("BRG-A", 10, 2, 30, 0), calc("SNS-B", 10, 1, 25, 0),
                       inb("BRG-A", FOUR), calc("BRG-A", 30, 2, 30, 20)], P100)
    assert v.rows[0].required == 60
```

(마지막 테스트는 SNS-B 10개·BRG-A 30개가 섞인 인위적 경우 — 규칙 확인용)

- [ ] **Step 2: 실패 확인** — `pytest tests/test_verdict.py -q` → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `pytest -q`
- [ ] **Step 5: 커밋** — `feat: compute verdict card from tool results`

---

### Task 5: tkinter 앱

**Files:**
- Create: `app.py`
- Test: `tests/test_app.py` (conftest에 세션당 Tk 루트 하나 공유하는 fixture `tk_root` — Windows에서 Tk 반복 생성 시 초기화 실패하는 문제, 직전 프로젝트 교훈. Tk를 만들 수 없으면 skip)

**Interfaces:**
- Consumes: `MaterialAgent`, `TurnResult`, `ToolCall`, `AgentError`, `build_verdict`, `Settings`, `load_settings`, `load_api_key`, `load_dataset`, `ToolBox`
- Produces:
  - `class MaterialApp(ttk.Frame)`: `__init__(self, master, agent: MaterialAgent | None, settings: Settings, startup_error: str | None = None)`
  - 속성: `limit_var: tk.StringVar`("16"/"8"), `fail_var: tk.BooleanVar`, `entry: ttk.Entry`, `chat: tk.Text`, `tool_tree: ttk.Treeview`, `detail: tk.Text`, `busy: bool`, `turn_no: int`
  - `send(self) -> None` — 입력칸 텍스트로 백그라운드 스레드에서 `agent.ask` 실행. `busy`이거나 빈 입력이면 아무것도 안 함
  - `new_conversation(self) -> None`
  - `_on_tool_call(self, turn_no: int, call: ToolCall) -> None`, `_on_turn_done(self, turn_no: int, result: TurnResult) -> None`, `_on_turn_error(self, message: str) -> None` — UI 스레드에서 호출(스레드는 `queue.Queue`에 넣고 `after(100, ...)`로 꺼냄)
  - `main() -> None` — `참고/`에서 설정·데이터, `.env`에서 키를 읽어 앱 실행(최대화). 키가 없으면 `agent=None`, `startup_error=".env에 ANTHROPIC_API_KEY가 없습니다"`

**화면 (설계 문서 시안 A):**
- 상단 바: `[새 대화]`, `실행 한도` Combobox(16, 8 — 기본은 `settings.max_tool_calls`), `입고 조회 실패 모드` Checkbutton(바뀌면 `agent.toolbox.inbound_fails`에 반영)
- 왼쪽: `chat` Text(태그로 사용자/에이전트/오류/판정 카드 색 구분, 읽기 전용), 아래 `entry` + `[보내기]`, Enter로 보내기
- 오른쪽 위: `tool_tree` — 발화마다 부모 행 `"질문 {n} · 도구 {k}/{N}회"`, 자식 행 `"{i}. {name}({인자}) → {요약}"`, 실패 행은 `error` 태그(빨간색). 오른쪽 아래 `detail` Text: 선택한 행의 결과 원문 JSON(`json.dumps(indent=2, ensure_ascii=False)`)
- 요약 문구(빌더 재량, 예): get_bom → `"BRG-A×2, SNS-B×1"`/`"등록되지 않은 제품"`, get_inventory → `"25개"`, get_inbound_schedule → `"10/16 15:00 20개"`/`"없음"`/`"시작 전 합계 20개"`, calculate_shortage → `"필요 40 · 현재부족 10 · 시작기준부족 0"`, 오류 → `"오류: {메시지}"`
- 판정 카드: `_on_turn_done`에서 `build_verdict(result.tool_calls, agent.last_bom)`가 있으면 답변 아래에 단계(색: 가능=초록, 조건부 가능=주황, 불가=빨강, 확인 불가=회색) + 부품별 `필요/재고/반영 입고/현재부족/시작기준부족` 표, 끝에 `"※ 자재 기준 판단 · 설비·인력 미확인"`. 한도 도달 시 `"※ 도구 실행 한도 도달"` 한 줄 추가
- 대기 중: `chat`에 `"확인 중…"` 표시, `entry`/`보내기` 비활성. 끝나면 지우고 활성
- `new_conversation`: `agent.reset()`, chat·tool_tree·detail 비우고 `turn_no = 0`
- `agent is None`이면 `startup_error`를 chat에 표시하고 보내기 비활성

- [ ] **Step 1: 실패하는 테스트 작성** (가짜 에이전트: `ask`가 정해진 `TurnResult` 반환, `reset` 호출 기록, `toolbox` 속성, `last_bom`)

```python
def test_turn_renders_tool_rows_and_verdict(app):
    app._on_tool_call(1, call("get_bom", {"product_code": "P-100"}, True, {"found": True, "parts": P100}))
    app._on_turn_done(1, TurnResult("생산 불가입니다", [... 9시 시나리오 calls ...], False))
    parent = app.tool_tree.get_children()[0]
    assert app.tool_tree.item(parent, "text").startswith("질문 1 · 도구")
    assert "생산 불가입니다" in app.chat.get("1.0", "end") and "불가" in app.chat.get("1.0", "end")

def test_error_row_tagged(app):
    app._on_tool_call(1, call("get_inbound_schedule", {"part_code": "BRG-A"}, False, {"error": INBOUND_FAIL_MESSAGE}))
    row = app.tool_tree.get_children(app.tool_tree.get_children()[0])[0]
    assert "error" in app.tool_tree.item(row, "tags")

def test_selecting_row_shows_json(app):
    ...  # selection_set + 이벤트 처리 후 detail에 '"current_stock": 25'

def test_send_ignored_while_busy(app):            # Review Focus 4
    app.busy = True; app.entry.insert(0, "q"); app.send()
    assert app.agent.asked == []

def test_new_conversation_clears(app):
    app._on_tool_call(1, call(...)); app.new_conversation()
    assert app.tool_tree.get_children() == () and app.agent.reset_called and app.turn_no == 0

def test_fail_switch_sets_toolbox(app):
    app.fail_var.set(True); app.update()
    assert app.agent.toolbox.inbound_fails is True

def test_no_api_key_disables_send(tk_root):
    a = MaterialApp(tk_root, None, load_settings(), ".env에 ANTHROPIC_API_KEY가 없습니다")
    assert "ANTHROPIC_API_KEY" in a.chat.get("1.0", "end")
```

- [ ] **Step 2: 실패 확인** — `pytest tests/test_app.py -q` → FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `pytest -q` (8회 반복 실행해도 skip/실패 없음: `for i in 1..8: pytest -q tests/test_app.py`)
- [ ] **Step 5: 실제 실행 확인** — `python app.py`로 띄워 대화 1(센서 B)·대화 2(9시)를 실제 API로 한 번 실행, 화면 확인
- [ ] **Step 6: 커밋** — `feat: add tkinter demo app`

---

### Task 6: 실제 API 시나리오 검증 · README

**Files:**
- Create: `scripts/run_scenarios.py`, `docs/검증/시나리오_검증.md`, `README.md`, `docs/images/*.png`

**Interfaces:**
- Consumes: Task 1~4 공개 함수 (앱 없이 `MaterialAgent` 직접 사용)

**시나리오** (각 항목 새 대화, `이어서`만 같은 대화; 한도 16):
- 질문 모음 1~8 (질문 9는 아래 대화 5와 동일 내용이라 대화 5로 검증)
- 대화 1→2 (한 대화: "센서 B 재고만 알려줘." → "내일 P-100을 20개 만들 수 있을까? 부족한 부품은 입고 예정까지 확인해줘." → 시작 시각을 물으면 "오전 9시야.")
- 대화 3, 4 (새 대화)
- 대화 5: `inbound_fails=True`로 "내일 오후 4시에 P-100을 20개 생산할 수 있어?"
- 한도 8로 대화 2를 한 번 더 (8회로도 끝나는지)

- [ ] **Step 1: `scripts/run_scenarios.py` 작성** — 위 시나리오를 실행해 발화별로 `질문 / 실행한 도구(이름·인자·요약) / 답변 / 판정 카드 단계`를 Markdown으로 `docs/검증/실행결과_<YYYYMMDD_HHMM>.md`에 저장. 키가 없으면 안내 후 종료
- [ ] **Step 2: 실행** — `python scripts/run_scenarios.py` (실제 API 호출 비용 발생, 약 20 발화)
- [ ] **Step 3: 대조표 작성** — `docs/검증/시나리오_검증.md`: 시나리오마다 기대 결과(질문_모음의 →, 실습_2.docx 대화 예시)와 실제 결과, 통과/불통과, 불통과면 원인. 불통과가 있으면 프롬프트·도구 설명을 고치고(테스트 추가 후) 해당 시나리오만 다시 실행
- [ ] **Step 4: README 작성** — 목적, 설치(`pip install -r requirements.txt`, `.env`), 실행(`python app.py`), 화면 설명(상단 바·대화·도구 기록·판정 카드), 도구 4개 표, 시연 순서(대화 1~5), 검증 결과 링크, 테스트 실행(`pytest -q`)
- [ ] **Step 5: 스크린샷** — 앱에서 대화 2(9시) 결과 화면과 입고 조회 실패 모드 결과 화면을 캡처해 `docs/images/`에 저장, README에 삽입
- [ ] **Step 6: 커밋** — `docs: scenario verification and README` (process.md 행 포함)
