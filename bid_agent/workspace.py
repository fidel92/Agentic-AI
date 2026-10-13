"""Workspace folder: keeps your data, API key and outputs OUTSIDE the code folder.

    BidAgentWorkspace/
    ├── data/       your bid workbook (.xlsx)
    ├── secrets/    .env with ANTHROPIC_API_KEY=...   (never commit or share)
    ├── output/     drafts, finals, approval log
    └── .gitignore  ignores everything, in case this folder is ever put under git

The code folder (golf_bid_agent) can then be pushed to GitHub with no data or keys in it.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import data as _data

SUBDIRS = {
    "data": "Put your bid workbook (.xlsx) here. The agent reads only this folder's workbook.",
    "secrets": "Holds .env with ANTHROPIC_API_KEY=sk-ant-... Never commit, email or share this folder.",
    "output": "Draft and final estimates/proposals and approval_log.jsonl are written here.",
    "knowledge": ("Put company documents the agent can search here: specs, past proposals, policies, "
                  "supplier quotes, field notes (.md .txt .docx .pdf .csv). Only matching passages are "
                  "sent to Claude when it searches."),
}
REQUIRED = ("data", "secrets", "output")
ENV_NAME = ".env"


def pick_workspace(cli_value: str | None, cfg: dict) -> Path | None:
    """--workspace flag, then BID_AGENT_WORKSPACE env var, then config 'workspace'."""
    val = cli_value or os.environ.get("BID_AGENT_WORKSPACE") or cfg.get("workspace")
    return Path(val).expanduser().resolve() if val else None


def init_workspace(path: str | os.PathLike, copy_sample: bool = True) -> Path:
    ws = Path(path).expanduser().resolve()
    for name, note in SUBDIRS.items():
        (ws / name).mkdir(parents=True, exist_ok=True)
        readme = ws / name / "README.txt"
        if not readme.exists():
            readme.write_text(note + "\n", encoding="utf-8")
    gi = ws / ".gitignore"
    if not gi.exists():
        gi.write_text("# This workspace holds private data and API keys - never commit it.\n*\n", encoding="utf-8")
    example = ws / "secrets" / ".env.example"
    if not example.exists():
        example.write_text("# Copy to .env (same folder) and paste your key\nANTHROPIC_API_KEY=sk-ant-...\n",
                           encoding="utf-8")
    sample = _data.ROOT / "data" / "bid_data.xlsx"
    if copy_sample and sample.exists() and not list((ws / "data").glob("*.xlsx")):
        shutil.copy2(sample, ws / "data" / "bid_data.xlsx")
    return ws


def activate(path: Path) -> Path:
    """Lock the agent to this workspace folder."""
    if not path.is_dir():
        raise FileNotFoundError(
            f"Workspace folder '{path}' does not exist. Create it with:  python run_agent.py --init-workspace \"{path}\"")
    missing = [d for d in REQUIRED if not (path / d).is_dir()]
    if missing:
        raise FileNotFoundError(f"Workspace '{path}' is missing folder(s) {missing}. Run --init-workspace on it.")
    (path / "knowledge").mkdir(exist_ok=True)
    _data.WORKSPACE = path
    return path


def find_workbook(cfg: dict) -> Path:
    d = _data.resolve("data")
    books = sorted(p for p in d.glob("*.xlsx") if not p.name.startswith("~$"))
    if not books:
        raise FileNotFoundError(f"No .xlsx workbook found in {d}. Put your bid workbook there.")
    if len(books) == 1:
        return books[0]
    preferred = Path(cfg.get("workbook_path", "bid_data.xlsx")).name
    for b in books:
        if b.name == preferred:
            return b
    raise FileNotFoundError(
        f"Several workbooks in {d}: {[b.name for b in books]}. Keep one, name it {preferred}, "
        "or pass --workbook data/<file>.xlsx")


def env_file() -> Path:
    """Where the API key lives: <workspace>/secrets/.env, or <agent folder>/.env without a workspace."""
    if _data.WORKSPACE is not None:
        return _data.WORKSPACE / "secrets" / ENV_NAME
    return _data.ROOT / ENV_NAME


def load_env() -> Path:
    f = env_file()
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v
    return f
