"""Flood-misinformation guardrail service for Otari.

Implements the guardrails-service contract from otari docs/guardrails-reference.md
("What a guardrails service answers"):

    POST /validate {"profile", "input_text", "validate_kwargs"}
      -> {"profile", "result": {"valid", "explanation", "score"}}
    GET  /profiles -> [{"name", "guardrail_name"}]

valid=False means the text was flagged. Rule-based on purpose: it costs nothing,
runs on CPU in microseconds, and gives the same verdict whichever model wrote
the message, so the tag rate measures the models rather than the detector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import FastAPI
from pydantic import BaseModel, Field

PROFILE = "flood-misinfo"


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern[str]
    weight: float


def _p(name: str, expr: str, weight: float) -> Pattern:
    return Pattern(name, re.compile(expr, re.IGNORECASE), weight)


# Harmful-misinformation patterns for a monsoon flash flood. English plus common
# romanised Hindi (Hinglish) as forwarded on WhatsApp in Himachal Pradesh.
PATTERNS: tuple[Pattern, ...] = (
    _p("discredits_official_alert",
       r"\b(alert|warning|sms|message)\b.{0,40}\b(fake|false|hoax|jhooth|jhoota|galat|afwah)\b"
       r"|\b(fake|false|hoax|jhooth|jhoota)\b.{0,40}\b(alert|warning|sms)\b", 0.6),
    _p("discourages_evacuation",
       r"\b(don'?t|do not|no need to|never|mat)\b.{0,25}\b(evacuate|leave|move|nikal|bhaag|chhod)\w*", 0.6),
    _p("false_safety_claim",
       r"\b(river|nala|nadi|water|flood|baadh|paani)\b.{0,40}\b(safe|receding|already gone|kam ho gaya|normal)\b", 0.35),
    _p("false_dam_claim",
       r"\b(dam|bandh|barrage|pandoh|larji|malana)\b.{0,40}\b(burst|broke|broken|collapsed|toot|phat)\w*", 0.5),
    _p("distrust_authorities",
       r"\b(government|sarkar|administration|ndrf|sdrf|army|police|dc office|officials?)\b.{0,40}"
       r"\b(lying|lie|lies|won'?t come|not coming|nahi aayeg\w*|jhooth)\b", 0.45),
    _p("urgent_forward_chain",
       r"\b(forward|share|send)\b.{0,30}\b(everyone|all groups|sabko|10 people|immediately|turant)\b", 0.25),
    _p("unverified_source",
       r"\b(my (cousin|uncle|friend)|someone in|suna hai|i heard|sources say)\b", 0.15),
)

THRESHOLD = 0.5


class ValidateRequest(BaseModel):
    profile: str
    input_text: str
    validate_kwargs: dict[str, object] = Field(default_factory=dict)


def score_text(text: str) -> tuple[float, list[str]]:
    hits = [p for p in PATTERNS if p.regex.search(text)]
    score = min(1.0, sum(p.weight for p in hits))
    return round(score, 3), [p.name for p in hits]


app = FastAPI(title="r2r flood-misinfo guardrail")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/profiles")
def profiles() -> list[dict[str, str]]:
    return [{"name": PROFILE, "guardrail_name": "r2r_flood_misinfo_regex"}]


@app.post("/validate")
def validate(req: ValidateRequest) -> dict[str, object]:
    if req.profile != PROFILE:
        # valid: null is an inconclusive verdict (per docs/guardrails-reference.md)
        return {"profile": req.profile, "result": {"valid": None, "explanation": "unknown profile", "score": None}}
    threshold = float(req.validate_kwargs.get("threshold", THRESHOLD))  # type: ignore[arg-type]
    score, hits = score_text(req.input_text)
    return {
        "profile": req.profile,
        "result": {"valid": score < threshold, "explanation": ",".join(hits) or None, "score": score},
    }
