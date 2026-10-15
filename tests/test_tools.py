"""도구 4개(get_bom, get_inventory, get_inbound_schedule, calculate_shortage) 테스트."""
import pytest

from material_agent.config import load_settings
from material_agent.data import load_dataset
from material_agent.tools import TOOL_SCHEMAS, ToolBox, ToolOutcome


@pytest.fixture
def tb():
    return ToolBox(load_dataset(), load_settings().base_time)


def test_get_bom_found(tb):
    r = tb.get_bom("P-100")
    assert r["found"] and [p["part_code"] for p in r["parts"]] == ["BRG-A", "SNS-B"]


def test_get_bom_unknown_product(tb):
    r = tb.get_bom("P-999")
    assert r == {"product_code": "P-999", "found": False, "message": "등록되지 않은 제품입니다"}


def test_codes_normalized(tb):
    assert tb.get_bom(" p-100 ")["found"]
    assert tb.get_inventory(" sns-b ")["current_stock"] == 25


def test_inventory_by_part_name(tb):
    assert tb.get_inventory("센서 B")["part_code"] == "SNS-B"
    assert tb.get_inventory("센서B")["current_stock"] == 25


def test_inventory_unknown_part(tb):
    r = tb.get_inventory("ZZZ-9")
    assert r["found"] is False and r["message"] == "등록되지 않은 부품입니다"


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


def test_inbound_same_time_included(tb):
    assert tb.get_inbound_schedule("CABLE-K", "2026-10-16T09:00:00+09:00")["inbound_before_start_total"] == 40


def test_inbound_start_without_zone(tb):
    assert tb.get_inbound_schedule("BRG-A", "2026-10-16T16:00:00")["inbound_before_start_total"] == 20


def test_inbound_bad_or_past_start_is_error(tb):
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


@pytest.mark.parametrize("qty", [0, -5, 2.5, "20", True])
def test_shortage_rejects_bad_qty(tb, qty):
    out = tb.run("calculate_shortage", {"part_code": "BRG-A", "production_qty": qty,
                                        "qty_per_unit": 2, "current_stock": 30, "inbound_before_start": 0})
    assert not out.ok and "양의 정수" in out.payload["error"]


def test_run_success_wraps_payload(tb):
    out = tb.run("get_inventory", {"part_code": "BRG-A"})
    assert out.ok and out.payload["current_stock"] == 30


def test_run_unknown_tool(tb):
    assert not tb.run("delete_stock", {}).ok


def test_run_missing_argument(tb):
    assert not tb.run("get_bom", {}).ok


def test_schemas_are_strict():
    assert {s["name"] for s in TOOL_SCHEMAS} == {"get_bom", "get_inventory", "get_inbound_schedule", "calculate_shortage"}
    assert all(s["strict"] and s["input_schema"]["additionalProperties"] is False for s in TOOL_SCHEMAS)
