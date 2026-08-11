"""Capture a Mahjong Soul button position in Akagi-NG's 16:9 coordinate space.

This script is read-only: it observes the cursor and the window under it, and
does not send any mouse or keyboard input.
"""

from __future__ import annotations

import argparse
import ctypes
import time
import traceback
from ctypes import wintypes


GA_ROOT = 2
PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def enable_dpi_awareness() -> None:
    """Keep cursor and client-area values in the same physical-pixel space."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def configure_win32() -> ctypes.WinDLL:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(Point)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [Point]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(Point)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    return user32


def window_title(user32: ctypes.WinDLL, hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value or "<无标题>"


def capture(user32: ctypes.WinDLL) -> dict[str, object]:
    cursor = Point()
    if not user32.GetCursorPos(ctypes.byref(cursor)):
        raise ctypes.WinError(ctypes.get_last_error())

    hwnd = user32.WindowFromPoint(cursor)
    hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    if not hwnd:
        raise RuntimeError("鼠标下方没有可识别的窗口")

    rect = wintypes.RECT()
    origin = Point(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("目标窗口客户区尺寸无效")

    # This is exactly the conversion used by WindowsInputExecutor.
    scale = min(width / 16.0, height / 9.0)
    play_width = 16.0 * scale
    play_height = 9.0 * scale
    play_left = origin.x + (width - play_width) / 2.0
    play_top = origin.y + (height - play_height) / 2.0
    normalized_x = (cursor.x - play_left) / scale
    normalized_y = (cursor.y - play_top) / scale

    return {
        "title": window_title(user32, hwnd),
        "screen": (cursor.x, cursor.y),
        "client_origin": (origin.x, origin.y),
        "client_size": (width, height),
        "client": (cursor.x - origin.x, cursor.y - origin.y),
        "normalized": (normalized_x, normalized_y),
        "inside_game": 0.0 <= normalized_x <= 16.0 and 0.0 <= normalized_y <= 9.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="倒计时后读取鼠标所在按钮，并换算为 Akagi-NG 的 16:9 坐标。"
    )
    parser.add_argument("--delay", type=int, default=5, help="每次取点前的倒计时秒数（默认 5）")
    parser.add_argument("--count", type=int, default=1, help="连续取点次数（默认 1）")
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="完成或报错后直接退出，适用于从已打开的终端运行",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    exit_code = 0
    try:
        if args.delay < 0 or args.count < 1:
            raise ValueError("--delay 必须 >= 0，--count 必须 >= 1")

        enable_dpi_awareness()
        user32 = configure_win32()
        print("请把鼠标移到目标按钮的正中心；脚本只读取位置，不会点击。")
        for index in range(1, args.count + 1):
            if args.count > 1:
                input(f"\n准备采集第 {index}/{args.count} 个位置，按 Enter 开始倒计时……")
            for remaining in range(args.delay, 0, -1):
                print(f"{remaining}...", end=" ", flush=True)
                time.sleep(1)
            print()

            result = capture(user32)
            normalized_x, normalized_y = result["normalized"]
            print(f"窗口标题：{result['title']}")
            print(f"屏幕坐标：{result['screen']}")
            print(f"客户区：左上={result['client_origin']}，尺寸={result['client_size']}")
            print(f"客户区坐标：{result['client']}")
            print(f"Akagi-NG 坐标：({normalized_x:.4f}, {normalized_y:.4f})")
            print(f"可粘贴值：({normalized_x:.2f}, {normalized_y:.2f})")
            if not result["inside_game"]:
                print("警告：鼠标位于 16:9 游戏画面之外（可能落在黑边、标题栏或错误窗口）。")
    except KeyboardInterrupt:
        print("\n已取消取点。")
        exit_code = 130
    except Exception:
        print("\n取点失败，完整错误如下：")
        traceback.print_exc()
        exit_code = 1
    finally:
        if not args.no_pause:
            try:
                input("\n按 Enter 键退出……")
            except (EOFError, KeyboardInterrupt):
                pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
