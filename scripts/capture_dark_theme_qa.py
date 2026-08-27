"""Capture representative dark-theme product states for visual QA only."""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "readme-assets"
BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "检查搜索“露营灯”的当前结果，复现问题、核对证据并探索一个可验证方向，但不要改变当前策略。"
DARK_ASSETS = ("qa-desktop-dark.png", "qa-mobile-dark-evidence.png")


def luma(page, selector: str) -> float:
    return float(page.locator(selector).evaluate("""el => {
      const values = (getComputedStyle(el).backgroundColor.match(/\d+/g) || []).slice(0, 3).map(Number);
      const [r = 0, g = 0, b = 0] = values;
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }"""))


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)

        # Build a real completed search state instead of capturing an empty shell.
        page.locator("#newTaskBtn").click()
        page.locator('.scene[data-scene="search"]').click()
        page.locator("#input").fill(PROMPT)
        page.locator("#sendBtn").click()
        page.wait_for_function("document.getElementById('stateText').textContent === '已完成'", timeout=30_000)
        for selector in (
            "#resultSnapshot:not([hidden])",
            "#resultAnalysis:not([hidden])",
            "#verificationSummary:not([hidden])",
            "#agentTrace:not([hidden])",
        ):
            page.wait_for_selector(selector, state="attached", timeout=8_000)

        page.locator('[data-theme-choice="dark"]').click()
        page.wait_for_function("document.documentElement.dataset.theme === 'dark'", timeout=3_000)

        # Dark must remain layered Graphite rather than one undifferentiated black slab.
        desktop_luma = {
            selector: luma(page, selector)
            for selector in ("body", ".topbar", ".sidebar", ".main", ".inspector")
        }
        if max(desktop_luma.values()) - min(desktop_luma.values()) < 4:
            raise RuntimeError(f"Dark desktop surfaces collapsed into one tonal slab: {desktop_luma}")

        page.locator("#inspectorToggle").click()
        page.locator('.tab[data-tab="evidence"]').click()
        page.wait_for_selector("#evidenceList .evidence-item", state="attached", timeout=5_000)
        page.locator("#resultAnalysis").evaluate("el => el.scrollIntoView({block:'center'})")
        page.wait_for_timeout(240)
        page.screenshot(path=str(ASSET_DIR / "qa-desktop-dark.png"), full_page=False)

        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(220)
        if not page.locator("#inspector").evaluate("el => el.classList.contains('open')"):
            page.locator("#inspectorToggle").click()
            page.wait_for_selector("#inspector.open", timeout=5_000)
        page.locator('.tab[data-tab="evidence"]').click()
        page.wait_for_timeout(160)

        sheet = page.locator("#inspector").bounding_box()
        if not sheet or sheet["height"] >= 852 * 0.86 or sheet["y"] <= 852 * 0.10:
            raise RuntimeError(f"Dark mobile Evidence sheet lost task-context geometry: {sheet}")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Dark mobile theme introduced horizontal overflow: {overflow}px")
        if luma(page, "#inspector .inspector-body") > 70:
            raise RuntimeError("Dark mobile Evidence sheet escaped the Graphite surface range")

        page.screenshot(path=str(ASSET_DIR / "qa-mobile-dark-evidence.png"), full_page=False)

        if browser_errors:
            raise RuntimeError("Browser errors during dark-theme visual QA:\n" + "\n".join(browser_errors))
        browser.close()

    for name in DARK_ASSETS:
        path = ASSET_DIR / name
        if not path.exists() or path.stat().st_size < 10_000:
            raise RuntimeError(f"Dark-theme visual capture failed: {path}")
        print(f"captured {name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
