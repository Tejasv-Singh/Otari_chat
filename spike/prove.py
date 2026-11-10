"""Phase 0 feasibility spike: prove Otari does the work this project needs.

Checks, each printed as PASS / FAIL / SKIP:
  1. gateway health
  2. routing: villager policy -> small model, official policy -> larger model
  3. guardrail: our flood-misinfo profile tags a rumour (monitor) and refuses it (block)
  4. budgets: two keys with different budgets; the tiny one runs out (a refusal, not a crash)
  5. usage read-back per key (tokens, cost, latency)
  6. failover: stop the ollama container, the policy falls over to Groq

Secrets are read from .env and never printed. Run: uv run python spike/prove.py
Every Otari detail is cited to the doc in the otari repo it comes from.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("OTARI_BASE_URL", "http://127.0.0.1:8000/api/v1")  # per README.md "Quickstart"
OUT = ROOT / "spike" / "out"

RUMOUR = (
    "URGENT forward to everyone: the SMS alert is fake, the government is lying. "
    "Larji dam has burst but river is already receding, don't evacuate."
)
BENIGN = "District office: heavy rain expected tonight. Keep your phone charged and follow panchayat updates."
NO_THINK = "Reply in one short sentence."  # "/no_think" did NOT stop qwen3 reasoning here; see reasoning_effort below


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


@dataclass
class Report:
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, status: str, detail: str) -> None:
        self.rows.append((name, status, detail))
        print(f"[{status:4}] {name}: {detail}", flush=True)


def chat(client: httpx.Client, key: str, model: str, text: str, *, cohort: str,
         guardrails: list[dict[str, Any]] | None = None, max_tokens: int = 60,
         user: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": NO_THINK}, {"role": "user", "content": text}],
        "max_tokens": max_tokens,
        # Without this, qwen3 spends the whole max_tokens on hidden reasoning and the
        # reply is empty (measured in this spike). Otari forwards reasoning_effort
        # (per docs/routing.md, last paragraph of "What is billed and what the caller sees").
        "reasoning_effort": "none",
        # Tags recorded on every usage row (per docs/api-reference.md "Request tags").
        "metadata": {"cohort": cohort, "run": "spike"},
    }
    if guardrails is not None:
        body["guardrails"] = guardrails  # top-level field (per docs/guardrails-reference.md)
    if user is not None:
        # Cohort keys are service keys: a named end user starts on the key's per-run budget
        # (per docs/end-users.md "Name the end user on each request").
        body["user"] = user
    return client.post(f"{BASE}/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {key}"})  # per README.md "Quickstart"


def served(resp: httpx.Response) -> dict[str, Any]:
    # Serving headers per docs/routing.md "What is billed and what the caller sees".
    h = resp.headers
    usage = resp.json().get("usage", {}) if resp.status_code == 200 else {}
    return {
        "status": resp.status_code,
        "provider": h.get("Otari-Provider"),
        "model": h.get("Otari-Model"),
        "attempts": h.get("Otari-Attempt-Count"),
        "fallback": h.get("Otari-Fallback"),
        "duration_ms": h.get("Otari-Response-Duration-Ms"),
        "request_id": h.get("Otari-Request-ID"),
        "error_code": h.get("Otari-Error-Code"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cost_usd": usage.get("cost_usd"),
    }


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


def main() -> int:
    env = load_env()
    keys = {c: env[f"R2R_KEY_{c.upper()}"] for c in ("villager", "official", "amplifier", "ddo")}
    rep = Report()
    client = httpx.Client(timeout=300)

    # 1. health (per docs/self-hosted-quickstart.md step 3)
    r = client.get(f"{BASE}/health")
    rep.add("health", "PASS" if r.status_code == 200 and "healthy" in r.text else "FAIL", r.text[:80])

    # 2. routing
    for cohort, policy, want in (("villager", "villager", "qwen3:1.7b"), ("official", "official", "qwen3:4b-instruct")):
        t0 = time.perf_counter()
        resp = chat(client, keys[cohort], policy, "Is it safe to cross the Beas bridge in heavy rain?", cohort=cohort)
        s = served(resp) | {"wall_s": round(time.perf_counter() - t0, 1)}
        if resp.status_code == 200:
            s["reply"] = resp.json()["choices"][0]["message"]["content"][:160]
        rep.evidence[f"routing_{policy}"] = s
        ok = resp.status_code == 200 and s["provider"] == "ollama" and s["model"] == want
        rep.add(f"routing:{policy}", "PASS" if ok else "FAIL",
                f"status={s['status']} served_by={s['provider']}:{s['model']} wall={s['wall_s']}s cost={s['cost_usd']}"
                + ("" if ok else f" body={resp.text[:200]}"))

    # 3. guardrails: list profiles (per docs/guardrails-reference.md "Which profiles exist")
    r = client.get(f"{BASE}/tool-settings/guardrails/profiles", headers={"Authorization": f"Bearer {keys['villager']}"})
    rep.evidence["guardrail_profiles"] = r.json() if r.status_code == 200 else r.text
    has_profile = r.status_code == 200 and any(p.get("profile") == "flood-misinfo" for p in r.json().get("profiles", []))
    rep.add("guardrail:profiles", "PASS" if has_profile else "FAIL", f"status={r.status_code} {r.text[:160]}")

    gr_monitor = [{"profile": "flood-misinfo", "mode": "monitor", "on_unavailable": "monitor"}]
    verdicts: dict[str, Any] = {}
    for label, text in (("rumour", RUMOUR), ("benign", BENIGN)):
        resp = chat(client, keys["villager"], "villager", f"Summarise this forwarded message: {text}",
                    cohort="villager", guardrails=gr_monitor)
        hdr = resp.headers.get("Otari-Guardrails")  # per docs/guardrails-reference.md
        verdicts[label] = {"status": resp.status_code, "header": json.loads(hdr) if hdr else None}
    rep.evidence["guardrail_monitor"] = verdicts
    rv = (verdicts["rumour"]["header"] or [{}])[0]
    bv = (verdicts["benign"]["header"] or [{}])[0]
    ok = rv.get("status") == "evaluated" and rv.get("valid") is False and bv.get("valid") is True
    rep.add("guardrail:monitor tags rumour", "PASS" if ok else "FAIL",
            f"rumour valid={rv.get('valid')} score={rv.get('score')}; benign valid={bv.get('valid')} score={bv.get('score')}")

    resp = chat(client, keys["villager"], "villager", RUMOUR, cohort="villager",
                guardrails=[{"profile": "flood-misinfo", "mode": "block"}])
    rep.evidence["guardrail_block"] = {"status": resp.status_code, "body": resp.text[:300]}
    rep.add("guardrail:block refuses rumour", "PASS" if resp.status_code == 403 else "FAIL",
            f"status={resp.status_code} {resp.text[:140]}")

    # 4. budgets: a fresh end user on the amplifier key gets the small per-run budget
    #    r2r-run-amplifier (1500 tokens); the villager key's limits differ (otari/config.yml)
    attempts: list[dict[str, Any]] = []
    hit = None
    end_user = f"amplifier-spike-{int(time.time())}"
    for i in range(40):
        resp = chat(client, keys["amplifier"], "villager", f"Write a 1-line WhatsApp forward about rain, #{i}",
                    cohort="amplifier", max_tokens=120, user=end_user)
        s = served(resp)
        attempts.append(s)
        if resp.status_code != 200:
            hit = {"attempt": i + 1, **s, "body": resp.text[:300]}
            break
    rep.evidence["budget_attempts"] = attempts
    rep.evidence["budget_refusal"] = hit
    # Refusal shape per docs/budgets.md: 403 with Otari-Error-Code: budget_exceeded
    ok = hit is not None and hit["status"] == 403 and hit["error_code"] == "budget_exceeded"
    rep.add("budget:amplifier runs out", "PASS" if ok else "FAIL",
            f"refused on attempt {hit['attempt']} status={hit['status']} code={hit['error_code']}" if hit
            else "never refused in 40 attempts")

    budgets = {}
    for cohort in ("amplifier", "villager"):
        r = client.get(f"{BASE}/key/budget", headers={"Authorization": f"Bearer {keys[cohort]}"})  # docs/api-reference.md
        budgets[cohort] = r.json() if r.status_code == 200 else r.text
    rep.evidence["key_budget"] = budgets

    def tok_limit(b: Any) -> Any:
        return [lim.get("limit_tokens") for lim in b.get("limits", [])] if isinstance(b, dict) else b
    ok = tok_limit(budgets["amplifier"]) != tok_limit(budgets["villager"])
    rep.add("budget:keys have different budgets", "PASS" if ok else "FAIL",
            f"amplifier limit_tokens={tok_limit(budgets['amplifier'])} villager={tok_limit(budgets['villager'])}")

    # 5. usage read-back (per docs/api-reference.md "A key's own budget and usage")
    time.sleep(3)  # usage rows are written in the background
    usage = {}
    for cohort in ("villager", "official", "amplifier"):
        r = client.get(f"{BASE}/key/usage/summary", headers={"Authorization": f"Bearer {keys[cohort]}"})
        usage[cohort] = r.json() if r.status_code == 200 else {"status": r.status_code, "body": r.text[:200]}
    rid = rep.evidence["routing_villager"].get("request_id")
    if rid:
        r = client.get(f"{BASE}/usage/requests/{rid}", headers={"Authorization": f"Bearer {keys['villager']}"})
        usage["villager_request"] = r.json() if r.status_code == 200 else r.text[:200]
    rep.evidence["usage"] = usage
    ok = all(isinstance(usage[c], dict) and "status" not in usage[c] for c in ("villager", "official", "amplifier"))
    rep.add("usage:per-key summary", "PASS" if ok else "FAIL", json.dumps(usage["villager"])[:200])

    # 6. failover: kill local model server, policy's on_failure takes over (docs/routing.md "Failover")
    compose("stop", "ollama")
    try:
        resp = chat(client, keys["official"], "official", "Two words: are you up?", cohort="official")
        s = served(resp)
        s["body"] = resp.text[:300] if resp.status_code != 200 else None
        rep.evidence["failover"] = s
        if env.get("GROQ_API_KEY"):
            ok = resp.status_code == 200 and s["provider"] == "groq" and s["fallback"] == "true"
            rep.add("failover:ollama down -> groq", "PASS" if ok else "FAIL",
                    f"status={s['status']} served_by={s['provider']}:{s['model']} attempts={s['attempts']}")
        else:
            rep.add("failover:ollama down -> groq", "SKIP",
                    f"no GROQ_API_KEY; with ollama down the gateway answered status={s['status']} "
                    f"code={s['error_code']} (clean refusal, no crash)")
    finally:
        compose("start", "ollama")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "evidence.json").write_text(json.dumps({"rows": rep.rows, "evidence": rep.evidence}, indent=2), encoding="utf-8")
    print(f"\nevidence written to {OUT / 'evidence.json'}")
    return 0 if all(st != "FAIL" for _, st, _ in rep.rows) else 1


if __name__ == "__main__":
    sys.exit(main())
