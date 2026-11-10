"""Offline calibration of the rule-based dynamics against stated plausibility targets.

    uv run python -m r2r.calibrate

Replays recorded Otari calls (data/cache/llm/prompts.jsonl, written by live runs of
`r2r.run`), so the grid search makes no model calls. Which messages get generated does
not depend on these parameters, so replay matches what a live run would do; a replay
miss (a message/persona pair never read live) counts as "not taken in" and is reported.

Targets were written down before the search and only constrain the *official warning
channel*. Rumour and model-choice outcomes are deliberately not calibrated: tuning
rumours until they matter would be tuning towards the research answer.
  T1  baseline: 35-65% of at-risk villagers evacuate before the flood
  T2  an official SMS alert raises that share by at least 15 points
  T3  baseline: at most 25% of villagers outside the flood zone evacuate anyway
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import logging
from pathlib import Path

import pandas as pd

from r2r.geodata import load_district
from r2r.llm import OtariClient
from r2r.model import FloodModel, Scenario, Villager

GRID: dict[str, list[float | int]] = {
    "relay_reach": [0.3, 0.45, 0.6],
    "rising_rate": [0.02, 0.04, 0.06],
    "far_relevance": [0.3, 0.6],
    "flood_tick": [12, 14],
    "threshold_mean": [0.6, 0.7],
}
FIXED = {"doubt_gain": 0.2}   # assumption, not calibrated (see module docstring)
T1, T2_MIN, T3_MAX = (0.35, 0.65), 0.15, 0.25
OUT = Path(__file__).resolve().parents[2] / "results" / "calibration.json"
RECORDED = OUT.parent / "sweeps" / "recorded"   # the live sweep whose calls are replayed


def recorded_run(scenario_name: str) -> str:
    """run_id of the qwen3 live run of this scenario in the recorded sweep."""
    rec = pd.read_parquet(RECORDED / "runs.parquet")
    return str(rec[(rec.model_set == "qwen3") & (rec.scenario_name == scenario_name)].run_id.iloc[-1])


def run_replay(sc: Scenario, district, keys, run: str) -> tuple[dict[str, float], int]:  # type: ignore[no-untyped-def]
    llm = OtariClient(keys, replay=True, replay_run=run)
    m = FloodModel(sc, llm=llm, district=district)
    while m.running:
        m.step()
    final = m.datacollector.get_model_vars_dataframe().iloc[-1]
    not_at_risk = sum(not v.at_risk for v in m.agents_by_type[Villager])
    return {
        "evac": float(final.evacuated_at_risk_share),
        "unneeded_share": float(final.unneeded_evacuations) / max(1, not_at_risk),
        "rumour_believers": float(final.rumour_believers_share),
    }, llm.replay_misses


def score(base: dict[str, float], sms: dict[str, float]) -> float:
    """0 when every target is met; otherwise the total distance outside the targets."""
    lo, hi = T1
    gap = max(0.0, lo - base["evac"]) + max(0.0, base["evac"] - hi)
    gap += max(0.0, T2_MIN - (sms["evac"] - base["evac"]))
    gap += max(0.0, base["unneeded_share"] - T3_MAX)
    return round(gap, 4)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    keys: dict[str, str] = {}   # replay only: no key is ever sent
    district = load_district(Scenario().district)
    runs = {"baseline": recorded_run("baseline"), "sms_alert": recorded_run("sms_alert")}
    rows = []
    names = list(GRID)
    for values in itertools.product(*GRID.values()):
        params = dict(zip(names, values)) | FIXED
        base_sc = dataclasses.replace(Scenario(), **params)
        base, m1 = run_replay(base_sc, district, keys, runs["baseline"])
        sms, m2 = run_replay(dataclasses.replace(base_sc, sms_alert=True), district, keys, runs["sms_alert"])
        rows.append({"params": params, "baseline": base, "sms_alert": sms, "score": score(base, sms),
                     "replay_misses": m1 + m2})
        print(f"{params}  base={base['evac']:.2f} sms={sms['evac']:.2f} "
              f"unneeded={base['unneeded_share']:.2f} score={rows[-1]['score']} misses={m1 + m2}", flush=True)
    rows.sort(key=lambda r: (r["score"], abs(r["baseline"]["evac"] - 0.5), r["replay_misses"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"targets": {"T1": T1, "T2_min": T2_MIN, "T3_max": T3_MAX}, "fixed": FIXED,
                               "grid": GRID, "ranked": rows}, indent=1), encoding="utf-8")
    met = [r for r in rows if r["score"] == 0]
    print(f"\n{len(met)} of {len(rows)} configurations meet every target. Best 5:")
    for r in rows[:5]:
        print(json.dumps({k: r[k] for k in ("params", "score", "replay_misses")}),
              {k: round(v, 2) for k, v in r["baseline"].items()}, "sms", round(r["sms_alert"]["evac"], 2))


if __name__ == "__main__":
    main()
