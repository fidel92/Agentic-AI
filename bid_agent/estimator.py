"""Deterministic bid estimator.

All arithmetic lives here (not in the LLM) so every number is reproducible and
traceable to a workbook rule (R-001 ... R-013) or an assumption in
config/assumptions.json. The agent calls this through tools.
"""
from __future__ import annotations

import copy
import math
from decimal import ROUND_HALF_UP, Decimal
from datetime import date

from .comparables import find_comparables
from .data import SCOPE_FIELDS, BidData

SOV_BY_MATERIAL_CATEGORY = {
    "green_construction": "Green / tee construction",
    "drainage": "Drainage",
    "irrigation": "Irrigation",
    "bunker": "Bunkers",
    "turf": "Turf establishment",
    "earthwork": "Earthwork & site preparation",
}
SOV_BY_WORK_TYPE = {
    "site_prep": "Earthwork & site preparation",
    "earthwork": "Earthwork & site preparation",
    "irrigation": "Irrigation",
    "drainage": "Drainage",
    "green_construction": "Green / tee construction",
    "turf": "Turf establishment",
    "bunker": "Bunkers",
    "supervision": "General conditions, supervision & equipment",
    "support": "General conditions, supervision & equipment",
}
GREEN_TYPES = {"putting_green_renovation", "new_putting_green"}

# Clarification gate (R-009): design-critical inputs per project type
REQUIRED_BY_TYPE = {
    "putting_green_renovation": ["green_area_sqft", "rootzone_depth_in"],
    "new_putting_green": ["green_area_sqft", "rootzone_depth_in", "drainage_pipe_lf", "irrigation_pipe_lf"],
    "tee_renovation": ["green_area_sqft", "sod_area_sqft"],
    "bunker_renovation": ["bunker_area_sqft"],
    "irrigation_renovation": ["irrigation_pipe_lf"],
    "drainage_renovation": ["drainage_pipe_lf"],
    "fairway_grassing": ["sod_area_sqft"],
}
FIELD_LABEL = {
    "green_area_sqft": "green/tee surface area (sq ft)",
    "rootzone_depth_in": "rootzone / growing-medium depth (in)",
    "drainage_pipe_lf": "drainage pipe length (LF)",
    "bunker_area_sqft": "bunker area (sq ft)",
    "irrigation_pipe_lf": "irrigation pipe length (LF)",
    "sod_area_sqft": "sod / turf area (sq ft)",
    "earthwork_cy": "earthwork volume (CY)",
}


def r2(x: float) -> float:
    """Round half away from zero to cents, matching Excel's ROUND()."""
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def ceil_to(x: float, step: float = 1) -> float:
    """Round up to a multiple of `step`, ignoring floating-point dust."""
    x = round(x, 6)
    return math.ceil(x / step) * step


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def estimate(data: BidData, cfg: dict, project_id: str, overrides: dict | None = None) -> dict:
    ov = copy.deepcopy(overrides or {})
    req = data.project(project_id)
    p = dict(req)
    for k, v in (ov.get("scope") or {}).items():
        p[k] = v
    for f in SCOPE_FIELDS + ["target_duration_days"]:
        p[f] = _num(p.get(f))

    mf = cfg["material_factors"]
    lab_cfg = cfg["labor"]
    access = str(p.get("site_access") or "moderate").lower()
    access_factor = ov.get("site_access_factor", cfg["site_access_factor"].get(access, 1.0))
    excluded = set(ov.get("exclude_items") or [])
    flags: list[dict] = []

    def flag(level, rule, msg, client_question=None):
        flags.append({"level": level, "rule": rule, "message": msg, "client_question": client_question})

    ptype = p["project_type"]
    trange = data.type_range(ptype)
    duration = int(p["target_duration_days"]) or int((trange or {}).get("min_planned_days", 10))

    # ------------------------------------------------------------------ R-009
    missing = [f for f in REQUIRED_BY_TYPE.get(ptype, []) if p[f] <= 0]
    for f in missing:
        if True:
            flag("CLARIFY", "R-009", f"Missing {FIELD_LABEL[f]} for a {ptype.replace('_', ' ')} project. "
                 "Request this from the client; affected items are excluded until provided.",
                 client_question=f"Please confirm the {FIELD_LABEL[f]} for this work.")
    if p["green_area_sqft"] > 0 and p["rootzone_depth_in"] <= 0:
        flag("CLARIFY", "R-001", "Green/tee area is given but rootzone depth is missing; rootzone mix not priced.",
             client_question="Please confirm the rootzone depth (in) for the green/tee surface.")
    if p["rootzone_depth_in"] > 0 and p["green_area_sqft"] <= 0 and "green_area_sqft" not in missing:
        flag("CLARIFY", "R-001", f"Rootzone depth of {p['rootzone_depth_in']:g} in is specified but the "
             "green/tee area is 0/blank; rootzone/growing-medium material is NOT priced until the area is confirmed.",
             client_question=f"Please confirm the area (sq ft) to receive the {p['rootzone_depth_in']:g}-in "
             "rootzone / growing-medium layer; this material is not yet included in the price.")
    notes = str(p.get("notes") or "")
    verify_note = "verify" in notes.lower()
    if verify_note:
        flag("REVIEW", "R-009", f"Request note says: \"{notes}\" Confirm dimensions/specs before sending.")
    for k in (ov.get("scope") or {}):
        flag("INFO", "Override", f"Reviewer override applied: {k} = {ov['scope'][k]} (request had {req.get(k)}).")

    # ------------------------------------------------------------- materials
    materials: list[dict] = []

    def add_mat(mat_id, base_qty, basis, label=None, round_step=1, waste=None):
        if base_qty <= 0 or mat_id in excluded:
            return
        m = data.material(mat_id)
        w = float(m["waste_factor"]) if waste is None else waste
        unit_cost = float((ov.get("unit_costs") or {}).get(mat_id, m["unit_cost_usd"]))
        order = ceil_to(base_qty * (1 + w), round_step)
        materials.append({
            "code": mat_id, "item": label or m["material_name"], "category": m["category"],
            "sov": SOV_BY_MATERIAL_CATEGORY.get(m["category"], "Other"),
            "unit": m["unit"], "base_qty": base_qty, "waste_factor": w,
            "waste_qty": round(order - base_qty, 3), "round_step": round_step,
            "order_qty": order, "unit_cost": unit_cost, "total": r2(order * unit_cost),
            "basis": basis,
        })

    area, depth = p["green_area_sqft"], p["rootzone_depth_in"]
    rootzone_cy = area * depth / 324 if area > 0 and depth > 0 else 0.0
    add_mat("MAT-001", rootzone_cy, f"R-001: {area:,.0f} sf x {depth:g} in / 324 = {rootzone_cy:,.1f} CY; "
            f"+ catalog waste, rounded up to {cfg['rounding']['rootzone_round_up_to_cy']} CY (R-002).",
            round_step=cfg["rounding"]["rootzone_round_up_to_cy"])

    gravel_blanket_t = 0.0
    if ptype in mf["gravel_blanket_project_types"] and area > 0:
        cy = area * mf["green_gravel_blanket_depth_in"] / 324
        gravel_blanket_t = cy * mf["gravel_tons_per_cy"]
        add_mat("MAT-002", gravel_blanket_t,
                f"USGA gravel blanket: {area:,.0f} sf x {mf['green_gravel_blanket_depth_in']} in / 324 = {cy:,.1f} CY "
                f"x {mf['gravel_tons_per_cy']} t/CY.", label="Drainage gravel - green gravel blanket")

    drain = p["drainage_pipe_lf"]
    add_mat("MAT-003", drain, "Measured length + catalog waste.")
    trench_t = drain * mf["trench_gravel_tons_per_lf"]
    if ptype == "bunker_renovation":
        add_mat("MAT-015", trench_t, f"{drain:,.0f} LF x {mf['trench_gravel_tons_per_lf']} t/LF trench stone.")
    else:
        add_mat("MAT-002", trench_t, f"{drain:,.0f} LF x {mf['trench_gravel_tons_per_lf']} t/LF trench backfill.",
                label="Drainage gravel - trench backfill")
    if ptype in mf["geotextile_project_types"]:
        add_mat("MAT-012", drain * mf["geotextile_sqft_per_drainage_lf"],
                f"Trench wrap: {drain:,.0f} LF x {mf['geotextile_sqft_per_drainage_lf']} sf/LF.")

    irr = p["irrigation_pipe_lf"]
    main_frac = float(ov.get("irrigation_mainline_fraction",
                             mf["irrigation_mainline_fraction_by_type"].get(ptype, 0.0)))
    if irr > 0:
        add_mat("MAT-004", irr * main_frac, f"Mainline share {main_frac:.0%} of {irr:,.0f} LF (assumed).")
        add_mat("MAT-005", irr * (1 - main_frac), f"Lateral share {1 - main_frac:.0%} of {irr:,.0f} LF.")
        lf_per_head = float(ov.get("lf_per_sprinkler_head", mf["irrigation_lf_per_sprinkler_head"]))
        add_mat("MAT-006", math.ceil(irr / lf_per_head), f"1 head per {lf_per_head:g} LF of pipe (assumed).")
        add_mat("MAT-013", math.ceil(irr / mf["irrigation_lf_per_fittings_allowance"]),
                f"1 allowance per {mf['irrigation_lf_per_fittings_allowance']:,} LF of pipe.", waste=0.0)
        sizing = (f"{main_frac:.0%} 4-in mainline / {1 - main_frac:.0%} 2-in lateral" if main_frac > 0
                  else "all 2-in lateral, as in Sample_Bid_Lines")
        flag("INFO", "R-002", f"Irrigation pipe sizing assumed ({sizing}); {math.ceil(irr / lf_per_head)} heads at "
             f"1 per {lf_per_head:g} LF. Confirm sizing and head layout with the irrigation design.")

    bunker = p["bunker_area_sqft"]
    sand_t = 0.0
    if bunker > 0:
        add_mat("MAT-007", bunker, "Bunker floor area + catalog waste.")
        sand_cy = bunker * mf["bunker_sand_depth_in"] / 324
        sand_t = sand_cy * mf["bunker_sand_tons_per_cy"]
        add_mat("MAT-008", sand_t, f"{bunker:,.0f} sf x {mf['bunker_sand_depth_in']} in / 324 = {sand_cy:,.1f} CY "
                f"x {mf['bunker_sand_tons_per_cy']} t/CY.")

    sod = p["sod_area_sqft"]
    turf_mat = ov.get("turf_material", mf["default_turf_material"])
    if sod > 0:
        if turf_mat == "MAT-009":
            add_mat("MAT-009", sod, "Measured area + catalog waste.")
        else:
            add_mat(turf_mat, sod / 43560, f"{sod:,.0f} sf / 43,560 = {sod / 43560:.3f} acre.")
        flag("INFO", "Assumption", f"Turf assumed to be {data.material(turf_mat)['material_name']}; "
             "confirm grass variety with the client.")

    for x in ov.get("extra_lines") or []:
        if x.get("category", "materials") == "materials":
            qty = float(x["quantity"])
            materials.append({
                "code": x.get("code", "ADD"), "item": x["description"], "category": "added",
                "sov": x.get("sov", "Other / added scope"), "unit": x.get("unit", "ls"), "base_qty": qty,
                "waste_factor": 0.0, "waste_qty": 0.0, "round_step": 1, "order_qty": qty,
                "unit_cost": float(x["unit_cost"]), "total": r2(qty * float(x["unit_cost"])),
                "basis": x.get("note", "Added by reviewer."),
            })

    # ----------------------------------------------------------------- labor
    import_cy = (rootzone_cy + (gravel_blanket_t + trench_t) / mf["gravel_tons_per_cy"]
                 + sand_t / mf["bunker_sand_tons_per_cy"])
    tasks = [
        ("LAB-001", p["earthwork_cy"], "CY", "Earthwork volume (site prep crew)"),
        ("LAB-002", p["earthwork_cy"] + import_cy, "CY",
         f"Earthwork {p['earthwork_cy']:,.0f} CY + imported material placement {import_cy:,.1f} CY"),
        ("LAB-003", irr, "LF", "Irrigation pipe installed"),
        ("LAB-004", drain, "LF", "Drainage pipe installed"),
        ("LAB-005", area if rootzone_cy > 0 or area > 0 else 0, "SF", "Green/tee surface shaped"),
        ("LAB-006", sod, "SF", "Sod installed"),
        ("LAB-007", bunker, "SF", "Bunker area built"),
    ]
    labor: list[dict] = []
    rates = ov.get("labor_rates") or {}
    task_hours = 0.0
    for code, qty, unit, basis in tasks:
        if qty <= 0 or code in excluded:
            continue
        L = data.labor_code(code)
        prod = float(L["illustrative_productivity_rate"])
        hrs = r2(qty / prod * access_factor)
        rate = float(rates.get(code, L["rate_usd_per_hour_loaded"]))
        task_hours += hrs
        labor.append({
            "code": code, "role": L["role"], "work_type": L["work_type"],
            "sov": SOV_BY_WORK_TYPE[L["work_type"]], "quantity": r2(qty), "qty_unit": unit,
            "productivity": prod, "productivity_unit": L["productivity_unit"],
            "access_factor": access_factor, "hours": hrs, "rate": rate,
            "total": r2(hrs * rate),
            "basis": f"R-003/R-004: {basis} / {prod:g} {L['productivity_unit']} x access factor {access_factor:g}",
        })

    # crew sizing (R-011)
    phd = lab_cfg["productive_hours_per_person_per_day"]
    paid = lab_cfg["paid_hours_per_day"]
    foremen = lab_cfg["foremen_on_site"]
    workers_required = math.ceil(round(task_hours / (phd * duration), 6)) if task_hours else 0
    crew_required = workers_required + foremen
    min_crew = int(trange["min_crew_size"]) if (trange and lab_cfg["use_type_min_crew"]) else 0
    max_crew = int(trange["max_crew_size"]) if trange else 99
    crew_size = int(ov.get("crew_size") or max(crew_required, min_crew))
    workers = max(crew_size - foremen, 0)
    if crew_required > max_crew:
        flag("REVIEW", "R-011", f"Task hours need a crew of {crew_required} to finish in {duration} days, above "
             f"the {ptype} maximum of {max_crew}. Consider a longer duration.")
    if ov.get("crew_size") and workers * phd * duration < task_hours:
        flag("REVIEW", "R-011", f"Override crew of {crew_size} cannot complete {task_hours:,.0f} task hours in "
             f"{duration} days at {phd} productive h/day.")

    L8 = data.labor_code("LAB-008")
    fh = foremen * duration * paid
    fr = float(rates.get("LAB-008", L8["rate_usd_per_hour_loaded"]))
    labor.append({
        "code": "LAB-008", "role": L8["role"], "work_type": "supervision", "sov": SOV_BY_WORK_TYPE["supervision"],
        "quantity": duration, "qty_unit": "days", "productivity": None, "productivity_unit": "crew supervision",
        "access_factor": 1.0, "hours": fh, "rate": fr, "total": r2(fh * fr),
        "basis": f"Supervision rule: {foremen} foreman x {duration} days x {paid} h/day",
    })
    support_hrs = r2(max(workers * duration * paid - task_hours, 0.0))
    if support_hrs > 0 and "SUPPORT" not in excluded:
        L1 = data.labor_code("LAB-001")
        sr = float(rates.get("LAB-001", L1["rate_usd_per_hour_loaded"]))
        labor.append({
            "code": "SUPPORT", "role": "Crew support & non-productive time", "work_type": "support",
            "sov": SOV_BY_WORK_TYPE["support"], "quantity": workers, "qty_unit": "workers",
            "productivity": None, "productivity_unit": "", "access_factor": 1.0,
            "hours": r2(support_hrs), "rate": sr, "total": r2(support_hrs * sr),
            "basis": (f"R-011: {workers} workers x {duration} days x {paid} paid h/day = "
                      f"{workers * duration * paid:,.0f} h, less {task_hours:,.1f} productive task h; "
                      "at general laborer rate (setup, layout, cleanup, standby)."),
        })
    for x in ov.get("extra_lines") or []:
        if x.get("category") == "labor":
            hrs, rate = float(x["quantity"]), float(x["unit_cost"])
            labor.append({"code": x.get("code", "ADD"), "role": x["description"], "work_type": "added",
                          "sov": x.get("sov", "Other / added scope"), "quantity": hrs, "qty_unit": "h",
                          "productivity": None, "productivity_unit": "", "access_factor": 1.0, "hours": hrs,
                          "rate": rate, "total": r2(hrs * rate), "basis": x.get("note", "Added by reviewer.")})

    # ------------------------------------------------------------- equipment
    ec = cfg["equipment"]
    ew = p["earthwork_cy"]
    raw_days = {
        "EQ-001": (ew / ec["excavator_cy_per_day"] + drain / ec["excavator_drainage_lf_per_day"]
                   + import_cy / ec["excavator_import_cy_per_day"]) * access_factor,
        "EQ-002": duration * ec["skid_steer_share_of_duration"],
        "EQ-003": (ew / ec["dump_truck_earthwork_cy_per_day"] + import_cy / ec["dump_truck_import_cy_per_day"])
        * access_factor,
        "EQ-004": (irr + drain) / ec["trencher_lf_per_day"] * access_factor,
        "EQ-005": ((area + bunker) / ec["roller_sqft_per_day"] + ew / ec["roller_earthwork_cy_per_day"])
        * access_factor,
    }
    eq_basis = {
        "EQ-001": f"{ew:,.0f} CY / {ec['excavator_cy_per_day']} + {drain:,.0f} LF / {ec['excavator_drainage_lf_per_day']}"
                  f" + {import_cy:,.0f} import CY / {ec['excavator_import_cy_per_day']} per day, x access",
        "EQ-002": f"{ec['skid_steer_share_of_duration']:.0%} of {duration}-day duration",
        "EQ-003": f"{ew:,.0f} CY / {ec['dump_truck_earthwork_cy_per_day']} + {import_cy:,.0f} import CY / "
                  f"{ec['dump_truck_import_cy_per_day']} per day, x access",
        "EQ-004": f"{irr + drain:,.0f} LF trenching / {ec['trencher_lf_per_day']} LF per day, x access",
        "EQ-005": f"{area + bunker:,.0f} sf / {ec['roller_sqft_per_day']:,} + {ew:,.0f} CY / "
                  f"{ec['roller_earthwork_cy_per_day']} per day, x access",
    }
    equipment = []
    eq_ov = ov.get("equipment_days") or {}
    for eq_id, d in raw_days.items():
        if eq_id in excluded:
            continue
        days = int(eq_ov.get(eq_id, ceil_to(d) if d > 0 else 0))
        if days <= 0:
            continue
        E = data.equip(eq_id)
        rate = float(E["daily_rate_usd"])
        equipment.append({"code": eq_id, "name": E["equipment_name"], "use": E["typical_use"], "days": days,
                          "rate": rate, "total": r2(days * rate),
                          "basis": "Reviewer override" if eq_id in eq_ov else eq_basis[eq_id]})
    for x in ov.get("extra_lines") or []:
        if x.get("category") == "equipment":
            d, r = float(x["quantity"]), float(x["unit_cost"])
            equipment.append({"code": x.get("code", "ADD"), "name": x["description"], "use": "added",
                              "days": d, "rate": r, "total": r2(d * r), "basis": x.get("note", "Added")})

    subs = []
    for x in ov.get("extra_lines") or []:
        if x.get("category") == "subcontractors":
            q, r = float(x.get("quantity", 1)), float(x["unit_cost"])
            subs.append({"code": x.get("code", "SUB"), "description": x["description"], "quantity": q,
                         "unit": x.get("unit", "ls"), "unit_cost": r, "total": r2(q * r),
                         "sov": x.get("sov", "Other / added scope"), "basis": x.get("note", "Added by reviewer.")})

    # ---------------------------------------------------- comps & markups
    comp = find_comparables(data, p, cfg)
    if not comp.get("region_matched"):
        flag("INFO", "R-010", f"Location '{p.get('location')}' is not a specific historical region; comparables "
             "were matched on scope and site access only.")
    if comp.get("excluded_low_quality"):
        flag("INFO", "R-013", f"{comp['excluded_low_quality']} historical {ptype} record(s) flagged "
             "'minor_scope_gap' were excluded from comparables.")

    mat_total = r2(sum(m["total"] for m in materials))
    lab_total = r2(sum(l["total"] for l in labor))
    eq_total = r2(sum(e["total"] for e in equipment))
    sub_total = r2(sum(s["total"] for s in subs))
    direct = r2(mat_total + lab_total + eq_total + sub_total)  # R-006

    has_clarify = any(f["level"] == "CLARIFY" for f in flags)
    cc = cfg["contingency"]
    if "contingency_pct" in ov:
        cont_pct, cont_basis = float(ov["contingency_pct"]), "Reviewer override"
    else:
        cont_pct = cc["base_by_site_access"].get(access, 0.10)
        cont_basis = f"Base {cont_pct:.1%} for {access} site access"
        if has_clarify or verify_note:
            cont_pct += cc["uncertainty_adder"]
            cont_basis += f" + {cc['uncertainty_adder']:.1%} scope-uncertainty adder"
        if cont_pct > cc["max_pct"]:
            cont_pct, cont_basis = cc["max_pct"], cont_basis + f" (capped at {cc['max_pct']:.1%})"
    contingency = r2(direct * cont_pct)  # R-007

    mk = cfg["markup"]
    if "markup_pct" in ov:
        mk_pct, mk_basis = float(ov["markup_pct"]), "Reviewer override"
    elif mk["method"] == "comparables_median" and comp["stats"]:
        mk_pct = comp["stats"]["markup_pct_median"]
        mk_basis = f"Median OH&P of {comp['stats']['count']} closest historical comparables"
    else:
        mk_pct, mk_basis = mk["default_pct"], "Default"
    markup = r2((direct + contingency) * mk_pct)  # R-008
    total = r2(direct + contingency + markup)

    # ------------------------------------------------ calibration checks
    st = comp.get("stats") or {}
    if st:
        med = st["final_cost_median"]
        if total < st["final_cost_min"] or total > st["final_cost_max"]:
            flag("REVIEW", "R-010", f"Bid total ${total:,.0f} is outside the comparable range "
                 f"${st['final_cost_min']:,.0f}-${st['final_cost_max']:,.0f} (median ${med:,.0f}, "
                 f"{(total / med - 1):+.0%}). Check scope and productivity assumptions.")
    if trange:
        lo, hi = trange["illustrative_low_cost_usd"], trange["illustrative_high_cost_usd"]
        if not lo <= total <= hi:
            flag("REVIEW", "Ranges", f"Bid total ${total:,.0f} is outside the {ptype} illustrative range "
                 f"${lo:,.0f}-${hi:,.0f}.")
        if not trange["min_planned_days"] <= duration <= trange["max_planned_days"]:
            flag("REVIEW", "Ranges", f"Target duration {duration} days is outside the typical "
                 f"{trange['min_planned_days']}-{trange['max_planned_days']} day range for {ptype}.")
    if access == "constrained":
        flag("REVIEW", "R-007", "Constrained site access: confirm haul routes, staging and turf protection.")

    # schedule risk (R-012)
    over = st.get("overrun_days_mean", comp.get("type_stats", {}).get("overrun_days_mean", 0)) or 0
    sched_cont = max(math.ceil(over), 0)
    schedule = {
        "target_duration_days": duration,
        "comps_mean_overrun_days": over,
        "comps_max_overrun_days": st.get("overrun_days_max"),
        "type_overrun_p80_days": comp.get("type_stats", {}).get("overrun_days_p80"),
        "comps_mean_weather_delay_days": st.get("weather_delay_days_mean"),
        "suggested_schedule_contingency_days": sched_cont,
        "suggested_completion_window_days": duration + sched_cont,
    }
    if sched_cont >= 2:
        flag("REVIEW", "R-012", f"Comparable projects overran plan by {over:.1f} days on average; "
             f"consider quoting {duration}-{duration + sched_cont} working days.")

    # cross-check vs Sample_Bid_Lines
    sample_check = []
    s = data.samples[data.samples["project_id"].str.upper() == p["project_id"].upper()]
    for _, r in s.iterrows():
        ours = sum(m["order_qty"] for m in materials if data.material(m["code"])["material_name"] == r["line_item"]
                   ) if any(m["code"].startswith("MAT") for m in materials) else 0
        sample_check.append({"line_item": r["line_item"], "unit": r["unit"], "sample_qty": float(r["order_quantity"]),
                             "estimate_qty": float(ours), "difference": float(ours - r["order_quantity"])})
    big = [c for c in sample_check if abs(c["difference"]) > 5]
    if big:
        flag("INFO", "Sample check", "Differs from Sample_Bid_Lines on: " + "; ".join(
            f"{c['line_item']} ({c['estimate_qty']:g} vs sample {c['sample_qty']:g})" for c in big)
             + (" (expected: part of the irrigation pipe is carried as 4-in mainline)." if main_frac > 0 and irr > 0
                else ""))

    exclusions = [
        "Permits, fees, bonds and testing laboratory services unless listed.",
        "Rock excavation, unsuitable soil removal, or subsurface conditions differing from the request.",
        "Irrigation controllers, pump station, electrical, and wire unless listed.",
        "Grow-in maintenance, fertility program and watering after sod installation.",
        "Repairs to existing utilities not shown on the request.",
    ]
    level_order = {"CLARIFY": 0, "REVIEW": 1, "INFO": 2}
    flags.sort(key=lambda f: level_order[f["level"]])
    sov: dict[str, float] = {}
    for line in materials + labor + subs:
        sov[line["sov"]] = sov.get(line["sov"], 0) + line["total"]
    sov["General conditions, supervision & equipment"] = sov.get(
        "General conditions, supervision & equipment", 0) + eq_total

    return {
        "project": p, "request": req, "overrides": ov,
        "estimate_date": date.today().isoformat(),
        "review_status": "NEEDS CLARIFICATION" if has_clarify else "READY FOR REVIEW",
        "materials": materials, "labor": labor, "equipment": equipment, "subcontractors": subs,
        "crew": {"task_hours": r2(task_hours), "productive_hours_per_day": phd, "paid_hours_per_day": paid,
                 "workers_required": workers_required, "crew_required": crew_required, "type_min_crew": min_crew,
                 "type_max_crew": max_crew, "crew_size": crew_size, "foremen": foremen, "workers": workers,
                 "duration_days": duration, "access_factor": access_factor,
                 "comps_median_crew": st.get("crew_median")},
        "schedule": schedule,
        "summary": {"materials": mat_total, "labor": lab_total, "equipment": eq_total, "subcontractors": sub_total,
                    "direct_cost": direct, "contingency_pct": cont_pct, "contingency_basis": cont_basis,
                    "contingency": contingency, "markup_pct": mk_pct, "markup_basis": mk_basis,
                    "markup": markup, "total_bid": total},
        "schedule_of_values": {k: r2(v) for k, v in sov.items()},
        "comparables": comp, "flags": flags, "sample_check": sample_check, "exclusions": exclusions,
        "assumptions": {"material_factors": mf, "labor": lab_cfg, "site_access_factor": access_factor,
                        "equipment": ec},
    }


def compact_summary(est: dict) -> dict:
    from .explain import build_steps, final_answer

    """Small JSON for the LLM: totals, crew, schedule, flags, comps stats."""
    s = est["summary"]
    return {
        "project_id": est["project"]["project_id"], "project_name": est["project"]["project_name"],
        "project_type": est["project"]["project_type"], "location": est["project"]["location"],
        "site_access": est["project"]["site_access"], "review_status": est["review_status"],
        "totals": s,
        "materials": [{k: m[k] for k in ("code", "item", "unit", "order_qty", "unit_cost", "total")}
                      for m in est["materials"]],
        "labor": [{k: l[k] for k in ("code", "role", "hours", "rate", "total")} for l in est["labor"]],
        "equipment": [{k: e[k] for k in ("code", "name", "days", "rate", "total")} for e in est["equipment"]],
        "subcontractors": est["subcontractors"],
        "crew": est["crew"], "schedule": est["schedule"],
        "comparables_stats": est["comparables"].get("stats"),
        "type_stats": est["comparables"].get("type_stats"),
        "flags": est["flags"], "sample_check": est["sample_check"], "overrides": est["overrides"],
        "calculation_steps": build_steps(est),
        "final_answer": final_answer(est),
    }
