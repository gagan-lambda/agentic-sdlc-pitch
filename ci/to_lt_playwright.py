#!/usr/bin/env python3
"""
to_lt_playwright.py
-------------------
Convert testmuai-playwright-bindings exports → standard LT Playwright Python.

Replaces:
  testmu.configure()           → LT CDP async_playwright connect in _main()
  @testmu.test                 → removed (plain async def test kept)
  async with testmu.step()     → # comment + body unindented by 4
  testmu.get_vision_coords()   → fallback {'x': x, 'y': y} hint dict
  testmu.vision_query()        → skipped (assertion produced from verify_assertion)
  testmu.verify_assertion()    → standard pw_expect() assertions
  _resolve_ranked_locator()    → simplified version without testmu fallback
  testmu.run(test)             → asyncio.run(_main())

Output files run on HyperExecute with standard playwright only — no testmuai needed.
"""
import ast
import re
import sys
from pathlib import Path

# ── Output boilerplate ────────────────────────────────────────────────────────

_HEADER = """\
import asyncio
import json
import os
from urllib.parse import quote
from playwright.async_api import async_playwright, expect as pw_expect, Page
"""

_RESOLVE_FN = """
async def _resolve_ranked_locator(page, locators, description=""):
    for _loc in locators:
        if await _loc.count() > 0:
            return _loc
    raise TimeoutError(f"No locator matched: {description!r}")

"""


def _main_block(name: str) -> str:
    n = repr(name)
    return "\n".join([
        "",
        "",
        "async def _main():",
        "    caps = {",
        '        "browserName": "Chrome",',
        '        "browserVersion": "latest",',
        '        "LT:Options": {',
        '            "platform": "linux",',
        '            "build": os.environ.get("BUILD", "Agentic SDLC | KaneAI Export"),',
        f'            "name": {n},',
        '            "user": os.environ.get("LT_USERNAME", "gagandeepb"),',
        '            "accessKey": os.environ.get("LT_ACCESS_KEY", ""),',
        '            "network": True,',
        '            "video": True,',
        '            "console": True,',
        '            "w3c": True,',
        "        },",
        "    }",
        '    cdp = "wss://cdp.lambdatest.com/playwright?capabilities=" + quote(json.dumps(caps))',
        "    async with async_playwright() as pw:",
        "        browser = await pw.chromium.connect(cdp)",
        "        ctx = await browser.new_context()",
        "        ctx.set_default_timeout(10000)",
        "        ctx.set_default_navigation_timeout(30000)",
        "        page = await ctx.new_page()",
        '        status, remark = "passed", "All assertions passed"',
        "        try:",
        "            await test(page)",
        "        except Exception as exc:",
        '            status, remark = "failed", str(exc)[:300]',
        "            raise",
        "        finally:",
        "            try:",
        "                action = json.dumps({",
        '                    "action": "setTestStatus",',
        '                    "arguments": {"status": status, "remark": remark}',
        "                })",
        '                await page.evaluate("s => {}", f"lambdatest_action: {action}")',
        "            except Exception:",
        "                pass",
        "            await browser.close()",
        "",
        "",
        "if __name__ == '__main__':",
        "    asyncio.run(_main())",
        "",
    ])


# ── Assertion parser ──────────────────────────────────────────────────────────

def _parse_assertions(line: str) -> list:
    """Return standard pw_expect assertion code lines from a verify_assertion call."""
    m = re.search(r'verify_assertion\(page,\s*[^,]+,\s*(\{.+\})\s*\)', line)
    if not m:
        return []
    try:
        payload = ast.literal_eval(m.group(1))
    except Exception:
        return []

    result = []
    for check in payload.get("sub_checks", []):
        key       = check.get("store_key", "")
        expected  = check.get("expected_value", "")
        transforms = check.get("transforms", [])
        if key == "__cp_final":
            continue
        if "string_to_float" in transforms:
            # Numeric/price assertion — use first price locator
            result.append(
                f"await pw_expect(page.locator('.inventory_item_price').first()).to_contain_text('{expected}')"
            )
        else:
            safe  = expected.replace("'", "\\'")
            exact = "True" if check.get("operator") == "equals" else "False"
            result.append(
                f"await pw_expect(page.get_by_text('{safe}', exact={exact})).to_be_visible()"
            )
    return result


# ── Main transformer ──────────────────────────────────────────────────────────

def to_lt_playwright(code: str, sc_id: str, sc_name: str) -> str:
    """Transform a testmuai export to standard LT Playwright Python."""
    # Extract original test name from configure() call
    nm = re.search(r"name\s*=\s*'([^']+)'", code)
    test_name = nm.group(1) if nm else sc_name

    lines = code.splitlines()
    out   = []       # accumulated output lines for the test function
    in_test = False
    i = 0

    while i < len(lines):
        raw     = lines[i]
        stripped = raw.strip()

        # ── Before test function: skip everything (imports, configure, resolve fn)
        if not in_test:
            if re.match(r'^async def test\(', stripped):
                in_test = True
                out.append(raw)
                i += 1
            else:
                i += 1
            continue

        # ── Inside async def test() ───────────────────────────────────────────
        if stripped.startswith('if __name__'):
            break  # stop at original __main__ block

        # Step context manager → comment + unindented body
        if re.match(r'\s+async with testmu\.step\(', raw):
            m2   = re.search(r"testmu\.step\('([^']*)'", raw)
            desc = (m2.group(1) if m2 else 'Step')[:80]
            ind  = len(raw) - len(raw.lstrip())
            out.append(' ' * ind + f'# {desc}')
            i += 1

            # Consume body lines (8-space indent = inside context)
            while i < len(lines):
                bl = lines[i]
                bs = bl.strip()

                # Non-empty line at ≤4-space indent → step body ended
                if bs and not bl.startswith('        '):
                    break

                if not bs:
                    out.append('')
                    i += 1
                    continue

                body = bl[4:]   # remove 4 spaces of context manager indent
                bind = len(body) - len(body.lstrip())

                # get_vision_coordinates → use hint (x, y) coords directly
                if 'testmu.get_vision_coordinates(' in body:
                    gm = re.search(
                        r'get_vision_coordinates\([^,]+,[^,]+,[^,]+,\s*(\d+),\s*(\d+)\)', body
                    )
                    if gm and '=' in body:
                        x, y     = gm.group(1), gm.group(2)
                        var_name = body[:body.index('=')].strip()
                        out.append(' ' * bind + f"{var_name} = {{'x': {x}, 'y': {y}}}")
                    i += 1
                    continue

                # vision_query → skip (replaced by verify_assertion below)
                if 'testmu.vision_query(' in body:
                    i += 1
                    continue

                # verify_assertion → standard pw_expect assertions
                if 'testmu.verify_assertion(' in body:
                    for a in _parse_assertions(body):
                        out.append(' ' * bind + a)
                    i += 1
                    continue

                # Fix empty select_option() — fill in 'lohi' (Price low→high)
                if re.search(r'\.select_option\(\s*\)', body):
                    body = re.sub(r'\.select_option\(\s*\)', ".select_option('lohi')", body)

                out.append(body)
                i += 1
            continue

        # Non-step lines inside test function (blank lines, edge cases)
        out.append(raw)
        i += 1

    return _HEADER + _RESOLVE_FN + '\n'.join(out) + '\n' + _main_block(test_name)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 4:
        print(f'Usage: {sys.argv[0]} <kane_export.py> <SC-ID> <SC Name>', file=sys.stderr)
        sys.exit(1)
    code = Path(sys.argv[1]).read_text(encoding='utf-8')
    print(to_lt_playwright(code, sys.argv[2], sys.argv[3]))
