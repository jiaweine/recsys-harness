"""Verify Graphite Charcoal keeps visible depth between product surfaces."""
from __future__ import annotations

import os
import re

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "检查搜索“露营灯”的当前结果，只做复现和证据核对，不改变策略。"


def rgb(value: str) -> tuple[int, int, int]:
    match = re.search(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", value)
    if not match:
        raise RuntimeError(f"Could not parse computed color: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(value: str) -> float:
    r, g, b = rgb(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def bg(page, selector: str) -> str:
    return page.locator(selector).first.evaluate("el => getComputedStyle(el).backgroundColor")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)

        page.locator("#newTaskBtn").click()
        page.locator('.scene[data-scene="search"]').click()
        page.locator("#input").fill(PROMPT)
        page.locator("#sendBtn").click()
        page.wait_for_function("document.getElementById('stateText').textContent === '已完成'", timeout=30_000)
        page.wait_for_selector("#resultAnalysis:not([hidden]) .result-analysis-block", timeout=10_000)

        chrome = bg(page, ".sidebar")
        workspace = bg(page, ".main")
        panel = bg(page, ".result-analysis-block")
        panel_head = bg(page, ".analysis-head")
        composer = bg(page, ".composer")
        inspector = bg(page, ".inspector")

        levels = {
            "chrome": (chrome, luminance(chrome)),
            "workspace": (workspace, luminance(workspace)),
            "panel": (panel, luminance(panel)),
            "panel_head": (panel_head, luminance(panel_head)),
            "composer": (composer, luminance(composer)),
            "inspector": (inspector, luminance(inspector)),
        }

        if not levels["chrome"][1] < levels["workspace"][1] < levels["panel"][1] < levels["panel_head"][1]:
            raise RuntimeError(f"Graphite surface ladder collapsed: {levels}")
        if contrast(chrome, workspace) < 1.035:
            raise RuntimeError(f"Chrome and workspace are visually indistinguishable: {levels}")
        if contrast(workspace, panel) < 1.07:
            raise RuntimeError(f"Workspace and result panel are too close: {levels}")
        if contrast(panel, panel_head) < 1.06:
            raise RuntimeError(f"Panel and panel header are too close: {levels}")
        if contrast(workspace, composer) < 1.11:
            raise RuntimeError(f"Composer does not lift enough from workspace: {levels}")
        if abs(luminance(inspector) - luminance(chrome)) > 0.004:
            raise RuntimeError(f"Inspector no longer reads as product chrome: {levels}")

        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(180)
        mobile_workspace = bg(page, ".main")
        mobile_composer = bg(page, ".composer")
        if contrast(mobile_workspace, mobile_composer) < 1.1:
            raise RuntimeError(
                f"Mobile composer collapsed into workspace: main={mobile_workspace}, composer={mobile_composer}"
            )
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Graphite Charcoal introduced mobile overflow: {overflow}px")

        browser.close()


if __name__ == "__main__":
    main()
