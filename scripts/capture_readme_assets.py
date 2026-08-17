"""Capture README screenshots from the real product UI and smoke-test responsive interaction."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "readme-assets"
BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "最近搜索“露营灯”的结果不太准，结合附件帮我复现、诊断并探索一个可验证的改进方向，但先不要改变当前策略。"


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
    post_json("/api/conversations", {"scene": "search", "title": "露营灯搜索体验复核"})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        browser_errors: list[str] = []
        bad_responses: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)
        page.on(
            "response",
            lambda response: bad_responses.append(f"{response.status} {response.url}")
            if response.status >= 400 and response.url.startswith(BASE_URL)
            else None,
        )
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.getElementById('dataMeta').textContent.includes('内容')", timeout=15_000)

        page.locator("#fileInput").set_input_files({
            "name": "search-context.json",
            "mimeType": "application/json",
            "buffer": json.dumps({"query": "露营灯", "note": "首屏结果相关性需要复核"}, ensure_ascii=False).encode("utf-8"),
        })
        page.wait_for_selector(".attachment-card", timeout=10_000)
        page.wait_for_timeout(650)
        page.screenshot(path=str(ASSET_DIR / "product-workbench.png"))

        if page.locator(".history-item").count() and page.locator(".history-item").first.evaluate("el => el.tagName") != "BUTTON":
            raise RuntimeError("History items must be keyboard-native buttons")

        page.evaluate("""() => {
          const original = window.fetch.bind(window);
          let failed = false;
          window.fetch = (input, init) => {
            const url = typeof input === 'string' ? input : String(input?.url || '');
            if (!failed && url.includes('/api/runs/')) {
              failed = true;
              return Promise.reject(new TypeError('simulated transient run polling failure'));
            }
            return original(input, init);
          };
        }""")

        page.locator("#input").fill(PROMPT)
        page.locator("#sendBtn").click()
        page.wait_for_function("document.getElementById('stateText').textContent === '已完成'", timeout=30_000)
        page.wait_for_timeout(250)
        page.locator("#scrollArea").evaluate("el => { el.scrollTop = 0; }")
        page.screenshot(path=str(ASSET_DIR / "product-run.png"))

        page.locator(".tab[data-tab='evidence']").click()
        page.wait_for_timeout(120)
        page.screenshot(path=str(ASSET_DIR / "product-evidence.png"))

        page.set_viewport_size({"width": 430, "height": 900})
        page.wait_for_timeout(150)
        page.locator("#inspectorToggle").click()
        page.wait_for_selector("#inspector.open", timeout=5_000)
        page.locator(".tab[data-tab='evidence']").click()
        page.wait_for_timeout(150)
        page.screenshot(path=str(ASSET_DIR / "product-mobile.png"), full_page=False)

        for selector in ("#sendBtn", "#attachBtn", "#networkBtn", "#newTaskBtn", "#inspectorToggle", "#inspectorClose"):
            locator = page.locator(selector)
            if not locator.is_visible():
                continue
            box = locator.bounding_box()
            if not box or box["height"] < 44 or box["width"] < 44:
                raise RuntimeError(f"Touch target too small: {selector} -> {box}")

        if browser_errors:
            raise RuntimeError("Browser errors during product smoke test: " + " | ".join(browser_errors))
        if bad_responses:
            raise RuntimeError("Same-origin HTTP failures during product smoke test: " + " | ".join(bad_responses))
        browser.close()

    for name in ("product-workbench.png", "product-run.png", "product-evidence.png", "product-mobile.png"):
        path = ASSET_DIR / name
        if not path.exists() or path.stat().st_size < 10_000:
            raise RuntimeError(f"Screenshot capture failed: {path}")
        print(f"captured {name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
