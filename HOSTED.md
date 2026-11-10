# Running on otari.ai (hosted Otari)

The same simulation runs through two gateways:

| | Self-hosted Otari (`docker compose`) | otari.ai |
|---|---|---|
| Model families | `qwen3`, `gemma3` on local Ollama | `llama-hosted`, `gptoss-hosted` from the otari.ai catalog |
| Villagers and amplifiers | qwen3:1.7b / gemma3:1b | Llama 3.1 8B / gpt-oss-20b |
| Officials and district office | qwen3:4b-instruct / gemma3:4b | Llama 3.3 70B / gpt-oss-120b |
| After-action analyst | not used | gpt-oss-120b with Otari's **code-execution** tool |
| Keys | one per cohort, declared in `otari/config.yml` | your otari.ai key (one for all cohorts, or one per cohort) |
| Per-run budgets | yes: each run is a fresh end user on a service key | only with service keys you create in the dashboard (below) |
| Misinformation guardrail | our `/validate` service | not configured on the workspace: tag rate stays 0 |
| Cost | none | otari.ai credit: about $0.02–0.03 per run |

All models are open-weight. The client code is the same for both: only the base URL,
the key and the model names differ (`src/r2r/llm.py` `make_client`,
`src/r2r/experiments.py` `MODEL_SETS`).

## Set up

1. Create a key on otari.ai (**API keys**, **Create key**; per Otari
   `docs/quickstart.md`) and put it in `.env`. `.env` is gitignored; never commit it.

   ```
   OTARI_API_KEY=otk_...
   ```

   An account in the European Union also sets
   `OTARI_BASE_URL=https://eu.api.otari.ai/api/v1`.

2. One run through otari.ai (about 3 minutes):

   ```bash
   uv run python -m r2r.run --seed 42 --model-set llama-hosted
   ```

3. The hosted sweep, both hosted families × five scenarios (about 30 minutes):

   ```bash
   uv run python -m r2r.experiments --name hosted --models llama-hosted gptoss-hosted --seeds 42
   ```

4. Replay every recorded family, local and hosted, then analyse and export:

   ```bash
   uv run python -m r2r.experiments --name main --replay-from recorded hosted
   ```

   ```bash
   uv run python -m r2r.analyze --name main
   ```

   ```bash
   uv run python dashboard/export.py
   ```

## What Otari does in hosted runs

- **Open-weight models in distinct roles.** A small model plays villagers and
  amplifiers, a larger one plays officials and the district office, and a third role,
  the analyst, writes the after-action report.
- **Routing.** Agents send catalog ids such as `meta/llama-3.1-8b-instruct`. Otari
  picks the provider for each request (the recorded runs were served by Bedrock and
  DeepInfra) and reports it in the `Otari-Provider` header.
- **Code execution.** When the run ends, the district office sends the run's
  tick-by-tick record to the analyst model with the `otari_code_execution` tool. Otari
  runs the model's Python in its sandbox and the model writes the report from the
  result. Otari's usage API confirms it per run (`by_tool: code_execution`), and the
  count is saved as `otari_tool_calls_ddo`.
- **Usage per cohort.** Every request carries `run`, `cohort` and `purpose` tags, so
  Otari's usage API reports each cohort separately even when all cohorts share one key.

## Model behaviour seen only on otari.ai

- **Refusals.** Llama 3.1 8B declines many rumour requests ("I can't fulfill that
  request."). A refusal is not forwarded: the amplifier has nothing to send, and the
  count is a model variable (`refusals`). The local qwen3 and gemma3 models never
  refused.
- **Reasoning tokens.** gpt-oss reasons before it answers and the reasoning counts
  against `max_tokens`; a 24-token reading came back empty. The client gives those
  models extra output tokens (`REASONING_HEADROOM` in `src/r2r/llm.py`).
- **End users.** An ordinary key may not name an end user: otari.ai answers
  `403 'user' field does not match the authenticated API key's user`. The client then
  stops naming one. A service key (below) may.

## Optional: per-run budgets and routing policies on otari.ai

An API key can only run inference and read its own budget and usage. Budgets, service
keys and routing policies are created in the otari.ai dashboard by an organization
owner or admin (Otari `docs/api-reference.md`, "Authentication"). With them, hosted runs
get the same budget events as the self-hosted ones.

**Per-run budgets** (Otari `docs/end-users.md`):

1. **Organization → Spend & budgets → Add budget**, one per cohort, with a token limit
   under "Token and request limits": villager 60000, official 20000, amplifier 1500,
   district office 15000.
2. **API keys → Create key → Advanced → Service key**, one per cohort, each with its
   cohort's budget as the **Default end-user budget**.
3. Put the four keys in `.env`:

   ```
   R2R_HOSTED_KEY_VILLAGER=otk_...
   R2R_HOSTED_KEY_OFFICIAL=otk_...
   R2R_HOSTED_KEY_AMPLIFIER=otk_...
   R2R_HOSTED_KEY_DDO=otk_...
   ```

Each run then names a fresh end user per cohort, so the amplifier's small budget runs
out mid-run and that cohort goes silent, exactly as on the self-hosted gateway.

**Routing policies with fallbacks** (Otari `docs/routing.md`, "In the dashboard"):

1. **Routing → Create policy → Fallback**. Name it `r2r-villager`, main model
   Llama 3.1 8B, and under "If unavailable, use" add gpt-oss-20b.
2. The same for `r2r-official`: main model Llama 3.3 70B, backup gpt-oss-120b.
3. Run the family that calls those policy names:

   ```bash
   uv run python -m r2r.run --seed 42 --model-set routed-hosted
   ```

## Comparing hosted and local families

They are not like-for-like unless the service keys above exist. With one ordinary key
there is no per-run budget, so hosted amplifiers keep writing rumours for the whole
run, while local amplifiers go silent around tick 5–7. Hosted runs also have no
guardrail tags. The analysis report states this next to the table.
