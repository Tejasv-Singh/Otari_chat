"""The flood-misinfo guardrail service: contract shape and pattern coverage."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

_spec = importlib.util.spec_from_file_location(
    "guardrail_app", Path(__file__).resolve().parents[1] / "otari" / "guardrail" / "app.py")
assert _spec and _spec.loader
app_mod = importlib.util.module_from_spec(_spec)
sys.modules["guardrail_app"] = app_mod   # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(app_mod)
client = TestClient(app_mod.app)


def verdict(text: str) -> dict:
    r = client.post("/validate", json={"profile": "flood-misinfo", "input_text": text, "validate_kwargs": {}})
    assert r.status_code == 200
    return r.json()["result"]


def test_contract_shape_and_profiles() -> None:
    assert client.get("/profiles").json() == [{"name": "flood-misinfo", "guardrail_name": "r2r_flood_misinfo_regex"}]
    r = client.post("/validate", json={"profile": "other", "input_text": "x"}).json()
    assert r["result"]["valid"] is None     # unknown profile -> inconclusive, per the Otari contract


def test_flags_english_and_hinglish_rumours() -> None:
    assert verdict("The SMS alert is fake, don't evacuate!")["valid"] is False
    assert verdict("last year our houses were looted. Don't leave, forward to everyone")["valid"] is False
    assert verdict("Alert jhooth hai, ghar mat chhodo")["valid"] is False


def test_flags_devanagari_rumours() -> None:
    assert verdict("यह अलर्ट फर्जी है, घर मत छोड़ो")["valid"] is False
    assert verdict("नदी का पानी कम हो रहा है, गांव में ही रहें")["valid"] is False


def test_passes_official_guidance() -> None:
    for text in ("Move to higher ground now. This warning is real.",
                 # a real qwen3:4b relay that an earlier pattern flagged ("do not ... evacuate"
                 # matched across the sentence boundary)
                 "Water rising fast! Move immediately to higher ground! Stay safe! Do not wait! Evacuate now!",
                 "MOVE UPHILL IMMEDIATELY! Stay safe. Do not wait. Evacuate now. Report to panchayat office.",
                 "नदी के पास रहने वाले लोग तुरंत ऊंचाई पर चले जाएं। यह चेतावनी सच है।"):
        v = verdict(text)
        assert v["valid"] is True, (text, v)
