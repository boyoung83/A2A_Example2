"""앱을 띄워 데모 대화를 실제 API로 진행하면서 앱 창을 녹화해 README용 GIF를 만든다.

실행: python scripts/record_demo.py   (실제 API 호출 비용 발생)
결과: docs/images/demo.gif

- 앱 창만 그려서 찍으므로(PrintWindow) 화면 위에 덮인 다른 프로그램 표시는 들어가지 않는다.
- Claude 응답을 기다리는 동안 화면이 바뀌지 않는 구간은 짧게 줄이고, 판정이 나온 장면은 잠시 멈춘다.
"""
import ctypes
import hashlib
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic  # noqa: E402
from PIL import Image  # noqa: E402

from app import ANDON, CHAT_BG, STATUS_STYLE, STEEL, USER_BUBBLE, MaterialApp  # noqa: E402
from capture_screens import grab_window  # noqa: E402
from material_agent.agent import MaterialAgent  # noqa: E402
from material_agent.config import PROJECT_DIR, load_api_key, load_settings  # noqa: E402
from material_agent.data import load_dataset  # noqa: E402
from material_agent.tools import ToolBox  # noqa: E402

OUT = PROJECT_DIR / "docs" / "images" / "demo.gif"
WIDTH = 1000            # GIF 가로 크기 (저장소 용량을 위해 줄임)
FRAME_MS = 400          # 녹화 간격
TYPE_MS = 45            # 글자 입력 간격
MAX_STILL = 0.5         # 화면이 그대로인 구간의 최대 표시 시간(초)
HOLD = 3.0              # 판정이 나온 장면을 멈춰 보여주는 시간(초)

# (동작, 값): send=질문 입력 후 보내기, new=새 대화, fail=입고 조회 실패 모드 켜기, hold=현재 장면 멈춤
SCRIPT = [
    ("send", "센서 B 재고만 알려줘."),
    ("send", "내일 P-100을 20개 만들 수 있을까? 부족한 부품은 입고 예정까지 확인해줘."),
    ("send", "오전 9시야."),
    ("hold", None),
    ("new", None),
    ("fail", None),
    ("send", "내일 오후 4시에 P-100을 20개 생산할 수 있어?"),
    ("hold", None),
]


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    settings = load_settings()
    agent = MaterialAgent(anthropic.Anthropic(api_key=load_api_key()),
                          ToolBox(load_dataset(), settings.base_time), settings.base_time)
    root = tk.Tk()
    root.title("자재대응 Agent")
    root.geometry("1400x880+20+20")
    app = MaterialApp(root, agent, settings)
    app.pack(fill="both", expand=True)

    frames: list[list] = []   # [이미지, 표시 시간(초), 멈춤 여부]
    last = {"hash": None, "t": time.monotonic()}
    steps = list(SCRIPT)

    def snap(hold: float = 0.0):
        image = grab_window(root)
        digest = hashlib.md5(image.tobytes()).hexdigest()
        now = time.monotonic()
        if frames and digest == last["hash"] and not hold:
            frames[-1][1] = min(MAX_STILL, frames[-1][1] + (now - last["t"]))  # 바뀐 게 없으면 앞 장면을 조금 늘림
        else:
            frames.append([image, hold or 0.1, bool(hold)])
        last.update(hash=digest, t=now)

    def tick():
        snap()
        root.after(FRAME_MS, tick)

    def type_text(text: str, i: int = 0):
        if i < len(text):
            app.entry.insert("end", text[i])
            app._update_placeholder()
            snap()
            root.after(TYPE_MS, type_text, text, i + 1)
        else:
            root.after(300, lambda: (app.send(), root.after(300, step)))

    def step():
        if app.busy:
            root.after(200, step)
            return
        if not steps:
            root.after(300, finish)
            return
        action, value = steps.pop(0)
        root.update()
        app.chat.yview_moveto(1.0)
        if action == "send":
            type_text(value)
        elif action == "hold":
            root.after(400, lambda: (snap(hold=HOLD), step()))
        elif action == "new":
            app.new_conversation()
            root.after(500, step)
        elif action == "fail":
            app.fail_switch.toggle()
            root.after(700, step)

    def finish():
        save(frames)
        root.destroy()

    root.after(800, lambda: (snap(), tick(), step()))
    root.mainloop()


def shared_palette(smalls: list[Image.Image]) -> Image.Image:
    """모든 프레임이 함께 쓸 256색 팔레트. 안돈 신호·카톡 노랑 같은 강조색은 큰 색 견본으로 넣어 반드시 포함시킨다."""
    keys = [*ANDON.values(), STATUS_STYLE["조건부 가능"][1], STATUS_STYLE["확인 불가"][1], USER_BUBBLE, STEEL, CHAT_BG]
    w, h = smalls[0].size
    picks = [smalls[0], *[s for s in smalls[len(smalls) // 3::len(smalls) // 3]], smalls[-1]]
    board = Image.new("RGB", (w, h * len(picks) + 200), "white")
    for i, im in enumerate(picks):
        board.paste(im, (0, i * h))
    swatch = w // len(keys)
    for i, color in enumerate(keys):
        board.paste(Image.new("RGB", (swatch, 200), color), (i * swatch, h * len(picks)))
    return board.quantize(colors=256, method=Image.Quantize.MEDIANCUT)


def save(frames):
    """프레임을 줄이고 공용 팔레트로 색을 줄여 GIF로 저장한다."""
    smalls, durations = [], []
    for image, seconds, held in frames:
        h = round(image.height * WIDTH / image.width)
        smalls.append(image.resize((WIDTH, h), Image.LANCZOS))
        durations.append(int((seconds if held else min(seconds, MAX_STILL)) * 1000))
    palette = shared_palette(smalls)
    images = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in smalls]  # 점찍기 없이 깔끔하게
    durations[-1] = int(HOLD * 1000)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(OUT, save_all=True, append_images=images[1:], duration=durations, loop=0,
                   optimize=True, disposal=1)
    total = sum(durations) / 1000
    print(f"저장: {OUT} ({len(images)}장, {total:.0f}초, {OUT.stat().st_size / 1e6:.1f}MB)", flush=True)


if __name__ == "__main__":
    main()
