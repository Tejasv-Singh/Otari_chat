"""Check that replay mode reproduces live runs (Phase 3 validation).

    uv run python scripts/check_replay.py --recorded recorded   # offline, every run of a live sweep
    uv run python scripts/check_replay.py --live [--sms-alert]  # makes one live run through Otari

--recorded NAME replays each live run of results/sweeps/NAME with the parameters it
ran with and checks that the replayed call log (status and text of every call) is
identical to the live one.

--live runs the current defaults:
  A  replay of the recorded live sweep's calls for this scenario
  B  a fresh live run through Otari
  C  replay of B's own calls
C == B shows that replay is exact. A vs B shows how far a fresh live run moves (the
models sample, so answers vary between live calls). B's calls are kept in
results/replay_check/ and are NOT merged into the shared replay cache.
"""

from __future__ import annotations

import argparse
import dataclasses
import json

import pandas as pd

from r2r.experiments import MODEL_SETS, SCENARIOS
from r2r.llm import OtariClient, load_keys
from r2r.model import FloodModel, Scenario
from r2r.run import RESULTS, run_scenario

KEYS = ("evacuated_at_risk_share", "caught", "unneeded_evacuations", "rumour_believers_share",
        "misinfo_tag_rate", "llm_calls", "replay_misses")
# Dynamics the "recorded" sweep ran with, before calibration (see CALIBRATION.md).
PRE_CALIBRATION = dict(flood_tick=15, relay_reach=0.6, doubt_gain=0.0, far_relevance=1.0, rising_rate=0.08)


def check_recorded(name: str) -> bool:
    keys, d = load_keys(), RESULTS / "sweeps" / name
    all_ok = True
    for row in pd.read_parquet(d / "runs.parquet").itertuples():
        live = pd.read_parquet(d / row.run_id / "llm_calls.parquet")
        sc = dataclasses.replace(Scenario(seed=int(row.seed)), policies=MODEL_SETS[row.model_set],
                                 **SCENARIOS[row.scenario_name], **PRE_CALIBRATION)
        c = OtariClient(keys, policies=MODEL_SETS[row.model_set], replay=True, replay_run=row.run_id)
        m = FloodModel(sc, llm=c)
        while m.running:
            m.step()
        same = [(r.status, r.text) for r in c.log] == list(zip(live.status, live.text))
        all_ok &= same and c.replay_misses == 0
        print(f"{row.run_id} {row.model_set:7} {row.scenario_name:15} calls={len(live):4} "
              f"identical={same} misses={c.replay_misses}", flush=True)
    print(f"\nall recorded runs replay exactly: {all_ok}")
    return all_ok


def check_live(sms_alert: bool) -> bool:
    out = RESULTS / "replay_check"
    out.mkdir(parents=True, exist_ok=True)
    keys, sc = load_keys(), Scenario(sms_alert=sms_alert)
    live_log = out / f"live_{sc.label}.jsonl"
    live_log.unlink(missing_ok=True)

    rec = pd.read_parquet(RESULTS / "sweeps" / "recorded" / "runs.parquet")
    hit = rec[(rec.model_set == "qwen3") & (rec.scenario_name == ("sms_alert" if sms_alert else "baseline"))]
    a = run_scenario(sc, OtariClient(keys, replay=True, replay_run=hit.run_id.iloc[-1]), save=False)
    live = OtariClient(keys, cache_path=live_log)
    b = run_scenario(sc, live, out_root=out)
    c = run_scenario(sc, OtariClient(keys, replay=True, replay_run=live.run_id, cache_path=live_log), save=False)

    rows = {"A replay(recorded)": a, "B live": b, "C replay(B)": c}
    print(f"{'':20}" + "".join(f"{k:>24}" for k in KEYS))
    for name, s in rows.items():
        print(f"{name:20}" + "".join(f"{str(s.get(k)):>24}" for k in KEYS))
    exact = all(b[k] == c[k] for k in KEYS if k != "replay_misses") and c["replay_misses"] == 0
    print(f"\nreplay exact (C == B): {exact}")
    (out / f"check_{sc.label}.json").write_text(json.dumps(
        {"scenario": sc.label, "exact": exact, **{n: {k: s.get(k) for k in KEYS} for n, s in rows.items()}},
        indent=1, default=str), encoding="utf-8")
    return exact


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recorded", metavar="NAME")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--sms-alert", action="store_true")
    args = ap.parse_args()
    ok = True
    if args.recorded:
        ok &= check_recorded(args.recorded)
    if args.live:
        ok &= check_live(args.sms_alert)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
