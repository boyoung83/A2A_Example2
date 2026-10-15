"""앱을 띄워 대화 시나리오를 실제 API로 실행하고 README용 화면을 캡처한다.

실행: python scripts/capture_screens.py   (실제 API 호출 비용 발생)
결과: docs/images/app_9am.png, docs/images/app_inbound_fail.png
"""
import ctypes
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
from PIL import ImageGrab  # noqa: E402

from app import MaterialApp  # noqa: E402
from material_agent.agent import MaterialAgent  # noqa: E402
from material_agent.config import PROJECT_DIR, load_api_key, load_settings  # noqa: E402
from material_agent.data import load_dataset  # noqa: E402
from material_agent.tools import ToolBox  # noqa: E402

OUT = PROJECT_DIR / "docs" / "images"

# (저장 이름, 입고 조회 실패 모드, 차례로 보낼 발화)
SHOTS = [
    ("app_9am.png", False, ["센서 B 재고만 알려줘.",
                            "내일 P-100을 20개 만들 수 있을까? 부족한 부품은 입고 예정까지 확인해줘.",
                            "오전 9시야."]),
    ("app_inbound_fail.png", True, ["내일 오후 4시에 P-100을 20개 생산할 수 있어?"]),
]


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 캡처 좌표가 화면 배율과 맞도록
    except (AttributeError, OSError):
        pass
    settings = load_settings()
    agent = MaterialAgent(anthropic.Anthropic(api_key=load_api_key()),
                          ToolBox(load_dataset(), settings.base_time), settings.base_time)
    root = tk.Tk()
    root.title("자재대응 Agent — 생산 가능 여부 확인")
    root.geometry("1400x860+20+20")
    root.attributes("-topmost", True)  # 캡처 영역을 다른 창이 가리지 않도록
    app = MaterialApp(root, agent, settings)
    app.pack(fill="both", expand=True)
    OUT.mkdir(parents=True, exist_ok=True)
    plan = [(name, fails, list(utterances)) for name, fails, utterances in SHOTS]

    def step():
        if app.busy:
            root.after(300, step)
            return
        name, fails, utterances = plan[0]
        if utterances:
            if len(utterances) == len(SHOTS[len(SHOTS) - len(plan)][2]):  # 새 장면 시작
                app.new_conversation()
                app.fail_var.set(fails)
            app.entry.insert(0, utterances.pop(0))
            app.send()
            root.after(300, step)
            return
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(OUT / name)
        print(f"저장: {OUT / name}", flush=True)
        plan.pop(0)
        if plan:
            root.after(300, step)
        else:
            root.after(500, root.destroy)

    root.after(800, step)
    root.mainloop()


if __name__ == "__main__":
    main()
