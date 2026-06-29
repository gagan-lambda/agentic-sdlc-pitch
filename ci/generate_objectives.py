#!/usr/bin/env python3
"""
Generate kane-cli run objectives using kane-cli generate (KaneAI test case generator).

kane-cli generate produces structured test cases (scenarios + steps) from a natural-language
prompt and optional requirements file. This script calls it in --agent mode, parses the
generate_snapshot NDJSON event, converts each test case's kane_steps into a single
objective string for kane-cli run, and writes ci/objectives.json.

Usage:
    python3 ci/generate_objectives.py
    python3 ci/generate_objectives.py --url https://myapp.com
    python3 ci/generate_objectives.py --url https://myapp.com --requirements /path/to/reqs.json
    python3 ci/generate_objectives.py --url https://myapp.com --requirements https://host/reqs.json
    python3 ci/generate_objectives.py --limit 3 --dry-run
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT     = Path(__file__).parent.parent
DEFAULT_REQS     = PROJECT_ROOT / "scenarios" / "scenarios.json"
OUTPUT_FILE      = Path(__file__).parent / "objectives.json"
DEFAULT_URL      = "https://www.saucedemo.com/"
KANE_TIMEOUT     = 120  # seconds to wait for generation



def load_requirements_doc(reqs_path: str) -> tuple:
    """
    Load requirements from file. Returns (base_url, ac_list).
    Supports three formats:
      - scenarios.json array: [{kane_url, source_description, steps, ...}, ...]
      - Object wrapper:       {"base_url": "...", "acceptance_criteria": [...]}
      - Legacy AC array:      [{"id": "AC-001", "description": ...}, ...]
    """
    data = json.loads(Path(reqs_path).read_text())

    # Object wrapper format
    if isinstance(data, dict):
        return data.get("base_url"), data.get("acceptance_criteria", [])

    # scenarios.json format — array with kane_url per item
    if data and "kane_url" in data[0]:
        base_url = data[0]["kane_url"]
        ac_list = [
            {
                "id":           s.get("requirement_id") or s.get("id", f"AC-{i+1:03d}"),
                "description":  s.get("source_description", s.get("title", "")),
                "kane_steps":   s.get("steps", []),
                "kane_one_liner": s.get("expected_result", ""),
            }
            for i, s in enumerate(data)
        ]
        return base_url, ac_list

    # Legacy AC array
    return None, data


def build_prompt(base_url: str, ac_list: list) -> str:
    """Build the generation prompt from URL + AC summaries."""
    prompt = f"Generate end-to-end browser test scenarios for the application at {base_url}"
    if ac_list:
        summaries = [f"{r['id']}: {r['description']}" for r in ac_list[:10]]
        prompt += ". Requirements: " + " | ".join(summaries)
    return prompt


def run_kane_generate(prompt: str, limit: int, files_arg=None) -> list[dict]:
    """
    Run kane-cli generate --agent and parse the generate_snapshot event.
    Returns list of test case dicts: {title, objective, sc_id, name}
    """
    cmd = [
        "kane-cli", "generate", prompt,
        "--agent",
        "--scenario-limit", str(limit),
        "--per-scenario-limit", "1",
    ]
    if files_arg:
        cmd += ["--files", files_arg]

    print(f"Running: {' '.join(cmd[:5])} ...")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=KANE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        print(f"ERROR: kane-cli generate timed out after {KANE_TIMEOUT}s", file=sys.stderr)
        sys.exit(1)

    combined = result.stdout + "\n" + result.stderr
    snapshot = None
    done_ev  = None

    for line in combined.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "generate_snapshot":
            snapshot = ev
        elif ev.get("type") == "generate_done":
            done_ev = ev

    if done_ev:
        status = done_ev.get("status", "?")
        print(f"generate_done: status={status}, scenarios={done_ev.get('scenario_count')}, "
              f"cases={done_ev.get('case_count')}")

    if not snapshot:
        print("[generate] WARNING: no generate_snapshot event — kane-cli generate produced no output",
              file=sys.stderr)
        return []

    return extract_objectives(snapshot)


def steps_to_objective(kane_steps: list) -> str:
    """Convert kane_steps[].c (action strings) into a single run objective."""
    actions = [s["c"].rstrip(".") for s in kane_steps if s.get("c")]
    return "; ".join(actions) + "."


def extract_objectives(snapshot: dict) -> list[dict]:
    """Extract one objective per scenario (first test case) from generate_snapshot."""
    objectives = []
    for i, scenario in enumerate(snapshot.get("scenarios", []), 1):
        sc_id = f"SC-{i:03d}"
        title = scenario.get("title", sc_id)
        for tc in scenario.get("test_cases", [])[:1]:  # one test case per scenario
            steps    = tc.get("kane_steps", [])
            objective = steps_to_objective(steps) if steps else tc.get("description", "")
            objectives.append({
                "id":        sc_id,
                "tc_id":     str(tc.get("id", "")),
                "name":      f"{sc_id}: {title}",
                "objective": objective,
            })
            break
    return objectives


def main():
    parser = argparse.ArgumentParser(
        description="Generate kane-cli objectives via kane-cli generate"
    )
    parser.add_argument("--url", default=None,
                        help="Override app base URL (auto-read from requirements doc if not set)")
    parser.add_argument("--requirements", default=None,
                        help="Path to requirements JSON (default: scenarios/scenarios.json)")
    parser.add_argument("--limit", type=int, default=5,
                        help="Max scenarios to generate (default: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print objectives without writing to file")
    args = parser.parse_args()

    # Resolve requirements file
    reqs_path = args.requirements or str(DEFAULT_REQS)
    if not Path(reqs_path).exists():
        print(f"ERROR: requirements file not found: {reqs_path}", file=sys.stderr)
        sys.exit(1)

    # Read URL from requirements doc; --url overrides
    doc_url, ac_list = load_requirements_doc(reqs_path)
    base_url = args.url or doc_url or DEFAULT_URL

    if not args.url and doc_url:
        print(f"URL read from requirements doc: {base_url}")
    elif args.url:
        print(f"URL overridden via --url: {base_url}")
    else:
        print(f"URL fallback (not in doc): {base_url}")

    prompt = build_prompt(base_url, ac_list)

    print(f"Requirements: {reqs_path}")
    print(f"Limit       : {args.limit} scenarios\n")

    objectives = run_kane_generate(prompt, args.limit, reqs_path)

    if not objectives:
        if OUTPUT_FILE.exists():
            print(f"[generate] WARNING: kane-cli generate returned 0 scenarios — "
                  f"using existing {OUTPUT_FILE.name} as fallback")
            return  # keep existing objectives.json, exit cleanly
        print("ERROR: no objectives extracted and no existing objectives.json to fall back to.",
              file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerated {len(objectives)} objectives:")
    for o in objectives:
        print(f"  {o['id']}: {o['objective'][:100]}...")

    if args.dry_run:
        print("\n--- objectives.json (dry run) ---")
        print(json.dumps(objectives, indent=2))
        return

    OUTPUT_FILE.write_text(json.dumps(objectives, indent=2))
    print(f"\nWritten to {OUTPUT_FILE}")
    print("Next step: python3 ci/flow1_pipeline.py  or  python3 ci/flow2_pipeline.py")


if __name__ == "__main__":
    main()
