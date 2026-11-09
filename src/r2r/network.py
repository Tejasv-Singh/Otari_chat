"""Village contact network: who hears from whom.

Two villages are linked when they are within `contact_km` of each other (kind "geo")
or belong to the same panchayat (kind "panchayat"); both can apply ("geo+panchayat").
Any village left with no link is joined to its nearest neighbour (kind "nearest"),
because in practice every village has some contact with the next one along the road.
Finally, separate clusters (side valleys) are joined by their closest pair of villages
(kind "bridge"), standing in for the valley road and market towns, so the graph is
always connected.
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np


def build_contact_graph(villages: gpd.GeoDataFrame, contact_km: float) -> nx.Graph:
    """Nodes are `village_id`s carrying name, panchayat, in_flood_zone; edges carry kind and dist_km."""
    g = nx.Graph()
    ids = villages["village_id"].to_numpy()
    for row in villages.itertuples():
        g.add_node(int(row.village_id), name=row.name, panchayat=int(row.panchayat),
                   in_flood_zone=bool(row.in_flood_zone), x=float(row.geometry.x), y=float(row.geometry.y))
    xy = np.column_stack([villages.geometry.x.to_numpy(), villages.geometry.y.to_numpy()])
    dist_km = np.linalg.norm(xy[:, None, :] - xy[None, :, :], axis=2) / 1000.0
    pan = villages["panchayat"].to_numpy()
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            geo = dist_km[i, j] <= contact_km
            same = pan[i] == pan[j]
            if geo or same:
                kind = "geo+panchayat" if geo and same else ("geo" if geo else "panchayat")
                g.add_edge(int(ids[i]), int(ids[j]), kind=kind, dist_km=round(float(dist_km[i, j]), 2))
    for i in range(n):
        if g.degree(int(ids[i])) == 0 and n > 1:
            d = dist_km[i].copy()
            d[i] = np.inf
            j = int(np.argmin(d))
            g.add_edge(int(ids[i]), int(ids[j]), kind="nearest", dist_km=round(float(d[j]), 2))
    # Side valleys only meet through the main valley road and its market towns.
    # Join separate clusters one at a time by their closest pair of villages.
    index = {int(v): k for k, v in enumerate(ids)}
    while nx.number_connected_components(g) > 1:
        comps = [sorted(index[v] for v in c) for c in nx.connected_components(g)]
        base = comps[0]
        rest = [k for c in comps[1:] for k in c]
        sub = dist_km[np.ix_(base, rest)]
        a, b = np.unravel_index(int(np.argmin(sub)), sub.shape)
        i, j = base[a], rest[b]
        g.add_edge(int(ids[i]), int(ids[j]), kind="bridge", dist_km=round(float(dist_km[i, j]), 2))
    return g


def network_summary(g: nx.Graph) -> dict[str, object]:
    kinds: dict[str, int] = {}
    for _, _, k in g.edges(data="kind"):
        kinds[k] = kinds.get(k, 0) + 1
    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "edge_kinds": kinds,
        "components": nx.number_connected_components(g),
        "mean_degree": round(2 * g.number_of_edges() / max(1, g.number_of_nodes()), 2),
    }
