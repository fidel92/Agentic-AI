#!/usr/bin/env python3
"""Interactive console for the Bid Estimating Agent.

    python run_agent.py
    > estimate GC-1004
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from bid_agent import workspace
from bid_agent.agent import BidAgent
from bid_agent.data import load_config, load_data
from bid_agent.tools import BidTools

BANNER = r"""
==========================================================================
  Golf Course Bid Estimating Agent
  Type a request, e.g.  "estimate GC-1004"  |  "use 12% contingency"
  "tee area is 16,000 sf"  |  "approve it"
  Commands: /projects  /steps <id>  /kb <search words>  /kb  /reset  /quit
==========================================================================
"""


def ensure_api_key():
    """Use ANTHROPIC_API_KEY from the environment or the secrets .env; otherwise ask once and save it."""
    env_path = workspace.load_env()
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return
    print("No Claude API key found.")
    print("Create one at https://platform.claude.com -> API Keys -> Create Key, then paste it below.")
    key = getpass.getpass("API key (input hidden, starts with sk-ant-): ").strip().strip('"')
    if not key.startswith("sk-ant-"):
        sys.exit("That doesn't look like a Claude API key (it should start with sk-ant-). Run again to retry.")
    os.environ["ANTHROPIC_API_KEY"] = key
    if input(f"Save it to {env_path} so you don't have to paste it again? [Y/n] ").strip().lower() in ("", "y", "yes"):
        env_path.parent.mkdir(parents=True, exist_ok=True)
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(f"ANTHROPIC_API_KEY={key}\n")
        print(f"Saved to {env_path}. Keep it private; don't email, share or commit it.\n")


def setup_workspace(a, cfg):
    """Handle --init-workspace / --workspace. Returns the active workspace (or None)."""
    if a.init_workspace:
        ws = workspace.init_workspace(a.init_workspace)
        print(f"Workspace ready: {ws}")
        for d in workspace.SUBDIRS:
            print(f"  {ws / d}")
        print(f"\nNext:  python run_agent.py --workspace \"{ws}\"")
        sys.exit(0)
    ws = workspace.pick_workspace(a.workspace, cfg)
    if ws:
        try:
            workspace.activate(ws)
        except FileNotFoundError as e:
            sys.exit(str(e))
    return ws


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", help="Folder holding data/, secrets/ and output/ (or set BID_AGENT_WORKSPACE).")
    ap.add_argument("--init-workspace", metavar="FOLDER", help="Create a new workspace folder and exit.")
    ap.add_argument("--workbook", help="Workbook path inside the workspace (default: the .xlsx in data/).")
    ap.add_argument("--config", help="Path to assumptions.json")
    ap.add_argument("--model", help="Claude model ID (overrides config).")
    ap.add_argument("prompt", nargs="*", help="Optional first request, e.g. estimate GC-1004")
    a = ap.parse_args()

    cfg = load_config(a.config)
    ws = setup_workspace(a, cfg)
    ensure_api_key()
    if a.model:
        cfg.setdefault("agent", {})["model"] = a.model
    try:
        data = load_data(cfg, a.workbook)
    except (FileNotFoundError, PermissionError) as e:
        sys.exit(str(e))
    agent = BidAgent(BidTools(data, cfg), cfg)
    print(BANNER)
    print(f"  Workspace: {ws or '(agent folder)'}\n  Workbook: {data.source}\n"
          f"  Output: {agent.tools.out_dir}\n  Model: {agent.model}\n")

    pending = " ".join(a.prompt).strip()
    while True:
        try:
            text = pending or input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return
        pending = ""
        if not text:
            continue
        if text in ("/quit", "/exit", "quit", "exit"):
            return
        if text == "/reset":
            agent.reset()
            print("  (conversation and overrides cleared)")
            continue
        if text == "/projects":
            for p in agent.tools.list_projects():
                print(f"  {p['project_id']}  {p['project_name']:<40} {p['project_type']:<26} {p['location']}")
            continue
        if text == "/kb":
            st = agent.tools.kb.stats()
            print(f"  Knowledge folder: {st['folder']}\n  Documents: {len(st['documents'])}  "
                  f"Passages: {st['passages']}")
            for d in st["documents"]:
                print(f"    - {d}")
            for w in st["warnings"]:
                print(f"  ! {w}")
            continue
        if text.startswith("/kb "):
            for i, h in enumerate(agent.tools.kb.search(text[4:], top_k=5), 1):
                print(f"\n  {i}. [{h['source']}, {h['location']}] score {h['score']}\n     {h['text'][:400]}")
            print()
            continue
        if text.startswith("/steps"):
            from bid_agent.explain import render_text
            parts = text.split()
            pid = parts[1] if len(parts) > 1 else (list(agent.tools.session)[-1] if agent.tools.session else "")
            if not pid:
                print("  usage: /steps GC-1004")
                continue
            try:
                agent.tools._latest(agent.tools._pid(pid))
                print("\n" + render_text(agent.tools.session[agent.tools._pid(pid)]["estimate"]) + "\n")
            except KeyError as e:
                print(f"  {e}")
            continue
        try:
            print("\nagent > " + agent.ask(text) + "\n")
        except Exception as e:
            print(f"\n  [error] {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    main()
