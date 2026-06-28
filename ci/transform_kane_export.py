#!/usr/bin/env python3
"""
Patch kane-cli Python export for native testmuai-playwright-bindings execution on HyperExecute.

Kane exports use:
  - testmu.configure() for setup
  - @testmu.test async def test(page)
  - async with testmu.step(...) blocks
  - testmu.get_vision_coordinates() for vision clicks  ← AI visual element location
  - testmu.vision_query() / testmu.verify_assertion()  ← AI visual assertions

All of the above are PRESERVED — we only patch testmu.configure() with the
correct build name and SC name. testmuai-playwright-bindings handles browser
connection to LT cloud via TESTMU_RUN_TARGET=cloud + HYE_HUB env vars.
"""
import re
import sys

# Match the full testmu.configure(...) call — may span multiple lines
_CONFIGURE_RE = re.compile(r'testmu\.configure\(.*?\)', re.DOTALL)


def transform(kane_code: str, sc_id: str, sc_name: str) -> str:
    """
    Minimal patch: update testmu.configure() with SC name and build from env.
    All vision bindings (vision_query, verify_assertion, get_vision_coordinates)
    are preserved so AI visual assertions run natively on HyperExecute.
    """
    new_configure = (
        "testmu.configure(\n"
        "    build=os.environ.get('BUILD', 'Agentic SDLC | KaneAI Export'),\n"
        f"    name={repr(sc_name)},\n"
        "    network=True,\n"
        "    video=True,\n"
        "    variables={'__cp_final': 'true'},\n"
        "    default_action_timeout_ms=10000,\n"
        "    default_navigation_timeout_ms=30000,\n"
        ")"
    )

    code = _CONFIGURE_RE.sub(new_configure, kane_code, count=1)

    # Ensure `import os` is present (needed for os.environ.get in configure)
    if 'import os' not in code:
        code = code.replace('import testmu', 'import os\nimport testmu', 1)

    return code


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print(f'Usage: {sys.argv[0]} <kane_export.py> <SC-ID> <SC Name>', file=sys.stderr)
        sys.exit(1)
    code = open(sys.argv[1]).read()
    print(transform(code, sys.argv[2], sys.argv[3]))
