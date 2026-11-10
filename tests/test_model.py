"""Model invariants on a synthetic district with a fake Otari."""

from __future__ import annotations

import dataclasses

import pytest
from fake_otari import KEYS, FakeOtari
from test_space import CFG, synthetic

from r2r import prompts
from r2r.geodata import District, flood_zone, select_villages
from r2r.llm import OtariClient
from r2r.model import Amplifier, DistrictDisasterOffice, FloodModel, PanchayatOfficial, Scenario, Villager


def district() -> District:
    boundary, rivers, places = synthetic()
    zone = flood_zone(rivers, boundary, CFG)
    v = select_villages(places, rivers, zone.geometry.iloc[0], CFG)
    return District(CFG, boundary, v, rivers, rivers.iloc[0:0], zone)


def run(scenario: Scenario, fake: FakeOtari | None = None) -> tuple[FloodModel, FakeOtari]:
    fake = fake or FakeOtari()
    llm = OtariClient(KEYS, run_id="t", cache_path=None, retry_backoff_s=0, transport=fake.transport())
    m = FloodModel(dataclasses.replace(scenario, district=CFG), llm=llm, district=district())
    while m.running:
        m.step()
    return m, fake


BASE = Scenario(seed=11, ticks=20, villagers_per_village=3)


@pytest.fixture(scope="module")
def baseline() -> FloodModel:
    return run(BASE)[0]


def test_population_size_and_types(baseline: FloodModel) -> None:
    n = len(baseline.agents) - len(baseline.village_agents)
    assert 50 <= n <= 150
    assert len(baseline.agents_by_type[DistrictDisasterOffice]) == 1
    assert len(baseline.agents_by_type[PanchayatOfficial]) == CFG.n_panchayats
    assert len(baseline.agents_by_type[Amplifier]) == BASE.n_amplifiers
    assert baseline.tick == BASE.ticks and not baseline.running


def test_states_and_beliefs_stay_valid(baseline: FloodModel) -> None:
    for v in baseline.agents_by_type[Villager]:
        assert 0 <= v.danger <= 1 and 0 <= v.rumour_belief <= 1
        assert v.status in {"home", "evacuating", "safe", "caught"}
        if v.status == "caught":
            assert v.at_risk
        if v.at_risk:
            assert v.status != "home"     # after the flood, at-risk people are either out or caught


def test_people_who_reached_safety_are_outside_the_flood_zone(baseline: FloodModel) -> None:
    zone = baseline.district.flood_zone.geometry.iloc[0]
    for v in baseline.agents_by_type[Villager].select(lambda v: v.status == "safe"):
        assert not zone.contains(v.geometry)


def test_same_seed_same_results() -> None:
    a, _ = run(BASE)
    b, _ = run(BASE)
    cols = ["evacuated_at_risk_share", "caught", "mean_danger", "rumour_believers_share", "messages"]
    assert a.datacollector.get_model_vars_dataframe()[cols].equals(b.datacollector.get_model_vars_dataframe()[cols])


def test_interpretations_are_shared_per_persona(baseline: FloodModel) -> None:
    interpret = [r for r in baseline.llm.log if r.purpose == "interpret" and r.status == "ok"]
    assert len(interpret) <= len(baseline.messages) * len(prompts.PERSONAS)


def test_rumours_are_tagged_and_tag_rate_recorded(baseline: FloodModel) -> None:
    df = baseline.datacollector.get_model_vars_dataframe()
    rumours = [m for m in baseline.messages if m.kind == "rumour"]
    assert rumours and all(m.misinfo_flag for m in rumours if m.recipients)
    assert 0 < df["misinfo_tag_rate"].iloc[-1] <= 1


def test_budget_exhaustion_is_a_model_event_not_a_crash() -> None:
    m, _ = run(BASE, FakeOtari(budget_calls={"amplifier": 3}))
    df = m.datacollector.get_model_vars_dataframe()
    assert m.tick == BASE.ticks
    assert "amplifier" in m.llm.silent
    assert df["budget_events"].iloc[-1] == 1
    assert sum(x.kind == "rumour" for x in m.messages) == 3


def test_otari_outage_does_not_crash_the_run() -> None:
    m, _ = run(BASE, FakeOtari(always_fail=True))
    assert m.tick == BASE.ticks and m.messages == []


def test_interventions_change_outcomes_in_the_expected_direction(baseline: FloodModel) -> None:
    sms, _ = run(dataclasses.replace(BASE, sms_alert=True))
    fc, _ = run(dataclasses.replace(BASE, sms_alert=True, fact_check=True))
    share = lambda m: m.datacollector.get_model_vars_dataframe()["evacuated_at_risk_share"].iloc[-1]  # noqa: E731
    assert share(sms) >= share(baseline)
    assert any(x.kind == "factcheck" for x in fc.messages)
    rb = lambda m: m.datacollector.get_model_vars_dataframe()["rumour_believers_share"].iloc[-1]  # noqa: E731
    assert rb(fc) <= rb(sms)


def test_otari_usage_flows_into_the_datacollector(baseline: FloodModel) -> None:
    mv = baseline.datacollector.get_model_vars_dataframe()
    log = baseline.llm.log
    for cohort in ("villager", "official", "amplifier", "ddo"):
        served = [r for r in log if r.cohort == cohort and r.status == "ok"]
        assert mv[f"tokens_{cohort}"].iloc[-1] == sum(r.prompt_tokens + r.completion_tokens for r in served)
        assert mv[f"cost_{cohort}"].iloc[-1] == pytest.approx(sum(r.cost_usd for r in served))
        assert mv[f"tokens_{cohort}"].is_monotonic_increasing          # cumulative per tick
    assert mv["tokens_villager"].iloc[-1] > 0 and mv["latency_ms_villager"].iloc[-1] > 0
    assert mv["llm_calls"].iloc[-1] == sum(r.status == "ok" for r in log)


def test_failovers_are_counted_as_a_model_variable() -> None:
    m, _ = run(BASE, FakeOtari(fallback=True))
    final = m.datacollector.get_model_vars_dataframe().iloc[-1]
    assert final.fallbacks == final.llm_calls > 0


def test_evacuation_never_reverses_and_nobody_is_caught_before_the_flood(baseline: FloodModel) -> None:
    mv = baseline.datacollector.get_model_vars_dataframe()
    assert mv["evacuated_at_risk_share"].is_monotonic_increasing
    assert (mv.loc[mv.tick < BASE.flood_tick, "caught"] == 0).all()
    assert mv["caught"].iloc[-1] == mv.loc[mv.tick == BASE.flood_tick, "caught"].iloc[0]   # caught at the flood only


def test_a_model_that_refuses_to_write_rumours_sends_none() -> None:
    m, _ = run(BASE, FakeOtari(refuse_rumours=True))
    final = m.datacollector.get_model_vars_dataframe().iloc[-1]
    assert final.rumours == 0 and final.refusals > 0
    assert not any(prompts.is_refusal(x.text) for x in m.messages)       # a refusal is never forwarded
    assert final.rumour_believers_share == 0


def test_after_action_report_uses_the_code_tool_on_the_analyst_model() -> None:
    m, fake = run(dataclasses.replace(BASE, analyst_model="openai/gpt-oss-120b"))
    assert m.after_action_report and "tick 12" in m.after_action_report
    body = fake.bodies[-1]                                               # the last call of the run
    assert body["model"] == "openai/gpt-oss-120b" and body["tools"] == [{"type": "otari_code_execution"}]
    assert "tick,evacuated_share,caught" in body["messages"][1]["content"]
    assert body["metadata"]["cohort"] == "ddo"                           # on the district office's key and budget
    assert run(BASE)[0].after_action_report is None                      # off unless an analyst model is set
