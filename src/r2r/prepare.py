"""Prepare (download + cache) a district and draw a preview map.

    uv run python -m r2r.prepare --district kullu --n-villages 40 [--refresh]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
from pathlib import Path

from r2r.config import DISTRICTS
from r2r.geodata import load_district
from r2r.network import build_contact_graph, network_summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--district", default="kullu", choices=sorted(DISTRICTS))
    ap.add_argument("--n-villages", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--refresh", action="store_true", help="re-download from OSM")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = DISTRICTS[args.district]
    overrides = {k: v for k, v in (("n_villages", args.n_villages), ("seed", args.seed)) if v is not None}
    cfg = dataclasses.replace(cfg, **overrides)
    d = load_district(cfg, refresh=args.refresh)
    g = build_contact_graph(d.villages, cfg.contact_km)
    v = d.villages
    summary = {
        "district": cfg.slug,
        "villages": len(v),
        "in_flood_zone": int(v["in_flood_zone"].sum()),
        "panchayats": int(v["panchayat"].nunique()),
        "flood_zone_km2": round(d.flood_zone.area.iloc[0] / 1e6, 1),
        "district_km2": round(d.boundary.area.iloc[0] / 1e6, 1),
        "rivers": len(d.rivers),
        "road_segments": len(d.roads),
        "network": network_summary(g),
    }
    print(json.dumps(summary, indent=2))
    if not args.no_plot:
        out = Path("data") / f"preview_{cfg.slug}.png"
        plot(d, g, out)
        print(f"preview map: {out}")


def plot(d, g, out: Path) -> None:  # type: ignore[no-untyped-def]
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 9))
    d.boundary.boundary.plot(ax=ax, color="#888", linewidth=0.8)
    d.flood_zone.plot(ax=ax, color="#9ecae1", alpha=0.6)
    d.roads.plot(ax=ax, color="#bbb", linewidth=0.5)
    d.rivers.plot(ax=ax, color="#3182bd", linewidth=0.8)
    for a, b in g.edges():
        na, nb = g.nodes[a], g.nodes[b]
        ax.plot([na["x"], nb["x"]], [na["y"], nb["y"]], color="#e6550d", alpha=0.25, linewidth=0.6)
    v = d.villages
    ax.scatter(v.geometry.x, v.geometry.y, c=v["panchayat"], cmap="tab10", s=40,
               edgecolors=["#de2d26" if f else "#333" for f in v["in_flood_zone"]], linewidths=1.5, zorder=3)
    ax.set_title(f"{d.cfg.slug.title()}: {len(v)} villages, flood-risk zone (blue), contacts (orange)\n"
                 "red ring = village inside flood zone; colour = panchayat (proxy)")
    ax.set_axis_off()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110, bbox_inches="tight")


if __name__ == "__main__":
    main()
