"""R-010 historical calibration and R-012 schedule risk from Historical_Projects."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .data import BidData

# Request field -> historical field
FIELD_MAP = {
    "green_area_sqft": "green_or_tee_area_sqft",
    "rootzone_depth_in": "rootzone_depth_in",
    "drainage_pipe_lf": "drainage_pipe_lf",
    "bunker_area_sqft": "bunker_area_sqft",
    "irrigation_pipe_lf": "irrigation_pipe_lf",
    "sod_area_sqft": "sod_area_sqft",
    "earthwork_cy": "earthwork_cy",
}


def find_comparables(data: BidData, project: dict, cfg: dict, k: int | None = None) -> dict:
    ccfg = cfg["comparables"]
    k = k or ccfg["k"]
    h = data.history.copy()
    h["overrun_days"] = h["actual_duration_days"] - h["planned_duration_days"]
    same_type = h[h["project_type"] == project["project_type"]]
    excluded = same_type[same_type["data_quality"].isin(ccfg["exclude_data_quality"])]
    pool = same_type[~same_type["data_quality"].isin(ccfg["exclude_data_quality"])].copy()

    if pool.empty:
        return {"comparables": [], "stats": {}, "excluded_low_quality": len(excluded),
                "type_stats": {}, "method": "no comparables of this project type"}

    # scope distance on log scale, normalised by spread within the project type
    dist = np.zeros(len(pool))
    for req_f, hist_f in FIELD_MAP.items():
        hv = np.log1p(pool[hist_f].astype(float).values)
        rv = math.log1p(float(project.get(req_f) or 0))
        spread = np.std(np.log1p(same_type[hist_f].astype(float).values)) or 1.0
        dist += ((hv - rv) / spread) ** 2
    dist = np.sqrt(dist)

    region = str(project.get("location") or "")
    known_regions = set(h["region"].unique())
    region_known = region in known_regions
    if region_known:
        dist += np.where(pool["region"].values == region, 0.0, ccfg["region_mismatch_penalty"])
    dist += np.where(pool["site_access"].values == project.get("site_access"), 0.0,
                     ccfg["access_mismatch_penalty"])
    pool["similarity_distance"] = dist
    top = pool.nsmallest(k, "similarity_distance")

    cols = ["history_id", "region", "site_access", "final_cost_usd", "average_crew_size",
            "planned_duration_days", "actual_duration_days", "overrun_days", "weather_delay_days",
            "contingency_pct", "overhead_profit_markup_pct", "green_or_tee_area_sqft",
            "rootzone_depth_in", "drainage_pipe_lf", "irrigation_pipe_lf", "bunker_area_sqft",
            "sod_area_sqft", "earthwork_cy", "notes", "similarity_distance"]
    comps = top[cols].round({"similarity_distance": 3}).to_dict(orient="records")
    comps = [{k2: (v.item() if hasattr(v, "item") else v) for k2, v in c.items()} for c in comps]

    fc = top["final_cost_usd"]
    stats = {
        "count": int(len(top)),
        "final_cost_min": round(float(fc.min()), 2),
        "final_cost_median": round(float(fc.median()), 2),
        "final_cost_max": round(float(fc.max()), 2),
        "crew_median": float(top["average_crew_size"].median()),
        "planned_days_median": float(top["planned_duration_days"].median()),
        "overrun_days_mean": round(float(top["overrun_days"].mean()), 2),
        "overrun_days_max": int(top["overrun_days"].max()),
        "weather_delay_days_mean": round(float(top["weather_delay_days"].mean()), 2),
        "contingency_pct_median": float(top["contingency_pct"].median()),
        "markup_pct_median": float(top["overhead_profit_markup_pct"].median()),
    }
    type_stats = {
        "count": int(len(pool)),
        "final_cost_p10": round(float(pool["final_cost_usd"].quantile(0.10)), 2),
        "final_cost_median": round(float(pool["final_cost_usd"].median()), 2),
        "final_cost_p90": round(float(pool["final_cost_usd"].quantile(0.90)), 2),
        "overrun_days_mean": round(float(pool["overrun_days"].mean()), 2),
        "overrun_days_p80": float(pool["overrun_days"].quantile(0.80)),
    }
    return {
        "comparables": comps, "stats": stats, "type_stats": type_stats,
        "excluded_low_quality": int(len(excluded)), "region_matched": region_known,
        "method": ("Same project type, data_quality complete; ranked by log-scaled scope "
                   "distance" + (" + region" if region_known else "") + " + site access."),
    }
