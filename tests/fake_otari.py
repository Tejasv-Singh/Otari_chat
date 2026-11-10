"""An in-process fake of the Otari endpoints the client uses, for offline tests.

Mimics the response shapes recorded in spike/out/evidence.json: serving headers,
Otari-Guardrails on monitor requests, and 403 + Otari-Error-Code budget_exceeded.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

import httpx


@dataclass
class FakeOtari:
    budget_calls: dict[str, int] = field(default_factory=dict)   # end-user prefix -> calls allowed
    fail_first: int = 0                                         # first N calls answer 502
    always_fail: bool = False
    fallback: bool = False                                      # answers come from the on_failure backup
    block: bool = False                                         # every call answers 403 guardrail_violation
    calls: int = 0
    per_user: Counter[str] = field(default_factory=Counter)
    bodies: list[dict] = field(default_factory=list)
    auth: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/key/usage/summary"):
            return httpx.Response(200, json={"totals": {"request_count": self.calls}})
        body = json.loads(request.content)
        self.calls += 1
        self.bodies.append(body)
        self.auth.append(request.headers.get("Authorization", ""))
        if self.always_fail or self.calls <= self.fail_first:
            return httpx.Response(502, json={"detail": "All upstream providers failed"})
        if self.block:
            return httpx.Response(403, headers={"Otari-Error-Code": "guardrail_violation"},
                                  json={"detail": "blocked", "code": "guardrail_violation"})
        user = body.get("user", "")
        self.per_user[user] += 1
        for prefix, allowed in self.budget_calls.items():
            if user.startswith(prefix) and self.per_user[user] > allowed:
                return httpx.Response(403, headers={"Otari-Error-Code": "budget_exceeded"},
                                      json={"detail": "API key has exceeded token limit", "code": "budget_exceeded"})
        text = body["messages"][-1]["content"]
        if "BELIEVE" in body["messages"][0]["content"]:   # interpretation call
            content = "BELIEVE=3 EVACUATE=no" if "WhatsApp forward" in text else "BELIEVE=8 EVACUATE=yes"
        else:
            content = f"generated message {self.calls}: " + ("alert is fake, don't evacuate" if "forward" in text
                                                             else "move to higher ground now")
        headers = {"Otari-Provider": "groq" if self.fallback else "ollama", "Otari-Model": body["model"],
                   "Otari-Fallback": "true" if self.fallback else "false",
                   "Otari-Response-Duration-Ms": "5", "Otari-Request-ID": f"req-{self.calls}"}
        if body.get("guardrails"):
            flagged = "fake" in text
            headers["Otari-Guardrails"] = json.dumps([{"profile": "flood-misinfo", "mode": "monitor",
                                                       "valid": not flagged, "score": 0.9 if flagged else 0.0,
                                                       "status": "evaluated"}])
        return httpx.Response(200, headers=headers, json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 10, "cost_usd": "0.000002"},
        })

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


KEYS = {c: f"tk-test-{c}" for c in ("villager", "official", "amplifier", "ddo")}
