"""OtariClient: the only way the simulation talks to a model.

One virtual key per agent cohort, retries on transient upstream errors, budget
exhaustion turned into a *model event* (the cohort goes silent), a prompt cache, and
a per-call usage log that feeds the Mesa DataCollector.

Every Otari detail is cited to the otari repo doc it comes from (commit e74a036).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
COHORTS = ("villager", "official", "amplifier", "ddo")
Status = Literal["ok", "cached", "silent", "budget_exhausted", "upstream_error", "refused"]

# Which Otari routing policy each cohort calls by default (otari/config.yml `routing.policies`).
DEFAULT_POLICIES: dict[str, str] = {
    "villager": "villager", "amplifier": "villager", "official": "official", "ddo": "official",
}
GUARDRAIL_PROFILE = "flood-misinfo"  # served by otari/guardrail/app.py
# Bump when the guardrail patterns change: cached verdicts from older patterns are then ignored.
GUARDRAIL_VERSION = 2
TRANSIENT = {408, 429, 500, 502, 503, 504}

LOCAL_BASE_URL = "http://127.0.0.1:8000/api/v1"      # per otari README.md "Quickstart"
# otari.ai's API (per otari docs/quickstart.md "4. Send a request"); OTARI_BASE_URL overrides
# it, e.g. https://eu.api.otari.ai/api/v1 for an account in the European Union.
HOSTED_BASE_URL = "https://api.otari.ai/api/v1"
# Tool type run by Otari itself (per otari docs/tools.md "Built-in tools").
CODE_EXECUTION_TOOL = {"type": "otari_code_execution"}
# gpt-oss always reasons before it answers and the reasoning counts against max_tokens
# (measured on otari.ai: a 24-token reading came back empty). Models listed here get this
# many extra output tokens; the caller's own max_tokens still names the prompt in the cache.
REASONING_HEADROOM: dict[str, int] = {"openai/gpt-oss-20b": 400, "openai/gpt-oss-120b": 400}


@dataclass
class LLMResult:
    text: str
    status: Status
    cohort: str
    purpose: str
    tick: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    provider: str | None = None
    model: str | None = None
    fallback: bool = False
    attempts: int = 0
    misinfo_flag: bool | None = None   # guardrail verdict on this call's input; None = not checked
    misinfo_score: float | None = None
    # Otari-Request-ID: opens the request in Otari's dashboard at /requests?request=<id>
    # (per otari docs/dashboard.md "Requests and Sessions").
    request_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "cached")


def read_dotenv(path: Path = ROOT / ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def load_keys(gateway: str = "local") -> dict[str, str]:
    """Cohort keys from the environment, falling back to .env. Never logged.

    local:  R2R_KEY_<COHORT>, the keys declared in otari/config.yml.
    hosted: R2R_HOSTED_KEY_<COHORT> if set (one otari.ai key per cohort, see HOSTED.md),
            otherwise the single OTARI_API_KEY for every cohort.
    """
    dotenv = read_dotenv()

    def env(name: str) -> str | None:
        return os.environ.get(name) or dotenv.get(name) or None

    keys: dict[str, str] = {}
    for c in COHORTS:
        if gateway == "hosted":
            val = env(f"R2R_HOSTED_KEY_{c.upper()}") or env("OTARI_API_KEY")
            if not val:
                raise RuntimeError("OTARI_API_KEY is not set; add your otari.ai key to .env (see HOSTED.md)")
        else:
            val = env(f"R2R_KEY_{c.upper()}")
            if not val:
                raise RuntimeError(f"R2R_KEY_{c.upper()} is not set; run `python scripts/init_env.py`")
        keys[c] = val
    return keys


def make_client(gateway: str = "local", **kw: Any) -> "OtariClient":
    """A live client for the local gateway or for otari.ai."""
    if gateway == "hosted":
        base = os.environ.get("OTARI_BASE_URL") or read_dotenv().get("OTARI_BASE_URL") or HOSTED_BASE_URL
        return OtariClient(load_keys("hosted"), base_url=base, gateway="hosted", **kw)
    return OtariClient(load_keys("local"), **kw)


@dataclass
class OtariClient:
    keys: dict[str, str]
    policies: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_POLICIES))
    base_url: str = LOCAL_BASE_URL
    gateway: str = "local"           # "local" (self-hosted) or "hosted" (otari.ai); informational
    # Name a per-run end user on every request. Only a service key may do that: any other
    # key is refused with 403 "'user' field does not match the authenticated API key's user"
    # (measured on otari.ai), after which the client stops naming one.
    name_end_user: bool = True
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # Every live call (answers, budget refusals, upstream errors) is appended here with
    # its run id. It is read back only in replay mode (offline calibration, sweeps).
    cache_path: Path | None = ROOT / "data" / "cache" / "llm" / "prompts.jsonl"
    replay: bool = False
    # Replay this recorded run: its records are served per prompt in the order they
    # happened (so an outage followed by a retry of the same prompt replays exactly).
    # Prompts it never made fall back to the latest successful answer from any run.
    replay_run: str | None = None
    max_retries: int = 2
    retry_backoff_s: float = 1.0     # sleep attempt * this between retries
    timeout_s: float = 180.0
    transport: httpx.BaseTransport | None = None

    silent: set[str] = field(default_factory=set, init=False)
    log: list[LLMResult] = field(default_factory=list, init=False)
    replay_misses: int = field(default=0, init=False)
    # In-run cache: identical prompts within one run are answered once. Live runs never
    # reuse answers from earlier runs, so every run spends its own Otari budget.
    _cache: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _replay: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _queue: dict[str, deque[dict[str, Any]]] = field(default_factory=dict, init=False)
    _http: httpx.Client = field(init=False)

    def __post_init__(self) -> None:
        self._http = httpx.Client(timeout=self.timeout_s, transport=self.transport)
        if self.replay and self.cache_path and self.cache_path.exists():
            own: dict[str, dict[str, Any]] = {}
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                key, value = rec["key"], rec["value"]
                if self.replay_run and rec.get("run") == self.replay_run:
                    self._queue.setdefault(key, deque()).append(value)
                    if value.get("status") == "ok":
                        own[key] = value
                elif value.get("status") in ("ok", "budget_exhausted"):
                    self._replay[key] = value   # last record wins
            self._replay |= own   # the replayed run's own answers win over other runs'

    # ------------------------------------------------------------------ public

    def chat(self, cohort: str, system: str, user: str, *, purpose: str, tick: int,
             max_tokens: int = 120, check_misinfo: bool = False, model: str | None = None,
             tools: list[dict[str, Any]] | None = None) -> LLMResult:
        """One chat completion for `cohort`. Never raises for model-side failures.

        check_misinfo runs the guardrail on this call's *input* in monitor mode: Otari
        checks input only ("output is accepted but not enforced yet", per
        docs/guardrails-reference.md), so the message being interpreted is tagged here.
        """
        policy = model or self.policies[cohort]   # `model` overrides the cohort's policy for one call
        base = LLMResult(text="", status="silent", cohort=cohort, purpose=purpose, tick=tick)
        if cohort in self.silent:
            self.log.append(base)
            return base

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        key = self._cache_key(policy, messages, max_tokens, check_misinfo, tools)
        if key in self._cache:
            v = self._cache[key]
            res = LLMResult(text=v["text"], status="cached", cohort=cohort, purpose=purpose, tick=tick,
                            model=v.get("model"), provider=v.get("provider"),
                            misinfo_flag=v.get("misinfo_flag"), misinfo_score=v.get("misinfo_score"))
            self.log.append(res)
            return res

        if self.replay:
            res = self._from_replay(key, base)
            self.log.append(res)
            return res

        body: dict[str, Any] = {
            "model": policy,
            "messages": messages,
            "max_tokens": max_tokens + REASONING_HEADROOM.get(policy, 0),
            # qwen3 otherwise spends max_tokens on hidden reasoning (spike/SPIKE.md finding 4);
            # Otari forwards it (per docs/routing.md "What is billed and what the caller sees").
            "reasoning_effort": "none",
            # Tags on every usage row (per docs/api-reference.md "Request tags").
            "metadata": {"run": self.run_id, "cohort": cohort, "purpose": purpose},
        }
        if self.name_end_user:
            # Cohort keys are service keys: naming a per-run end user starts it on the
            # cohort's per-run budget (per docs/end-users.md "Name the end user on each request").
            body["user"] = self.end_user(cohort)
        if tools:
            body["tools"] = tools   # Otari runs otari_* tools itself (per docs/tools.md "Who runs a tool")
        if check_misinfo:
            # Top-level field (per docs/guardrails-reference.md "The guardrails request field").
            body["guardrails"] = [{"profile": GUARDRAIL_PROFILE, "mode": "monitor", "on_unavailable": "monitor"}]

        res = self._post(cohort, body, base)
        if res.status in ("ok", "budget_exhausted", "upstream_error"):
            self._store(key, res)
        self.log.append(res)
        return res

    def _from_replay(self, key: str, base: LLMResult) -> LLMResult:
        q = self._queue.get(key)
        v = q.popleft() if q else self._replay.get(key)
        if v is None:
            self.replay_misses += 1
            return _replace(base, status="upstream_error")
        if v.get("status") == "upstream_error":   # a recorded outage: the message was lost live too
            return _replace(base, status="upstream_error")
        if v.get("status") == "budget_exhausted":
            self.silent.add(base.cohort)
            return _replace(base, status="budget_exhausted")
        self._cache[key] = v
        return _replace(base, status="ok", **{k: v[k] for k in _STORED if k in v and k != "status"})

    def end_user(self, cohort: str) -> str:
        return f"{cohort}-{self.run_id}"

    def key_usage(self, cohort: str) -> dict[str, Any]:
        """Otari's own per-key usage for this run, filtered by the run tag
        (per docs/api-reference.md "A key's own budget and usage" and "Request tags")."""
        # Both tags must match (repeatable `tag=key:value`, per docs/api-reference.md "Request tags"),
        # so cohorts that share one key, as on otari.ai, are still reported separately.
        r = self._http.get(f"{self.base_url}/key/usage/summary",
                           params=[("tag", f"run:{self.run_id}"), ("tag", f"cohort:{cohort}")],
                           headers=self._auth(cohort))
        return r.json() if r.status_code == 200 else {"error": r.status_code}

    def key_budget(self, cohort: str) -> dict[str, Any]:
        r = self._http.get(f"{self.base_url}/key/budget", headers=self._auth(cohort))  # docs/api-reference.md
        return r.json() if r.status_code == 200 else {"error": r.status_code}

    def close(self) -> None:
        self._http.close()

    # ----------------------------------------------------------------- private

    def _auth(self, cohort: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys[cohort]}"}  # per README.md "Quickstart"

    def _post(self, cohort: str, body: dict[str, Any], base: LLMResult) -> LLMResult:
        last_err = ""
        for attempt in range(1, self.max_retries + 2):
            t0 = time.perf_counter()
            try:
                r = self._http.post(f"{self.base_url}/chat/completions", json=body, headers=self._auth(cohort))
            except httpx.TransportError as exc:
                last_err = type(exc).__name__
                time.sleep(attempt * self.retry_backoff_s)
                continue
            latency = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                return self._parse_ok(r, base, latency, attempt)
            code = r.headers.get("Otari-Error-Code") or _error_code(r)
            if r.status_code == 403 and code == "budget_exceeded":
                # Budget refusal: 403 + Otari-Error-Code budget_exceeded (per docs/budgets.md).
                # A model event, not a crash: this cohort goes silent for the rest of the run.
                self.silent.add(cohort)
                log.info("cohort %s exhausted its Otari budget at tick %s", cohort, base.tick)
                return _replace(base, status="budget_exhausted", attempts=attempt, latency_ms=latency,
                                request_id=r.headers.get("Otari-Request-ID"))
            if r.status_code == 403 and "user" in body and "'user' field" in r.text:
                # Not a service key: it may not name an end user. Stop naming one and resend.
                log.info("key for %s is not a service key; requests no longer name an end user", cohort)
                self.name_end_user = False
                body.pop("user")
                return self._post(cohort, body, base)
            if r.status_code in TRANSIENT:
                last_err = f"{r.status_code}"
                time.sleep(attempt * self.retry_backoff_s)
                continue
            log.warning("Otari refused %s call: %s %s", cohort, r.status_code, code)
            return _replace(base, status="refused", attempts=attempt, latency_ms=latency)
        log.warning("Otari upstream error for %s after retries: %s", cohort, last_err)
        return _replace(base, status="upstream_error", attempts=self.max_retries + 1)

    def _parse_ok(self, r: httpx.Response, base: LLMResult, latency: float, attempt: int) -> LLMResult:
        data = r.json()
        usage = data.get("usage") or {}
        text = (data["choices"][0]["message"].get("content") or "").strip()
        h = r.headers  # serving headers per docs/routing.md "What is billed and what the caller sees"
        flag, score = None, None
        raw = h.get("Otari-Guardrails")  # per docs/guardrails-reference.md
        if raw:
            for v in json.loads(raw):
                if v.get("profile") == GUARDRAIL_PROFILE and v.get("status") == "evaluated":
                    flag, score = v.get("valid") is False, v.get("score")
        return _replace(
            base, text=text, status="ok", attempts=attempt,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=float(usage.get("cost_usd") or 0.0),  # inline cost, docs/api-reference.md
            latency_ms=float(h.get("Otari-Response-Duration-Ms") or latency),
            provider=h.get("Otari-Provider"), model=h.get("Otari-Model"),
            fallback=h.get("Otari-Fallback") == "true", request_id=h.get("Otari-Request-ID"),
            misinfo_flag=flag, misinfo_score=score,
        )

    @staticmethod
    def _cache_key(policy: str, messages: list[dict[str, str]], max_tokens: int, check: bool,
                   tools: list[dict[str, Any]] | None = None) -> str:
        parts: list[Any] = [policy, messages, max_tokens, GUARDRAIL_VERSION if check else None]
        if tools:
            parts.append(tools)   # appended only when present, so earlier recordings keep their keys
        blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _store(self, key: str, res: LLMResult) -> None:
        value = {k: getattr(res, k) for k in _STORED}
        if res.status == "ok":
            self._cache[key] = value     # refusals and errors are recorded for replay, never served
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "run": self.run_id, "value": value}, ensure_ascii=False) + "\n")


_STORED = ("status", "text", "model", "provider", "misinfo_flag", "misinfo_score",
           "prompt_tokens", "completion_tokens", "cost_usd", "latency_ms", "fallback")


def _error_code(r: httpx.Response) -> str | None:
    try:
        d = r.json().get("detail")
        return d.get("code") if isinstance(d, dict) else r.json().get("code")
    except Exception:
        return None


def _replace(base: LLMResult, **kw: Any) -> LLMResult:
    return LLMResult(**(asdict(base) | kw))
