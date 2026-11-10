"""Rumour-to-Relief Mesa model.

Rule-based dynamics (movement, belief updates, word of mouth, the flood itself);
the model is called only to *generate* messages and to *interpret* them. Built on the
installed Mesa 3.5 API (Model(rng=...), Agent(model), model.agents_by_type,
AgentSet.shuffle_do, DataCollector) and mesa-geo 0.9.3 (GeoAgent, GeoSpace).

Time line (one tick ~ 30 minutes):
  warning_tick  District Disaster Office learns of a cloudburst upstream
  rumour_start  Amplifiers start forwarding rumours that discourage evacuation
  rising_tick   river visibly rising: people in the flood zone notice
  flood_tick    flash flood: anyone still at home in the flood zone is caught
"""

from __future__ import annotations

import dataclasses
import itertools
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import mesa
import mesa_geo as mg
import networkx as nx
import numpy as np
from shapely.geometry import Point

from r2r import prompts
from r2r.config import DistrictConfig
from r2r.geodata import District, load_district
from r2r.llm import COHORTS, LLMResult, OtariClient
from r2r.network import build_contact_graph
from r2r.space import Village, build_space

KIND_HINT = {
    "alert": "SMS from the district administration",
    "relay": "message from your panchayat pradhan",
    "rumour": "WhatsApp forward from a village group",
    "factcheck": "message from the district fact-check service",
}
OFFICIAL_KINDS = {"alert", "relay", "factcheck"}


@dataclass(frozen=True)
class Scenario:
    seed: int = 42
    ticks: int = 20
    villagers_per_village: int = 2
    n_amplifiers: int = 4
    warning_tick: int = 2
    rumour_start: int = 3
    rising_tick: int = 9
    flood_tick: int = 14             # calibrated
    relay_every: int = 3
    rumour_every: int = 2
    phone_share: float = 0.8
    relay_reach: float = 0.6         # calibrated: share of a panchayat an official reaches per relay
    whatsapp_reach: float = 0.6
    # Rule-based dynamics. Values set by `python -m r2r.calibrate` against stated
    # plausibility targets (see CALIBRATION.md); not fitted to observed evacuation data.
    belief_gain: float = 0.45        # how far one believed message moves danger perception
    doubt_gain: float = 0.2          # rumour that leaves a reader "unsure" still erodes urgency
    far_relevance: float = 0.3       # weight of a "families near the river" warning for people not near it
    rising_rate: float = 0.04        # danger added per tick by seeing the river rise (flood zone only)
    wom_pull: float = 0.1            # pull towards the village's mean danger each tick
    wom_leave_bonus: float = 0.05    # extra danger when neighbours are seen leaving
    threshold_mean: float = 0.6      # mean danger at which a person evacuates
    # interventions
    sms_alert: bool = False
    fact_check: bool = False
    local_language: bool = False
    # model choice: cohort -> Otari routing policy (None = defaults in r2r.llm)
    policies: dict[str, str] | None = None
    district: DistrictConfig = field(default_factory=DistrictConfig)

    @property
    def label(self) -> str:
        on = [n for n in ("sms_alert", "fact_check", "local_language") if getattr(self, n)]
        return "+".join(on) or "baseline"


@dataclass
class Message:
    id: int
    kind: str            # alert | relay | rumour | factcheck
    cohort: str          # who generated it (Otari key)
    author: int          # agent unique_id
    tick: int
    text: str
    lang: str            # "hi" or "en"
    misinfo_flag: bool | None = None   # guardrail verdict, set when first interpreted
    misinfo_score: float | None = None
    recipients: set[int] = field(default_factory=set)
    fact_checked: bool = False


# ------------------------------------------------------------------------- agents

class Person(mg.GeoAgent):
    cohort: str = "villager"

    def __init__(self, model: "FloodModel", geometry: Point, crs: str, village_id: int) -> None:
        super().__init__(model, geometry, crs)
        self.village_id = village_id


class Villager(Person):
    cohort = "villager"

    def __init__(self, model: "FloodModel", geometry: Point, crs: str, village_id: int,
                 persona: str, at_risk: bool) -> None:
        super().__init__(model, geometry, crs, village_id)
        rng = model.rng
        self.persona = persona
        base = {"trusting_elder": 0.8, "wary_farmer": 0.35, "young_bilingual": 0.6}[persona]
        self.trust_official = float(np.clip(base + rng.normal(0, 0.08), 0.05, 0.95))
        self.threshold = float(np.clip(rng.normal(model.params.threshold_mean, 0.08), 0.35, 0.9))
        self.danger = float(np.clip(rng.normal(0.15, 0.05), 0, 1))
        self.rumour_belief = 0.0
        self.at_risk = at_risk
        self.has_phone = bool(rng.random() < model.params.phone_share)
        self.status = "home"   # home -> evacuating -> safe; or caught at the flood
        self.inbox: list[Message] = []
        self.seen: set[int] = set()

    def receive(self, msg: Message) -> None:
        if msg.id not in self.seen:
            self.seen.add(msg.id)
            self.inbox.append(msg)
            msg.recipients.add(self.unique_id)

    def step(self) -> None:
        m: FloodModel = self.model  # type: ignore[assignment]
        p = m.params
        for msg in self.inbox:
            reading = m.read(msg, self.persona)
            if reading is None:   # villager cohort silent / model unavailable: message not taken in
                continue
            believe = reading.believe
            if msg.lang not in prompts.PERSONA_LANGS[self.persona]:
                believe *= 0.4    # could not read it properly
            trust = self.trust_official if msg.kind in OFFICIAL_KINDS else 1 - 0.5 * self.trust_official
            eff = believe * trust
            delta = p.belief_gain * eff * reading.evacuate
            if delta > 0 and not self.at_risk:
                delta *= p.far_relevance
            if msg.kind == "rumour" and reading.evacuate == 0:
                delta = -p.doubt_gain * eff
            self.danger = float(np.clip(self.danger + delta, 0, 1))
            if msg.kind == "rumour" and reading.evacuate <= 0:
                self.rumour_belief = max(self.rumour_belief, eff)
            if msg.kind == "factcheck":
                self.rumour_belief *= 1 - eff
        self.inbox.clear()


class PanchayatOfficial(Person):
    cohort = "official"

    def __init__(self, model: "FloodModel", geometry: Point, crs: str, village_id: int, panchayat: int) -> None:
        super().__init__(model, geometry, crs, village_id)
        self.panchayat = panchayat
        self.alerted_at: int | None = None

    def step(self) -> None:
        m: FloodModel = self.model  # type: ignore[assignment]
        if self.alerted_at is None or (m.tick - self.alerted_at) % m.params.relay_every:
            return
        names = m.panchayat_names[self.panchayat]
        msg = m.generate("official", "relay", self, prompts.official_relay(m.lang_official, names, m.tick),
                         m.lang_official)
        if msg:
            for v in m.villagers_in_panchayat[self.panchayat]:
                if m.rng.random() < m.params.relay_reach:
                    v.receive(msg)


class Amplifier(Person):
    cohort = "amplifier"

    def __init__(self, model: "FloodModel", geometry: Point, crs: str, village_id: int, angle: str) -> None:
        super().__init__(model, geometry, crs, village_id)
        self.angle = angle
        self.rounds = 0
        self.group: list[Villager] = []

    def step(self) -> None:
        m: FloodModel = self.model  # type: ignore[assignment]
        s = m.params
        if m.tick < s.rumour_start or (m.tick - s.rumour_start) % s.rumour_every:
            return
        self.rounds += 1
        home = m.village_names[self.village_id]
        msg = m.generate("amplifier", "rumour", self, prompts.amplifier_rumour(self.angle, home, self.rounds), "hi")
        if msg:
            for v in self.group:
                if m.rng.random() < s.whatsapp_reach:
                    v.receive(msg)


class DistrictDisasterOffice(Person):
    cohort = "ddo"

    def step(self) -> None:
        m: FloodModel = self.model  # type: ignore[assignment]
        s = m.params
        if m.tick == s.warning_tick:
            for o in m.agents_by_type[PanchayatOfficial]:
                o.alerted_at = m.tick + 1   # official channel to pradhans, always on
            if s.sms_alert:
                at_risk_names = [m.village_names[i] for i in m.flood_villages]
                msg = m.generate("ddo", "alert", self, prompts.ddo_alert(m.lang_official, at_risk_names),
                                 m.lang_official)
                if msg:
                    for v in m.agents_by_type[Villager]:
                        if v.has_phone:
                            v.receive(msg)
        if s.fact_check:
            for rumour in m.messages:
                if rumour.kind == "rumour" and rumour.misinfo_flag and not rumour.fact_checked:
                    rumour.fact_checked = True
                    fc = m.generate("ddo", "factcheck", self, prompts.factcheck(rumour.text, m.lang_official),
                                    m.lang_official)
                    if fc:
                        for v in m.agents_by_type[Villager]:
                            if v.unique_id in rumour.recipients:
                                v.receive(fc)


# -------------------------------------------------------------------------- model

class FloodModel(mesa.Model):
    def __init__(self, scenario: Scenario = Scenario(), llm: OtariClient | None = None,
                 district: District | None = None) -> None:
        super().__init__(rng=scenario.seed)
        self.params = scenario
        self.tick = 0
        self.llm = llm
        if llm is not None and scenario.policies:
            llm.policies.update(scenario.policies)
        self.lang_official = "hi" if scenario.local_language else "en"
        self.district = district or load_district(scenario.district)
        dv = self.district.villages
        self.graph: nx.Graph = build_contact_graph(dv, scenario.district.contact_km)
        self.space, self.village_agents = build_space(self, self.district)
        self.crs = self.space.crs
        self.village_names = dict(zip(dv["village_id"].astype(int), dv["name"].astype(str)))
        self.flood_villages = [int(i) for i in dv.loc[dv["in_flood_zone"], "village_id"]]
        self.panchayat_of = dict(zip(dv["village_id"].astype(int), dv["panchayat"].astype(int)))
        self.panchayat_names: dict[int, list[str]] = {}
        for vid, p in self.panchayat_of.items():
            self.panchayat_names.setdefault(p, []).append(self.village_names[vid])

        self.messages: list[Message] = []
        self._msg_ids = itertools.count()
        self._readings: dict[tuple[int, str], prompts.Reading | None] = {}
        self.generation_failures: Counter[str] = Counter()
        self._populate(dv)

        reporters: dict[str, Any] = {
            "tick": lambda m: m.tick,
            "evacuated_at_risk_share": lambda m: m._share(lambda v: v.at_risk and v.status in ("evacuating", "safe"),
                                                          lambda v: v.at_risk),
            "caught": lambda m: sum(v.status == "caught" for v in m.agents_by_type[Villager]),
            "unneeded_evacuations": lambda m: sum(not v.at_risk and v.status in ("evacuating", "safe")
                                                  for v in m.agents_by_type[Villager]),
            "mean_danger": lambda m: float(m.agents_by_type[Villager].agg("danger", np.mean)),
            "rumour_believers_share": lambda m: m._share(lambda v: v.rumour_belief > 0.5),
            "messages": lambda m: len(m.messages),
            "rumours": lambda m: sum(x.kind == "rumour" for x in m.messages),
            "misinfo_checked": lambda m: sum(x.misinfo_flag is not None for x in m.messages),
            "misinfo_flagged": lambda m: sum(bool(x.misinfo_flag) for x in m.messages),
            "misinfo_tag_rate": lambda m: m._tag_rate(),
            "llm_calls": lambda m: m._llm_count(lambda r: r.status == "ok"),
            "cache_hits": lambda m: m._llm_count(lambda r: r.status == "cached"),
            "silent_calls": lambda m: m._llm_count(lambda r: r.status == "silent"),
            "budget_events": lambda m: m._llm_count(lambda r: r.status == "budget_exhausted"),
            "upstream_errors": lambda m: m._llm_count(lambda r: r.status in ("upstream_error", "refused")),
            "fallbacks": lambda m: m._llm_count(lambda r: r.fallback),
            "silent_cohorts": lambda m: ",".join(sorted(m.llm.silent)) if m.llm else "",
        }
        for c in COHORTS:
            reporters[f"tokens_{c}"] = lambda m, c=c: m._llm_sum(c, lambda r: r.prompt_tokens + r.completion_tokens)
            reporters[f"cost_{c}"] = lambda m, c=c: m._llm_sum(c, lambda r: r.cost_usd)
            reporters[f"latency_ms_{c}"] = lambda m, c=c: m._llm_mean_latency(c)
        self.datacollector = mesa.DataCollector(
            model_reporters=reporters,
            agenttype_reporters={Villager: {"danger": "danger", "rumour_belief": "rumour_belief",
                                            "status": "status", "village_id": "village_id",
                                            "persona": "persona", "at_risk": "at_risk"}},
        )
        self.datacollector.collect(self)

    # ------------------------------------------------------------ construction

    def _jitter(self, vid: int, metres: float = 150.0) -> Point:
        p = self.village_agents[vid].geometry
        dx, dy = self.rng.normal(0, metres, 2)
        return Point(p.x + dx, p.y + dy)

    def _populate(self, dv) -> None:  # type: ignore[no-untyped-def]
        s, crs = self.params, self.crs
        personas = list(prompts.PERSONAS)
        flood = set(self.flood_villages)
        people: list[Person] = []
        self.villagers_in_panchayat: dict[int, list[Villager]] = {}
        for vid in sorted(self.village_names):
            for _ in range(s.villagers_per_village):
                persona = personas[int(self.rng.choice(len(personas), p=[0.35, 0.35, 0.30]))]
                v = Villager(self, self._jitter(vid), crs, vid, persona, at_risk=vid in flood)
                people.append(v)
                self.villagers_in_panchayat.setdefault(self.panchayat_of[vid], []).append(v)
        for p, names in sorted(self.panchayat_names.items()):
            vid = min(i for i, q in self.panchayat_of.items() if q == p)
            people.append(PanchayatOfficial(self, self._jitter(vid), crs, vid, p))
        # Amplifiers live in the best-connected villages (most WhatsApp reach).
        hubs = sorted(self.graph.degree, key=lambda kv: (-kv[1], kv[0]))[: s.n_amplifiers]
        villagers_by_village: dict[int, list[Villager]] = {}
        for v in people:
            if isinstance(v, Villager):
                villagers_by_village.setdefault(v.village_id, []).append(v)
        for k, (vid, _) in enumerate(hubs):
            a = Amplifier(self, self._jitter(vid), crs, vid, prompts.RUMOUR_ANGLES[k % len(prompts.RUMOUR_ANGLES)])
            reach = nx.single_source_shortest_path_length(self.graph, vid, cutoff=2)
            a.group = [v for r in sorted(reach) for v in villagers_by_village.get(r, [])]
            people.append(a)
        centre = self.district.boundary.geometry.iloc[0].centroid
        kullu = dv[dv["name"].str.contains("Kullu", case=False)]
        ddo_point = kullu.geometry.iloc[0] if len(kullu) else centre
        people.append(DistrictDisasterOffice(self, ddo_point, crs, -1))
        self.space.add_agents(people)
        self.safe_points = {vid: self.village_agents[vid].geometry for vid in self.village_names if vid not in flood}

    # ---------------------------------------------------------------- LLM layer

    def generate(self, cohort: str, kind: str, author: Person, prompt: str, lang: str) -> Message | None:
        if self.llm is None:
            return None
        res = self.llm.chat(cohort, prompts.SYSTEM_WRITER, prompt, purpose=f"generate:{kind}",
                            tick=self.tick, max_tokens=90)
        if not res.ok or not res.text:
            self.generation_failures[cohort] += 1
            return None
        msg = Message(next(self._msg_ids), kind, cohort, author.unique_id, self.tick, res.text, lang)
        self.messages.append(msg)
        return msg

    def read(self, msg: Message, persona: str) -> prompts.Reading | None:
        """One interpretation per (message, persona), shared by every villager with that persona.
        The guardrail runs on this call's input, so this is where the message gets tagged."""
        key = (msg.id, persona)
        if key not in self._readings:
            res: LLMResult | None = None
            if self.llm is not None:
                res = self.llm.chat("villager", prompts.SYSTEM_READER,
                                    prompts.interpret(persona, KIND_HINT[msg.kind], msg.text),
                                    purpose="interpret", tick=self.tick, max_tokens=24, check_misinfo=True)
            if res is None or not res.ok:
                self._readings[key] = None
            else:
                if msg.misinfo_flag is None and res.misinfo_flag is not None:
                    msg.misinfo_flag, msg.misinfo_score = res.misinfo_flag, res.misinfo_score
                self._readings[key] = prompts.parse_reading(res.text)
        return self._readings[key]

    # ------------------------------------------------------------------ dynamics

    def step(self) -> None:
        self.tick += 1
        s = self.params
        for cls in (DistrictDisasterOffice, PanchayatOfficial, Amplifier, Villager):
            self.agents_by_type[cls].shuffle_do("step")
        villagers = self.agents_by_type[Villager]
        # Seeing the river rise (rule-based); rumours that "the river is going down" blunt it.
        if self.tick >= s.rising_tick:
            for v in villagers.select(lambda v: v.at_risk and v.status == "home"):
                v.danger = min(1.0, v.danger + s.rising_rate * (1 - v.rumour_belief))
        # Word of mouth inside each village (rule-based, no model call).
        by_village: dict[int, list[Villager]] = {}
        for v in villagers:
            by_village.setdefault(v.village_id, []).append(v)
        for group in by_village.values():
            mean = float(np.mean([v.danger for v in group]))
            leaving = any(v.status in ("evacuating", "safe") for v in group)
            for v in group:
                bonus = s.wom_leave_bonus if leaving else 0.0
                v.danger = float(np.clip(v.danger + s.wom_pull * (mean - v.danger) + bonus, 0, 1))
        # Movement: evacuating -> safe at the nearest village outside the flood zone.
        for v in villagers.select(lambda v: v.status == "evacuating"):
            v.status = "safe"
            target = min(self.safe_points.values(), key=lambda p: p.distance(v.geometry), default=v.geometry)
            v.geometry = Point(target.x, target.y)
        # The flood comes before this tick's new decisions: deciding to leave now is too late.
        if self.tick == s.flood_tick:
            for v in villagers.select(lambda v: v.at_risk and v.status == "home"):
                v.status = "caught"
        for v in villagers.select(lambda v: v.status == "home" and v.danger >= v.threshold):
            v.status = "evacuating"
        self.datacollector.collect(self)
        if self.tick >= s.ticks:
            self.running = False

    # --------------------------------------------------------------- reporters

    def _share(self, pred, base=lambda v: True) -> float:  # type: ignore[no-untyped-def]
        pool = [v for v in self.agents_by_type[Villager] if base(v)]
        return round(sum(1 for v in pool if pred(v)) / len(pool), 4) if pool else 0.0

    def _tag_rate(self) -> float:
        checked = [x for x in self.messages if x.misinfo_flag is not None]
        return round(sum(bool(x.misinfo_flag) for x in checked) / len(checked), 4) if checked else 0.0

    def _llm_count(self, pred) -> int:  # type: ignore[no-untyped-def]
        return sum(1 for r in self.llm.log if pred(r)) if self.llm else 0

    def _llm_sum(self, cohort: str, f) -> float:  # type: ignore[no-untyped-def]
        return round(sum(f(r) for r in self.llm.log if r.cohort == cohort), 6) if self.llm else 0.0

    def _llm_mean_latency(self, cohort: str) -> float:
        if not self.llm:
            return 0.0
        lat = [r.latency_ms for r in self.llm.log if r.cohort == cohort and r.status == "ok"]
        return round(float(np.mean(lat)), 1) if lat else 0.0

    def messages_table(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in dataclasses.asdict(m).items() if k != "recipients"} | {"reach": len(m.recipients)}
                for m in self.messages]
