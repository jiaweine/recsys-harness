from __future__ import annotations

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("RECSYS_ACCESSIBILITY_URL", "http://127.0.0.1:8766")
ACCESS_TOKEN = os.environ.get("LINGJING_ACCESS_TOKEN", "")


def active_id(page) -> str:
    return page.evaluate("document.activeElement?.id || ''")


def main() -> None:
    if len(ACCESS_TOKEN) < 16:
        raise AssertionError("accessibility QA requires the production access token")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(BASE_URL, wait_until="networkidle")

        gate = page.locator("#authGate")
        gate.wait_for(state="visible")
        form = page.locator("#authForm")
        assert form.get_attribute("role") == "dialog"
        assert form.get_attribute("aria-modal") == "true"
        assert form.get_attribute("aria-labelledby") == "authTitle"
        assert form.get_attribute("aria-describedby") == "authDescription"
        assert page.locator(".shell").evaluate("element => element.inert") is True
        page.locator("#authKey").wait_for(state="visible")
        page.wait_for_function("document.activeElement?.id === 'authKey'")

        page.locator("#authKey").focus()
        page.keyboard.press("Shift+Tab")
        assert active_id(page) == "authSubmit"
        page.keyboard.press("Tab")
        assert active_id(page) == "authKey"

        page.locator("#authKey").fill(ACCESS_TOKEN)
        page.locator("#authSubmit").click()
        page.wait_for_load_state("networkidle")
        page.locator("#authGate").wait_for(state="hidden")
        page.wait_for_function("document.querySelector('.shell')?.inert === false")

        first = page.locator("#tab-progress")
        second = page.locator("#tab-evidence")
        last = page.locator("#tab-data")
        first.wait_for(state="visible")
        assert first.get_attribute("aria-controls") == "panel-progress"
        assert page.locator("#panel-progress").get_attribute("role") == "tabpanel"
        assert page.locator("#panel-progress").get_attribute("aria-labelledby") == "tab-progress"
        assert first.get_attribute("tabindex") == "0"
        assert second.get_attribute("tabindex") == "-1"

        first.focus()
        page.keyboard.press("ArrowRight")
        assert active_id(page) == "tab-evidence"
        assert second.get_attribute("aria-selected") == "true"
        assert page.locator("#panel-evidence").get_attribute("aria-hidden") == "false"
        assert page.locator("#panel-progress").get_attribute("aria-hidden") == "true"

        page.keyboard.press("End")
        assert active_id(page) == "tab-data"
        assert last.get_attribute("aria-selected") == "true"
        page.keyboard.press("Home")
        assert active_id(page) == "tab-progress"
        assert first.get_attribute("aria-selected") == "true"

        messages = page.locator("#messageList")
        assert messages.get_attribute("role") == "log"
        assert messages.get_attribute("aria-relevant") == "additions text"
        assert page.locator("#running").get_attribute("aria-atomic") == "true"

        page.set_viewport_size({"width": 600, "height": 900})
        toggle = page.locator("#inspectorToggle")
        toggle.wait_for(state="visible")
        toggle.focus()
        toggle.click()
        inspector = page.locator("#inspector")
        page.wait_for_function("document.getElementById('inspector')?.classList.contains('open')")
        assert inspector.get_attribute("role") == "dialog"
        assert inspector.get_attribute("aria-modal") == "true"
        assert inspector.get_attribute("aria-hidden") == "false"
        page.wait_for_function("document.getElementById('inspector')?.contains(document.activeElement)")
        assert active_id(page) in {"tab-progress", "tab-evidence", "tab-data", "inspectorClose"}

        page.keyboard.press("Escape")
        page.wait_for_function("!document.getElementById('inspector')?.classList.contains('open')")
        assert inspector.get_attribute("aria-hidden") == "true"
        page.wait_for_function("document.activeElement?.id === 'inspectorToggle'")

        browser.close()

    print("accessibility boundaries verified")


if __name__ == "__main__":
    main()
