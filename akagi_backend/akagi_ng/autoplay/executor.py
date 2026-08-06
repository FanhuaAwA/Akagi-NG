from __future__ import annotations

import ctypes
import math
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from akagi_ng.core.logging import logger as base_logger
from akagi_ng.schema.constants import Platform
from akagi_ng.settings import local_settings

logger = base_logger.bind(module="autoplay-executor")

INPUT_EVENT_COUNT = 2
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
MIN_TARGET_WIDTH = 640
MIN_TARGET_HEIGHT = 360
GOLD_MIN_RED = 150
GOLD_MIN_GREEN = 95
GOLD_MAX_BLUE = 130
GOLD_COMPONENT_MIN_PIXELS = 180
GOLD_COMPONENT_MIN_ASPECT = 1.8
GOLD_COMPONENT_MAX_ASPECT = 8.0
type OperationListProvider = Callable[[], list[dict]]
type OperationStepProvider = Callable[[], int | None]
type CancelPredicate = Callable[[], bool]


@dataclass(slots=True)
class WindowObject:
    hwnd: int
    name: str


@dataclass(slots=True)
class WindowGeometry:
    left: int
    top: int
    width: int
    height: int


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_ulong),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_ulong),
        ("biSizeImage", ctypes.c_ulong),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_ulong),
        ("biClrImportant", ctypes.c_ulong),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_ulong * 3)]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class InputUnion(ctypes.Union):
    _fields_: ClassVar = [("mi", MouseInput)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", InputUnion)]


class WindowsInputExecutor:
    def __init__(self):
        self._target_hwnd: int | None = None
        self._user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
        self._input = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
        self._gdi32 = ctypes.windll.gdi32 if hasattr(ctypes, "windll") else None

    @property
    def available(self) -> bool:
        return self._user32 is not None and os.name == "nt"

    def ensure_target_window(self, platform: Platform | str, custom_keyword: str = "") -> bool:
        if not self.available:
            return False
        keywords = self._window_keywords(platform, custom_keyword)
        if self._check_window(keywords):
            logger.info(f"Reusing autoplay target window hwnd={self._target_hwnd}")
            return True
        self._target_hwnd = None
        selected = self._auto_select_window(platform, custom_keyword)
        if selected is None:
            logger.warning(
                f"Failed to locate autoplay target window for platform={platform} keyword={custom_keyword!r}"
            )
            return False
        logger.info(f"Selected autoplay target window hwnd={selected.hwnd} title={selected.name!r}")
        return True

    def move_to(self, target: tuple[int, int], *, cancel_requested: CancelPredicate) -> bool:
        if not self.available:
            return False
        start = POINT()
        self._user32.GetCursorPos(ctypes.byref(start))
        start_point = (start.x, start.y)
        distance = math.hypot(target[0] - start_point[0], target[1] - start_point[1])
        path = self._build_bezier_path(start_point, target)
        if not path:
            return True
        duration = min(0.24, max(0.12, distance / 2200.0))
        step_delay = duration / max(len(path), 1)
        for point in path:
            if cancel_requested():
                return False
            self._user32.SetCursorPos(int(point[0]), int(point[1]))
            time.sleep(step_delay)
        return True

    def left_click(self) -> None:
        if self._input is None:
            return

        try:
            extra = ctypes.c_ulong(0)
            down = INPUT()
            down.type = 0
            down.union.mi = MouseInput(
                dx=0,
                dy=0,
                mouseData=0,
                dwFlags=0x0002,
                time=0,
                dwExtraInfo=ctypes.pointer(extra),
            )
            up = INPUT()
            up.type = 0
            up.union.mi = MouseInput(
                dx=0,
                dy=0,
                mouseData=0,
                dwFlags=0x0004,
                time=0,
                dwExtraInfo=ctypes.pointer(extra),
            )
            event_arr = (INPUT * INPUT_EVENT_COUNT)(down, up)
            sent = self._input.SendInput(INPUT_EVENT_COUNT, ctypes.byref(event_arr), ctypes.sizeof(INPUT))
            if sent != INPUT_EVENT_COUNT:
                raise RuntimeError(f"SendInput sent {sent}/{INPUT_EVENT_COUNT} events")
        except Exception:
            self._user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.012)
            self._user32.mouse_event(0x0004, 0, 0, 0, 0)

    def scroll_vertical(self, notches: int) -> bool:
        """Scroll inside the target window; positive values move the list upward."""
        if self._user32 is None or notches == 0:
            return False
        self._user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(notches) * WHEEL_DELTA, 0)
        return True

    def focus_target_window(self) -> bool:
        if self._target_hwnd is None or self._user32 is None:
            return False
        try:
            if self._user32.IsIconic(self._target_hwnd):
                self._user32.ShowWindow(self._target_hwnd, 9)
            self._user32.SetForegroundWindow(self._target_hwnd)
            if self._user32.GetForegroundWindow() == self._target_hwnd:
                return True
            time.sleep(0.05)
            return self._user32.GetForegroundWindow() == self._target_hwnd
        except Exception:
            return False

    def get_target_geometry(self) -> WindowGeometry | None:
        geometry = self._get_window_geometry(self._target_hwnd)
        if geometry is None or geometry.width < MIN_TARGET_WIDTH or geometry.height < MIN_TARGET_HEIGHT:
            return None
        return geometry

    def find_enabled_gold_button(self, geometry: WindowGeometry) -> tuple[int, int] | None:
        """Find a large enabled Mahjong Soul gold button in the lower-right area.

        Locked result buttons are grey. Detecting the rendered gold component
        means auto-join does not guess whether the result animation has ended,
        and it also avoids relying on a resolution-specific confirmation point.
        """
        capture = self._capture_target_bgra(geometry)
        if capture is None:
            return None
        pixels, bitmap_width, client_offset_x, client_offset_y = capture
        stride = 2
        x_start = int(geometry.width * 0.68)
        x_end = int(geometry.width * 0.99)
        y_start = int(geometry.height * 0.60)
        y_end = int(geometry.height * 0.98)
        grid_width = max(0, (x_end - x_start) // stride)
        grid_height = max(0, (y_end - y_start) // stride)
        if grid_width == 0 or grid_height == 0:
            return None

        mask = bytearray(grid_width * grid_height)
        for grid_y in range(grid_height):
            client_y = y_start + grid_y * stride
            bitmap_y = client_offset_y + client_y
            row_offset = bitmap_y * bitmap_width * 4
            for grid_x in range(grid_width):
                client_x = x_start + grid_x * stride
                pixel_offset = row_offset + (client_offset_x + client_x) * 4
                blue = pixels[pixel_offset]
                green = pixels[pixel_offset + 1]
                red = pixels[pixel_offset + 2]
                if self._is_enabled_gold(red, green, blue):
                    mask[grid_y * grid_width + grid_x] = 1

        components = self._connected_components(mask, grid_width, grid_height)
        candidate = self._select_gold_button_component(components, geometry, stride)
        if candidate is None:
            return None
        count, min_x, min_y, max_x, max_y = candidate
        client_center_x = x_start + (min_x + max_x) * stride // 2
        client_center_y = y_start + (min_y + max_y) * stride // 2
        target = (geometry.left + client_center_x, geometry.top + client_center_y)
        logger.info(f"Detected enabled gold button at screen={target} matched_pixels={count}.")
        return target

    @staticmethod
    def _is_enabled_gold(red: int, green: int, blue: int) -> bool:
        return (
            red >= GOLD_MIN_RED
            and green >= GOLD_MIN_GREEN
            and blue <= GOLD_MAX_BLUE
            and red * 100 > green * 112
            and green * 100 > blue * 115
        )

    @staticmethod
    def _select_gold_button_component(
        components: list[tuple[int, int, int, int, int]],
        geometry: WindowGeometry,
        stride: int,
    ) -> tuple[int, int, int, int, int] | None:
        candidates: list[tuple[int, int, int, int, int]] = []
        for component in components:
            count, min_x, min_y, max_x, max_y = component
            width = (max_x - min_x + 1) * stride
            height = (max_y - min_y + 1) * stride
            large_enough = (
                count >= GOLD_COMPONENT_MIN_PIXELS
                and width >= max(72, int(geometry.width * 0.055))
                and height >= max(22, int(geometry.height * 0.025))
            )
            aspect = width / max(height, 1)
            if large_enough and GOLD_COMPONENT_MIN_ASPECT <= aspect <= GOLD_COMPONENT_MAX_ASPECT:
                candidates.append(component)
        return max(candidates) if candidates else None

    @staticmethod
    def _connected_components(
        mask: bytearray, width: int, height: int
    ) -> list[tuple[int, int, int, int, int]]:
        components: list[tuple[int, int, int, int, int]] = []
        for start in range(len(mask)):
            if mask[start] != 1:
                continue
            mask[start] = 2
            stack = [start]
            count = 0
            min_x = max_x = start % width
            min_y = max_y = start // width
            while stack:
                current = stack.pop()
                x = current % width
                y = current // width
                count += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for neighbor in (current - 1, current + 1, current - width, current + width):
                    if neighbor < 0 or neighbor >= len(mask) or mask[neighbor] != 1:
                        continue
                    neighbor_x = neighbor % width
                    if abs(neighbor_x - x) > 1:
                        continue
                    mask[neighbor] = 2
                    stack.append(neighbor)
            components.append((count, min_x, min_y, max_x, max_y))
        return components

    def _capture_target_bgra(  # noqa: PLR0911
        self, geometry: WindowGeometry
    ) -> tuple[bytes, int, int, int] | None:
        if self._target_hwnd is None or self._user32 is None or self._gdi32 is None:
            return None
        window_rect = RECT()
        if not self._user32.GetWindowRect(self._target_hwnd, ctypes.byref(window_rect)):
            return None
        bitmap_width = window_rect.right - window_rect.left
        bitmap_height = window_rect.bottom - window_rect.top
        if bitmap_width <= 0 or bitmap_height <= 0:
            return None
        client_offset_x = geometry.left - window_rect.left
        client_offset_y = geometry.top - window_rect.top

        window_dc = self._user32.GetWindowDC(self._target_hwnd)
        if not window_dc:
            return None
        memory_dc = self._gdi32.CreateCompatibleDC(window_dc)
        bitmap = self._gdi32.CreateCompatibleBitmap(window_dc, bitmap_width, bitmap_height)
        if not memory_dc or not bitmap:
            if bitmap:
                self._gdi32.DeleteObject(bitmap)
            if memory_dc:
                self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(self._target_hwnd, window_dc)
            return None
        previous = self._gdi32.SelectObject(memory_dc, bitmap)
        try:
            if not self._user32.PrintWindow(self._target_hwnd, memory_dc, 2):
                return None
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = bitmap_width
            info.bmiHeader.biHeight = -bitmap_height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            buffer = ctypes.create_string_buffer(bitmap_width * bitmap_height * 4)
            copied = self._gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                bitmap_height,
                buffer,
                ctypes.byref(info),
                0,
            )
            if copied != bitmap_height:
                return None
            return buffer.raw, bitmap_width, client_offset_x, client_offset_y
        finally:
            self._gdi32.SelectObject(memory_dc, previous)
            self._gdi32.DeleteObject(bitmap)
            self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(self._target_hwnd, window_dc)

    def normalized_to_screen(self, geometry: WindowGeometry, coord: tuple[float, float]) -> tuple[int, int]:
        scale = min(geometry.width / 16.0, geometry.height / 9.0)
        play_width = 16.0 * scale
        play_height = 9.0 * scale
        offset_x = geometry.left + (geometry.width - play_width) / 2.0
        offset_y = geometry.top + (geometry.height - play_height) / 2.0
        return (
            int(offset_x + coord[0] * scale),
            int(offset_y + coord[1] * scale),
        )

    def operation_still_available(
        self,
        expected_types: tuple[int, ...],
        get_operation_list: OperationListProvider,
    ) -> bool:
        if not expected_types:
            return False
        operation_list = get_operation_list()
        return any(op.get("type") in expected_types for op in operation_list)

    def click_with_retry(  # noqa: PLR0913
        self,
        target: tuple[int, int],
        expected_types: tuple[int, ...],
        get_operation_list: OperationListProvider | None,
        *,
        get_operation_step: OperationStepProvider | None = None,
        expected_step: int | None = None,
        cancel_requested: CancelPredicate,
    ) -> bool:
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            if cancel_requested():
                return False
            logger.info(
                f"Autoplay click attempt {attempt}/{max_attempts} at screen={target} expected_types={expected_types}"
            )
            self.left_click()

            can_verify_step = get_operation_step is not None and expected_step is not None
            can_verify_operations = get_operation_list is not None and bool(expected_types)
            if not can_verify_step and not can_verify_operations:
                logger.info("Autoplay click accepted without state-change verification.")
                return True

            for _ in range(6):
                time.sleep(0.1)
                step_resolved = can_verify_step and get_operation_step() != expected_step
                operation_resolved = (
                    not can_verify_step
                    and can_verify_operations
                    and not self.operation_still_available(expected_types, get_operation_list)
                )
                if step_resolved or operation_resolved:
                    logger.info(f"Autoplay click verified on attempt {attempt}/{max_attempts}.")
                    return True
                if cancel_requested():
                    return False

            if attempt < max_attempts:
                logger.warning(
                    f"Autoplay click attempt {attempt}/{max_attempts} did not clear expected operations; retrying."
                )
                jitter_x = random.randint(-2, 2)
                jitter_y = random.randint(-2, 2)
                self._user32.SetCursorPos(target[0] + jitter_x, target[1] + jitter_y)
                time.sleep(0.015)
                self._user32.SetCursorPos(target[0], target[1])
                time.sleep(0.015)
        logger.warning(f"Autoplay click failed after {max_attempts} attempts at screen={target}.")
        return False

    def _check_window(self, keywords: tuple[str, ...]) -> bool:
        if self._target_hwnd is None or self._user32 is None:
            return False
        if not (self._user32.IsWindow(self._target_hwnd) and self._user32.IsWindowVisible(self._target_hwnd)):
            return False
        title = self._get_window_title(self._target_hwnd).lower()
        return bool(title and any(keyword in title for keyword in keywords))

    def _auto_select_window(self, platform: Platform | str, custom_keyword: str = "") -> WindowObject | None:
        keywords = self._window_keywords(platform, custom_keyword)
        windows = self._get_windows()
        if not windows:
            return None

        for window in windows:
            lowered = window.name.lower()
            if any(keyword in lowered for keyword in keywords):
                self._target_hwnd = window.hwnd
                return window

        return None

    def _get_window_title(self, hwnd: int) -> str:
        if self._user32 is None:
            return ""
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        title = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, title, length + 1)
        return title.value.strip()

    def _window_keywords(self, platform: Platform | str, custom_keyword: str = "") -> tuple[str, ...]:
        raw_custom = [part.strip().lower() for part in custom_keyword.replace("|", ",").split(",") if part.strip()]
        if raw_custom:
            return tuple(raw_custom)

        normalized = platform if isinstance(platform, Platform) else Platform(platform)
        match normalized:
            case Platform.MAJSOUL | Platform.AUTO:
                return ("mahjong soul", "majsoul", "jantama", "\u96c0\u9b42")
            case Platform.TENHOU:
                return ("tenhou", "\u5929\u9cf3")
            case Platform.RIICHI_CITY:
                return ("riichi city", "\u9ebb\u5c06\u4e00\u756a\u8857")
            case Platform.AMATSUKI:
                return ("amatsuki", "\u5929\u6708\u9ebb\u5c06")
        return ("mahjong", "riichi")

    def _get_windows(self) -> list[WindowObject]:
        if self._user32 is None:
            return []

        windows: list[WindowObject] = []
        current_pid = os.getpid()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_windows_proc(hwnd: int, _lparam: int) -> bool:  # noqa: PLR0911
            if not self._user32.IsWindowVisible(hwnd):
                return True
            if self._user32.IsIconic(hwnd):
                return True
            length = self._user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, title, length + 1)
            name = title.value.strip()
            if not name or self._is_ignored_window(name):
                return True
            pid = ctypes.c_ulong()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == current_pid:
                return True
            geometry = self._get_window_geometry(int(hwnd))
            if geometry is None or geometry.width < MIN_TARGET_WIDTH or geometry.height < MIN_TARGET_HEIGHT:
                return True
            windows.append(WindowObject(hwnd=int(hwnd), name=name))
            return True

        self._user32.EnumWindows(enum_windows_proc, 0)
        return windows

    def _get_window_geometry(self, hwnd: int | None) -> WindowGeometry | None:
        if hwnd is None or self._user32 is None:
            return None
        rect = RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        origin = POINT(0, 0)
        if not self._user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        return WindowGeometry(
            left=origin.x,
            top=origin.y,
            width=rect.right - rect.left,
            height=rect.bottom - rect.top,
        )

    def _build_bezier_path(self, start: tuple[int, int], end: tuple[int, int]) -> list[tuple[float, float]]:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        distance = math.hypot(dx, dy)
        if distance < 1:
            return [end]

        steps = max(int(local_settings.autoplay.input.bezier_steps), 10)
        steps = min(42, steps + max(0, int(distance / 18)))
        smoothing = max(0.0, min(local_settings.autoplay.input.bezier_smoothing, 1.0))
        if smoothing <= 0:
            return [
                (
                    start[0] + dx * index / steps,
                    start[1] + dy * index / steps,
                )
                for index in range(1, steps + 1)
            ]

        normal_x = -dy / distance
        normal_y = dx / distance
        bend = distance * 0.16 * smoothing
        control_1 = (
            start[0] + dx * 0.33 + normal_x * bend,
            start[1] + dy * 0.33 + normal_y * bend,
        )
        control_2 = (
            start[0] + dx * 0.66 - normal_x * bend * 0.75,
            start[1] + dy * 0.66 - normal_y * bend * 0.75,
        )

        path: list[tuple[float, float]] = []
        for index in range(1, steps + 1):
            t = index / steps
            omt = 1.0 - t
            x = omt**3 * start[0] + 3 * omt**2 * t * control_1[0] + 3 * omt * t**2 * control_2[0] + t**3 * end[0]
            y = omt**3 * start[1] + 3 * omt**2 * t * control_1[1] + 3 * omt * t**2 * control_2[1] + t**3 * end[1]
            path.append((x, y))
        return path

    def _is_ignored_window(self, name: str) -> bool:
        lowered = name.lower()
        return any(token in lowered for token in ("akagi-ng", "akagi ng", "dashboard", "hud"))
