"""Verify runtime polling shares one native clone/parse across existing response consumers."""
from __future__ import annotations

import os
import time

from playwright.sync_api import expect, sync_playwright


BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "检查搜索“露营灯”的当前结果，只做复现和证据核对，不改变策略。"


def wait_for_runtime_cache(page, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = page.evaluate("window.XushuRuntimeBus?.snapshot?.() || {}")
        matched = int(last.get("matchedResponses") or 0)
        parsed = int(last.get("parsedResponses") or 0)
        if matched > 0 and parsed == matched:
            return last
        time.sleep(0.05)
    raise RuntimeError(f"Runtime response cache did not settle before timeout: {last}")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.locator("body.ready").wait_for(state="attached", timeout=15_000)
        if not page.evaluate("Boolean(window.XushuRuntimeBus?.snapshot)"):
            raise RuntimeError("Runtime response cache did not initialize before product consumers")

        page.locator("#newTaskBtn").click()
        page.locator('.scene[data-scene="search"]').click()
        page.locator("#input").fill(PROMPT)
        page.locator("#sendBtn").click()
        expect(page.locator("#stateText")).to_have_text("已完成", timeout=30_000)
        page.wait_for_selector("#resultSnapshot:not([hidden])", timeout=10_000)
        page.wait_for_selector("#agentTrace:not([hidden])", timeout=10_000)
        page.wait_for_selector("#runControlPlane:not([hidden])", timeout=10_000)
        page.wait_for_selector("#learningLedger:not([hidden])", state="attached", timeout=10_000)
        stats = wait_for_runtime_cache(page)

        matched = stats["matchedResponses"]
        if stats["parseErrors"] != 0:
            raise RuntimeError(f"Runtime cache saw JSON parse errors: {stats}")
        if stats["parsedResponses"] != matched:
            raise RuntimeError(f"Runtime JSON was not parsed exactly once per matched response: {stats}")
        if stats["sourceClones"] != matched:
            raise RuntimeError(f"Runtime cache created more than one native source clone per response: {stats}")
        if stats["virtualClones"] < matched * 3:
            raise RuntimeError(f"Existing product/trace/control readers did not use virtual clones: {stats}")
        if stats["cloneReads"] != stats["virtualClones"]:
            raise RuntimeError(f"Clone accounting drifted from virtual-clone accounting: {stats}")
        if stats["jsonReads"] < matched * 4:
            raise RuntimeError(f"App plus three UI consumers did not share every parsed payload: {stats}")
        if stats["parsedResponses"] >= stats["jsonReads"]:
            raise RuntimeError(f"Shared cache did not reduce parse work below logical JSON reads: {stats}")

        if browser_errors:
            raise RuntimeError("Browser errors during runtime response-cache QA:\n" + "\n".join(browser_errors))
        browser.close()


if __name__ == "__main__":
    main()
