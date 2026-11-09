"""mesa-geo space holding the district's villages.

Uses the installed mesa-geo 0.9.3 API: GeoSpace(crs=..., warn_crs_conversion=...),
GeoAgent(model, geometry, crs), AgentCreator(agent_class, model=, crs=).from_GeoDataFrame,
GeoSpace.add_agents (checked with inspect.signature on 2026-11-10).
"""

from __future__ import annotations

import mesa
import mesa_geo as mg

from r2r.geodata import District


class Village(mg.GeoAgent):
    """A village location. People agents (Phase 2) live in villages; villages do not act."""

    village_id: int
    name: str
    panchayat: int
    in_flood_zone: bool
    river_dist_m: float

    def __init__(self, model: mesa.Model, geometry, crs: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(model, geometry, crs)


def build_space(model: mesa.Model, district: District) -> tuple[mg.GeoSpace, list[Village]]:
    crs = f"EPSG:{district.cfg.utm_epsg}"
    space = mg.GeoSpace(crs=crs, warn_crs_conversion=False)
    creator = mg.AgentCreator(Village, model=model, crs=crs)
    villages: list[Village] = creator.from_GeoDataFrame(district.villages.to_crs(crs))
    space.add_agents(villages)
    return space, villages
