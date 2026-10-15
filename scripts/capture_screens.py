"""앱을 띄워 대화 시나리오를 실제 API로 실행하고 README용 화면을 캡처한다 (앱 창만 그려서 저장).

실행: python scripts/capture_screens.py   (실제 API 호출 비용 발생)
결과: docs/images/app_9am.png, docs/images/app_inbound_fail.png
"""
import ctypes
import ctypes.wintypes
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402
from PIL import Image  # noqa: E402

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


def grab_window(root: tk.Tk) -> Image.Image:
    """PrintWindow로 앱 창 자체만 그려 이미지로 만든다.
    화면 전체를 찍지 않으므로 다른 프로그램이 화면 위에 덮어 그리는 표시(보안 워터마크 등)가 들어가지 않는다."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
    rect = ctypes.wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right, rect.bottom
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 1 | 2)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buffer = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buffer, ctypes.byref(header), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return Image.frombuffer("RGB", (w, h), buffer, "raw", "BGRX", 0, 1)


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
        app.chat.yview_moveto(1.0)  # 마지막 메시지(판정 카드)까지 보이게
        root.update()
        grab_window(root).save(OUT / name)
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
