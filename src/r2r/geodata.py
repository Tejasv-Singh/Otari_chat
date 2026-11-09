"""Fetch, cache and prepare one district's geography from OpenStreetMap.

Everything is fetched once through osmnx (Nominatim for the boundary, one combined
Overpass query for places, rivers and roads) and cached as GeoPackage layers under
data/cache/<slug>/. Later runs never touch the network.

Gram panchayat boundaries are almost never mapped in OSM for Himachal Pradesh, so
panchayats are approximated by deterministic spatial clustering of villages
(`assign_panchayats`). This is a modelling proxy and is labelled as such.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

from r2r.config import DistrictConfig

log = logging.getLogger(__name__)

CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "cache"
WGS84 = "EPSG:4326"


@dataclass
class District:
    """A prepared district. All layers are in the config's UTM CRS (metres)."""

    cfg: DistrictConfig
    boundary: gpd.GeoDataFrame
    villages: gpd.GeoDataFrame      # columns: village_id, name, place, panchayat, in_flood_zone, river_dist_m, geometry
    rivers: gpd.GeoDataFrame
    roads: gpd.GeoDataFrame
    flood_zone: gpd.GeoDataFrame    # one polygon


# --------------------------------------------------------------------------- fetch

def _configure_osmnx() -> None:
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_ROOT / "osmnx")
    ox.settings.requests_timeout = 600
    # One Overpass request for the whole district instead of many sub-queries:
    # public servers queue each request, so fewer requests is much faster.
    ox.settings.max_query_area_size = 1e12
    ox.settings.http_user_agent = "rumour-to-relief-research/0.1 (hackathon simulation)"


def _with_retries(fn, attempts: int = 5, base_wait: float = 15.0):  # type: ignore[no-untyped-def]
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # network errors and Overpass 429/502/504
            if i == attempts - 1:
                raise
            wait = base_wait * 2**i
            log.warning("OSM request failed (%s); retrying in %.0fs", exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_raw(cfg: DistrictConfig, refresh: bool = False) -> dict[str, gpd.GeoDataFrame]:
    """Download (or load cached) raw OSM layers for a district, in WGS84."""
    out_dir = CACHE_ROOT / cfg.slug
    gpkg = out_dir / "raw.gpkg"
    layers = ("boundary", "places", "rivers", "roads")
    if gpkg.exists() and not refresh:
        return {name: gpd.read_file(gpkg, layer=name) for name in layers}

    import osmnx as ox

    _configure_osmnx()
    out_dir.mkdir(parents=True, exist_ok=True)
    boundary = _with_retries(lambda: ox.geocode_to_gdf(cfg.query))[["geometry"]]
    true_poly: BaseGeometry = boundary.geometry.iloc[0]
    # The full boundary has thousands of vertices and Overpass rejects the query
    # (HTTP 413). Query a simplified, slightly enlarged outline instead (~200 m) and
    # clip the results back to the true boundary below.
    poly: BaseGeometry = true_poly.buffer(0.003).simplify(0.002, preserve_topology=True)
    tags: dict[str, object] = {
        "place": list(cfg.place_tags),
        "waterway": "river",
        "highway": list(cfg.road_tags),
    }
    feats = _with_retries(lambda: ox.features_from_polygon(poly, tags)).reset_index()
    feats = feats[feats.geometry.intersects(true_poly)]

    def pick(col: str, values: list[str] | str) -> gpd.GeoDataFrame:
        if col not in feats:
            return gpd.GeoDataFrame(geometry=[], crs=WGS84)
        vals = [values] if isinstance(values, str) else values
        keep = [c for c in ("element", "id", "name", "name:en", "place", "waterway", "highway", "geometry") if c in feats]
        return feats.loc[feats[col].isin(vals), keep].copy()

    raw = {
        "boundary": boundary,
        "places": pick("place", list(cfg.place_tags)),
        "rivers": pick("waterway", "river"),
        "roads": pick("highway", list(cfg.road_tags)),
    }
    for name, gdf in raw.items():
        gdf = gdf.copy()
        for c in gdf.columns:
            if c != "geometry" and gdf[c].dtype == object:
                gdf[c] = gdf[c].astype("string")
        gdf.to_file(gpkg, layer=name, driver="GPKG")
    log.info("cached raw OSM layers to %s", gpkg)
    return raw


# ------------------------------------------------------------------------ prepare

def flood_zone(rivers: gpd.GeoDataFrame, boundary: gpd.GeoDataFrame, cfg: DistrictConfig) -> gpd.GeoDataFrame:
    """Flood-risk zone: named rivers buffered by `flood_buffer_m`, clipped to the district."""
    r = rivers
    if cfg.river_names and "name" in r:
        mask = r["name"].fillna("").str.contains("|".join(cfg.river_names), case=False, regex=True)
        if mask.any():
            r = r[mask]
    zone = r.geometry.buffer(cfg.flood_buffer_m).union_all().intersection(boundary.geometry.iloc[0])
    return gpd.GeoDataFrame(geometry=[zone], crs=boundary.crs)


def assign_panchayats(points: gpd.GeoSeries, k: int, seed: int) -> np.ndarray:
    """Deterministic k-means on village coordinates as a gram-panchayat proxy.

    Seeds by farthest-point sampling from a seeded start, then 20 Lloyd iterations.
    """
    xy = np.column_stack([points.x.to_numpy(), points.y.to_numpy()])
    k = max(1, min(k, len(xy)))
    rng = np.random.default_rng(seed)
    centers = [xy[rng.integers(len(xy))]]
    for _ in range(1, k):
        d = np.min([np.linalg.norm(xy - c, axis=1) for c in centers], axis=0)
        centers.append(xy[int(np.argmax(d))])
    c = np.array(centers)
    for _ in range(20):
        labels = np.argmin(np.linalg.norm(xy[:, None, :] - c[None, :, :], axis=2), axis=1)
        new = np.array([xy[labels == j].mean(axis=0) if np.any(labels == j) else c[j] for j in range(k)])
        if np.allclose(new, c):
            break
        c = new
    return labels


def select_villages(places: gpd.GeoDataFrame, rivers: gpd.GeoDataFrame, zone: BaseGeometry,
                    cfg: DistrictConfig) -> gpd.GeoDataFrame:
    """Pick `n_villages` named settlements, a fixed share inside the flood zone, reproducibly."""
    p = places.copy()
    p["geometry"] = p.geometry.representative_point()
    p["name"] = p["name"].fillna(p.get("name:en", pd.Series(index=p.index, dtype="string")))
    p = p[p["name"].notna()].drop_duplicates(subset="name").reset_index(drop=True)
    p["river_dist_m"] = p.geometry.distance(rivers.geometry.union_all()).round(0)
    p["in_flood_zone"] = p.geometry.within(zone)
    inside, outside = p[p["in_flood_zone"]], p[~p["in_flood_zone"]]
    n = min(cfg.n_villages, len(p))
    n_in = min(len(inside), round(n * cfg.flood_zone_share))
    n_out = min(len(outside), n - n_in)
    n_in = min(len(inside), n - n_out)
    chosen = pd.concat([inside.sample(n_in, random_state=cfg.seed), outside.sample(n_out, random_state=cfg.seed)])
    chosen = chosen.sort_values("name").reset_index(drop=True)
    chosen["panchayat"] = assign_panchayats(chosen.geometry, cfg.n_panchayats, cfg.seed)
    chosen["village_id"] = np.arange(len(chosen))
    cols = ["village_id", "name", "place", "panchayat", "in_flood_zone", "river_dist_m", "geometry"]
    return gpd.GeoDataFrame(chosen[cols], geometry="geometry", crs=places.crs)


def load_district(cfg: DistrictConfig, refresh: bool = False) -> District:
    """Raw OSM (cached) -> projected, prepared layers (cached as prepared.gpkg)."""
    # Cache key covers every config field, so changing a buffer or share re-prepares.
    digest = hashlib.sha1(repr(cfg).encode()).hexdigest()[:10]
    prepared = CACHE_ROOT / cfg.slug / f"prepared_{digest}.gpkg"
    if prepared.exists() and not refresh:
        L = {n: gpd.read_file(prepared, layer=n) for n in ("boundary", "villages", "rivers", "roads", "flood_zone")}
        return District(cfg, **L)

    raw = fetch_raw(cfg, refresh=refresh)
    crs = f"EPSG:{cfg.utm_epsg}"
    boundary = raw["boundary"].to_crs(crs)
    rivers = raw["rivers"].to_crs(crs)
    roads = raw["roads"].to_crs(crs)
    places = raw["places"].to_crs(crs)
    zone = flood_zone(rivers, boundary, cfg)
    villages = select_villages(places, rivers, zone.geometry.iloc[0], cfg)
    d = District(cfg, boundary, villages, rivers, roads, zone)
    for name in ("boundary", "villages", "rivers", "roads", "flood_zone"):
        getattr(d, name).to_file(prepared, layer=name, driver="GPKG")
    return d
