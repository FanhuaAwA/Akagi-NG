"""Sample the actual lobby-chrome colors used by AutoJoin readiness anchors.

This script is read-only: it captures the Mahjong Soul window and prints the
mean RGB at every normalized 16:9 position listed in LOBBY_ANCHORS, so the
hard-coded anchor colors can be recalibrated after a client UI update.

Run it while the Mahjong Soul lobby is visible, then paste the output into a
bug report. The script never clicks or moves the mouse.
"""

from __future__ import annotations

import ctypes
import sys
import time

from akagi_ng.autoplay.executor import (
    LOBBY_ANCHORS,
    LOBBY_ANCHOR_SAMPLE_GRID,
    WindowsInputExecutor,
)
from akagi_ng.schema.constants import Platform


def sample_window(executor: WindowsInputExecutor, platform: Platform) -> None:
    if not executor.ensure_target_window(platform, ""):
        print("ERROR: no Mahjong Soul window found.", file=sys.stderr)
        return
    geometry = executor.get_target_geometry()
    if geometry is None:
        print("ERROR: target window geometry is invalid.", file=sys.stderr)
        return
    print(
        f"window geometry: left={geometry.left} top={geometry.top} width={geometry.width} height={geometry.height}"
    )
    capture = executor._capture_target_bgra(geometry)
    if capture is None:
        print("ERROR: could not capture the target window.", file=sys.stderr)
        return
    pixels, bitmap_width, client_offset_x, client_offset_y = capture

    scale = min(geometry.width / 16.0, geometry.height / 9.0)
    play_width = 16.0 * scale
    play_height = 9.0 * scale
    play_left = client_offset_x + (geometry.width - play_width) / 2.0
    play_top = client_offset_y + (geometry.height - play_height) / 2.0

    print(f"scale={scale:.2f} play_left={play_left:.1f} play_top={play_top:.1f}")
    print("normalized(x,y) -> sampled mean RGB (tolerance 38):")
    for x, y, width, height, expected_r, expected_g, expected_b in LOBBY_ANCHORS:
        left = play_left + (x - width / 2.0) * scale
        top = play_top + (y - height / 2.0) * scale
        patch_width = max(1.0, width * scale)
        patch_height = max(1.0, height * scale)
        red = green = blue = 0
        samples = 0
        for sample_y in range(LOBBY_ANCHOR_SAMPLE_GRID):
            pixel_y = int(
                top + (sample_y + 0.5) * patch_height / LOBBY_ANCHOR_SAMPLE_GRID
            )
            for sample_x in range(LOBBY_ANCHOR_SAMPLE_GRID):
                pixel_x = int(
                    left + (sample_x + 0.5) * patch_width / LOBBY_ANCHOR_SAMPLE_GRID
                )
                if pixel_x < 0 or pixel_y < 0:
                    continue
                offset = (pixel_y * bitmap_width + pixel_x) * 4
                if offset + 3 >= len(pixels):
                    continue
                blue += pixels[offset]
                green += pixels[offset + 1]
                red += pixels[offset + 2]
                samples += 1
        avg_r = red / samples if samples else 0
        avg_g = green / samples if samples else 0
        avg_b = blue / samples if samples else 0
        match = (
            abs(avg_r - expected_r) <= 38
            and abs(avg_g - expected_g) <= 38
            and abs(avg_b - expected_b) <= 38
        )
        print(
            f"  ({x}, {y}) expected=({expected_r},{expected_g},{expected_b}) "
            f"actual=({avg_r:.0f},{avg_g:.0f},{avg_b:.0f}) {'MATCH' if match else 'MISMATCH'}"
        )


def main() -> int:
    if hasattr(ctypes, "windll"):
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                pass

    executor = WindowsInputExecutor()
    if not executor.available:
        print("ERROR: Windows input executor is unavailable.", file=sys.stderr)
        return 1
    platform = Platform.MAJSOUL
    print("Make sure the Mahjong Soul lobby is visible and bring the window to front.")
    for remaining in range(5, 0, -1):
        print(f"{remaining}...", end=" ", flush=True)
        time.sleep(1)
    print()
    sample_window(executor, platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
