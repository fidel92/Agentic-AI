#!/usr/bin/env python3
"""No-AI mode: run the estimator directly and write DRAFT documents.

    python estimate.py GC-1004
    python estimate.py GC-1005 --overrides '{"scope": {"green_area_sqft": 16000}, "contingency_pct": 0.12}'
    python estimate.py --all
"""
from __future__ import annotations

import argparse
import json

from bid_agent import workspace
from bid_agent.data import load_config, load_data
from bid_agent.tools import BidTools


def show(t: BidTools, pid: str, overrides: dict | None, brief: bool = False):
    s = t.run_estimate(pid, overrides)
    if not brief:
        from bid_agent.explain import render_text
        print("\n" + "#" * 72 + f"\n# {s['project_id']}  {s['project_name']}\n" + "#" * 72)
        print(render_text(t.session[s["project_id"]]["estimate"]))
        paths = t.save_draft_documents(pid)
        print(f"   Draft Excel: {paths['excel']}\n   Draft Word:  {paths['word']}")
        return
    tot = s["totals"]
    print(f"\n{s['project_id']}  {s['project_name']}  [{s['project_type']}, {s['location']}, "
          f"{s['site_access']} access]")
    print(f"  Review gate: {s['review_status']}")
    for k in ("materials", "labor", "equipment", "subcontractors", "direct_cost"):
        print(f"  {k.replace('_', ' ').title():<22}${tot[k]:>12,.2f}")
    print(f"  Contingency {tot['contingency_pct']:>6.1%}     ${tot['contingency']:>12,.2f}  ({tot['contingency_basis']})")
    print(f"  OH&P        {tot['markup_pct']:>6.1%}     ${tot['markup']:>12,.2f}  ({tot['markup_basis']})")
    print(f"  {'TOTAL BID':<22}${tot['total_bid']:>12,.2f}")
    cs = s["comparables_stats"] or {}
    if cs:
        print(f"  Comparables: ${cs['final_cost_min']:,.0f} / ${cs['final_cost_median']:,.0f} / "
              f"${cs['final_cost_max']:,.0f} (low/median/high)")
    for f in s["flags"]:
        if f["level"] != "INFO":
            print(f"  [{f['level']}] {f['message']}")
    paths = t.save_draft_documents(pid)
    print(f"  Draft Excel: {paths['excel']}\n  Draft Word:  {paths['word']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_id", nargs="?")
    ap.add_argument("--overrides", help="JSON overrides (same keys the agent uses)")
    ap.add_argument("--all", action="store_true", help="Estimate every project in the workbook")
    ap.add_argument("--brief", action="store_true", help="Summary only, skip the step-by-step walkthrough")
    ap.add_argument("--approve", action="store_true", help="Prompt for approval and write FINAL documents")
    ap.add_argument("--workspace", help="Folder holding data/, secrets/ and output/ (or BID_AGENT_WORKSPACE)")
    ap.add_argument("--workbook")
    ap.add_argument("--config")
    a = ap.parse_args()
    cfg = load_config(a.config)
    ws = workspace.pick_workspace(a.workspace, cfg)
    if ws:
        workspace.activate(ws)
    t = BidTools(load_data(cfg, a.workbook), cfg)
    ov = json.loads(a.overrides) if a.overrides else None
    ids = [p["project_id"] for p in t.list_projects()] if a.all else [a.project_id]
    if not ids or ids == [None]:
        ap.error("give a project_id or --all")
    for pid in ids:
        show(t, pid, ov, a.brief)
        if a.approve:
            print(t.request_final_approval(pid, "Approved from estimate.py (no-AI mode)."))


if __name__ == "__main__":
    main()
