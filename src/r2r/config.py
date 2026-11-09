"""District and space configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DistrictConfig:
    """Which district to model and how to turn OSM data into a simulation space.

    Defaults model Kullu, where the Beas and its tributaries flooded in the 2023 monsoon.
    """

    slug: str = "kullu"
    query: str = "Kullu district, Himachal Pradesh, India"
    # Rivers whose banks form the flood-risk zone; empty tuple = every OSM river.
    # OSM spells the Parbati as "Parvati River" and "Parvathy River" in Kullu.
    river_names: tuple[str, ...] = ("Beas", "Parbati", "Parvati", "Parvathy", "Sainj", "Tirthan", "Sutlej")
    utm_epsg: int = 32643            # UTM 43N, metres; covers Himachal Pradesh
    # Villages sit on slopes above the river centre line; 600 m catches the valley
    # floor (231 of 1,840 OSM places in Kullu; 400 m caught 161).
    flood_buffer_m: float = 600.0
    n_villages: int = 40             # villages kept in the model
    flood_zone_share: float = 0.4    # share of kept villages drawn from inside the flood zone
    n_panchayats: int = 10           # used only when OSM has no gram panchayat boundaries
    contact_km: float = 4.0          # villages this close are in contact
    seed: int = 42
    place_tags: tuple[str, ...] = field(default=("village", "hamlet", "town"))
    road_tags: tuple[str, ...] = field(default=("trunk", "primary", "secondary", "tertiary"))


MANDI = DistrictConfig(slug="mandi", query="Mandi district, Himachal Pradesh, India",
                       river_names=("Beas", "Uhl", "Suketi", "Sutlej"))
DISTRICTS: dict[str, DistrictConfig] = {"kullu": DistrictConfig(), "mandi": MANDI}
