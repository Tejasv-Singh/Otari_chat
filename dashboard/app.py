"""Rumour-to-Relief dashboard (Solara, on Mesa's visualization stack).

    uv run solara run dashboard/app.py

Steps a FloodModel tick by tick in the browser. The model layer is *replayed* from
the recorded live sweep (results/sweeps/recorded, see r2r.viewer), so the dashboard
makes no model calls and needs neither Otari nor API keys. Pick a model family and
an intervention, press Reset, then Play or Step.

Needs the local caches written by earlier phases: data/cache/<district>/ (r2r.prepare)
and data/cache/llm/prompts.jsonl (the live sweep).
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import solara
from matplotlib.figure import Figure
from mesa.visualization import SolaraViz, make_plot_component
from mesa.visualization.utils import update_counter
from mesa_geo.visualization import make_geospace_component

from r2r.experiments import GATEWAY, MODEL_SETS, SCENARIOS
from r2r.model import Amplifier, DistrictDisasterOffice, FloodModel, PanchayatOfficial, Villager
from r2r.viewer import recorded_families, recorded_runs, replay_scenario
from r2r.space import Village

# Status colours (also used by the static export, docs/index.html).
STATUS_COLOUR = {"home": "#8a94a6", "evacuating": "#e0a100", "safe": "#1f9d55", "caught": "#d64545"}
COHORT_COLOUR = {"villager": "#3b6fd8", "official": "#7c4dcc", "amplifier": "#d6457a", "ddo": "#222222"}
FAMILY_COLOUR = {"qwen3": "#3b6fd8", "gemma3": "#e0a100", "llama-hosted": "#7c4dcc", "gptoss-hosted": "#1f9d8f",
                 "routed-hosted": "#d6457a"}


class ReplayFloodModel(FloodModel):
    """A FloodModel built from keyword parameters (as SolaraViz requires), replaying
    the recorded live run of the chosen (model family, intervention) cell."""

    def __init__(self, model_family: str = "qwen3", intervention: str = "baseline", seed: int = 42) -> None:
        sc, client = replay_scenario(model_family, intervention, seed)
        super().__init__(sc, llm=client)
        self.model_family = model_family
        # Background layers: plain GeoDataFrames are drawn as GeoJSON by mesa-geo's map.
        self.space.add_layer(self.district.flood_zone[["geometry"]])
        names = sc.district.river_names
        rivers = self.district.rivers
        self.space.add_layer(rivers[rivers["name"].isin(names)][["geometry"]] if "name" in rivers else rivers)


def titled(title: str, ylabel: str):  # type: ignore[no-untyped-def]
    """post_process hook for make_plot_component: a readable title and axis label."""
    def post(ax) -> None:  # type: ignore[no-untyped-def]
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("tick")
        ax.set_ylabel(ylabel)
    return post


def portrayal(agent) -> dict:  # type: ignore[no-untyped-def]
    base = {"marker_type": "CircleMarker", "weight": 1, "opacity": 1, "fill_opacity": 0.9}
    if isinstance(agent, Villager):
        believer = agent.rumour_belief > 0.5
        return base | {"radius": 5, "fill_color": STATUS_COLOUR[agent.status],
                       "color": "#d6457a" if believer else "#ffffff", "weight": 3 if believer else 1}
    if isinstance(agent, Village):
        return base | {"radius": 3, "color": "#1d4ed8" if agent.in_flood_zone else "#6b7280",
                       "fill_color": "#ffffff", "fill_opacity": 1}
    cohort = ("official" if isinstance(agent, PanchayatOfficial) else "amplifier" if isinstance(agent, Amplifier)
              else "ddo" if isinstance(agent, DistrictDisasterOffice) else "villager")
    return base | {"radius": 6, "fill_color": COHORT_COLOUR[cohort], "color": "#ffffff"}


@solara.component
def Header(model: ReplayFloodModel) -> None:
    update_counter.get()
    mv = model.datacollector.get_model_vars_dataframe().iloc[-1]
    p = model.params
    phase = ("before the warning" if model.tick < p.warning_tick else "warning issued" if model.tick < p.rising_tick
             else "river rising" if model.tick < p.flood_tick else "flash flood")
    run = model.llm.replay_run if model.llm else None
    where = "otari.ai" if GATEWAY.get(model.model_family) == "hosted" else "self-hosted Otari"
    solara.Markdown(
        f"### {model.model_family} ({where}) · {p.label} · tick {model.tick}/{p.ticks} ({phase})\n"
        f"**{mv.evacuated_at_risk_share:.0%}** of at-risk villagers out · **{int(mv.caught)}** caught · "
        f"**{mv.rumour_believers_share:.0%}** believe a rumour · guardrail tag rate "
        f"**{mv.misinfo_tag_rate:.0%}** · silent cohorts: {mv.silent_cohorts or 'none'}\n\n"
        f"<small>Replaying live run `{run or 'none recorded'}` — no model calls. "
        f"Villager fill: <span style='color:{STATUS_COLOUR['home']}'>home</span> / "
        f"<span style='color:{STATUS_COLOUR['evacuating']}'>evacuating</span> / "
        f"<span style='color:{STATUS_COLOUR['safe']}'>safe</span> / "
        f"<span style='color:{STATUS_COLOUR['caught']}'>caught</span>; pink ring = believes a rumour. "
        f"Larger dots: <span style='color:{COHORT_COLOUR['official']}'>officials</span>, "
        f"<span style='color:{COHORT_COLOUR['amplifier']}'>amplifiers</span>, "
        f"<span style='color:{COHORT_COLOUR['ddo']}'>district office</span>. Blue: flood zone and rivers. "
        f"Page 1: belief, cost per cohort and guardrail tag rate over time. Page 2: model comparison.</small>"
    )


@solara.component
def Messages(model: ReplayFloodModel) -> None:
    update_counter.get()
    msgs = model.messages_table()[-8:][::-1]
    lines = []
    if model.after_action_report:
        lines += ["#### After-action report (numbers computed with Otari's code-execution tool)",
                  model.after_action_report, ""]
    refused = sum(model.refusals.values())
    lines.append("#### Latest messages (model-written)" + (f" · {refused} requests refused by the model" if refused else ""))
    if not msgs:
        lines.append("_No messages yet._")
    for m in msgs:
        tag = " 🚩 **tagged**" if m.get("misinfo_flag") else ""
        text = " ".join(str(m["text"]).split())[:260]
        lines.append(f"- t{m['tick']} **{m['kind']}** ({m['cohort']}, {m['lang']}, reach {m['reach']}){tag}: {text}")
    solara.Markdown("\n".join(lines))


@solara.component
def ModelComparison(model: ReplayFloodModel) -> None:
    """The research question across all recorded runs (results/sweeps/main)."""
    runs = recorded_runs("main")
    if runs.empty:
        solara.Markdown("Run `python -m r2r.experiments --name main --replay-from recorded` first.")
        return
    order = list(SCENARIOS)
    fig = Figure(figsize=(9, 3.4))
    for ax, (col, title) in zip(fig.subplots(1, 3), [("evacuated_at_risk_share", "At-risk villagers evacuated"),
                                                      ("rumour_believers_share", "Villagers believing a rumour"),
                                                      ("misinfo_tag_rate", "Guardrail tag rate")]):
        piv = runs.pivot_table(index="scenario_name", columns="model_set", values=col).reindex(order)
        piv = piv[[f for f in MODEL_SETS if f in piv.columns]]
        piv.plot.bar(ax=ax, rot=30, width=0.8, color=[FAMILY_COLOUR.get(f, "#888888") for f in piv.columns])
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("")
        ax.set_ylim(0, 1 if col != "misinfo_tag_rate" else max(0.1, float(piv.max().max()) * 1.3))
        ax.legend(fontsize=8)
    fig.tight_layout()
    solara.FigureMatplotlib(fig)
    plt.close("all")
    table = runs.pivot_table(index="scenario_name", columns="model_set",
                             values=["evacuated_at_risk_share", "caught"]).reindex(order).round(2)
    table.columns = [f"{'evacuated' if v.startswith('evac') else 'caught'} ({fam})" for v, fam in table.columns]
    table.index.name = "intervention"
    solara.Markdown("One seed per cell; two live runs of one cell differ by about 6 points from model sampling "
                    "alone (CALIBRATION.md).\n\n" + table.to_markdown())


model_params = {
    # Only families with a recorded live run can be replayed (self-hosted and otari.ai sweeps).
    "model_family": {"type": "Select", "value": "qwen3", "values": recorded_families() or list(MODEL_SETS),
                     "label": "Model family"},
    "intervention": {"type": "Select", "value": "baseline", "values": list(SCENARIOS), "label": "Intervention"},
    "seed": 42,
}

page = SolaraViz(
    ReplayFloodModel(),
    components=[
        (Header, 0),
        (make_geospace_component(portrayal, zoom=9, scroll_wheel_zoom=True), 0),
        (Messages, 0),
        make_plot_component({"mean_danger": "#3b6fd8", "rumour_believers_share": "#d6457a",
                             "evacuated_at_risk_share": "#1f9d55"}, page=1,
                            post_process=titled("Belief over time", "share / mean")),
        make_plot_component({f"cost_{c}": COHORT_COLOUR[c] for c in COHORT_COLOUR}, page=1,
                            post_process=titled("Notional cost per cohort (cumulative)", "USD")),
        make_plot_component({"misinfo_tag_rate": "#d64545"}, page=1,
                            post_process=titled("Guardrail tag rate", "tagged / checked")),
        (ModelComparison, 2),
    ],
    model_params=model_params,
    name="Rumour-to-Relief",
    play_interval=400,
)
