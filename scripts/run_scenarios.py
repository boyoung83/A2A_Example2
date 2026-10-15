"""질문 모음·대화 시나리오를 실제 API로 실행해 결과를 Markdown으로 저장한다.

실행: python scripts/run_scenarios.py   (실제 API 호출 비용 발생)
결과: docs/검증/실행결과_<YYYYMMDD_HHMM>.md
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from app import summarize  # noqa: E402
from material_agent.agent import AgentError, MaterialAgent  # noqa: E402
from material_agent.config import PROJECT_DIR, load_api_key, load_settings  # noqa: E402
from material_agent.data import load_dataset  # noqa: E402
from material_agent.tools import ToolBox  # noqa: E402
from material_agent.verdict import build_verdict  # noqa: E402

# (이름, 발화 목록, 입고 조회 실패 모드, 실행 한도). 발화 목록은 한 대화 안에서 차례로 보낸다.
SCENARIOS = [
    ("질문 1 · 재고만 조회", ["센서 B는 지금 몇 개 있어?"], False, 16),
    ("질문 2 · 현재 재고로 충분", ["내일 오전 9시에 P-100을 10개 생산할 수 있어?"], False, 16),
    ("질문 3 · 시작 시각 빠짐", ["내일 P-100을 20개 생산할 수 있어?", "오전 9시야."], False, 16),
    ("질문 4 · 시작 시각 바꾸기", ["내일 오전 9시에 P-100을 20개 생산할 수 있어?", "그럼 오후 4시로 옮기면?"], False, 16),
    ("질문 5 · 생산 수량 바꾸기", ["내일 오후 4시에 P-100을 20개 생산할 수 있어?", "30개로 늘리면?"], False, 16),
    ("질문 6 · 없는 제품", ["내일 P-999를 10개 생산할 수 있어?"], False, 16),
    ("질문 7 · 잘못된 수량", ["내일 오전 9시에 P-100을 마이너스 5개 생산할 수 있어?"], False, 16),
    ("질문 8 · 날짜와 수량 빠짐", ["P-100 만들 수 있어?"], False, 16),
    ("대화 1→2 · 재고 조회 후 생산 가능 여부", ["센서 B 재고만 알려줘.",
                                        "내일 P-100을 20개 만들 수 있을까? 부족한 부품은 입고 예정까지 확인해줘.",
                                        "오전 9시야."], False, 16),
    ("대화 3 · 오후 4시 20개", ["내일 오후 4시에 P-100을 20개 생산할 수 있어?"], False, 16),
    ("대화 4 · 없는 제품", ["내일 P-999를 10개 생산할 수 있어?"], False, 16),
    ("대화 5 · 입고 조회 실패 (질문 9)", ["내일 오후 4시에 P-100을 20개 생산할 수 있어?"], True, 16),
    ("한도 8회 · 대화 2", ["내일 P-100을 20개 만들 수 있을까? 부족한 부품은 입고 예정까지 확인해줘.", "오전 9시야."], False, 8),
]


def run(selected: list[str] | None = None) -> Path:
    key = load_api_key()
    if not key:
        sys.exit(".env에 ANTHROPIC_API_KEY가 없습니다 (.env.example 참고)")
    settings = load_settings()
    client = anthropic.Anthropic(api_key=key)
    lines = [f"# 시나리오 실행 결과 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"기준시각 {settings.base_time.isoformat()} · 모델 claude-opus-5-5", ""]
    for name, utterances, fails, limit in SCENARIOS:
        if selected and not any(s in name for s in selected):
            continue
        print(f"▶ {name}", flush=True)
        toolbox = ToolBox(load_dataset(), settings.base_time)
        toolbox.inbound_fails = fails
        agent = MaterialAgent(client, toolbox, settings.base_time)
        lines += [f"## {name}", "", f"입고 조회 실패 모드: {'켬' if fails else '끔'} · 실행 한도: {limit}회", ""]
        for text in utterances:
            lines += [f"**사용자:** {text}", ""]
            try:
                result = agent.ask(text, limit)
            except AgentError as e:
                lines += [f"**오류:** {e}", ""]
                break
            lines.append(f"도구 {len(result.tool_calls)}/{limit}회" + (" (한도 도달)" if result.limit_reached else ""))
            for i, c in enumerate(result.tool_calls, 1):
                args = ", ".join(f"{k}={v}" for k, v in c.args.items() if v is not None)
                lines.append(f"{i}. `{c.name}({args})` → {summarize(c)}")
            verdict = build_verdict(result.tool_calls, agent.last_bom)
            lines += ["", f"**에이전트:** {result.text}", "",
                      f"**판정 카드:** {verdict.status if verdict else '없음'}", ""]
    out = PROJECT_DIR / "docs" / "검증" / f"실행결과_{datetime.now():%Y%m%d_%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(f"저장: {run(sys.argv[1:] or None)}")
