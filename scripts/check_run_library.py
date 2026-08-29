"""Exercise the desktop Run Library against the real running product."""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "检查搜索“露营灯”的当前结果，只做复现和证据核对，不改变策略。"


def current_rows(page):
    return page.locator("#historyList .history-item[aria-current='page']")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        # The real sample task can finish before Playwright observes the transient
        # live row. Keep the first run-poll response in the QA browser briefly so
        # the product's genuine running state is observable without faking a run
        # response or slowing the server itself. The request still reaches the real
        # backend and the unmodified response is delivered after the hold.
        page.add_init_script(
            """
            (() => {
              const nativeFetch = window.fetch.bind(window);
              let holdNextRunPoll = false;
              window.__armRunPollHoldForQa = () => { holdNextRunPoll = true; };
              window.fetch = async (...args) => {
                const response = await nativeFetch(...args);
                const request = args[0];
                const url = typeof request === 'string' ? request : (request?.url || '');
                const method = String(args[1]?.method || request?.method || 'GET').toUpperCase();
                let pathname = '';
                try { pathname = new URL(url, window.location.origin).pathname; } catch {}
                if (holdNextRunPoll && method === 'GET' && /^\\/api\\/runs\\/[^/]+$/.test(pathname)) {
                  holdNextRunPoll = false;
                  await new Promise(resolve => setTimeout(resolve, 1500));
                }
                return response;
              };
            })();
            """
        )

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        page.wait_for_selector("#historyList .history-item .history-scene", timeout=8_000)

        if current_rows(page).count() != 1:
            raise RuntimeError("Run Library must mark exactly one current persisted task on boot")
        current = current_rows(page).first
        if "current" not in (current.get_attribute("class") or ""):
            raise RuntimeError("Current history task is missing the Graphite selected state")
        if current.locator(".history-scene").inner_text().strip() != "搜":
            raise RuntimeError("Current search task lost its scene badge")
        if current.locator(".history-state").inner_text().strip() != "当前":
            raise RuntimeError("Completed current task is not labeled 当前")
        if page.locator("#historyList .history-item[aria-current='false']").count() < 1:
            raise RuntimeError("Run Library did not keep historical tasks distinct from the current task")

        # A new draft is intentionally absent from persisted history. Once execution
        # creates its conversation, the newest row must become the selected live run.
        page.locator("#newTaskBtn").click()
        if current_rows(page).count() != 0:
            raise RuntimeError("Draft mode left a persisted history row marked current")
        page.locator("#input").fill(PROMPT)
        page.evaluate("window.__armRunPollHoldForQa()")
        page.locator("#sendBtn").click()
        page.wait_for_selector("#historyList .history-item.current.running .history-state.live", timeout=8_000)
        live = page.locator("#historyList .history-item.current.running").first
        if live.get_attribute("aria-current") != "page":
            raise RuntimeError("Running task is not the current Run Library selection")
        if live.locator(".history-state").inner_text().strip() != "运行中":
            raise RuntimeError("Active task is not explicitly labeled 运行中")
        if live.locator(".history-scene").inner_text().strip() != "搜":
            raise RuntimeError("Active search task lost its scene badge")

        page.wait_for_function("document.getElementById('stateText').textContent === '已完成'", timeout=30_000)
        page.wait_for_function("""() => {
          const rows = document.querySelectorAll("#historyList .history-item[aria-current='page']");
          return rows.length === 1 && !rows[0].classList.contains('running') && rows[0].querySelector('.history-state')?.textContent === '当前';
        }""", timeout=8_000)

        completed = current_rows(page).first
        completed_id = completed.get_attribute("data-id")
        other = page.locator("#historyList .history-item[aria-current='false']").first
        other_id = other.get_attribute("data-id")
        if not completed_id or not other_id or completed_id == other_id:
            raise RuntimeError("Run Library did not expose a distinct historical task for navigation")

        other.click()
        page.wait_for_function(
            """expected => document.querySelectorAll("#historyList .history-item[aria-current='page']").length === 1 && document.querySelector("#historyList .history-item[aria-current='page']")?.dataset.id === expected""",
            arg=other_id,
            timeout=8_000,
        )
        if page.locator(f"#historyList .history-item[data-id='{completed_id}']").get_attribute("aria-current") != "false":
            raise RuntimeError("Previous current task stayed selected after navigating history")

        if browser_errors:
            raise RuntimeError("Browser errors during Run Library QA: " + " | ".join(browser_errors))
        browser.close()


if __name__ == "__main__":
    main()
