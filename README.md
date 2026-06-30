# Agentic SDLC — LambdaTest

> Requirements go in. **KaneAI authors the tests.** HyperExecute runs them. AI explains failures. Objectives auto-improve every run.

---

## What this is

A fully automated testing pipeline where **KaneAI** — LambdaTest's AI browser agent — authors every test case from a plain-English objective. No selectors. No scripts. No page objects.

You give KaneAI an objective:

> *"Login to saucedemo as standard_user with password secret_sauce, click the Add to cart button for Sauce Labs Backpack, and verify the cart badge shows 1."*

KaneAI opens a real browser, figures out the UI, executes the steps, and saves the verified test directly into LambdaTest Test Manager. HyperExecute runs it in parallel. If it fails, Claude explains why and rewrites the objective — automatically, before the next run.

---

## The Pipeline

```
Push to main  ──────────────────────────────────────────────────────────────►
                                                                              │
        [optional: requirements URL provided via manual dispatch]             │
                    ↓                                                         │
           Claude extracts Acceptance Criteria                                │
                    ↓                                                         │
           Claude writes crisp objectives (max 5)                            │
                    ↓                                                         │
              committed to ci/objectives.json                                 │
                                                                              │
◄─────────────────────────────────────────────────────────────────────────────┘
                    ↓
         kane-cli runs each objective on a real browser (KaneAI)
         ├── 3 SCs run in parallel
         ├── inline self-heal: if it fails, Claude rewrites + retries immediately
         └── saves verified test to LambdaTest Test Manager
                    ↓
         Test Run created → linked to HyperExecute
                    ↓
         HyperExecute runs all tests in parallel (5 VMs, 1 retry)
         └── pipeline polls until all sessions reach final state
                    ↓
         AI Root Cause Analysis
         ├── LT AI RCA (polls per-session endpoint)
         └── Claude RCA fallback (from kane-cli failure detail)
                    ↓
         Traceability Matrix → GitHub Step Summary
         ├── Authoring column (Phase 1) + Execution column (Phase 3)
         └── HE job link + TM Test Run Report link
                    ↓
         Auto-improve: Claude rewrites objectives for all failed SCs
         └── commits improved objectives.json back to main [skip ci]
```

On the **next push**, the pipeline starts from the improved objectives automatically.

---

## Triggers

| How | What happens |
|-----|-------------|
| **Push to `main`** (`ci/`, `requirements/`, `hyperexecute.yaml`) | Pipeline auto-runs using committed `objectives.json` |
| **Manual dispatch — no URL** | Same as push — uses committed `objectives.json` |
| **Manual dispatch + requirements URL** | Downloads doc → Claude extracts ACs → Claude generates objectives → full pipeline |

> Auto-improve commits use `[skip ci]` so they don't re-trigger the pipeline.

---

## Stage-by-stage

### Stage 1 — Requirements Analysis *(only when a requirements URL is provided)*

Claude reads the document and extracts Acceptance Criteria.

**Supported URL formats:**

| Source | Example |
|--------|---------|
| Google Docs | `docs.google.com/document/d/<ID>/edit` |
| Google Drive | `drive.google.com/file/d/<ID>/view` |
| GitHub file | `github.com/user/repo/blob/main/reqs.md` |
| GitHub Gist | `gist.github.com/user/<ID>` |
| Any raw URL | `https://example.com/requirements.txt` |

All formats are auto-detected. Make the doc publicly accessible before running.

**Output:** `requirements/analyzed_requirements.json`

---

### Stage 2 — Objective Generation *(only when a requirements URL is provided)*

Claude generates up to 5 crisp, intent-based objectives from the extracted ACs.

**Format enforced (strictly):**
```
Login to <url> as <user> with password <pass>, <one physical action>, and verify <one immediately visible result>.
```

**Good:**
```
Login to https://www.saucedemo.com/ as standard_user with password secret_sauce,
click the Add to cart button for Sauce Labs Backpack, and verify the cart badge shows 1.
```

**Bad (never do this):**
```
# Multiple actions chained
add Sauce Labs Backpack to the cart and navigate to the cart page, and verify...

# State transition (timing-sensitive)
...and verify the button changes to 'Remove'.

# Step-by-step micro-instructions
Navigate to the URL, type 'standard_user' into the username field, click the Login button...
```

**Output:** `ci/objectives.json` — committed to the repo for reuse across runs.

---

### Stage 3 — KaneAI Authoring *(Phase 1)*

`kane-cli run` executes each objective on a real browser. KaneAI uses AI vision — no pre-written selectors. Each verified test is saved into LambdaTest Test Manager.

- **3 SCs run in parallel** (staggered 3s apart)
- **600s timeout per SC**
- **Tier 1 — Infra retry:** transient failures (CDP disconnect, browser crash) → retry same objective
- **Tier 2 — Inline self-heal:** logic failures → Claude rewrites objective → immediate retry
- **Cross-run self-heal:** at start of next run, Claude pre-heals any objectives that failed last time

---

### Stage 4 — Test Run + HyperExecute *(Phases 2 & 3)*

Creates a LambdaTest Test Manager test run, links all authored test cases, and triggers HyperExecute.

| Setting | Value |
|---------|-------|
| Environment | Configurable via `TM_ENVIRONMENT_ID` (default: Win10, Firefox, desktop) |
| Concurrency | 5 parallel VMs |
| Retry on failure | 1 retry |

The pipeline polls HyperExecute until all sessions reach a final state before proceeding.

---

### Stage 5 — Root Cause Analysis

Two-layer RCA for every failed scenario:

**Layer 1 — LT AI RCA:**
- Triggers `POST /insights/api/v3/public/rca/generate` for the HE job
- Waits 60s for generation, then polls per-session RCA endpoint
- Summarised to 3 bullets by Claude Haiku
- If `triggered=0` (TM sessions not indexed by job), tries session-level RCA directly before falling back

**Layer 2 — Claude RCA fallback** (when LT AI RCA is unavailable):
- Reads kane-cli `failure_detail` from `run_history.json`
- Generates structured analysis: what was asked, what kane-cli did, what needs fixing
- Marked as `source: claude-fallback` — never shown in the main traceability table

**Output:** `ci/rca_results.json`

---

### Stage 6 — Traceability Matrix

Full matrix linking every result back to its origin — appears in GitHub Step Summary after every run.

```
AC → Objective → SC → TM Test Case → Authoring result → HE Execution result → AI RCA
```

Columns:
- **Authoring** — did kane-cli successfully author the test? (Phase 1)
- **Execution** — did HyperExecute pass the test? (Phase 3)
- **RCA** — LT AI RCA only; blank for claude-fallback entries

---

### Stage 7 — Auto-Improve *(end of every run)*

After HE results and RCA are in, Claude rewrites objectives for **all failed SCs**:

- Phase 1 authoring failure → uses kane-cli run summary
- HE execution failure → uses LT AI RCA text as healing context
- Applies the same strict rules: 1 action, 1 immediately-visible assertion, no "changes to"

The improved `objectives.json` is committed with `[skip ci]`. The next push starts from better objectives automatically.

> Custom URL runs (`REQUIREMENTS_URL` set) never overwrite the committed saucedemo objectives — only default runs auto-improve.

---

## Forking this repo

### 1. GitHub Secrets

Go to **Settings → Secrets and variables → Actions → Secrets**

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `LT_USERNAME` | LambdaTest username | [accounts.lambdatest.com/security](https://accounts.lambdatest.com/security) |
| `LT_ACCESS_KEY` | LambdaTest access key | [accounts.lambdatest.com/security](https://accounts.lambdatest.com/security) |
| `ANTHROPIC_API_KEY` | Claude API key | [console.anthropic.com](https://console.anthropic.com) |

### 2. GitHub Variables

Go to **Settings → Secrets and variables → Actions → Variables**

| Variable | Description | How to find it |
|----------|-------------|----------------|
| `TM_PROJECT_ID` | Test Manager project ID (ULID) | KaneAI → your project → URL contains the project ID |
| `TM_ENVIRONMENT_ID` | Test environment ID (integer) | Test Manager → Environments → create or select → ID in API response |

If these variables are not set, the pipeline falls back to the default saucedemo project and environment.

### 3. Configure kane-cli for your project

```bash
kane-cli login --username $LT_USERNAME --access-key $LT_ACCESS_KEY
kane-cli config project YOUR_TM_PROJECT_ID
kane-cli config folder  YOUR_TM_FOLDER_ID   # optional: scope to a folder
```

Update the `kane-cli config project` step in `.github/workflows/flow2.yml` with your project ID.

### 4. Update objectives

Replace `ci/objectives.json` with objectives for your app:

```json
[
  {
    "id": "SC-001",
    "ac_id": "AC-001",
    "name": "SC-001: short description",
    "objective": "Login to https://yourapp.com as user with password pass, click <one thing>, and verify <one immediately visible result>."
  }
]
```

Or trigger a manual dispatch with a `requirements_url` to generate objectives automatically from your requirements doc.

### 5. Push

```bash
git push origin main
```

Watch the run at **Actions → Agentic SDLC — KaneAI Pipeline**.

---

## Running locally

```bash
pip install -r requirements.txt
npm install -g @testmuai/kane-cli@latest

kane-cli login --username $LT_USERNAME --access-key $LT_ACCESS_KEY
kane-cli config project YOUR_TM_PROJECT_ID

# Full pipeline
LT_USERNAME=<u> LT_ACCESS_KEY=<k> ANTHROPIC_API_KEY=<k> \
  TM_PROJECT_ID=<id> TM_ENVIRONMENT_ID=<id> \
  python3 ci/flow2_pipeline.py

# Single SC (for quick testing)
python3 ci/flow2_pipeline.py --sc SC-001

# Skip Phase 1 (reuse last kane-cli sessions)
python3 ci/flow2_pipeline.py --skip-phase1
```

---

## Key files

| File | Purpose |
|------|---------|
| `ci/objectives.json` | Current test objectives (auto-updated by pipeline) |
| `ci/flow2_pipeline.py` | Main pipeline: Phase 1 (kane-cli), Phase 2 (TM), Phase 3 (HE) |
| `ci/self_heal.py` | Cross-run and inline objective healing via Claude |
| `ci/rca.py` | LT AI RCA + Claude fallback RCA |
| `ci/traceability.py` | Builds the requirements → results matrix |
| `ci/generate_objectives.py` | Claude-powered objective generation from ACs |
| `ci/analyze_requirements.py` | Extracts ACs from a requirements URL |
| `hyperexecute.yaml` | HE configuration (discovers kane/ test.py files) |
| `requirements/analyzed_requirements.json` | Extracted ACs (auto-generated, not hand-edited) |

---

## Edge cases

| Situation | Behaviour |
|-----------|-----------|
| SC fails authoring | Infra retry first; then inline Claude heal + retry; cross-run heal on next push |
| All SCs fail Phase 1 | Pipeline aborts before creating HE job |
| Phase 2 returns no TC IDs | HE poll skipped immediately (no 30-min timeout) |
| LT AI RCA `triggered=0` | Tries session-level RCA directly; Claude fallback if still empty |
| RCA session returns 404 | Skipped instantly — no retry loop |
| HE session status `completed` | Treated as in-progress retry (not passed) — waits for final result |
| Auto-improve commit | `[skip ci]` prevents re-triggering the pipeline |
| Custom URL run | Objectives generated for that URL; never overwrites default saucedemo objectives |
| First run (no history) | Cross-run heal skips gracefully — nothing to heal |
| Private Google Doc URL | Download fails — make the doc public ("Anyone with link can view") |
