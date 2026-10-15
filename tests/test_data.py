"""CSV 자료 읽기 테스트."""
from datetime import datetime

import pytest

from material_agent.data import KST, load_dataset, parse_time


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
    with pytest.raises(ValueError):
        parse_time("내일 오후")
