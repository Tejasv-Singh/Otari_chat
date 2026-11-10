"""Export recorded runs to a static page for GitHub Pages.

    uv run python dashboard/export.py            # -> docs/index.html

The self-hosted Otari gateway is not publicly reachable, so the public page shows
recorded runs: every (model family x intervention) cell of the live sweep is replayed
(r2r.viewer, no model calls) and its tick-by-tick state is embedded in one HTML file,
next to the sweep comparison and the Otari usage recorded live. The page needs no
server: open it from disk or publish docs/ with GitHub Pages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
from pathlib import Path


from r2r.analyze import kendall_tau, rank_interventions
from r2r.experiments import GATEWAY, MODEL_SETS, SCENARIOS
from r2r.viewer import capture, geography, make_model, recorded_families, recorded_runs, sweep_table

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "static_template.html"
OUT = HERE.parent / "docs" / "index.html"
PLACEHOLDER = "/*__R2R_DATA__*/null"


def build_payload(sweep: str = "main") -> dict:
    runs, geo = {}, None
    families = recorded_families()
    for fam in families:
        for scen in SCENARIOS:
            m = make_model(fam, scen)
            geo = geo or geography(m)
            runs[f"{fam}/{scen}"] = capture(m)
            print(f"captured {fam}/{scen}: replay of {runs[f'{fam}/{scen}']['replay_run']}, "
                  f"misses={runs[f'{fam}/{scen}']['replay_misses']}", flush=True)
    sw = recorded_runs(sweep)
    ranks = {f: rank_interventions(d) for f, d in sw.groupby("model_set")} if not sw.empty else {}
    fams = [f for f in families if f in ranks]
    pairs = [{"a": a, "b": b, "tau": kendall_tau(ranks[a], ranks[b]), "same_top": ranks[a][0] == ranks[b][0]}
             for a, b in itertools.combinations(fams, 2)]
    # What each family runs: local policies resolve to these Ollama models (otari/config.yml);
    # hosted families send otari.ai catalog ids directly.
    models = {"qwen3": {"villager": "qwen3:1.7b", "official": "qwen3:4b-instruct"},
              "gemma3": {"villager": "gemma3:1b", "official": "gemma3:4b"}}
    for f in families:
        models.setdefault(f, {"villager": MODEL_SETS[f]["villager"], "official": MODEL_SETS[f]["official"]})
    return {
        "generated": dt.date.today().isoformat(),
        "families": {f: MODEL_SETS[f] for f in families},
        "gateway": {f: GATEWAY[f] for f in families},
        "models": models,
        "pairs": pairs,
        "scenarios": list(SCENARIOS),
        "geo": geo,
        "runs": runs,
        "sweep": sweep_table(sweep),
        "ranking": {f: ranks[f] for f in fams},
    }


def render(payload: dict, template: Path = TEMPLATE) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    data = data.replace("</", "<\\/")   # never close the <script> early
    html = template.read_text(encoding="utf-8")
    assert PLACEHOLDER in html, "template lost its data placeholder"
    return html.replace(PLACEHOLDER, data)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", default="main")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    html = render(build_payload(args.sweep))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    (args.out.parent / ".nojekyll").touch()   # serve files as-is on GitHub Pages
    print(f"wrote {args.out} ({len(html) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
