"""참고 폴더의 CSV(소요 부품·재고·입고 예정)를 읽어 dataclass로 돌려준다."""
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "참고"


@dataclass(frozen=True)
class BomRow:
    product_code: str
    part_code: str
    part_name: str
    qty_per_unit: int
    unit: str


@dataclass(frozen=True)
class StockRow:
    part_code: str
    part_name: str
    current_stock: int
    unit: str


@dataclass(frozen=True)
class InboundRow:
    inbound_id: str
    part_code: str
    arrival: datetime
    qty: int
    unit: str


@dataclass(frozen=True)
class Dataset:
    bom: list[BomRow]
    inventory: list[StockRow]
    inbound: list[InboundRow]


def parse_time(text: str) -> datetime:
    """ISO 시각 문자열을 해석한다. 시간대가 없으면 한국 시각(+09:00)으로 본다."""
    value = datetime.fromisoformat(str(text).strip())  # 잘못된 형식이면 ValueError
    return value if value.tzinfo else value.replace(tzinfo=KST)


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_dataset(data_dir: Path = DATA_DIR) -> Dataset:
    bom = [BomRow(r["제품코드"], r["부품코드"], r["부품명"], int(r["제품1개당소요량"]), r["단위"])
           for r in _read(data_dir / "제품별_소요부품.csv")]
    inventory = [StockRow(r["부품코드"], r["부품명"], int(r["현재재고"]), r["단위"])
                 for r in _read(data_dir / "현재재고.csv")]
    inbound = [InboundRow(r["입고번호"], r["부품코드"], parse_time(r["입고예정시각"]), int(r["입고예정수량"]), r["단위"])
               for r in _read(data_dir / "입고예정.csv")]
    return Dataset(bom, inventory, inbound)
