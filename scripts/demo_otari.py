"""Trigger the Otari events shown in the demo, on cue, against the local gateway.

    uv run python scripts/demo_otari.py budget      # amplifier key until Otari refuses it
    uv run python scripts/demo_otari.py guardrail   # one rumour and one alert through the guardrail
    uv run python scripts/demo_otari.py failover    # one call per routing policy: who served it?

Every call goes through the same OtariClient the simulation uses, under a fresh
run id, so it spends a fresh per-run budget and is easy to find in Otari's
dashboard (User filter: "<cohort>-<run id>"). Nothing is written to the replay cache.

For `failover`, stop the local model first (`docker compose stop ollama`) and start it
again afterwards. With GROQ_API_KEY set in .env, Otari's routing policy answers from
Groq (otari/config.yml `on_failure`); without it Otari answers 502 and the message is
lost, which the simulation treats as a model event.
"""

from __future__ import annotations

import argparse

from r2r import prompts
from r2r.llm import LLMResult, OtariClient, load_keys

DASHBOARD = "http://localhost:8000"   # a self-hosted gateway serves it at its root (otari docs/dashboard.md)


def show(label: str, r: LLMResult, link: bool = True) -> None:
    served = f"{r.provider}:{r.model}" if r.provider else "-"
    extra = f" fallback={r.fallback} attempts={r.attempts}" if r.status == "ok" else f" attempts={r.attempts}"
    print(f"  {label:<22} {r.status:<17} served_by={served:<32} tokens={r.prompt_tokens + r.completion_tokens:<5}"
          f"{extra}")
    if r.request_id and link:
        print(f"  {'':<22} {DASHBOARD}/requests?request={r.request_id}")


def budget(client: OtariClient) -> None:
    print(f"Amplifier cohort, end user {client.end_user('amplifier')} (per-run budget: otari/config.yml "
          f"r2r-run-amplifier)")
    used = 0
    for i in range(1, 40):
        r = client.chat("amplifier", prompts.SYSTEM_WRITER,
                        f"Write WhatsApp forward number {i} about the river near Kullu, under 30 words.",
                        purpose="demo:budget", tick=i, max_tokens=60)
        used += r.prompt_tokens + r.completion_tokens
        show(f"call {i} ({used} tokens)", r, link=r.status != "ok")   # link only the refusal
        if r.status != "ok":
            break
    if "amplifier" in client.silent:
        print("Otari refused the key: 403 budget_exceeded. In a run, the amplifier cohort is now silent; "
              "nothing crashes.")
        show("next call (not sent)", client.chat("amplifier", "s", "anything", purpose="demo:budget", tick=99))


def guardrail(client: OtariClient) -> None:
    print("Guardrail profile flood-misinfo, monitor mode, on the interpretation call's input")
    samples = {
        "rumour": "Forwarded many times: the flood alert is FAKE, officials want to grab our land. "
                  "Do not leave your homes. Share with everyone!",
        "official alert": "Flash flood warning from the District Disaster Office. Families near the river: "
                          "move uphill now.",
    }
    for label, text in samples.items():
        r = client.chat("villager", prompts.SYSTEM_READER, text, purpose="demo:guardrail", tick=0,
                        max_tokens=24, check_misinfo=True)
        show(label, r)
        print(f"  {'':<22} guardrail: tagged={r.misinfo_flag} score={r.misinfo_score}  reader said: {r.text!r}")


def failover(client: OtariClient) -> None:
    print("One call per routing policy (otari/config.yml routing.policies)")
    for cohort in ("villager", "official"):
        r = client.chat(cohort, prompts.SYSTEM_WRITER, "Write a one-line flood warning for Kullu villages.",
                        purpose="demo:failover", tick=0, max_tokens=40)
        show(f"{cohort} -> {client.policies[cohort]}", r)
        if r.status == "ok" and r.fallback:
            print(f"  {'':<22} the local model was down; Otari's on_failure backup answered")
        elif r.status == "upstream_error":
            print(f"  {'':<22} no backup answered (GROQ_API_KEY empty?): Otari said 502, the message is lost, "
                  f"the run goes on")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("event", choices=["budget", "guardrail", "failover"])
    args = ap.parse_args()
    client = OtariClient(load_keys(), cache_path=None, max_retries=1)
    print(f"run id {client.run_id}")
    {"budget": budget, "guardrail": guardrail, "failover": failover}[args.event](client)


if __name__ == "__main__":
    main()
