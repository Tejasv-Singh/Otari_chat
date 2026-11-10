"""Run one scenario against the local Otari gateway and save everything it produced.

    uv run python -m r2r.run --seed 42 --sms-alert --fact-check --local-language

Writes results/<run_id>/: model.parquet (DataCollector model vars), agents.parquet,
messages.json, llm_calls.parquet, otari_usage.json (Otari's own per-key usage for the
run, filtered by the run tag), summary.json.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd

from r2r.llm import COHORTS, OtariClient, load_keys
from r2r.model import FloodModel, Scenario, Villager

RESULTS = Path(__file__).resolve().parents[2] / "results"


class GatewayUnavailable(RuntimeError):
    pass


def preflight(client: OtariClient) -> None:
    """Refuse to start a run against an unreachable gateway (per otari
    docs/self-hosted-quickstart.md step 3, GET /api/v1/health)."""
    try:
        r = client._http.get(f"{client.base_url}/health", timeout=10)
        ok = r.status_code == 200 and "healthy" in r.text
    except Exception as exc:  # connection refused, dropped, timed out
        raise GatewayUnavailable(f"Otari health check failed: {type(exc).__name__}") from exc
    if not ok:
        raise GatewayUnavailable(f"Otari health check failed: HTTP {r.status_code}")


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _without_emails(obj: Any) -> Any:
    """otari.ai labels usage rows with the account's email address. Results are committed
    and published, so no address is ever written to disk."""
    if isinstance(obj, dict):
        return {k: _without_emails(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_without_emails(v) for v in obj]
    return _EMAIL.sub("<account>", obj) if isinstance(obj, str) else obj


def run_scenario(scenario: Scenario, client: OtariClient | None = None, out_root: Path = RESULTS,
                 save: bool = True) -> dict[str, Any]:
    client = client or OtariClient(load_keys())
    if not client.replay:
        preflight(client)
    t0 = time.perf_counter()
    model = FloodModel(scenario, llm=client)
    while model.running:
        model.step()
        last = model.datacollector.get_model_vars_dataframe().iloc[-1]
        logging.info("tick %2d  evac(at-risk)=%.2f  danger=%.2f  rumour=%.2f  calls=%d cache=%d silent=%s",
                     model.tick, last.evacuated_at_risk_share, last.mean_danger, last.rumour_believers_share,
                     last.llm_calls, last.cache_hits, last.silent_cohorts or "-")
    wall = time.perf_counter() - t0
    mv = model.datacollector.get_model_vars_dataframe()
    final = mv.iloc[-1].to_dict()
    summary = {
        "run_id": client.run_id,
        "scenario": scenario.label,
        "policies": dict(client.policies),
        "seed": scenario.seed,
        "wall_s": round(wall, 1),
        "villagers": len(model.agents_by_type[Villager]),
        "agents": len(model.agents) - len(model.village_agents),
        **{k: final[k] for k in ("evacuated_at_risk_share", "caught", "unneeded_evacuations",
                                 "rumour_believers_share", "messages", "rumours", "refusals", "misinfo_tag_rate",
                                 "llm_calls", "cache_hits", "budget_events", "upstream_errors", "fallbacks",
                                 "silent_cohorts")},
        # A run where many calls never reached a model measures the outage, not the models.
        "replay": client.replay,
        "replay_misses": client.replay_misses,
        "valid": client.replay_misses == 0 and bool(final["upstream_errors"] <= 0.1 * max(1, final["llm_calls"] + final["upstream_errors"])),
        "gateway": client.gateway,
        "after_action_report": model.after_action_report,
        "tokens": {c: final[f"tokens_{c}"] for c in COHORTS},
        "cost_usd_notional": {c: final[f"cost_{c}"] for c in COHORTS},
    }
    if save:
        out = out_root / client.run_id
        out.mkdir(parents=True, exist_ok=True)
        mv.assign(run_id=client.run_id, scenario=scenario.label).to_parquet(out / "model.parquet")
        av = model.datacollector.get_agenttype_vars_dataframe(Villager).reset_index()
        av.assign(run_id=client.run_id).to_parquet(out / "agents.parquet")
        (out / "messages.json").write_text(json.dumps(model.messages_table(), ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        pd.DataFrame([dataclasses.asdict(r) for r in client.log]).to_parquet(out / "llm_calls.parquet")
        # Replayed runs never reach Otari; their gateway usage is the recorded live run's.
        usage = {} if client.replay else {c: _without_emails(client.key_usage(c)) for c in COHORTS}
        (out / "otari_usage.json").write_text(json.dumps(usage, indent=1), encoding="utf-8")
        summary["otari_usage_totals"] = {c: (u.get("totals") or {}) for c, u in usage.items()}
        # Built-in tools Otari ran for each cohort (e.g. code_execution), from its usage API.
        summary["otari_tool_calls"] = {c: sum(t.get("calls", 0) for t in (u.get("by_tool") or []))
                                       for c, u in usage.items()}
        if model.after_action_report:
            (out / "after_action.md").write_text(model.after_action_report + "\n", encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
        summary["out_dir"] = str(out)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ticks", type=int, default=20)
    ap.add_argument("--sms-alert", action="store_true")
    ap.add_argument("--fact-check", action="store_true")
    ap.add_argument("--local-language", action="store_true")
    ap.add_argument("--villager-policy", default=None, help="Otari routing policy for villagers/amplifiers")
    ap.add_argument("--official-policy", default=None, help="Otari routing policy for officials/DDO")
    ap.add_argument("--model-set", default=None,
                    help="a model family from r2r.experiments.MODEL_SETS, e.g. llama-hosted or gptoss-hosted "
                         "(those run through otari.ai, see HOSTED.md); overrides the two policy flags")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    policies: dict[str, str] = {}
    if args.villager_policy:
        policies |= {"villager": args.villager_policy, "amplifier": args.villager_policy}
    if args.official_policy:
        policies |= {"official": args.official_policy, "ddo": args.official_policy}
    sc = Scenario(seed=args.seed, ticks=args.ticks, sms_alert=args.sms_alert, fact_check=args.fact_check,
                  local_language=args.local_language, policies=policies or None)
    client = None
    if args.model_set:
        from r2r.experiments import GATEWAY, scenario_for   # imported here: experiments imports this module
        from r2r.llm import make_client
        sc = scenario_for(args.model_set, "baseline", args.seed, ticks=args.ticks, sms_alert=args.sms_alert,
                          fact_check=args.fact_check, local_language=args.local_language)
        client = make_client(GATEWAY[args.model_set])
    print(json.dumps(run_scenario(sc, client), indent=1, default=str))


if __name__ == "__main__":
    main()
