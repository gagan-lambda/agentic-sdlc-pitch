"""
Playwright conftest — connects to LambdaTest CDP for cloud execution,
falls back to local Chromium when LT credentials are absent.
Browser is driven by the BROWSER env var set per matrix lane in HyperExecute.
"""
import json
import os
import re
from urllib.parse import quote

import pytest
from playwright.sync_api import sync_playwright

LT_USERNAME   = os.environ.get("LT_USERNAME", "")
LT_ACCESS_KEY = os.environ.get("LT_ACCESS_KEY", "")
BROWSER       = os.environ.get("BROWSER", "chrome")
BUILD_NAME    = f"Agentic SDLC | Run {os.environ.get('GITHUB_RUN_NUMBER','local')}"
TARGET_URL    = os.environ.get("TARGET_URL", "https://ecommerce-playground.lambdatest.io/")

_BROWSER_CAPS = {
    "chrome":  {"browserName": "chrome",  "platform": "Windows 11"},
    "firefox": {"browserName": "firefox", "platform": "Windows 11"},
    "safari":  {"browserName": "safari",  "platform": "macOS Sequoia"},
    "android": {"browserName": "chrome",  "platformName": "android", "deviceName": "Pixel 7", "platformVersion": "13", "isRealMobile": True},
}


def _lt_cdp_url(test_name: str) -> str:
    caps = {
        **_BROWSER_CAPS.get(BROWSER, {"browserName": "chrome", "platform": "Windows 11"}),
        "build":     BUILD_NAME,
        "name":      test_name,
        "video":     True,
        "network":   True,
        "console":   True,
        "user":      LT_USERNAME,
        "accessKey": LT_ACCESS_KEY,
    }
    return f"wss://cdp.lambdatest.com/playwright?capabilities={quote(json.dumps(caps))}"


@pytest.fixture()
def page(request):
    test_name = re.sub(r"[^\w\s-]", "", request.node.name)[:80]
    with sync_playwright() as p:
        if LT_USERNAME and LT_ACCESS_KEY:
            browser = p.chromium.connect(_lt_cdp_url(test_name), timeout=120_000)
            ctx = browser.new_context()
            pg  = ctx.new_page()
            yield pg
            status = "failed" if hasattr(request.node, "rep_call") and request.node.rep_call.failed else "passed"
            pg.evaluate(f"_ => {{}}", f"lambdatest_action: {{\"action\": \"setTestStatus\", \"arguments\": {{\"status\": \"{status}\"}}}}")
            ctx.close()
            browser.close()
        else:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            pg  = ctx.new_page()
            yield pg
            ctx.close()
            browser.close()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
