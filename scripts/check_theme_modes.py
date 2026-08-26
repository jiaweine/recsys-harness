"""Verify regular light and dark workspace themes against the real product."""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")


def rgb(page, selector: str) -> str:
    return page.locator(selector).evaluate("el => getComputedStyle(el).backgroundColor")


def assert_theme(page, theme: str) -> None:
    expected = {
        "light": {
            "body": "rgb(246, 247, 249)",
            ".topbar": "rgb(255, 255, 255)",
            ".sidebar": "rgb(243, 244, 246)",
            ".main": "rgb(255, 255, 255)",
            ".inspector": "rgb(250, 250, 250)",
        },
        "dark": {
            "body": "rgb(9, 9, 11)",
            ".topbar": "rgb(14, 14, 16)",
            ".sidebar": "rgb(13, 13, 15)",
            ".main": "rgb(16, 16, 18)",
            ".inspector": "rgb(13, 13, 15)",
        },
    }[theme]
    actual_theme = page.locator("html").get_attribute("data-theme")
    if actual_theme != theme:
        raise RuntimeError(f"Expected theme {theme!r}, got {actual_theme!r}")
    for selector, expected_color in expected.items():
        actual = rgb(page, selector)
        if actual != expected_color:
            raise RuntimeError(f"{theme} theme {selector} background drifted: {actual} != {expected_color}")
    active = page.locator(f'[data-theme-choice="{theme}"]')
    if active.get_attribute("aria-pressed") != "true":
        raise RuntimeError(f"{theme} theme button is not exposed as selected")
    other = "dark" if theme == "light" else "light"
    if page.locator(f'[data-theme-choice="{other}"]').get_attribute("aria-pressed") != "false":
        raise RuntimeError(f"Inactive {other} theme button incorrectly reports selected")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page = context.new_page()
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        page.evaluate("localStorage.removeItem('xushu-theme')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)

        # A new user gets the regular light workspace, not a near-black canvas.
        assert_theme(page, "light")
        if page.locator('meta[name="theme-color"]').get_attribute("content") != "#f6f7f9":
            raise RuntimeError("Regular theme did not publish its light browser chrome color")

        page.locator('[data-theme-choice="dark"]').click()
        assert_theme(page, "dark")
        if page.locator('meta[name="theme-color"]').get_attribute("content") != "#09090b":
            raise RuntimeError("Dark theme did not publish its Graphite browser chrome color")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        assert_theme(page, "dark")

        # Explicitly switch back and verify persistence in the other direction too.
        page.locator('[data-theme-choice="light"]').click()
        assert_theme(page, "light")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        assert_theme(page, "light")

        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(180)
        picker = page.locator(".theme-picker")
        if not picker.is_visible():
            raise RuntimeError("Theme picker disappeared on mobile")
        for theme in ("light", "dark"):
            button = page.locator(f'[data-theme-choice="{theme}"]')
            box = button.bounding_box()
            if not box or box["height"] < 44 or box["width"] < 44:
                raise RuntimeError(f"Mobile {theme} theme control lost its 44px touch target: {box}")
        page.locator('[data-theme-choice="dark"]').click()
        assert_theme(page, "dark")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Dark theme introduced mobile page overflow: {overflow}px")
        page.locator('[data-theme-choice="light"]').click()
        assert_theme(page, "light")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Regular theme introduced mobile page overflow: {overflow}px")

        if browser_errors:
            raise RuntimeError("Browser errors during theme QA:\n" + "\n".join(browser_errors))
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
