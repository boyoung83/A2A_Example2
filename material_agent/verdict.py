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
    history = conversation_calls if conversation_calls is not None else turn_calls
    inbound_total: dict[str, int | None] = {}  # 부품 → 마지막 입고 조회의 시작 전 합계 (조회 실패면 None)
    stock: dict[str, int] = {}  # 부품 → 마지막 재고 조회 값
    for c in history:
        if c.name == "get_inbound_schedule":
            if c.ok and c.args.get("production_start"):
                inbound_total[c.payload["part_code"]] = c.payload["inbound_before_start_total"]
            elif not c.ok and c.payload.get("lookup_failed"):
                inbound_total[c.payload.get("part_code") or _code(c.args.get("part_code", ""))] = None
        elif c.name == "get_inventory" and c.ok and c.payload.get("found"):
            stock[c.payload["part_code"]] = c.payload["current_stock"]
    inbound_ok = {p for p, t in inbound_total.items() if t is not None}
    inbound_err = {p for p, t in inbound_total.items() if t is None}

    rows = []
    for part in bom_parts:
        calc = last_calc.get(part["part_code"])
        if calc is None:
            return None
        # Claude가 계산 도구에 넣은 값이 실제 조회 결과와 다르면 카드를 만들지 않는다
        if calc["qty_per_unit"] != part["qty_per_unit"]:
            return None
        if part["part_code"] in stock and calc["current_stock"] != stock[part["part_code"]]:
            return None
        if (calc["shortage_now"] > 0 and part["part_code"] in inbound_ok
                and calc["inbound_before_start"] != inbound_total[part["part_code"]]):
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
