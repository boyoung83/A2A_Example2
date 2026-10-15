"""판정 카드(가능/조건부 가능/불가/확인 불가) 계산 테스트."""
from material_agent.agent import ToolCall
from material_agent.config import load_settings
from material_agent.data import load_dataset
from material_agent.tools import INBOUND_FAIL_MESSAGE, ToolBox
from material_agent.verdict import build_verdict

TB = ToolBox(load_dataset(), load_settings().base_time)
P100 = [{"part_code": "BRG-A", "part_name": "베어링 A", "qty_per_unit": 2, "unit": "개"},
        {"part_code": "SNS-B", "part_name": "센서 B", "qty_per_unit": 1, "unit": "개"}]
NINE, FOUR = "2026-10-16T09:00:00+09:00", "2026-10-16T16:00:00+09:00"


def calc(part, qty, per, stock, inbound):
    args = {"part_code": part, "production_qty": qty, "qty_per_unit": per,
            "current_stock": stock, "inbound_before_start": inbound}
    return ToolCall("calculate_shortage", args, True, TB.calculate_shortage(**args))


def inb(part, start, ok=True):
    args = {"part_code": part, "production_start": start}
    payload = TB.get_inbound_schedule(part, start) if ok else {"error": INBOUND_FAIL_MESSAGE, "lookup_failed": True}
    return ToolCall("get_inbound_schedule", args, ok, payload)


def test_enough_stock_is_possible():
    v = build_verdict([calc("BRG-A", 10, 2, 30, 0), calc("SNS-B", 10, 1, 25, 0)], P100)
    assert v.status == "가능" and [r.shortage_now for r in v.rows] == [0, 0]
    assert [r.part_name for r in v.rows] == ["베어링 A", "센서 B"]


def test_9am_is_not_possible():
    v = build_verdict([inb("BRG-A", NINE), calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)], P100)
    assert v.status == "불가" and v.rows[0].shortage_at_start == 10


def test_4pm_is_conditional():
    v = build_verdict([inb("BRG-A", FOUR), calc("BRG-A", 20, 2, 30, 20), calc("SNS-B", 20, 1, 25, 0)], P100)
    assert v.status == "조건부 가능"


def test_30_units_not_possible():
    v = build_verdict([inb("BRG-A", FOUR), inb("SNS-B", FOUR),
                       calc("BRG-A", 30, 2, 30, 20), calc("SNS-B", 30, 1, 25, 0)], P100)
    assert v.status == "불가" and [r.shortage_at_start for r in v.rows] == [10, 5]


def test_inbound_failure_is_unconfirmed():
    v = build_verdict([inb("BRG-A", FOUR, ok=False), calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)], P100)
    assert v.status == "확인 불가" and v.rows[0].inbound_error


def test_no_card_when_asking_start_time():
    assert build_verdict([inb("BRG-A", None), calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)], P100) is None


def test_no_card_when_part_missing():
    assert build_verdict([calc("BRG-A", 10, 2, 30, 0)], P100) is None


def test_no_card_without_bom():
    assert build_verdict([calc("SNS-B", 10, 1, 25, 0)], None) is None


def test_uses_last_calculation_per_part():
    v = build_verdict([calc("BRG-A", 10, 2, 30, 0), calc("SNS-B", 10, 1, 25, 0),
                       inb("BRG-A", FOUR), calc("BRG-A", 30, 2, 30, 20)], P100)
    assert v.rows[0].required == 60


def test_inbound_checked_in_earlier_turn_counts():
    # 질문 5 이어서: "30개로 늘리면?" — Claude가 앞 발화의 베어링 A 입고 조회를 재사용
    earlier = [inb("BRG-A", FOUR)]
    turn = [inb("SNS-B", FOUR), calc("BRG-A", 30, 2, 30, 20), calc("SNS-B", 30, 1, 25, 0)]
    v = build_verdict(turn, P100, earlier + turn)
    assert v.status == "불가" and [r.shortage_at_start for r in v.rows] == [10, 5]


def test_no_card_when_inbound_copied_from_other_start_time():
    # 앞 발화는 16시 기준(입고 20 반영), 이번 발화 "오전 9시는?"에서 Claude가 20을 그대로 넣은 경우
    earlier = [inb("BRG-A", FOUR)]
    turn = [calc("BRG-A", 20, 2, 30, 20), calc("SNS-B", 20, 1, 25, 0)]
    nine = [inb("BRG-A", NINE)]
    assert build_verdict(turn, P100, earlier + nine + turn) is None


def test_no_card_when_qty_per_unit_mismatch():
    assert build_verdict([calc("BRG-A", 10, 1, 30, 0), calc("SNS-B", 10, 1, 25, 0)], P100) is None


def test_no_card_when_stock_mismatch_with_inventory_lookup():
    inv = ToolCall("get_inventory", {"part_code": "BRG-A"}, True,
                   {"found": True, "part_code": "BRG-A", "part_name": "베어링 A", "current_stock": 30, "unit": "개"})
    turn = [inv, calc("BRG-A", 10, 2, 50, 0), calc("SNS-B", 10, 1, 25, 0)]
    assert build_verdict(turn, P100, turn) is None


def test_bad_start_time_error_is_not_lookup_failure():
    bad = ToolCall("get_inbound_schedule", {"part_code": "BRG-A", "production_start": "내일"}, False,
                   {"error": "생산 시작 시각을 해석할 수 없습니다"})
    turn = [bad, calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)]
    assert build_verdict(turn, P100, turn) is None


def test_failed_lookup_by_part_name_marks_part():
    fail_tb = ToolBox(load_dataset(), load_settings().base_time)
    fail_tb.inbound_fails = True
    args = {"part_code": "베어링 A", "production_start": FOUR}
    out = fail_tb.run("get_inbound_schedule", args)
    turn = [ToolCall("get_inbound_schedule", args, out.ok, out.payload),
            calc("BRG-A", 20, 2, 30, 0), calc("SNS-B", 20, 1, 25, 0)]
    v = build_verdict(turn, P100, turn)
    assert v.status == "확인 불가" and v.rows[0].inbound_error
