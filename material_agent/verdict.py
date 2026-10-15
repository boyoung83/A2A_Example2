"""판정 카드: 이번 발화의 계산 도구 결과로 가능/조건부 가능/불가/확인 불가를 정한다.

Claude의 답변 문장에서 판정을 읽지 않고 수치로만 정한다. 판단에 필요한 조회가 끝나지 않은 발화
(예: 시작 시각을 되묻는 답변)에는 카드를 만들지 않는다.
"""
from dataclasses import dataclass

from material_agent.agent import ToolCall
from material_agent.tools import _code


@dataclass(frozen=True)
class PartRow:
    part_code: str
    part_name: str
    required: int
    current_stock: int
    inbound_before_start: int
    shortage_now: int
    shortage_at_start: int
    inbound_error: bool


@dataclass(frozen=True)
class Verdict:
    status: str
    rows: list[PartRow]


def build_verdict(turn_calls: list[ToolCall], bom_parts: list[dict] | None,
                  conversation_calls: list[ToolCall] | None = None) -> Verdict | None:
    """수량은 이번 발화의 계산 결과만 쓴다. 입고 확인 여부는 같은 대화의 앞 발화 조회도 인정한다
    (Claude가 바로 앞에서 조회한 입고 결과를 재사용하는 경우). 부품마다 마지막 입고 조회 결과를 따른다."""
    if not bom_parts:
        return None
    last_calc = {c.payload["part_code"]: c.payload
                 for c in turn_calls if c.name == "calculate_shortage" and c.ok}
    inbound_state: dict[str, str] = {}  # 부품 → "ok" | "error"
    for c in conversation_calls if conversation_calls is not None else turn_calls:
        if c.name != "get_inbound_schedule":
            continue
        part = _code(c.args.get("part_code", ""))
        if not c.ok:
            inbound_state[part] = "error"
        elif c.args.get("production_start"):
            inbound_state[part] = "ok"
    inbound_ok = {p for p, s in inbound_state.items() if s == "ok"}
    inbound_err = {p for p, s in inbound_state.items() if s == "error"}

    rows = []
    for part in bom_parts:
        calc = last_calc.get(part["part_code"])
        if calc is None:
            return None
        if calc["shortage_now"] > 0 and part["part_code"] not in inbound_ok | inbound_err:
            return None  # 부족한 부품의 시작 시각 기준 입고 확인이 없음
        rows.append(PartRow(part["part_code"], part["part_name"], calc["required"], calc["current_stock"],
                            calc["inbound_before_start"], calc["shortage_now"], calc["shortage_at_start"],
                            part["part_code"] in inbound_err))

    short = [r for r in rows if r.shortage_now > 0]
    if any(r.shortage_at_start > 0 and not r.inbound_error for r in short):
        status = "불가"
    elif any(r.inbound_error for r in short):
        status = "확인 불가"
    elif short:
        status = "조건부 가능"
    else:
        status = "가능"
    return Verdict(status, rows)
