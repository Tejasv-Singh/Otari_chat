"""Answer the research question from a sweep: do conclusions change with the model?

    uv run python -m r2r.analyze --name main

For each model family, interventions are ranked by evacuation of at-risk villagers
(ties broken by fewer people caught). The rankings are then compared across families
with Kendall's tau, and the per-run Otari numbers are summarised. Writes
results/sweeps/<name>/RESULTS.md.
"""

from __future__ import annotations

import argparse
import itertools

import pandas as pd

from r2r.run import RESULTS

OUTCOMES = ["evacuated_at_risk_share", "caught", "unneeded_evacuations", "rumour_believers_share",
            "misinfo_tag_rate"]


def kendall_tau(a: list[str], b: list[str]) -> float:
    """Kendall's tau between two rankings of the same items (1 = identical order)."""
    pos_a, pos_b = {x: i for i, x in enumerate(a)}, {x: i for i, x in enumerate(b)}
    pairs = list(itertools.combinations(a, 2))
    s = sum(1 if (pos_a[x] - pos_a[y]) * (pos_b[x] - pos_b[y]) > 0 else -1 for x, y in pairs)
    return round(s / len(pairs), 3) if pairs else 1.0


def rank_interventions(df: pd.DataFrame) -> list[str]:
    g = df.groupby("scenario_name")[["evacuated_at_risk_share", "caught"]].mean()
    return list(g.sort_values(["evacuated_at_risk_share", "caught"], ascending=[False, True]).index)


def report(runs: pd.DataFrame) -> str:
    lines = ["# Sweep results", ""]
    n_seeds = runs["seed"].nunique()
    lines += [f"{len(runs)} runs: {runs['model_set'].nunique()} model families x "
              f"{runs['scenario_name'].nunique()} scenarios x {n_seeds} seed(s).", ""]
    if n_seeds == 1:
        lines += ["> One seed per cell: differences are single realisations, not estimates with error bars.", ""]

    lines += ["## Outcomes by model family and intervention", ""]
    table = runs.groupby(["model_set", "scenario_name"])[OUTCOMES].mean().round(3)
    lines += [table.to_markdown(), ""]

    lines += ["## Does the intervention ranking depend on the model?", ""]
    ranks = {m: rank_interventions(d) for m, d in runs.groupby("model_set")}
    for m, r in ranks.items():
        lines.append(f"- **{m}**: " + " > ".join(r))
    lines.append("")
    for a, b in itertools.combinations(ranks, 2):
        tau = kendall_tau(ranks[a], ranks[b])
        same_top = ranks[a][0] == ranks[b][0]
        lines.append(f"- {a} vs {b}: Kendall tau = {tau}; best intervention "
                     f"{'agrees' if same_top else 'DIFFERS'} ({ranks[a][0]} vs {ranks[b][0]})")
    lines.append("")

    lines += ["## Effect of each intervention vs baseline (evacuation share, points)", ""]
    eff = runs.groupby(["model_set", "scenario_name"])["evacuated_at_risk_share"].mean().unstack(0)
    if "baseline" in eff.index:
        lines += [((eff - eff.loc["baseline"]) * 100).round(1).drop(index="baseline").to_markdown(), ""]

    lines += ["## Otari usage per run (mean)", ""]
    cols = [c for c in runs.columns if c.startswith(("tokens_", "cost_", "otari_latency_ms_"))]
    # A replayed sweep's own wall time is the replay's; the live run's time is what matters.
    cols += ["llm_calls", "cache_hits", "budget_events", "fallbacks",
             "live_wall_s" if "live_wall_s" in runs.columns else "wall_s"]
    lines += [runs.groupby("model_set")[cols].mean().round(6).T.to_markdown(), ""]
    silent = runs.groupby("model_set")["silent_cohorts"].apply(lambda s: sorted(set(",".join(s.fillna("")).split(",")) - {""}))
    lines += ["Cohorts that ran out of their per-run Otari budget: "
              + "; ".join(f"{m}: {', '.join(v) or 'none'}" for m, v in silent.items()), ""]
    lines += ["Costs are notional (local models priced per size class in otari/config.yml), not real charges.", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default="main")
    args = ap.parse_args()
    d = RESULTS / "sweeps" / args.name
    text = report(pd.read_parquet(d / "runs.parquet"))
    (d / "RESULTS.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
