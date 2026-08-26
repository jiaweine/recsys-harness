"""Exercise the Run Context Strip against the real running product."""
from __future__ import annotations

import json
import os
from urllib import request

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
HISTORY_TITLE = "推荐上下文复核"
PROMPT = "检查搜索“露营灯”的当前结果，只做复现和证据核对，不改变策略。"


def post_json(path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    historical = post_json(
        "/api/conversations",
        {"scene": "recommend", "title": HISTORY_TITLE},
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        page.wait_for_selector("#runContextStrip .run-context-scene", timeout=8_000)
        page.wait_for_function(
            f"document.querySelector('#historyList .history-item[aria-current=\"page\"]')?.dataset.id === {json.dumps(str(historical['id']))}",
            timeout=8_000,
        )

        strip = page.locator("#runContextStrip")
        if "推荐" not in strip.locator(".run-context-scene").inner_text():
            raise RuntimeError("Run Context did not reflect the current recommendation scene")
        if page.locator(".run-context-target").count():
            raise RuntimeError("Run Context invented a target for a history task without real result analysis")

        page.locator("#newTaskBtn").click()
        page.locator('.scene[data-scene="search"]').click()
        page.wait_for_function(
            "document.querySelector('#runContextStrip .run-context-scene')?.textContent.includes('搜索')",
            timeout=5_000,
        )
        if page.locator("#runContextStrip .run-context-time").count():
            raise RuntimeError("Draft Run Context kept a persisted history timestamp")

        page.locator("#input").fill(PROMPT)
        page.locator("#sendBtn").click()
        page.wait_for_selector('#runContextStrip .run-context-state[data-tone="live"]', timeout=8_000)
        page.wait_for_function("document.getElementById('stateText').textContent === '已完成'", timeout=30_000)
        page.wait_for_function(
            """() => {
              const target = document.querySelector('#runContextStrip .run-context-target .run-context-value');
              return target && target.textContent.includes('露营灯');
            }""",
            timeout=8_000,
        )
        page.wait_for_function(
            "document.getElementById('taskState').textContent === '可继续追问'",
            timeout=8_000,
        )
        page.wait_for_function(
            "document.querySelector('#runContextStrip .run-context-state')?.textContent.includes('已完成')",
            timeout=8_000,
        )

        if "已完成" not in strip.locator(".run-context-state").inner_text():
            raise RuntimeError("Run Context must show execution completion instead of the follow-up affordance")
        if "可继续追问" not in page.locator("#taskState").inner_text():
            raise RuntimeError("Task header no longer exposes the follow-up affordance after completion")
        if strip.locator(".run-context-time").count() != 1:
            raise RuntimeError("Persisted completed task did not expose its update time in Run Context")
        verification = strip.locator(".run-context-verification")
        if verification.count() != 1 or verification.inner_text().strip() not in {"验证通过", "待复核"}:
            raise RuntimeError("Run Context did not mirror the real Snapshot verification state")

        historical_button = page.locator(
            f'#historyList .history-item[data-id="{historical["id"]}"]'
        )
        historical_button.click()
        page.wait_for_function(
            f"document.getElementById('taskTitle').textContent === {json.dumps(HISTORY_TITLE, ensure_ascii=False)}",
            timeout=8_000,
        )
        page.wait_for_function(
            "document.querySelector('#runContextStrip .run-context-scene')?.textContent.includes('推荐')",
            timeout=5_000,
        )
        if page.locator("#runContextStrip .run-context-target").count():
            raise RuntimeError("Historical navigation left the previous search target in Run Context")

        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(180)
        if page.locator(".main-head").is_visible():
            raise RuntimeError("Mobile QA expected the desktop task header to be hidden")
        if not strip.is_visible():
            raise RuntimeError("Run Context disappeared on the task-first mobile layout")
        mobile_title = strip.locator(".run-context-mobile-title")
        if not mobile_title.is_visible() or HISTORY_TITLE not in mobile_title.inner_text():
            raise RuntimeError("Mobile Run Context did not carry the current task title")
        box = strip.bounding_box()
        if not box or box["height"] < 34:
            raise RuntimeError(f"Mobile Run Context lost its compact identity bar height: {box}")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Run Context introduced page-level horizontal overflow: {overflow}px")

        if browser_errors:
            raise RuntimeError("Browser errors during Run Context QA: " + " | ".join(browser_errors))
        browser.close()


if __name__ == "__main__":
    main()
