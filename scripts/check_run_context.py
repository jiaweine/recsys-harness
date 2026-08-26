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


def assert_no_page_overflow(page, label: str) -> None:
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if overflow > 1:
        raise RuntimeError(f"{label} introduced page-level horizontal overflow: {overflow}px")


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
        page.wait_for_function("document.getElementById('taskState').textContent === '可继续追问'", timeout=8_000)
        page.wait_for_function(
            "document.querySelector('#runContextStrip .run-context-state')?.textContent.includes('已完成')",
            timeout=8_000,
        )
        # Run completion precedes the asynchronous history refresh that supplies the persisted timestamp.
        # Wait for the context to reconcile with that persisted history row instead of racing it.
        page.wait_for_function(
            """() => {
              const selectedTime = document.querySelector('#historyList .history-item[aria-current="page"] small');
              const contextTime = document.querySelector('#runContextStrip .run-context-time .run-context-value');
              return Boolean(selectedTime?.textContent.trim() && contextTime?.textContent.trim());
            }""",
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

        # Completed mobile run: keep identity, lifecycle and verification; hide lower-priority detail.
        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(180)
        if page.locator(".main-head").is_visible():
            raise RuntimeError("Mobile QA expected the desktop task header to be hidden")
        if not strip.locator(".run-context-mobile-title").is_visible():
            raise RuntimeError("Mobile Run Context lost the current task title")
        if strip.locator(".run-context-target").is_visible():
            raise RuntimeError("Mobile Run Context should hide the result target to protect title space")
        if strip.locator(".run-context-time").is_visible():
            raise RuntimeError("Mobile Run Context should hide persisted time")
        if strip.locator(".run-context-scene .run-context-value").is_visible():
            raise RuntimeError("Mobile Run Context should keep only the compact scene badge")
        if not strip.locator(".run-context-state").is_visible() or not verification.is_visible():
            raise RuntimeError("Mobile Run Context must preserve lifecycle and verification signals")
        internal_overflow = strip.evaluate("el => el.scrollWidth - el.clientWidth")
        if internal_overflow > 1:
            raise RuntimeError(f"Mobile Run Context still requires horizontal scrolling: {internal_overflow}px")
        assert_no_page_overflow(page, "Completed mobile Run Context")

        # Real trace rows must keep a visible gutter between phase and summary copy.
        page.locator("#inspectorToggle").click()
        page.wait_for_selector("#agentTrace:not([hidden]) .trace-step", timeout=8_000)
        first_step = page.locator("#agentTrace .trace-step").first
        phase_box = first_step.locator(".trace-phase").bounding_box()
        copy_box = first_step.locator(".trace-summary-copy").bounding_box()
        if not phase_box or not copy_box:
            raise RuntimeError("Mobile trace geometry is unavailable")
        phase_gap = copy_box["x"] - (phase_box["x"] + phase_box["width"])
        if phase_gap < 6:
            raise RuntimeError(f"Mobile trace phase/title gutter is too tight: {phase_gap}px")
        assert_no_page_overflow(page, "Mobile trace")
        page.locator("#inspectorClose").click()

        # Return to desktop to navigate the hidden sidebar history item.
        page.set_viewport_size({"width": 1440, "height": 900})
        historical_button = page.locator(f'#historyList .history-item[data-id="{historical["id"]}"]')
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
        if not strip.is_visible():
            raise RuntimeError("Run Context disappeared on the task-first mobile layout")
        mobile_title = strip.locator(".run-context-mobile-title")
        if not mobile_title.is_visible() or HISTORY_TITLE not in mobile_title.inner_text():
            raise RuntimeError("Mobile Run Context did not carry the current task title")
        box = strip.bounding_box()
        if not box or box["height"] < 34:
            raise RuntimeError(f"Mobile Run Context lost its compact identity bar height: {box}")
        if strip.locator(".run-context-time").is_visible():
            raise RuntimeError("Historical mobile Run Context exposed low-priority timestamp text")
        if strip.evaluate("el => el.scrollWidth - el.clientWidth") > 1:
            raise RuntimeError("Historical mobile Run Context requires horizontal scrolling")
        assert_no_page_overflow(page, "Historical mobile Run Context")

        if browser_errors:
            raise RuntimeError("Browser errors during Run Context QA: " + " | ".join(browser_errors))
        browser.close()


if __name__ == "__main__":
    main()
