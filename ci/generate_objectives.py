#!/usr/bin/env python3
"""
Generate kane-cli objectives from requirements using Claude.

Reads requirements/analyzed_requirements.json (acceptance criteria + steps),
calls Claude to write a precise kane-cli objective string per AC,
and writes the result to ci/objectives.json for use by flow1/flow2 pipelines.

Usage:
    python3 ci/generate_objectives.py
    python3 ci/generate_objectives.py --dry-run   # print without writing

Requirements:
    pip install anthropic
    ANTHROPIC_API_KEY env var set
"""
import argparse
import json
import os
import sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_ROOT   = Path(__file__).parent.parent
REQUIREMENTS   = PROJECT_ROOT / "requirements" / "analyzed_requirements.json"
USER_STORIES   = PROJECT_ROOT / "requirements" / "user-stories.md"
OUTPUT_FILE    = Path(__file__).parent / "objectives.json"
BASE_URL       = "https://www.saucedemo.com/"
MODEL          = "claude-sonnet-4-6"


SYSTEM_PROMPT = """\
You are a QA automation expert who writes precise browser test objectives for kane-cli.

kane-cli takes a single natural-language objective string and executes it as a
headless browser test. A good objective:
- Starts with the full URL to navigate to
- Lists the exact UI actions in order (click, fill, select, etc.)
- Ends with a specific, observable assertion (cart badge count, text visible, button label)
- Is concise — one sentence, no bullet points
- Uses concrete details from the site (button labels, field names, visible text)
- Keeps the total number of distinct UI actions to 5 or fewer — kane-cli has a limited step budget

The site under test is www.saucedemo.com. Key UI facts:
- Login page: https://www.saucedemo.com/ — username: 'standard_user', password: 'secret_sauce'
- After login the inventory page https://www.saucedemo.com/inventory.html shows product tiles
- Each product tile has an 'Add to cart' button; after clicking it the button changes to 'Remove'
- Cart badge (top-right nav) shows item count after adding a product
- Cart page: https://www.saucedemo.com/cart.html — click the cart icon to navigate there
- Sort dropdown (top-right of inventory): 'Name (A to Z)', 'Name (Z to A)',
  'Price (low to high)', 'Price (high to low)'
- Cheapest product when sorted low-to-high: 'Sauce Labs Onesie' at $7.99
- Alphabetically first product (A to Z): 'Sauce Labs Backpack'
- Product detail page: click the product name or image on the inventory page
- NO search bar, NO category sidebar, NO add-to-cart modal popups

CRITICAL RULES — violating these causes known test failures:
1. ALWAYS start from https://www.saucedemo.com/ and log in: type username (1 action),
   type password (2), click Login (3). These 3 steps count toward the 5-action budget.
2. Maximum 5 UI actions before the final assertion — kane-cli has a strict step limit.
3. For cart count test: login(3) + click Add to cart(1) = 4 actions → assert badge shows 1.
4. For cart page test: login(3) + add to cart(1) + click cart icon(1) = 5 → assert name/price.
5. For remove test: login(3) + add to cart(1) + click Remove on inventory(1) = 5 → assert badge gone.
6. For sort test: login(3) + open sort dropdown(1) + select option(1) = 5 → assert first product.
7. NEVER assert on a cart total sum — assert on individual product prices only.
"""


def generate_objective(client: anthropic.Anthropic, ac: dict) -> str:
    """Ask Claude to write a kane-cli objective for one acceptance criterion."""
    prompt = f"""Write a single kane-cli objective string for this acceptance criterion.

Acceptance Criterion ID: {ac['id']}
Description: {ac['description']}
Steps hint: {', '.join(ac.get('kane_steps', []))}
One-liner summary: {ac.get('kane_one_liner', '')}

Site base URL: {BASE_URL}

Return ONLY the objective string — no quotes, no explanation, no prefix."""

    message = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print objectives without writing to file")
    parser.add_argument("--ids", nargs="+",
                        help="Only generate for specific AC IDs e.g. AC-001 AC-004")
    parser.add_argument("--limit", type=int, default=5,
                        help="Max number of objectives to generate (default: 5)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    if not REQUIREMENTS.exists():
        print(f"ERROR: {REQUIREMENTS} not found", file=sys.stderr)
        sys.exit(1)

    requirements = json.loads(REQUIREMENTS.read_text())

    if args.ids:
        requirements = [r for r in requirements if r["id"] in args.ids]
        if not requirements:
            print(f"ERROR: none of {args.ids} found in requirements", file=sys.stderr)
            sys.exit(1)

    if not args.ids and args.limit:
        requirements = requirements[:args.limit]

    client = anthropic.Anthropic(api_key=api_key)

    print(f"Generating objectives for {len(requirements)} acceptance criteria...")
    print(f"Model: {MODEL}\n")

    objectives = []
    for i, ac in enumerate(requirements, 1):
        print(f"  [{i}/{len(requirements)}] {ac['id']}: {ac['description'][:60]}...")
        objective = generate_objective(client, ac)
        sc_id = f"SC-{int(ac['id'].split('-')[1]):03d}"
        entry = {
            "id":        sc_id,
            "ac_id":     ac["id"],
            "name":      f"{sc_id}: {ac.get('kane_one_liner', ac['description'][:50])}",
            "objective": objective,
        }
        objectives.append(entry)
        print(f"           → {objective[:100]}...")

    print(f"\nGenerated {len(objectives)} objectives.")

    if args.dry_run:
        print("\n--- objectives.json (dry run) ---")
        print(json.dumps(objectives, indent=2))
        return

    OUTPUT_FILE.write_text(json.dumps(objectives, indent=2))
    print(f"Written to {OUTPUT_FILE}")
    print("\nNext step: run flow1 or flow2 pipeline — they will auto-load objectives.json")


if __name__ == "__main__":
    main()
