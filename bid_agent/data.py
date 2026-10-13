"""Load the bid workbook and assumptions."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "assumptions.json"

SCOPE_FIELDS = [
    "green_area_sqft", "rootzone_depth_in", "drainage_pipe_lf", "bunker_area_sqft",
    "irrigation_pipe_lf", "sod_area_sqft", "earthwork_cy",
]


def load_config(path: str | os.PathLike | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as f:
        return json.load(f)


class FolderLockError(PermissionError):
    pass


# When a workspace folder is active, data/ secrets/ output/ live there and the agent may
# ONLY touch that folder. Otherwise it falls back to its own folder (ROOT).
WORKSPACE: Path | None = None


def base_dir() -> Path:
    return WORKSPACE or ROOT


def resolve(path: str | os.PathLike) -> Path:
    """Resolve a path and refuse anything outside the allowed folder (folder lock).

    Relative paths are taken from the workspace (or the agent folder when no workspace is
    set). Symlinks and '..' are resolved first, so they cannot escape.
    """
    base = base_dir().resolve()
    p = Path(path).expanduser()
    p = (p if p.is_absolute() else base / p).resolve()
    if p != base and base not in p.parents:
        raise FolderLockError(
            f"Blocked: '{p}' is outside the allowed folder '{base}'. "
            "Copy the file into that folder (e.g. data/) and point to it there.")
    return p


@dataclass
class BidData:
    projects: pd.DataFrame
    materials: pd.DataFrame
    equipment: pd.DataFrame
    history: pd.DataFrame
    rules: pd.DataFrame
    labor: pd.DataFrame
    samples: pd.DataFrame
    ranges: pd.DataFrame
    source: Path

    # --- convenience lookups -------------------------------------------------
    def project(self, project_id: str) -> dict:
        pid = project_id.strip().upper()
        row = self.projects[self.projects["project_id"].str.upper() == pid]
        if row.empty:
            raise KeyError(
                f"Project '{project_id}' not found. Known IDs: "
                + ", ".join(self.projects["project_id"].tolist())
            )
        rec = row.iloc[0].to_dict()
        return {k: (v.item() if hasattr(v, "item") else v) for k, v in rec.items()}

    def material(self, mat_id: str) -> dict:
        return self.materials.set_index("material_id").loc[mat_id].to_dict()

    def labor_code(self, code: str) -> dict:
        return self.labor.set_index("labor_code").loc[code].to_dict()

    def equip(self, eq_id: str) -> dict:
        return self.equipment.set_index("equipment_id").loc[eq_id].to_dict()

    def type_range(self, project_type: str) -> dict | None:
        r = self.ranges[self.ranges["project_type"] == project_type]
        return None if r.empty else r.iloc[0].to_dict()


@lru_cache(maxsize=4)
def _load(path: str) -> BidData:
    x = pd.read_excel(path, sheet_name=None)
    need = ["Project_Requests", "Material_Catalog", "Equipment_Rates", "Historical_Projects",
            "Estimation_Rules", "Labor_Productivity", "Sample_Bid_Lines", "Project_Type_Ranges"]
    missing = [s for s in need if s not in x]
    if missing:
        raise ValueError(f"Workbook is missing sheet(s): {missing}")
    return BidData(
        projects=x["Project_Requests"], materials=x["Material_Catalog"],
        equipment=x["Equipment_Rates"], history=x["Historical_Projects"],
        rules=x["Estimation_Rules"], labor=x["Labor_Productivity"],
        samples=x["Sample_Bid_Lines"], ranges=x["Project_Type_Ranges"], source=Path(path),
    )


def load_data(cfg: dict, workbook: str | None = None) -> BidData:
    if workbook is None and WORKSPACE is not None:
        from .workspace import find_workbook
        return _load(str(find_workbook(cfg)))
    return _load(str(resolve(workbook or cfg["workbook_path"])))
