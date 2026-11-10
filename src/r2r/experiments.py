"""Experiment runner: sweep intervention x model family x seed through Otari.

    uv run python -m r2r.experiments --name main --models qwen3 gemma3 --seeds 42

Each run gets a fresh run_id, so a fresh per-run Otari budget for every cohort
(service-key end users, see otari/config.yml). Results go to results/sweeps/<name>/:
  runs.parquet      one row per run: outcomes, tokens, notional cost, Otari usage
  model.parquet     every run's DataCollector model variables, tick by tick
  <run_id>/         the per-run files written by r2r.run
Rerunning the same command resumes: finished (model, scenario, seed) cells are skipped.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

from r2r.llm import OtariClient, load_keys
from r2r.model import Scenario
from r2r.run import RESULTS, run_scenario

# Model families: cohort -> Otari routing policy (otari/config.yml `routing.policies`).
MODEL_SETS: dict[str, dict[str, str]] = {
    "qwen3": {"villager": "villager", "amplifier": "villager", "official": "official", "ddo": "official"},
    "gemma3": {"villager": "villager-gemma", "amplifier": "villager-gemma",
               "official": "official-gemma", "ddo": "official-gemma"},
}
SCENARIOS: dict[str, dict[str, bool]] = {
    "baseline": {},
    "sms_alert": {"sms_alert": True},
    "fact_check": {"fact_check": True},
    "local_language": {"local_language": True},
    "all": {"sms_alert": True, "fact_check": True, "local_language": True},
}


def flatten(summary: dict[str, Any]) -> dict[str, Any]:
    row = {k: v for k, v in summary.items() if not isinstance(v, dict)}
    for c, n in summary.get("tokens", {}).items():
        row[f"tokens_{c}"] = n
    for c, n in summary.get("cost_usd_notional", {}).items():
        row[f"cost_{c}"] = n
    for c, u in summary.get("otari_usage_totals", {}).items():
        row[f"otari_requests_{c}"] = u.get("request_count")
        row[f"otari_tokens_{c}"] = u.get("total_tokens")
        row[f"otari_latency_ms_{c}"] = u.get("avg_latency_ms")
        row[f"otari_errors_{c}"] = u.get("error_count")
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="main")
    ap.add_argument("--models", nargs="+", default=list(MODEL_SETS), choices=list(MODEL_SETS))
    ap.add_argument("--scenarios", nargs="+", default=list(SCENARIOS), choices=list(SCENARIOS))
    ap.add_argument("--seeds", nargs="+", type=int, default=[42])
    ap.add_argument("--replay-from", default=None,
                    help="replay the LLM layer recorded by an earlier live sweep of this name instead of calling "
                         "Otari; Otari usage columns are copied from that sweep")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    out = RESULTS / "sweeps" / args.name
    out.mkdir(parents=True, exist_ok=True)
    runs_path, model_path = out / "runs.parquet", out / "model.parquet"
    runs = pd.read_parquet(runs_path) if runs_path.exists() else pd.DataFrame()
    done = set(zip(runs.get("model_set", []), runs.get("scenario_name", []), runs.get("seed", [])))
    keys = load_keys()
    rec = pd.read_parquet(RESULTS / "sweeps" / args.replay_from / "runs.parquet") if args.replay_from else None
    cells = [(m, s, seed) for seed in args.seeds for m in args.models for s in args.scenarios]
    print(f"{len(cells)} cells, {len(done & set(cells))} already done -> {out}", flush=True)

    for i, (mset, sname, seed) in enumerate(cells, 1):
        if (mset, sname, seed) in done:
            continue
        sc = dataclasses.replace(Scenario(seed=seed), policies=MODEL_SETS[mset], **SCENARIOS[sname])
        live = None
        if rec is not None:
            hit = rec[(rec.model_set == mset) & (rec.scenario_name == sname) & (rec.seed == seed)]
            if hit.empty:
                print(f"[{i}/{len(cells)}] {mset} {sname} seed={seed}: no recorded live run, skipped", flush=True)
                continue
            live = hit.iloc[-1]
        t0 = time.perf_counter()
        # Replay serves this cell's own recorded live run first (its answers, refusals and
        # outages in order); see OtariClient.replay_run.
        client = OtariClient(keys, replay=live is not None, replay_run=None if live is None else live["run_id"])
        summary = run_scenario(sc, client=client, out_root=out)
        if not summary["valid"]:
            print(f"[{i}/{len(cells)}] {mset} {sname} seed={seed}: INVALID ({summary['upstream_errors']} upstream "
                  f"errors), not recorded; rerun the command to retry this cell", flush=True)
            continue
        row = flatten(summary) | {"model_set": mset, "scenario_name": sname, "seed": seed}
        if live is not None:
            for c in [c for c in rec.columns if c.startswith("otari_")] + ["run_id", "wall_s"]:
                row[f"live_{c}" if c in ("run_id", "wall_s") else c] = live[c]
        runs = pd.concat([runs, pd.DataFrame([row])], ignore_index=True)
        runs.to_parquet(runs_path)
        mv = pd.read_parquet(Path(summary["out_dir"]) / "model.parquet").assign(
            model_set=mset, scenario_name=sname, seed=seed)
        prev = pd.read_parquet(model_path) if model_path.exists() else pd.DataFrame()
        pd.concat([prev, mv], ignore_index=True).to_parquet(model_path)
        print(f"[{i}/{len(cells)}] {mset:7} {sname:15} seed={seed}  evac={row['evacuated_at_risk_share']:.2f} "
              f"caught={row['caught']} rumour_believers={row['rumour_believers_share']:.2f} "
              f"tag_rate={row['misinfo_tag_rate']:.2f} calls={row['llm_calls']} "
              f"silent={row['silent_cohorts'] or '-'}  {time.perf_counter() - t0:.0f}s", flush=True)

    cols = ["model_set", "scenario_name", "seed", "evacuated_at_risk_share", "caught", "rumour_believers_share",
            "misinfo_tag_rate", "llm_calls", "wall_s"]
    print(runs[cols].to_string(index=False))
    (out / "README.json").write_text(json.dumps({"models": MODEL_SETS, "scenarios": SCENARIOS,
                                                 "seeds": args.seeds}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
