"""에이전트 도구 4개와 Claude에 넘길 도구 정의(TOOL_SCHEMAS)."""
from dataclasses import dataclass
from datetime import datetime

from material_agent.data import Dataset, parse_time

INBOUND_FAIL_MESSAGE = "입고 예정 조회 실패: 입고 시스템 응답 없음"
BAD_QTY_MESSAGE = "생산 수량은 양의 정수여야 합니다"


class InboundLookupError(Exception):
    """입고 예정 조회 자체가 실패함 (입고 예정이 없는 것과 다름)."""

    def __init__(self, message: str, part_code: str):
        super().__init__(message)
        self.part_code = part_code


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    payload: dict


def _code(text: str) -> str:
    return str(text).strip().upper()


def _squash(text: str) -> str:
    """비교용: 공백·하이픈을 없애고 대문자로 ("센서 B" == "센서B", "SNS B" == "SNS-B")."""
    return "".join(str(text).split()).replace("-", "").upper()


def _is_int(value) -> bool:
    return type(value) is int  # bool·float·문자열 제외


class ToolBox:
    def __init__(self, dataset: Dataset, base_time: datetime):
        self.dataset = dataset
        self.base_time = base_time
        self.inbound_fails = False

    def get_bom(self, product_code: str) -> dict:
        code = _code(product_code)
        rows = [r for r in self.dataset.bom if r.product_code == code]
        if not rows:
            return {"product_code": code, "found": False, "message": "등록되지 않은 제품입니다"}
        parts = [{"part_code": r.part_code, "part_name": r.part_name, "qty_per_unit": r.qty_per_unit, "unit": r.unit}
                 for r in rows]
        return {"product_code": code, "found": True, "parts": parts}

    def _find_part(self, part_code: str):
        """부품코드 또는 부품명으로 재고 행을 찾는다. 없으면 None."""
        key = _squash(part_code)
        return next((r for r in self.dataset.inventory if key in (_squash(r.part_code), _squash(r.part_name))), None)

    def _require_part(self, part_code: str) -> str:
        row = self._find_part(part_code)
        if row is None:
            raise ValueError(f"등록되지 않은 부품입니다: {part_code}")
        return row.part_code

    def get_inventory(self, part_code: str) -> dict:
        r = self._find_part(part_code)
        if r is None:
            return {"found": False, "query": str(part_code), "message": "등록되지 않은 부품입니다"}
        return {"found": True, "part_code": r.part_code, "part_name": r.part_name,
                "current_stock": r.current_stock, "unit": r.unit}

    def get_inbound_schedule(self, part_code: str, production_start: str | None = None) -> dict:
        # 부품명이 와도 같은 부품으로 조회한다. 모르는 부품은 "입고 예정 없음"(빈 배열)이 아니라 오류
        code = self._require_part(part_code)
        if self.inbound_fails:
            raise InboundLookupError(INBOUND_FAIL_MESSAGE, code)
        rows = sorted((r for r in self.dataset.inbound if r.part_code == code), key=lambda r: r.arrival)
        schedule = [{"inbound_id": r.inbound_id, "arrival": r.arrival.isoformat(), "qty": r.qty} for r in rows]
        result = {"part_code": code, "schedule": schedule}
        if production_start is None:
            return result
        try:
            start = parse_time(production_start)
        except ValueError:
            raise ValueError(f"생산 시작 시각을 해석할 수 없습니다: {production_start} (예: 2026-10-16T09:00:00+09:00)")
        if start < self.base_time:
            raise ValueError(f"생산 시작 시각이 기준시각({self.base_time.isoformat()})보다 이전입니다")
        total = 0
        for row, item in zip(rows, schedule):
            item["before_start"] = self.base_time < row.arrival <= start
            total += row.qty if item["before_start"] else 0
        result["production_start"] = start.isoformat()
        result["inbound_before_start_total"] = total
        return result

    def calculate_shortage(self, part_code: str, production_qty: int, qty_per_unit: int,
                           current_stock: int, inbound_before_start: int) -> dict:
        if not _is_int(production_qty) or production_qty <= 0:
            raise ValueError(f"{BAD_QTY_MESSAGE} (받은 값: {production_qty!r})")
        if not _is_int(qty_per_unit) or qty_per_unit <= 0:
            raise ValueError("제품1개당소요량은 양의 정수여야 합니다")
        for name, value in (("current_stock", current_stock), ("inbound_before_start", inbound_before_start)):
            if not _is_int(value) or value < 0:
                raise ValueError(f"{name}은(는) 0 이상의 정수여야 합니다")
        required = production_qty * qty_per_unit
        return {"part_code": self._require_part(part_code), "production_qty": production_qty, "qty_per_unit": qty_per_unit,
                "required": required, "current_stock": current_stock, "inbound_before_start": inbound_before_start,
                "shortage_now": max(0, required - current_stock),
                "shortage_at_start": max(0, required - current_stock - inbound_before_start)}

    def run(self, name: str, args: dict) -> ToolOutcome:
        """도구를 실행한다. 어떤 실패도 예외로 던지지 않고 ok=False 결과로 돌려준다."""
        handler = {"get_bom": self.get_bom, "get_inventory": self.get_inventory,
                   "get_inbound_schedule": self.get_inbound_schedule,
                   "calculate_shortage": self.calculate_shortage}.get(name)
        if handler is None:
            return ToolOutcome(False, {"error": f"알 수 없는 도구입니다: {name}"})
        try:
            return ToolOutcome(True, handler(**args))
        except InboundLookupError as e:
            # 조회 자체의 실패만 lookup_failed로 표시한다 (잘못된 입력과 구분)
            return ToolOutcome(False, {"error": str(e), "lookup_failed": True, "part_code": e.part_code})
        except ValueError as e:
            return ToolOutcome(False, {"error": str(e)})
        except TypeError as e:
            return ToolOutcome(False, {"error": f"도구 입력값이 올바르지 않습니다: {e}"})


def _schema(name: str, description: str, properties: dict) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties,
                             "required": list(properties), "additionalProperties": False}}


TOOL_SCHEMAS = [
    _schema("get_bom",
            "제품 한 개를 만드는 데 필요한 부품과 수량(제품1개당소요량)을 조회한다. "
            "생산 가능 여부를 판단할 때 가장 먼저 제품의 부품 구성을 확인하는 데 쓴다. "
            "등록되지 않은 제품이면 found=false를 돌려준다.",
            {"product_code": {"type": "string", "description": "제품코드 (예: P-100)"}}),
    _schema("get_inventory",
            "부품 하나의 현재 재고 수량을 조회한다. 부품코드(예: SNS-B) 또는 부품명(예: 센서 B)으로 조회할 수 있다.",
            {"part_code": {"type": "string", "description": "부품코드 또는 부품명"}}),
    _schema("get_inbound_schedule",
            "부품 하나의 입고 예정(입고 시각·수량)을 조회한다. production_start에 생산 시작 시각을 넣으면 "
            "각 입고가 시작 전에 도착하는지(before_start)와 시작 전 입고 합계(inbound_before_start_total)를 함께 돌려준다. "
            "입고 예정이 없으면 schedule은 빈 배열이며 이는 정상 결과다. 조회 실패는 오류로 돌아온다.",
            {"part_code": {"type": "string", "description": "부품코드 (예: BRG-A)"},
             "production_start": {"type": ["string", "null"],
                                  "description": "생산 시작 시각 ISO 8601 (+09:00), 모르면 null"}}),
    _schema("calculate_shortage",
            "부품 하나의 부족량을 계산한다. 필요수량 = 생산수량 × 제품1개당소요량, "
            "현재부족량 = max(0, 필요수량 - 현재재고), 시작시각기준부족량 = max(0, 필요수량 - 현재재고 - 시작전입고예정수량). "
            "답변에 쓰는 모든 수량은 이 도구의 결과를 사용한다.",
            {"part_code": {"type": "string", "description": "부품코드"},
             "production_qty": {"type": "integer", "description": "생산 수량 (양의 정수)"},
             "qty_per_unit": {"type": "integer", "description": "제품1개당소요량"},
             "current_stock": {"type": "integer", "description": "현재 재고"},
             "inbound_before_start": {"type": "integer",
                                      "description": "생산 시작 전 입고 예정 수량 합계 (없거나 반영하지 않으면 0)"}}),
]
