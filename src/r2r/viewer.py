"""Shared data layer for the dashboard and its static export.

Both views show *recorded* runs: a FloodModel whose model layer is replayed from a
live sweep (OtariClient replay mode, `replay_run` = that cell's live run). Replay needs
no keys and no gateway, and reproduces the live run call for call
(scripts/check_replay.py), so the dashboards work where Otari is not reachable.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pandas as pd
from pyproj import Transformer

from r2r.experiments import MODEL_SETS, SCENARIOS
from r2r.llm import COHORTS, OtariClient
from r2r.model import Amplifier, DistrictDisasterOffice, FloodModel, PanchayatOfficial, Scenario, Villager
from r2r.run import RESULTS

STATUS_CODE = {"home": 0, "evacuating": 1, "safe": 2, "caught": 3}
SERIES = (["tick", "evacuated_at_risk_share", "caught", "unneeded_evacuations", "mean_danger",
           "rumour_believers_share", "messages", "rumours", "misinfo_checked", "misinfo_flagged",
           "misinfo_tag_rate", "llm_calls", "cache_hits", "budget_events", "upstream_errors", "fallbacks"]
          + [f"{k}_{c}" for c in COHORTS for k in ("tokens", "cost", "latency_ms")])


def recorded_runs(sweep: str = "recorded") -> pd.DataFrame:
    path = RESULTS / "sweeps" / sweep / "runs.parquet"
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def recorded_run_id(model_set: str, scenario_name: str, seed: int = 42, sweep: str = "recorded") -> str | None:
    rec = recorded_runs(sweep)
    if rec.empty:
        return None
    hit = rec[(rec.model_set == model_set) & (rec.scenario_name == scenario_name) & (rec.seed == seed)]
    return None if hit.empty else str(hit.run_id.iloc[-1])


def replay_scenario(model_set: str, scenario_name: str, seed: int = 42) -> tuple[Scenario, OtariClient]:
    sc = dataclasses.replace(Scenario(seed=seed), policies=MODEL_SETS[model_set], **SCENARIOS[scenario_name])
    # keys are never sent in replay mode
    client = OtariClient({}, policies=dict(MODEL_SETS[model_set]), replay=True,
                         replay_run=recorded_run_id(model_set, scenario_name, seed))
    return sc, client


def make_model(model_set: str, scenario_name: str, seed: int = 42) -> FloodModel:
    sc, client = replay_scenario(model_set, scenario_name, seed)
    return FloodModel(sc, llm=client)


def _lonlat(model: FloodModel) -> Transformer:
    return Transformer.from_crs(model.crs, "EPSG:4326", always_xy=True)


def _ll(tr: Transformer, geom: Any) -> list[float]:
    lon, lat = tr.transform(geom.x, geom.y)
    return [round(lat, 5), round(lon, 5)]


def geography(model: FloodModel, simplify_m: float = 60.0) -> dict[str, Any]:
    """District layers in EPSG:4326 for web maps (simplified to keep the page small)."""
    d = model.district
    tr = _lonlat(model)

    def gj(gdf: pd.DataFrame) -> dict[str, Any]:
        g = gdf[["geometry"]].copy()
        g["geometry"] = g.geometry.simplify(simplify_m)
        return g.to_crs("EPSG:4326").__geo_interface__

    names = model.params.district.river_names
    rivers = d.rivers[d.rivers["name"].isin(names)] if "name" in d.rivers else d.rivers
    villages = [{"id": int(r.village_id), "name": str(r.name), "panchayat": int(r.panchayat),
                 "flood_zone": bool(r.in_flood_zone), "pos": _ll(tr, r.geometry)}
                for r in d.villages.itertuples()]
    return {"boundary": gj(d.boundary), "flood_zone": gj(d.flood_zone), "rivers": gj(rivers),
            "villages": villages, "district": model.params.district.slug}


def capture(model: FloodModel) -> dict[str, Any]:
    """Run `model` to the end and record what a viewer needs at every tick."""
    tr = _lonlat(model)
    villagers = sorted(model.agents_by_type[Villager], key=lambda a: a.unique_id)
    people = [{"id": v.unique_id, "village": v.village_id, "persona": v.persona, "at_risk": v.at_risk,
               "phone": v.has_phone, "home": _ll(tr, v.geometry)} for v in villagers]
    others = [{"kind": kind, "village": a.village_id, "pos": _ll(tr, a.geometry)}
              for kind, cls in (("official", PanchayatOfficial), ("amplifier", Amplifier),
                                ("ddo", DistrictDisasterOffice))
              for a in model.agents_by_type[cls]]

    def snap() -> list[list[float]]:
        return [[STATUS_CODE[v.status], round(v.danger, 3), round(v.rumour_belief, 3)] for v in villagers]

    states = [snap()]
    while model.running:
        model.step()
        states.append(snap())
    for p, v in zip(people, villagers):
        p["safe"] = _ll(tr, v.geometry) if v.status == "safe" else None

    mv = model.datacollector.get_model_vars_dataframe()
    series = {c: [None if pd.isna(x) else (round(float(x), 6)) for x in mv[c]] for c in SERIES if c in mv}
    series["silent_cohorts"] = [str(x) for x in mv["silent_cohorts"]]
    return {
        "scenario": model.params.label,
        "params": {k: getattr(model.params, k) for k in ("seed", "ticks", "warning_tick", "rumour_start",
                                                        "rising_tick", "flood_tick")},
        "policies": dict(model.llm.policies) if model.llm else {},
        "replay_run": model.llm.replay_run if model.llm else None,
        "replay_misses": model.llm.replay_misses if model.llm else 0,
        "villagers": people,
        "others": others,
        "states": states,
        "series": series,
        "messages": [{k: m[k] for k in ("id", "kind", "cohort", "tick", "text", "lang", "misinfo_flag",
                                        "misinfo_score", "reach") if k in m} for m in model.messages_table()],
    }


def sweep_table(sweep: str = "main") -> list[dict[str, Any]]:
    """One row per run of a sweep: final outcomes and Otari usage, for comparison views."""
    runs = recorded_runs(sweep)
    keep = ["model_set", "scenario_name", "seed", "evacuated_at_risk_share", "caught", "unneeded_evacuations",
            "rumour_believers_share", "misinfo_tag_rate", "llm_calls", "cache_hits", "budget_events",
            "upstream_errors", "fallbacks", "silent_cohorts", "live_run_id", "live_wall_s"]
    keep += [c for c in runs.columns if c.startswith(("tokens_", "cost_", "otari_"))]
    rows = runs[[c for c in keep if c in runs.columns]].to_dict("records")
    return [{k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in r.items()} for r in rows]
