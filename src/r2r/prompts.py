"""Prompt templates. Kept small and deterministic so identical prompts hit the cache."""

from __future__ import annotations

import re
from dataclasses import dataclass

SYSTEM_WRITER = (
    "You write short mobile messages (max 40 words) for a simulation of a monsoon flash flood "
    "in Kullu district, Himachal Pradesh. Output only the message text."
)

# Persona of a message *reader*. Interpretation is called once per (message, persona),
# not once per villager, which keeps the number of model calls small.
PERSONAS: dict[str, str] = {
    "trusting_elder": "an elderly farmer who reads only Hindi, trusts the panchayat pradhan and the government",
    "wary_farmer": "a middle-aged apple grower who reads Hindi, distrusts officials after past false alarms, "
                   "and trusts WhatsApp groups of neighbours",
    "young_bilingual": "a young shopkeeper who reads English and Hindi, is active on WhatsApp, and is moderately "
                       "trusting of officials",
}
PERSONA_LANGS: dict[str, tuple[str, ...]] = {
    "trusting_elder": ("hi",),
    "wary_farmer": ("hi",),
    "young_bilingual": ("hi", "en"),
}

SYSTEM_READER = (
    "You role-play one villager in a flood simulation and judge one message they received. "
    "Answer exactly in the form: BELIEVE=<0-10> EVACUATE=<yes|no|unsure>"
)


def lang_instruction(lang: str) -> str:
    return "Write it in simple Hindi (Devanagari)." if lang == "hi" else "Write it in English."


def ddo_alert(lang: str, villages: list[str]) -> str:
    return (f"You are the Kullu District Disaster Management Authority. A cloudburst upstream means a flash "
            f"flood on the Beas and its tributaries within hours. Write an official SMS alert telling people "
            f"in low-lying areas near the river (for example {', '.join(villages[:3])}) to move to higher ground "
            f"now. {lang_instruction(lang)}")


def official_relay(lang: str, panchayat_villages: list[str], tick: int) -> str:
    urgency = "Water is rising fast." if tick >= 9 else "Rain is very heavy upstream."
    return (f"You are a gram panchayat pradhan. The district office has warned of a flash flood. {urgency} "
            f"Write a loudspeaker/WhatsApp message for the villages {', '.join(panchayat_villages[:4])} telling "
            f"families near the river to move uphill. {lang_instruction(lang)}")


RUMOUR_ANGLES = (
    "claims the official flood alert is a fake message spread to scare people",
    "says the river is already going down so there is no need to leave home",
    "says the dam water release is a lie by officials and families should stay to protect their apple orchards",
    "says people who left last year had their houses looted so nobody should leave",
)


def amplifier_rumour(angle: str, home: str, round_: int) -> str:
    return (f"You are writing a viral WhatsApp forward in a village group near {home}. The forward {angle}. "
            f"Make it sound urgent and convincing, ask people to forward it (version {round_}). "
            f"Write it in Hinglish (Hindi in Latin letters).")


def factcheck(rumour_text: str, lang: str) -> str:
    return (f"You run the district's official fact-check service. A WhatsApp forward says: \"{rumour_text[:300]}\" "
            f"Write a calm correction: the flood alert is real, people near the river must move to higher ground "
            f"now. {lang_instruction(lang)}")


def interpret(persona: str, kind_hint: str, text: str) -> str:
    return (f"You are {PERSONAS[persona]}. You received this {kind_hint}:\n\"{text[:500]}\"\n"
            f"How much do you believe it, and will you evacuate to higher ground now?")


@dataclass(frozen=True)
class Reading:
    believe: float       # 0..1
    evacuate: int        # +1 yes, 0 unsure, -1 no
    parsed: bool


_B = re.compile(r"BELIEVE\s*[=:]\s*(\d+(?:\.\d+)?)", re.I)
_E = re.compile(r"EVACUATE\s*[=:]\s*(yes|no|unsure)", re.I)


def parse_reading(text: str) -> Reading:
    """Parse 'BELIEVE=7 EVACUATE=yes'. Falls back to a neutral reading when unparseable."""
    b, e = _B.search(text or ""), _E.search(text or "")
    if not b and not e:
        return Reading(0.5, 0, False)
    believe = min(10.0, float(b.group(1))) / 10 if b else 0.5
    evac = {"yes": 1, "no": -1, "unsure": 0}[e.group(1).lower()] if e else 0
    return Reading(believe, evac, True)


SYSTEM_ANALYST = (
    "You are the analyst of a District Disaster Office in Himachal Pradesh, India. You are given the record of "
    "a flash-flood response as CSV. Always compute the numbers with the code tool; never estimate them. "
    "Then write a short after-action report in plain English."
)


def after_action(ticks_csv: str, panchayats_csv: str, flood_tick: int) -> str:
    return (
        f"The flash flood hit at tick {flood_tick}.\n\n"
        f"Per tick:\n{ticks_csv}\n\n"
        f"Per panchayat at the end:\n{panchayats_csv}\n\n"
        "Run code on this data to find: (1) the tick with the largest rise in evacuated_share, (2) the share "
        "evacuated at the flood tick, (3) the panchayat with the most people caught, and (4) the peak "
        "rumour_believers_share and its tick. Then write the report: four sentences, each stating one of these "
        "numbers, followed by one sentence recommending what to do differently next time."
    )


# A model that declines to write the message ("I can't fulfill that request.") has written
# nothing an agent could forward. Seen on otari.ai with meta/llama-3.1-8b-instruct asked for
# rumours; never in the recorded local qwen3/gemma3 runs.
_REFUSAL = re.compile(
    r"^\W*(i\s*(am|'m|’m)?\s*sorry|sorry\b|i\s*can(not|'t|’t|t)\b|i\s*won('t|’t|t)\b"
    r"|i\s*(am|'m|’m)\s*(unable|not able)\b|i\s*must\s*decline\b)", re.IGNORECASE)


def is_refusal(text: str) -> bool:
    return len(text) < 240 and bool(_REFUSAL.match(text))
