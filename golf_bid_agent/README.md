# Golf Course Bid Estimating Agent

A console AI agent that builds a bid estimate from a `project_id`, flags anything that
needs attention, writes a **DRAFT** Excel estimate and Word proposal for you to review,
takes your changes in plain English, and only writes **FINAL** documents after you approve
them in the console. It never sends anything. You send the final documents yourself.

```
you > estimate GC-1004
agent > GC-1004 Bluebonnet New Green Construction: READY FOR REVIEW
        Total bid $194,376 (materials $89,548 · labor $51,108 · equipment $22,240 ...)
        Comparables $132k / $210k / $246k ... Draft files: output/GC-1004/...
you > use 10% contingency and 15% OH&P
you > approve it
 Type APPROVE to finalize: APPROVE
```

## 1. Setup (one time)

You need Python 3.10 or newer.

```bash
cd golf_bid_agent
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### Recommended: a workspace folder for your data and key

Keep your data, API key and outputs in a separate **workspace** folder, outside the code
folder. Then the code folder can go to GitHub with nothing private in it.

```bash
python run_agent.py --init-workspace "~/Desktop/BidAgentWorkspace"
```

This creates:

```
BidAgentWorkspace/
├── data/      ← put your bid workbook (.xlsx) here (a sample copy is added)
├── secrets/   ← your API key lives here as .env
├── output/    ← drafts, finals and the approval log
├── knowledge/ ← company documents the agent can search (specs, quotes, past proposals)
└── .gitignore ← ignores everything, in case this folder is ever put under git
```

Then always start the agent with that folder:

```bash
python run_agent.py --workspace "~/Desktop/BidAgentWorkspace"
```

To skip typing it every time, set it once:

- **Mac:** add `export BID_AGENT_WORKSPACE="$HOME/Desktop/BidAgentWorkspace"` to `~/.zshrc`
- **Windows:** run `setx BID_AGENT_WORKSPACE "$env:USERPROFILE\Desktop\BidAgentWorkspace"`
- **Or:** set `"workspace"` in `config/assumptions.json`. Don't do this if you'll publish the
  config, since it reveals your folder path.

**API key:** get one at https://platform.claude.com → **API Keys** → **Create Key**. The
first time you run the agent it asks you to paste the key (hidden) and saves it to
`secrets/.env` in your workspace. You can also create `secrets/.env` yourself with one
line, `ANTHROPIC_API_KEY=sk-ant-...`. An `ANTHROPIC_API_KEY` environment variable also
works and takes priority.

Without a workspace, the agent uses its own `data/` and `output/` folders and a `.env`
file next to `run_agent.py` (which `.gitignore` keeps out of git).

Open `config/assumptions.json` and fill in the `company` block (name, address, estimator).
The Word proposal uses it.

## 2. Run the agent

```bash
python run_agent.py                    # interactive
python run_agent.py estimate GC-1004   # start with a request
python run_agent.py --model claude-opus-5-5   # use a stronger model
```

Example requests:

| You type | What happens |
|---|---|
| `estimate GC-1001` | Full estimate, review brief, DRAFT .xlsx and .docx |
| `why is labor so high?` | Explains the line items and their basis |
| `tee area is 16,000 sf` | Re-runs with the corrected scope; clears the clarification |
| `use 12% contingency` / `markup 15%` | Changes the markups |
| `crew of 8` / `excavator 6 days` / `drop the roller` | Crew, equipment and exclusion overrides |
| `price sprigs instead of sod` | Swaps turf material (MAT-010) |
| `add a $4,500 survey sub under earthwork` | Adds a subcontract line |
| `show me the comparables` | Historical projects it calibrated against |
| `approve it` | Console approval prompt; writes FINAL files and logs the approval |

Console commands: `/projects`, `/steps GC-1004` (prints the exact step-by-step calculation),
`/reset` (clears the conversation and overrides), `/quit`.

### Step-by-step explanation and final answer

Every estimate comes back in two parts:

1. **How I built this estimate**: 12 numbered steps, each showing the formula with the real
   numbers and the result:
   1. Read the request
   2. Check for missing dimensions (R-009)
   3. Materials (R-001/002/005)
   4. Task labor (R-003/004)
   5. Crew sizing and supervision (R-011)
   6. Equipment days
   7. Direct cost (R-006)
   8. Contingency (R-007)
   9. Overhead & profit (R-008)
   10. Historical check (R-010)
   11. Schedule risk (R-012)
   12. Review flags
2. **Final answer**: total bid with the full breakdown, duration and crew, whether it's
   ready to send, open client questions, items for you to review, and the draft file paths.

After you ask for a change, the agent lists only the steps that changed (old → new) and
then gives the updated final answer. The same walkthrough is saved in the
**Calculation Steps** sheet of the Excel file, and `estimate.py` prints it too (add
`--brief` to skip it).

### No-AI mode

The calculations do not depend on the AI. You can run them directly without an API key:

```bash
python estimate.py GC-1004
python estimate.py GC-1005 --overrides "{\"scope\": {\"green_area_sqft\": 16000}}"
python estimate.py --all
python estimate.py GC-1004 --approve
```

## 3. What you get

Files are saved to `output/<project_id>/`. Each run is timestamped.

**`<id>_Estimate_DRAFT_*.xlsx`**: the internal working estimate. Its formulas stay live, so
you can change any blue input (quantities, waste %, unit costs, hours, days, contingency %,
OH&P %) and the totals update.

- Summary: cost build-up (R-006 to R-008) and the historical calibration
- Calculation Steps: the final answer, then every calculation step in plain language
- Schedule of Values: the client price breakdown, with markups spread across categories
- Review Flags: CLARIFY / REVIEW / INFO items, each with a Resolved column
- Materials, Labor, Equipment, Subcontractors: line items, each with its basis or formula
- Comparables, Sample Check, Scope & Assumptions

**`<id>_Bid_Proposal_DRAFT_*.docx`**: the client-facing proposal (scope, pricing, schedule,
assumptions, any information still needed from the client, exclusions, terms and
signature block). The header says DRAFT until you approve it.

**FINAL versions** are written only after you type `APPROVE` in the console. They have no
watermark and show who approved them and when. `output/approval_log.jsonl` keeps an audit
trail.

## 4. How it estimates

The math is done in Python (`bid_agent/estimator.py`), not by the AI, so every number can
be reproduced. The AI reads the results, explains them, turns your requested changes into
overrides, and enforces the review workflow.

| Rule | Implementation |
|---|---|
| R-001 Rootzone volume | area × depth ÷ 324 CY (only when both are given) |
| R-002 Order quantity | base × (1 + catalog waste), rounded up (rootzone to 5 CY) |
| R-003/R-004 Labor | quantity ÷ productivity × site-access factor × loaded rate |
| R-005/R-006 | material line totals; direct = materials + labor + equipment + subs |
| R-007 Contingency | 7.5% / 10% / 12.5% for good / moderate / constrained access, +2.5% if scope is uncertain, capped at 15% |
| R-008 OH&P | median markup of the closest historical comparables, shown separately |
| R-009 Clarification gate | missing design dimensions → **NEEDS CLARIFICATION**; the affected items are left out, not guessed |
| R-010 Historical calibration | 8 nearest same-type projects (scope, region, access); flags bids outside their range |
| R-011 Crew size | task hours ÷ 6.5 productive h ÷ days, raised to the project-type minimum crew; paid crew time not covered by tasks shows as a visible "crew support" line |
| R-012 Schedule risk | average overrun on comparables → suggested completion window |
| R-013 Data quality | `minor_scope_gap` history records are excluded from comparables |

Unit costs, waste factors, labor rates, productivity and equipment rates are read live
from the workbook (`data/bid_data.xlsx`). Update the workbook and the next estimate uses the
new values. Assumptions the workbook doesn't cover are in `config/assumptions.json`, where
you can change them: gravel blanket depth, trench stone per LF, bunker sand depth, heads per
LF of pipe, irrigation mainline share, equipment production rates, access factors,
contingency policy and so on.

To use a different workbook, put it in your workspace's `data/` folder. If there are
several, name the one to use `bid_data.xlsx`, or pass `--workbook data/<file>.xlsx`. It
must have the same sheet names and columns.

## Knowledge base (RAG)

The agent can search your company documents and cite them while it estimates.

1. Put documents in your workspace's `knowledge/` folder (or `knowledge/` in the agent
   folder if you don't use a workspace). Subfolders are fine. Good candidates:
   - specs and construction standards
   - past proposals and change orders
   - estimating and company policies
   - supplier quotes (a CSV with one quote per row works well)
   - field notes and site reports

   Supported formats: `.md .txt .docx .pdf .csv`.
2. That's it. New or changed files are picked up automatically the next time the agent
   searches.

**How it works:**

- Documents are split into short passages and ranked by keyword matching (BM25), entirely
  on your laptop. There's no extra service or key, and nothing is uploaded to build the
  index.
- The workbook's estimation rules, the notes on all 500 historical projects, and the
  project request notes are searchable too.
- **Automatic:** when you estimate a project, the agent looks for documents that match it
  (type, name, location, notes). If any clearly match, it lists them under "Related company
  documents" with their source.
- **On request:** ask things like *"what does our bunker spec say about sand depth?"*,
  *"do we have a supplier quote for sod?"* or *"which past jobs hit subsurface
  problems?"*. The agent searches and cites `[file, page/passage]`.

**Guardrails:**

- Documents never change the numbers by themselves. If a document suggests a different
  rate or quantity, the agent shows you the passage and proposes the change for you to
  accept.
- Text inside documents is treated as reference, never as instructions. Anything that
  tries to change the agent's rules is ignored and reported to you.
- The folder lock applies here too: a shortcut or symlink pointing outside the workspace is
  skipped.

**Console:** `/kb` lists what's indexed. `/kb rock excavation` runs a search yourself,
without using any API tokens.

**Privacy:** only the passages a search returns (a few short excerpts) are sent to Claude,
never whole documents. `knowledge/` is in `.gitignore`, so your documents are never pushed
to GitHub.

## Data access and privacy

- **Folder lock:** with a workspace, the agent reads and writes **only inside that
  workspace**. Without one, it is limited to its own folder. Any path outside it, including
  through `..` or a shortcut/symlink, is refused with a "Blocked" message.
- **Claude can't browse your computer.** It has no file tools. It only sees what the
  agent's own tools return.
- **What is sent to Claude:** your typed messages; the agent's instructions; the request
  row for the project you ask about; the estimate summary (line items, totals, flags); the
  8 closest historical projects; and, only if you ask, reference tables (rules, catalog,
  labor, equipment rates).
- **What is not sent:** the rest of the workbook (e.g. the other ~490 historical
  projects), your Excel/Word outputs, your API key file, or any file outside the workspace.
  (The key itself only goes to Anthropic as your login.)
- `estimate.py` sends nothing anywhere; it runs fully offline.

## Publishing to GitHub safely

The code folder is set up so it can be published without your key or data.

- **`.gitignore`** keeps out:
  - `.env`, any `secrets/` folder and `*.key` files
  - `output/` and workspace folders
  - any workbook in `data/` except the synthetic sample `bid_data.xlsx` (delete that line
    from `.gitignore` too if you don't want the sample published)
  - `.venv/` and `__pycache__/`
- **`.env.example`** shows other people where their key goes, without containing one.
- **`scripts/check_secrets.py`** scans everything git would publish for API keys
  (`sk-ant-...`), `.env` files, `secrets/` and generated output. Install it as an automatic
  check before every commit:

  ```bash
  git init
  python scripts/check_secrets.py --install-hook
  ```

  If a commit contains a key or a private file, the commit is stopped and the file is
  named. You can also run `python scripts/check_secrets.py` any time, and your future
  "push to GitHub" feature should call it before pushing.
- **If a key is ever pushed by mistake:** delete it immediately at platform.claude.com →
  API Keys and create a new one. Removing it from GitHub afterwards isn't enough, because
  it stays in the history.

## 5. Project layout

```
run_agent.py            console agent (Claude API + tools)
estimate.py             no-AI command line
config/assumptions.json company info, model, and estimator assumptions
data/bid_data.xlsx      synthetic sample workbook (real data goes in your workspace)
scripts/check_secrets.py  pre-commit secret scanner for GitHub
.gitignore, .env.example  keep keys and private data out of git
bid_agent/
  data.py               workbook loader
  estimator.py          deterministic estimating engine
  comparables.py        historical calibration + schedule risk
  report_xlsx.py        Excel working estimate
  report_docx.py        Word proposal
  tools.py              agent tools + human approval gate
  agent.py              Claude tool-use loop and system prompt
  explain.py            step-by-step walkthrough + final answer
  workspace.py          workspace folder: data/, secrets/, output/, knowledge/
  knowledge.py          local document search (RAG, BM25)
knowledge/              your company documents when no workspace is used (git-ignored)
output/                 outputs when no workspace is used
```

## Notes

- The workbook data is synthetic and illustrative. Check the productivity rates and
  assumptions against your own cost history before relying on the bids.
- The default model is `claude-sonnet-5-5`. Change it in `config/assumptions.json`, with
  `--model`, or with the `BID_AGENT_MODEL` environment variable.
