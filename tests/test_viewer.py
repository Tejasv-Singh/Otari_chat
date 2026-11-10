"""Dashboard data layer and static export, on the synthetic district with a fake Otari."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

from fake_otari import KEYS, FakeOtari
from test_model import BASE, district
from test_space import CFG

from r2r.llm import OtariClient
from r2r.model import FloodModel
from r2r.viewer import STATUS_CODE, capture, geography

ROOT = Path(__file__).resolve().parents[1]


def model() -> FloodModel:
    llm = OtariClient(KEYS, run_id="t", cache_path=None, retry_backoff_s=0, transport=FakeOtari().transport())
    return FloodModel(dataclasses.replace(BASE, district=CFG), llm=llm, district=district())


def load_export():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("r2r_export", ROOT / "dashboard" / "export.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["r2r_export"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_capture_records_every_tick_for_every_villager() -> None:
    m = model()
    geo, cap = geography(m), capture(m)
    n = len(cap["villagers"])
    assert len(cap["states"]) == BASE.ticks + 1 and all(len(s) == n for s in cap["states"])
    assert all(st in STATUS_CODE.values() and 0 <= d <= 1 and 0 <= r <= 1
               for s in cap["states"] for st, d, r in s)
    final = cap["states"][-1]
    for p, (st, _, _) in zip(cap["villagers"], final):
        assert (p["safe"] is not None) == (st == STATUS_CODE["safe"])
        assert -90 <= p["home"][0] <= 90 and -180 <= p["home"][1] <= 180
    assert len(cap["series"]["tick"]) == BASE.ticks + 1
    assert cap["series"]["evacuated_at_risk_share"][-1] == m.datacollector.get_model_vars_dataframe().iloc[-1][
        "evacuated_at_risk_share"]
    assert len(cap["messages"]) == len(m.messages)
    assert {"boundary", "flood_zone", "rivers", "villages"} <= set(geo)
    json.dumps({"geo": geo, "run": cap})   # embeddable as-is


def test_static_page_embeds_data_safely(tmp_path: Path) -> None:
    export = load_export()
    payload = {"runs": {"x": {"messages": [{"text": "evil </script><script>alert(1)</script>"}]}}}
    html = export.render(payload)
    assert export.PLACEHOLDER not in html
    body = html.split('<script id="r2r-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(body.replace("<\\/", "</")) == payload     # the data block is never closed early
