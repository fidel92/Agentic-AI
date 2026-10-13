"""Step-by-step explanation of how an estimate was built, plus the final answer.

Built only from the estimate dict, so the walkthrough always matches the numbers.
"""
from __future__ import annotations


def _usd(x: float, cents: bool = False) -> str:
    return f"${x:,.2f}" if cents else f"${x:,.0f}"


def _q(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".")


LABEL = {"green_area_sqft": "green/tee area (sf)", "rootzone_depth_in": "rootzone depth (in)",
         "drainage_pipe_lf": "drainage pipe (LF)", "bunker_area_sqft": "bunker area (sf)",
         "irrigation_pipe_lf": "irrigation pipe (LF)", "sod_area_sqft": "sod area (sf)",
         "earthwork_cy": "earthwork (CY)"}


def build_steps(est: dict) -> list[dict]:
    p, s, c, sc = est["project"], est["summary"], est["crew"], est["schedule"]
    comp = est["comparables"]
    st = comp.get("stats") or {}
    steps: list[dict] = []

    def add(title, rule, lines, result):
        steps.append({"step": len(steps) + 1, "title": title, "rule": rule, "details": lines, "result": result})

    # 1. request
    scope = [f"{LABEL[k]}: {_q(float(p[k]))}" for k in (
        "green_area_sqft", "rootzone_depth_in", "drainage_pipe_lf", "bunker_area_sqft",
        "irrigation_pipe_lf", "sod_area_sqft", "earthwork_cy") if float(p[k] or 0) > 0]
    lines = [f"Project type {p['project_type']}, {p['location']}, {p['site_access']} site access, "
             f"target {c['duration_days']} working days.", "Scope quantities: " + "; ".join(scope) + "."]
    for k, v in (est["overrides"].get("scope") or {}).items():
        lines.append(f"Reviewer override: {k} changed from {est['request'].get(k)} to {v}.")
    add("Read the project request", "Project_Requests", lines, f"{len(scope)} scope quantities loaded")

    # 2. clarification gate
    clar = [f for f in est["flags"] if f["level"] == "CLARIFY"]
    add("Check for missing design dimensions", "R-009",
        [f["message"] for f in clar] or ["All dimensions this project type needs are present."],
        "NEEDS CLARIFICATION, affected items left out" if clar else "Passed, nothing missing")

    # 3. materials
    lines = []
    for m in est["materials"]:
        lines.append(f"{m['item']}: {m['basis']} Base {_q(m['base_qty'])} {m['unit']} x (1 + {m['waste_factor']:.0%} "
                     f"waste) = order {_q(m['order_qty'])} {m['unit']} x {_usd(m['unit_cost'], True)} = "
                     f"{_usd(m['total'], True)}")
    add("Calculate material quantities and cost", "R-001, R-002, R-005", lines,
        f"Materials = {_usd(s['materials'], True)}")

    # 4. task labor
    tasks = [l for l in est["labor"] if l["productivity"]]
    lines = [f"{l['role']}: {_q(l['quantity'])} {l['qty_unit']} / {_q(l['productivity'])} per hour x access "
             f"factor {l['access_factor']:g} = {_q(l['hours'])} h x {_usd(l['rate'], True)}/h = {_usd(l['total'], True)}"
             for l in tasks]
    task_cost = sum(l["total"] for l in tasks)
    add("Calculate productive labor hours by task", "R-003, R-004", lines,
        f"{_q(c['task_hours'])} task hours, {_usd(task_cost, True)}")

    # 5. crew
    lines = [f"Workers needed = {_q(c['task_hours'])} h / ({c['productive_hours_per_day']:g} productive h/day x "
             f"{c['duration_days']} days), rounded up = {c['workers_required']}, plus {c['foremen']} foreman = "
             f"{c['crew_required']}.",
             f"Minimum crew for {p['project_type']} is {c['type_min_crew']} (Project_Type_Ranges), so the crew is "
             f"{c['crew_size']}. Comparable projects used a median of {c['comps_median_crew']}."]
    for l in est["labor"]:
        if l["code"] == "LAB-008":
            lines.append(f"Foreman: {_q(l['hours'])} h x {_usd(l['rate'], True)} = {_usd(l['total'], True)}.")
        if l["code"] == "SUPPORT":
            lines.append(f"Crew support time: {l['basis']} {_q(l['hours'])} h x {_usd(l['rate'], True)} = "
                         f"{_usd(l['total'], True)}.")
    add("Size the crew and add supervision", "R-011", lines,
        f"Crew of {c['crew_size']}; total labor = {_usd(s['labor'], True)}")

    # 6. equipment
    lines = [f"{e['name']}: {e['basis']} = {e['days']:g} days x {_usd(e['rate'])}/day = {_usd(e['total'], True)}"
             for e in est["equipment"]]
    add("Estimate equipment rental days", "Equipment_Rates", lines, f"Equipment = {_usd(s['equipment'], True)}")

    # 7. direct cost
    lines = [f"Materials {_usd(s['materials'], True)} + labor {_usd(s['labor'], True)} + equipment "
             f"{_usd(s['equipment'], True)} + subcontractors {_usd(s['subcontractors'], True)}"]
    for sb in est["subcontractors"]:
        lines.append(f"Subcontract: {sb['description']} {_usd(sb['total'], True)}")
    add("Add up the direct cost", "R-006", lines, f"Direct cost = {_usd(s['direct_cost'], True)}")

    # 8-9. markups
    add("Apply contingency", "R-007",
        [f"{s['contingency_basis']}.",
         f"{_usd(s['direct_cost'], True)} x {s['contingency_pct']:.1%} = {_usd(s['contingency'], True)}"],
        f"Contingency = {_usd(s['contingency'], True)}")
    add("Apply overhead & profit", "R-008",
        [f"{s['markup_basis']}.",
         f"({_usd(s['direct_cost'], True)} + {_usd(s['contingency'], True)}) x {s['markup_pct']:.1%} = "
         f"{_usd(s['markup'], True)}"],
        f"OH&P = {_usd(s['markup'], True)}")

    # 10. calibration
    if st:
        pos = (s["total_bid"] / st["final_cost_median"] - 1) if st["final_cost_median"] else 0
        inside = st["final_cost_min"] <= s["total_bid"] <= st["final_cost_max"]
        lines = [comp.get("method", ""),
                 f"Closest {st['count']} past projects finished at {_usd(st['final_cost_min'])} (low), "
                 f"{_usd(st['final_cost_median'])} (median), {_usd(st['final_cost_max'])} (high).",
                 f"This bid is {pos:+.0%} vs the median and {'inside' if inside else 'OUTSIDE'} that range."]
        add("Sanity-check against past projects", "R-010", lines,
            "Within the historical range" if inside else "Outside historical range, see review flags")

    # 11. schedule
    add("Check schedule risk", "R-012",
        [(f"Comparable projects ran {sc['comps_mean_overrun_days']:.1f} days over plan on average"
          if (sc['comps_mean_overrun_days'] or 0) > 0 else
          f"Comparable projects finished {abs(sc['comps_mean_overrun_days'] or 0):.1f} days ahead of plan on average")
         + f" (worst overrun {sc['comps_max_overrun_days']} days); mean weather delay "
         f"{sc['comps_mean_weather_delay_days']} days."],
        f"Quote {sc['target_duration_days']} working days"
        + (f", allow up to {sc['suggested_completion_window_days']}" if sc["suggested_completion_window_days"]
           > sc["target_duration_days"] else ""))

    # 12. flags
    n = {lv: [f for f in est["flags"] if f["level"] == lv] for lv in ("CLARIFY", "REVIEW", "INFO")}
    add("Collect review flags", "R-009, R-013",
        [f"[{f['level']}] {f['message']}" for f in n["CLARIFY"] + n["REVIEW"]]
        + [f"{len(n['INFO'])} informational assumption(s) recorded."],
        f"{len(n['CLARIFY'])} to clarify, {len(n['REVIEW'])} to review, {len(n['INFO'])} info")
    return steps


def final_answer(est: dict) -> dict:
    s, p = est["summary"], est["project"]
    clar = [f["client_question"] or f["message"] for f in est["flags"] if f["level"] == "CLARIFY"]
    review = [f["message"] for f in est["flags"] if f["level"] == "REVIEW"]
    ready = not clar
    return {
        "project": f"{p['project_id']} - {p['project_name']}",
        "total_bid": s["total_bid"],
        "breakdown": {"materials": s["materials"], "labor": s["labor"], "equipment": s["equipment"],
                      "subcontractors": s["subcontractors"], "direct_cost": s["direct_cost"],
                      "contingency": s["contingency"], "contingency_pct": s["contingency_pct"],
                      "overhead_profit": s["markup"], "overhead_profit_pct": s["markup_pct"]},
        "duration_days": est["schedule"]["target_duration_days"],
        "crew_size": est["crew"]["crew_size"],
        "recommendation": (("Ready for your review. Check the review items below, then approve." if review
                            else "Ready for your review. No open issues; approve when satisfied.")
                           if ready else "NOT ready to send. Get the client's answers to the open questions "
                           "first; the price leaves out the affected items."),
        "open_questions_for_client": clar,
        "items_for_reviewer": review,
    }


def render_text(est: dict) -> str:
    out = []
    for stp in build_steps(est):
        out.append(f"STEP {stp['step']}: {stp['title']}  ({stp['rule']})")
        out += [f"   - {d}" for d in stp["details"]]
        out.append(f"   => {stp['result']}")
    fa = final_answer(est)
    b = fa["breakdown"]
    out += ["", "=" * 72, f"FINAL ANSWER  {fa['project']}", "=" * 72,
            f"   Materials           {_usd(b['materials'], True):>14}",
            f"   Labor               {_usd(b['labor'], True):>14}",
            f"   Equipment           {_usd(b['equipment'], True):>14}",
            f"   Subcontractors      {_usd(b['subcontractors'], True):>14}",
            f"   Direct cost         {_usd(b['direct_cost'], True):>14}",
            f"   Contingency {b['contingency_pct']:>5.1%}   {_usd(b['contingency'], True):>14}",
            f"   OH&P        {b['overhead_profit_pct']:>5.1%}   {_usd(b['overhead_profit'], True):>14}",
            f"   TOTAL BID           {_usd(fa['total_bid'], True):>14}",
            f"   Duration {fa['duration_days']} working days, crew of {fa['crew_size']}",
            f"   Recommendation: {fa['recommendation']}"]
    for q in fa["open_questions_for_client"]:
        out.append(f"   ? Client question: {q}")
    for r in fa["items_for_reviewer"]:
        out.append(f"   ! Review: {r}")
    return "\n".join(out)
