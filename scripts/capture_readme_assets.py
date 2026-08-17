"""Capture README screenshots from the real product UI.

Run this while the FastAPI app is available at RECSYS_CAPTURE_URL
(default: http://127.0.0.1:8765). The script creates a real conversation,
submits a search experience task through the browser, waits for the Harness
to finish, and captures the workbench, run trace, and evidence views.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "readme-assets"
BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "最近搜索“露营灯”的结果不太准，帮我复现问题并给一个改进方案，但先不要上线。"


def post_json(path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    post_json(
        "/api/conversations",
        {"scene": "search", "title": "露营灯搜索体验复核"},
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=1,
        )
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function(
            "document.getElementById('dataMeta').textContent.includes('内容')",
            timeout=15_000,
        )
        page.wait_for_timeout(350)
        page.screenshot(path=str(ASSET_DIR / "product-workbench.png"))

        page.locator("#input").fill(PROMPT)
        page.locator("#sendBtn").click()
        page.wait_for_function(
            "document.getElementById('stateText').textContent === '已完成'",
            timeout=20_000,
        )
        page.wait_for_timeout(350)
        page.locator("#scrollArea").evaluate("el => { el.scrollTop = 0; }")
        page.screenshot(path=str(ASSET_DIR / "product-run.png"))

        page.locator(".tab[data-tab='evidence']").click()
        page.locator("#scrollArea").evaluate("el => { el.scrollTop = 95; }")
        page.wait_for_timeout(150)
        page.screenshot(path=str(ASSET_DIR / "product-evidence.png"))
        if browser_errors:
            raise RuntimeError("Browser errors during product smoke test: " + " | ".join(browser_errors))
        browser.close()

    for name in ("product-workbench.png", "product-run.png", "product-evidence.png"):
        path = ASSET_DIR / name
        if not path.exists() or path.stat().st_size < 10_000:
            raise RuntimeError(f"Screenshot capture failed: {path}")
        print(f"captured {name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
