"""Phase 1 invariants on a synthetic district (no network access needed)."""

from __future__ import annotations

import geopandas as gpd
import mesa
import numpy as np
from shapely.geometry import LineString, Point, box

from r2r.config import DistrictConfig
from r2r.geodata import District, assign_panchayats, flood_zone, select_villages
from r2r.network import build_contact_graph, network_summary
from r2r.space import Village, build_space

CRS = "EPSG:32643"
CFG = DistrictConfig(slug="test", n_villages=20, n_panchayats=4, contact_km=3.0, flood_buffer_m=500, seed=7)


def synthetic() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    boundary = gpd.GeoDataFrame(geometry=[box(0, 0, 20_000, 20_000)], crs=CRS)
    rivers = gpd.GeoDataFrame({"name": ["Beas"]}, geometry=[LineString([(10_000, 0), (10_000, 20_000)])], crs=CRS)
    rng = np.random.default_rng(1)
    pts = [Point(x, y) for x, y in rng.uniform(500, 19_500, size=(60, 2))]
    places = gpd.GeoDataFrame({"name": [f"v{i}" for i in range(60)], "place": "village"}, geometry=pts, crs=CRS)
    return boundary, rivers, places


def test_flood_zone_is_river_buffer() -> None:
    boundary, rivers, _ = synthetic()
    zone = flood_zone(rivers, boundary, CFG).geometry.iloc[0]
    assert zone.contains(Point(10_200, 5_000))
    assert not zone.contains(Point(11_000, 5_000))
    assert abs(zone.area - 20_000 * 1_000) < 1  # 2 x 500 m wide strip, clipped to the box


def test_village_selection_is_reproducible_and_flagged() -> None:
    boundary, rivers, places = synthetic()
    zone = flood_zone(rivers, boundary, CFG).geometry.iloc[0]
    a = select_villages(places, rivers, zone, CFG)
    b = select_villages(places, rivers, zone, CFG)
    assert len(a) == CFG.n_villages
    assert list(a["name"]) == list(b["name"])
    assert list(a["village_id"]) == list(range(len(a)))
    assert (a["in_flood_zone"] == (a["river_dist_m"] <= CFG.flood_buffer_m)).all()


def test_panchayats_deterministic_and_complete() -> None:
    _, _, places = synthetic()
    l1 = assign_panchayats(places.geometry, 5, seed=3)
    l2 = assign_panchayats(places.geometry, 5, seed=3)
    assert (l1 == l2).all()
    assert set(l1) == set(range(5))


def test_contact_graph_has_no_isolates_and_respects_rules() -> None:
    boundary, rivers, places = synthetic()
    zone = flood_zone(rivers, boundary, CFG).geometry.iloc[0]
    v = select_villages(places, rivers, zone, CFG)
    g = build_contact_graph(v, CFG.contact_km)
    assert g.number_of_nodes() == len(v)
    assert all(d > 0 for _, d in g.degree())
    for a, b, data in g.edges(data=True):
        same = g.nodes[a]["panchayat"] == g.nodes[b]["panchayat"]
        if data["kind"] == "geo":
            assert data["dist_km"] <= CFG.contact_km and not same
        if data["kind"] == "panchayat":
            assert same and data["dist_km"] > CFG.contact_km
    s = network_summary(g)
    assert s["nodes"] == len(v)
    assert s["components"] == 1


def test_geospace_holds_villages() -> None:
    boundary, rivers, places = synthetic()
    zone_gdf = flood_zone(rivers, boundary, CFG)
    v = select_villages(places, rivers, zone_gdf.geometry.iloc[0], CFG)
    d = District(CFG, boundary, v, rivers, rivers.iloc[0:0], zone_gdf)
    model = mesa.Model(rng=CFG.seed)
    space, villages = build_space(model, d)
    assert len(villages) == len(v)
    assert all(isinstance(a, Village) for a in space.agents)
    assert len(model.agents_by_type[Village]) == len(v)
    assert {a.village_id for a in villages} == set(v["village_id"])
