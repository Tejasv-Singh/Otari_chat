# Phase 0 spike: what self-hosted Otari does for free

Run on 2026-11-09/10. Windows 11 laptop, 16 cores, 16 GB RAM, CPU only.
Otari image `mzdotai/otari@sha256:893a197d…` (version `1b39b91`), docs read at repo
commit `e74a036`. Ollama 0.40.15 in a container. Reproduce:

```bash
python scripts/init_env.py      # writes .env with fresh secrets (never printed)
docker compose up -d --build
uv run python spike/prove.py    # prints PASS/FAIL/SKIP, writes spike/out/evidence.json
```

## Results

| Check | Result | Evidence |
|---|---|---|
| Gateway health | PASS | `GET /api/v1/health` → `healthy` |
| Keys and budgets declared in `config.yml` | PASS | Start-up log: "Applied 4 declared budget(s) and 4 declared API key(s) from config" |
| Routing `villager` → `ollama:qwen3:1.7b` | PASS | `Otari-Provider: ollama`, `Otari-Model: qwen3:1.7b`; about 1 s warm, 19 tokens |
| Routing `official` → `ollama:qwen3:4b-instruct` | PASS | `Otari-Model: qwen3:4b-instruct`; about 3 s warm, about 15 s cold |
| Custom guardrail profile listed | PASS | `GET /api/v1/tool-settings/guardrails/profiles` lists `flood-misinfo` |
| Guardrail tags a rumour (`monitor`) | PASS | `Otari-Guardrails` header: rumour `valid=false score=1.0`, benign `valid=true score=0` |
| Guardrail refuses a rumour (`block`) | PASS | `403 guardrail_violation` |
| Two keys, different budgets | PASS | `GET /api/v1/key/budget`: amplifier `limit_tokens=800`, villager `200000` |
| Tiny budget runs out | PASS | Run 1: 5 calls served, 6th → `403`, `Otari-Error-Code: budget_exceeded`, body `"API key has exceeded token limit"` |
| Usage read-back per key | PASS | `GET /api/v1/key/usage/summary`: tokens, `cost`, `avg_latency_ms`, `request_count`, `error_count` |
| Cost per request inline | PASS | `usage.cost_usd` on each response (notional local prices) |
| Dashboard | PASS | `GET /` → 200 on `http://localhost:8000/`; sign in with the master key |
| Failover Ollama → Groq | **SKIP** | No `GROQ_API_KEY` yet. With Ollama stopped, Otari answered `502 "All upstream providers failed"`: a clean refusal, no crash |

## Findings that change the design

1. **Guardrails check input only.** `on: ["output"]` is "accepted but not enforced yet"
   (otari docs/guardrails-reference.md). So a message is tagged when an agent
   *interprets* it: the forwarded text is the input of the interpretation call.
   Every message that travels is interpreted at least once, so the tag rate still
   covers all generated messages. It just costs no extra call.
2. **No misinformation guardrail ships with Otari.** The bundled profile detects prompt
   injection. Otari accepts any service implementing `POST /validate`, so we run our own
   (`otari/guardrail/app.py`). It uses rule-based English and Hinglish flood-rumour
   patterns and is deterministic, so the tag rate measures the *models*, not the
   detector.
3. **Free models spend no dollars**, so dollar budgets never trigger. Budgets use
   `token_limit`, and local models carry a small *notional* price so a cost-per-cohort
   chart is still meaningful. These are not real charges, and the README must say so.
4. **Qwen3 thinking must be switched off.** Without `reasoning_effort: "none"`, qwen3
   spends all of `max_tokens` on hidden reasoning and returns an empty reply. Putting
   `/no_think` in the prompt did *not* work. Otari forwards `reasoning_effort`.
5. **`qwen3:4b` ignores `reasoning_effort` and leaks its reasoning into the content.**
   Switched officials to `qwen3:4b-instruct`.
6. **Model swapping on CPU is slow.** With one loaded model, every villager/official
   switch reloaded a model (about 12 s). Compose now sets `OLLAMA_MAX_LOADED_MODELS=2`
   (about 4 GB of RAM for both).
7. **Budget refusals are `403`** with `Otari-Error-Code: budget_exceeded`. Upstream
   outages are `502`. The client wrapper maps the first to "cohort goes silent" and the
   second to "message lost this tick", and neither crashes the run.

## What works self-hosted for free

| Feature | Free self-hosted? | Notes |
|---|---|---|
| OpenAI-compatible gateway, virtual keys, budgets, routing, failover | Yes | Verified above (failover pending a Groq key) |
| Usage and cost per key, dashboard | Yes | Verified |
| Inference guardrails | Yes, input only | Custom service verified. The bundled prompt-injection profile needs the `encoderfile` image (x86 tag must be set) and was not needed |
| Local models (Ollama) | Yes | Keyless provider entry plus `api_base` |
| Groq / Cerebras fallback | Yes, free tier, no card | Free-tier rate limits apply. Not yet verified here |
| MCP | Not tested | Documented in otari `docs/mcp.md`. Not needed for this project |
| Code execution | Not tested | Runs as a local sandbox container (`code-exec` compose profile), so it should be free. Not needed |
| Web search | Not tested | SearXNG container profile is free. Not needed |

## Throughput estimate for the MVP

About 1 s per villager call and about 3 s per official call, warm, at 20–50 output
tokens. With rule-based dynamics, prompt caching and roughly 200–300 model calls per
run, one run should take **5–10 minutes**, inside the 15-minute target. That estimate
still needs confirming in Phase 2.

## Redesign needed?

No. Every core Otari feature works self-hosted at no cost. The adjustments above
(tagging on interpretation, token budgets, the instruct model, reasoning off) are
small and are already in the config.
