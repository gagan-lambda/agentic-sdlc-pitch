#!/usr/bin/env python3
"""
Autonomous self-healing for the agentic SDLC pipeline.

How it works:
  1. After every pipeline run, results are written to ci/run_history.json
     (keyed by SC ID → last status + failure detail + objective used)
  2. At the START of the next pipeline run, call heal_objectives():
     - For each SC that failed last time, ask Claude to rewrite the objective
       using the failure detail as context
     - Updated objectives are written back to ci/objectives.json
     - The pipeline then runs with the healed objectives automatically

Usage (called from flow1/flow2 pipelines):
    from self_heal import load_history, save_history, heal_objectives

    # At start of run:
    history = load_history()
    healed = heal_objectives(history, log)   # rewrites objectives.json if needed

    # After Phase 1:
    save_history(kane_results)
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HISTORY_FILE   = Path(__file__).parent / "run_history.json"
OBJECTIVES_FILE = Path(__file__).parent / "objectives.json"
MODEL          = "claude-sonnet-4-6"

SYSTEM_PROMPT = """\
You are a QA automation expert fixing failing browser test objectives for kane-cli.

kane-cli executes natural-language objectives as headless browser tests on automationexercise.com.
A previous objective failed — you must rewrite it to be more robust.

Key facts about automationexercise.com:
- Products page: https://automationexercise.com/products
- Each product card has hover-reveal "Add to cart" button and "View Product" link
- After adding to cart a modal appears with "Continue Shopping" or "View Cart" buttons
- Cart page: https://automationexercise.com/view_cart
- When cart is emptied the page shows "Cart is empty!" text (NOT a $0 total)
- Category sidebar: Women > Tops/Dress/Saree, Men > Tshirts/Jeans, Kids > Dress/Tops etc.
- Search bar is in the top navbar — type and press Enter

CRITICAL RULES — these patterns cause known failures, do not reproduce them:
1. NEVER use "Continue Shopping" — it causes the agent to add a second product before going
   to the cart. Always navigate to the cart by clicking "View Cart" in the modal.
2. NEVER assert on a cart grand total or price sum ($X total) — page shows per-item prices only.
3. For cart verification: add ONE product → click "View Cart" in modal → assert on cart page.
4. For cart counter: add product from the product detail page → assert cart icon shows count 1.
5. Maximum 5 UI actions before the final assertion — kane-cli has a limited step budget.
6. One sentence only, starts with the full URL, ends with a specific assertion.
"""


def load_history() -> dict:
    """Load run history. Returns {} if no history exists."""
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return {}


def save_history(kane_results: list, flow: str = "flow"):
    """Persist Phase 1 results to run_history.json."""
    history = load_history()
    for r in kane_results:
        sc_id = r.get("sc_id") or r.get("id", "unknown")
        history[sc_id] = {
            "flow":       flow,
            "status":     r.get("status"),
            "objective":  r.get("objective", ""),
            "failure_detail": r.get("failure_detail", ""),
            "updated_at": datetime.now().isoformat(),
        }
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def heal_objectives(history: dict, log=None) -> int:
    """
    For each SC that failed last run, ask Claude to rewrite the objective.
    Updates ci/objectives.json in place.
    Returns number of objectives healed.
    """
    failed = {sc_id: info for sc_id, info in history.items()
              if info.get("status") not in ("passed", None) or info.get("status") is None}

    if not failed:
        if log:
            log.info("[self-heal] No failures in history — nothing to heal")
        return 0

    if not OBJECTIVES_FILE.exists():
        if log:
            log.warning("[self-heal] objectives.json not found — skipping heal")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        if log:
            log.warning("[self-heal] ANTHROPIC_API_KEY not set — skipping autonomous heal")
        return 0

    try:
        import anthropic
    except ImportError:
        if log:
            log.warning("[self-heal] anthropic package not installed — skipping heal")
        return 0

    client   = anthropic.Anthropic(api_key=api_key)
    objectives = json.loads(OBJECTIVES_FILE.read_text())
    obj_map  = {o["id"]: o for o in objectives}
    healed   = 0

    for sc_id, info in failed.items():
        if sc_id not in obj_map:
            continue

        old_objective  = obj_map[sc_id].get("objective", "")
        failure_detail = info.get("failure_detail", "No detail captured")

        # Extract the run_end narrative summary if it was embedded by run_kane()
        run_summary = ""
        if "[run summary]:" in failure_detail:
            parts = failure_detail.split("\n[raw tail]:", 1)
            run_summary = parts[0].replace("[run summary]:", "").strip()

        if log:
            log.warning(f"[self-heal] {sc_id} failed last run — asking Claude to rewrite objective")

        prompt = f"""The following kane-cli objective failed on the previous pipeline run.

SC ID: {sc_id}
Failed objective:
{old_objective}

What kane-cli actually did (run summary):
{run_summary if run_summary else '(not available)'}

Raw failure detail:
{failure_detail[-600:] if len(failure_detail) > 600 else failure_detail}

Rewrite the objective to fix the issue. Apply the critical rules from your system prompt.
Return ONLY the new objective string — no quotes, no explanation."""

        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            new_objective = msg.content[0].text.strip()
            obj_map[sc_id]["objective"] = new_objective
            obj_map[sc_id]["healed_from"] = old_objective
            healed += 1

            if log:
                log.info(f"[self-heal] {sc_id} → {new_objective[:100]}...")
        except Exception as e:
            if log:
                log.error(f"[self-heal] {sc_id} Claude call failed: {e}")

    if healed:
        OBJECTIVES_FILE.write_text(json.dumps(list(obj_map.values()), indent=2))
        if log:
            log.info(f"[self-heal] Healed {healed} objective(s) → objectives.json updated")

    return healed
