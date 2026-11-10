"""OtariClient wrapper: budgets as model events, retries, cache, guardrail tags."""

from __future__ import annotations

from pathlib import Path

from fake_otari import KEYS, FakeOtari
from r2r.llm import OtariClient


def client(fake: FakeOtari, tmp_path: Path | None = None) -> OtariClient:
    return OtariClient(KEYS, run_id="run1", cache_path=(tmp_path / "c.jsonl") if tmp_path else None, retry_backoff_s=0,
                       transport=fake.transport())


def test_ok_call_sends_cohort_key_policy_and_end_user() -> None:
    fake = FakeOtari()
    r = client(fake).chat("amplifier", "sys", "hello", purpose="t", tick=1)
    assert r.status == "ok" and r.text
    body = fake.bodies[0]
    assert fake.auth[0] == "Bearer tk-test-amplifier"
    assert body["model"] == "villager"                 # amplifiers use the villager policy
    assert body["user"] == "amplifier-run1"           # per-run end user on the service key
    assert body["reasoning_effort"] == "none"
    assert body["metadata"] == {"run": "run1", "cohort": "amplifier", "purpose": "t"}
    assert (r.prompt_tokens, r.completion_tokens, r.provider) == (40, 10, "ollama")
    assert abs(r.cost_usd - 0.000002) < 1e-12


def test_budget_exhaustion_silences_cohort_without_raising() -> None:
    fake = FakeOtari(budget_calls={"amplifier": 2})
    c = client(fake)
    statuses = [c.chat("amplifier", "s", f"m{i}", purpose="t", tick=i).status for i in range(5)]
    assert statuses == ["ok", "ok", "budget_exhausted", "silent", "silent"]
    assert c.silent == {"amplifier"}
    assert fake.calls == 3                              # silent calls never reach Otari
    assert c.chat("villager", "s", "x", purpose="t", tick=5).status == "ok"   # other cohorts unaffected


def test_transient_errors_are_retried() -> None:
    fake = FakeOtari(fail_first=2)
    c = client(fake)
    c.max_retries = 2
    r = c.chat("official", "s", "x", purpose="t", tick=0)
    assert r.status == "ok" and r.attempts == 3


def test_persistent_upstream_failure_is_reported_not_raised() -> None:
    fake = FakeOtari(always_fail=True)
    c = client(fake)
    c.max_retries = 1
    r = c.chat("official", "s", "x", purpose="t", tick=0)
    assert r.status == "upstream_error" and not r.ok
    assert "official" not in c.silent                   # an outage is not a budget event


def test_identical_prompts_hit_the_in_run_cache_only(tmp_path: Path) -> None:
    fake = FakeOtari()
    c = client(fake, tmp_path)
    a = c.chat("villager", "s", "same", purpose="t", tick=0, check_misinfo=True)
    b = c.chat("villager", "s", "same", purpose="t", tick=1, check_misinfo=True)
    assert (a.status, b.status) == ("ok", "cached") and a.text == b.text
    assert fake.calls == 1
    # A new live run does not reuse earlier answers: it spends its own Otari budget.
    assert client(fake, tmp_path).chat("villager", "s", "same", purpose="t", tick=2,
                                       check_misinfo=True).status == "ok"
    assert fake.calls == 2


def test_replay_reproduces_answers_and_budget_refusals_offline(tmp_path: Path) -> None:
    fake = FakeOtari(budget_calls={"amplifier": 1})
    live = client(fake, tmp_path)
    live_status = [live.chat("amplifier", "s", f"m{i}", purpose="t", tick=i).status for i in range(3)]
    assert live_status == ["ok", "budget_exhausted", "silent"]
    calls_before = fake.calls
    rep = OtariClient(KEYS, cache_path=tmp_path / "c.jsonl", replay=True, transport=fake.transport())
    replayed = [rep.chat("amplifier", "s", f"m{i}", purpose="t", tick=i) for i in range(3)]
    assert [r.status for r in replayed] == live_status
    assert replayed[0].text == live.log[0].text and replayed[0].prompt_tokens == 40
    assert fake.calls == calls_before and rep.replay_misses == 0     # nothing reached Otari
    assert rep.chat("villager", "s", "never seen", purpose="t", tick=0).status == "upstream_error"
    assert rep.replay_misses == 1


def test_replay_of_a_run_reproduces_an_outage_then_retry(tmp_path: Path) -> None:
    fake = FakeOtari(fail_first=3)                       # 3 attempts = the first chat call fails
    live = client(fake, tmp_path)
    live_status = [live.chat("official", "s", "same prompt", purpose="t", tick=i).status for i in range(3)]
    assert live_status == ["upstream_error", "ok", "cached"]
    # Without the run, replay "heals" the outage (the later success wins) ...
    healed = OtariClient(KEYS, cache_path=tmp_path / "c.jsonl", replay=True)
    assert healed.chat("official", "s", "same prompt", purpose="t", tick=0).status == "ok"
    # ... replaying that run serves its records in order, so the outage happens again.
    rep = OtariClient(KEYS, cache_path=tmp_path / "c.jsonl", replay=True, replay_run="run1")
    assert [rep.chat("official", "s", "same prompt", purpose="t", tick=i).status for i in range(3)] == live_status
    assert rep.replay_misses == 0


def test_guardrail_verdict_parsed_from_header() -> None:
    fake = FakeOtari()
    c = client(fake)
    bad = c.chat("villager", "s", "the alert is fake", purpose="t", tick=0, check_misinfo=True)
    good = c.chat("villager", "s", "move uphill", purpose="t", tick=0, check_misinfo=True)
    plain = c.chat("villager", "s", "no check", purpose="t", tick=0)
    assert fake.bodies[0]["guardrails"][0] == {"profile": "flood-misinfo", "mode": "monitor",
                                               "on_unavailable": "monitor"}
    assert (bad.misinfo_flag, good.misinfo_flag, plain.misinfo_flag) == (True, False, None)
    assert "guardrails" not in fake.bodies[2]


def test_other_refusals_do_not_silence_the_cohort() -> None:
    fake = FakeOtari(block=True)
    c = client(fake)
    r = c.chat("villager", "s", "m", purpose="t", tick=0)
    assert r.status == "refused" and not r.ok and fake.calls == 1      # not retried
    assert c.silent == set()                                           # only a budget refusal silences


def test_failover_and_request_id_are_read_from_serving_headers() -> None:
    c = client(FakeOtari(fallback=True))
    r = c.chat("official", "s", "m", purpose="t", tick=0)
    assert r.ok and r.fallback and r.provider == "groq" and r.request_id == "req-1"
    local = client(FakeOtari()).chat("official", "s", "m", purpose="t", tick=0)
    assert not local.fallback and local.provider == "ollama"


def test_a_key_that_is_not_a_service_key_stops_naming_an_end_user() -> None:
    fake = FakeOtari(service_keys=False)          # otari.ai refuses `user` on an ordinary key
    c = client(fake)
    first = c.chat("villager", "s", "m1", purpose="t", tick=0)
    second = c.chat("villager", "s", "m2", purpose="t", tick=0)
    assert first.ok and second.ok and c.name_end_user is False
    assert "user" in fake.bodies[0] and "user" not in fake.bodies[1] and "user" not in fake.bodies[2]
    assert fake.calls == 3                          # the refused request was resent once, then no more


def test_tools_and_model_override_are_sent_and_cached_separately() -> None:
    fake = FakeOtari()
    c = client(fake)
    plain = c.chat("ddo", "s", "same", purpose="t", tick=0)
    tooled = c.chat("ddo", "s", "same", purpose="t", tick=0, model="openai/gpt-oss-120b",
                    tools=[{"type": "otari_code_execution"}])
    assert plain.status == tooled.status == "ok"    # not served from the plain call's cache entry
    assert fake.bodies[1]["model"] == "openai/gpt-oss-120b"
    assert fake.bodies[1]["tools"] == [{"type": "otari_code_execution"}] and "tools" not in fake.bodies[0]
    assert fake.bodies[1]["max_tokens"] == 120 + 400    # reasoning headroom for gpt-oss


def test_hosted_keys_fall_back_to_the_single_otari_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from r2r import llm
    monkeypatch.setattr(llm, "read_dotenv", lambda: {})
    for name in ("OTARI_API_KEY", *[f"R2R_HOSTED_KEY_{c.upper()}" for c in llm.COHORTS]):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTARI_API_KEY", "otk_test_shared")
    monkeypatch.setenv("R2R_HOSTED_KEY_AMPLIFIER", "otk_test_amplifier")
    keys = llm.load_keys("hosted")
    assert keys["villager"] == "otk_test_shared" and keys["amplifier"] == "otk_test_amplifier"
    assert llm.make_client("hosted").base_url == llm.HOSTED_BASE_URL
