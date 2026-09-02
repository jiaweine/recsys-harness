from __future__ import annotations

import json
import os
from urllib import request

from playwright.sync_api import expect, sync_playwright


BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")


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
    unsafe_title = '<img src=x onerror="window.__xushuInjected=1"> 安全标题'
    unsafe = post_json(
        "/api/conversations",
        {"scene": "audit", "title": unsafe_title},
    )
    recommend = post_json(
        "/api/conversations",
        {"scene": "recommend", "title": "推荐首屏历史复核"},
    )
    search = post_json(
        "/api/conversations",
        {"scene": "search", "title": "露营灯搜索当前任务"},
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        browser_errors: list[str] = []
        conversation_requests: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)
        page.on(
            "request",
            lambda req: conversation_requests.append(req.url)
            if "/api/conversations" in req.url
            else None,
        )

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.locator("#dataMeta", has_text="内容").wait_for(state="visible", timeout=15_000)
        current = page.locator('.history-item[aria-current="page"]')
        current.first.wait_for(state="attached", timeout=10_000)
        if current.count() != 1:
            raise RuntimeError("Workspace Switcher QA expected exactly one current history task")
        page.wait_for_timeout(300)

        if current.get_attribute("data-id") != str(search["id"]):
            raise RuntimeError("Workspace Switcher QA did not start on the newest persisted task")

        conversation_requests.clear()
        page.keyboard.press("Control+K")
        page.wait_for_selector("#commandPalette:not([hidden])", timeout=5_000)
        if "工作区导航" not in page.locator("#commandPalette").inner_text():
            raise RuntimeError("Command palette did not identify itself as the workspace switcher")
        if "最近任务" not in page.locator("#commandList").inner_text():
            raise RuntimeError("Workspace Switcher did not expose the recent-task group")

        page.locator("#commandInput").fill("安全标题")
        history_commands = page.locator("#commandList .history-command")
        history_commands.first.wait_for(state="visible", timeout=5_000)
        if history_commands.count() != 1:
            raise RuntimeError("Workspace Switcher filtering did not converge to one history command")
        unsafe_command = page.locator(
            f'#commandList .history-command[data-history-id="{unsafe["id"]}"]'
        )
        if unsafe_command.count() != 1:
            raise RuntimeError("Escaped history title could not be found in Workspace Switcher")
        if unsafe_command.locator("img").count():
            raise RuntimeError("User-provided history title created an unexpected DOM element")
        if "<img" not in unsafe_command.inner_text():
            raise RuntimeError("User-provided markup was not preserved as inert text")
        if page.evaluate("Boolean(window.__xushuInjected)"):
            raise RuntimeError("User-provided history title executed script in Workspace Switcher")

        page.locator("#commandInput").fill("推荐首屏历史复核")
        history_commands.first.wait_for(state="visible", timeout=5_000)
        if history_commands.count() != 1:
            raise RuntimeError("Workspace Switcher recommendation filter did not converge to one command")
        history_command = history_commands.first
        command_text = history_command.inner_text()
        if "推荐首屏历史复核" not in command_text or "推荐" not in command_text:
            raise RuntimeError("History command lost its real task title or scene metadata")
        if conversation_requests:
            raise RuntimeError(
                "Opening/searching Workspace Switcher unexpectedly fetched conversations API: "
                + ", ".join(conversation_requests)
            )

        page.keyboard.press("Enter")
        expect(page.locator("#taskTitle")).to_have_text("推荐首屏历史复核", timeout=10_000)
        page.locator(
            f'.history-item[aria-current="page"][data-id="{recommend["id"]}"]'
        ).wait_for(state="attached", timeout=10_000)
        if page.locator("#commandPalette").is_visible():
            raise RuntimeError("Workspace Switcher stayed open after navigating to a history task")

        page.keyboard.press("Control+K")
        page.locator("#commandInput").fill("工作区")
        page.wait_for_selector('#commandList [data-palette-command="workspace"]', timeout=5_000)
        page.locator("#commandInput").fill("推荐")
        page.wait_for_selector(
            f'#commandList .history-command[data-history-id="{recommend["id"]}"]',
            timeout=5_000,
        )
        page.keyboard.press("Escape")

        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(150)
        mobile_trigger = page.locator("#runCommandMobile")
        if not mobile_trigger.is_visible():
            raise RuntimeError("Mobile workspace navigation disappeared when no completed run exists")
        box = mobile_trigger.bounding_box()
        if not box or box["height"] < 44:
            raise RuntimeError(f"Mobile workspace navigation lost its 44px touch target: {box}")

        mobile_trigger.click()
        page.wait_for_selector("#commandPalette:not([hidden])", timeout=5_000)
        page.locator("#commandInput").fill("露营灯搜索当前任务")
        page.wait_for_selector(
            f'#commandList .history-command[data-history-id="{search["id"]}"]',
            timeout=5_000,
        )
        page.keyboard.press("Enter")
        expect(page.locator("#taskTitle")).to_have_text("露营灯搜索当前任务", timeout=10_000)
        page.locator(
            f'.history-item[aria-current="page"][data-id="{search["id"]}"]'
        ).wait_for(state="attached", timeout=10_000)

        if browser_errors:
            raise RuntimeError("Browser errors during Workspace Switcher QA:\n" + "\n".join(browser_errors))
        browser.close()


if __name__ == "__main__":
    main()
