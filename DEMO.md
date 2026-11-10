# Demo script (about 4 minutes 30 seconds)

What to show, in order, with what to say. The hackathon asks for a 3–5 minute video,
recorded by you: a screen recording with your own voice, no AI narration.

Otari page names below follow the Otari docs at commit `e74a036`
(`docs/dashboard.md`, `docs/routing.md`, `docs/budgets.md`). Those docs say the running
dashboard is the source of truth for exact labels, so do one rehearsal pass and adjust
the wording if a label differs.

## Before recording

1. Start the local stack and check it:

   ```bash
   docker compose up -d
   ```

   ```bash
   uv run python spike/prove.py
   ```

2. Warm both local models so the first on-camera call is fast:

   ```bash
   uv run python scripts/demo_otari.py failover
   ```

3. Check the hosted step works (it spends about one cent of otari.ai credit):

   ```bash
   uv run python scripts/demo_otari.py hosted
   ```

4. Sign in to both dashboards off camera: otari.ai, and the self-hosted one at
   `http://localhost:8000/` with the master key from your `.env` (per Otari
   `docs/run-the-dashboard.md`, "First-run walkthrough"). Never show `.env` or any key
   on screen.
5. Open four browser tabs: the otari.ai dashboard, the self-hosted Otari dashboard,
   the Solara dashboard (`uv run solara run dashboard/app.py`), and the public page
   <https://tejasv-singh.github.io/Otari_chat/>.
6. Keep one terminal visible, in the repository root, with a large font.

## The script

### 1. The question (0:00–0:30)

**Show:** the public page, top of the page, then drag the tick slider from 0 to 14.

**Say:** "A flash flood in Kullu. Eighty villagers, ten panchayat officials, four
rumour amplifiers and the district disaster office all talk through open-weight
models. We compare three interventions: an SMS alert, a fact-check bot and
local-language messaging. Our question is whether the answer changes when you swap
the model that plays the people. Every model call goes through Otari."

### 2. otari.ai: models in roles, and code execution (0:30–1:30)

**Run:**

```bash
uv run python scripts/demo_otari.py hosted
```

**Show:** the three lines: a villager served by Llama 3.1 8B, an official served by
Llama 3.3 70B, and the analyst on gpt-oss-120b with the code tool. Open the last
printed `https://otari.ai/requests?request=...` link: the request in otari.ai's
**Requests** tab. Then the Overview's **Usage** tab for today.

**Say:** "This is the hosted gateway, otari.ai. The same client code sends a small
open-weight model for villagers and a larger one for officials; Otari picks the
provider, here Bedrock. The third call is the district office's analyst: it gets the
run's numbers and Otari's code-execution tool. Otari runs the model's Python in its
sandbox and the model answers from the result."

**Show:** the public page, pick "gptoss · otari.ai", scroll to **After-action
report**.

**Say:** "Every hosted run ends this way. This report's numbers were computed by code
that Otari executed, not guessed by the model. Otari's usage record shows one
code-execution call per run."

### 3. Self-hosted Otari: keys, budgets, routing (1:30–2:00)

**Show:** the self-hosted dashboard, **API keys** page: four keys, one per cohort, with
the Budget column. Then the **Routing** page: open the `villager` policy.

**Say:** "The local model families run on a self-hosted Otari gateway. Each agent
cohort has its own virtual key and its own budget, and agents call a routing policy,
never a model. Swapping Qwen for Gemma is a routing change in Otari's config, with no
change to our code."

*(The policies come from `otari/config.yml`, so the page shows them read-only with
their full spec.)*

### 4. A budget runs out, and it is a model event (2:00–2:40)

**Run:**

```bash
uv run python scripts/demo_otari.py budget
```

**Show:** the calls counting up, then `budget_exhausted` on about the 14th call. Open
the printed `http://localhost:8000/requests?request=...` link: the refused request in
Otari's **Requests** tab.

**Say:** "The rumour amplifiers have a deliberately small per-run token budget, and
every run is its own end user, so this is a per-user budget. Otari refuses the key
with a 403. Our client does not crash and does not retry: the cohort goes silent for
the rest of the run. In the simulation the rumour mill runs dry. It happened in all
ten self-hosted runs."

**Show:** Solara dashboard, family `qwen3`, step a run past tick 9: the header reads
"silent cohorts: amplifier".

### 5. Guardrails tag misinformation (2:40–3:10)

**Run:**

```bash
uv run python scripts/demo_otari.py guardrail
```

**Show:** the rumour line with `tagged=True score=1.0`, the official alert with
`tagged=False`. Then the "Guardrail tag rate" chart on the public page for `qwen3`.

**Say:** "Otari checks each message against our own flood-misinformation guardrail, a
small service Otari calls for us, covering English, Hinglish and Hindi. It runs in
monitor mode: nothing is blocked, the verdict comes back in a response header, and the
tag rate becomes a variable of the model."

### 6. The local model dies (3:10–3:40)

**Run:**

```bash
docker compose stop ollama
```

```bash
uv run python scripts/demo_otari.py failover
```

**With a Groq key in `.env`, show:** `served_by=groq:...  fallback=True`, then the
request in Otari's Requests tab, which lists the models that were attempted.

**Say (with a key):** "We just killed the local model server. The same call still
answers, from Groq's free tier. The failover lives in Otari's routing policy; our code
did nothing."

**Without a Groq key, show:** `upstream_error` for both policies.

**Say (no key):** "We just killed the local model server. With no backup key
configured, Otari answers with a clean 502. Our client records a lost message for that
tick and the run carries on. Adding one free key to Otari's config turns this into a
failover, with no code change."

**Run (off camera or while talking):**

```bash
docker compose start ollama
```

### 7. The answer (3:40–4:30)

**Show:** the public page, "Does the model change the conclusion?" section: the two
bar charts and the ranking.

**Say:** "Four open-weight model families, two local and two on otari.ai. They do not
agree. Three rank 'all three interventions' first; Llama ranks the SMS alert alone
first. The fact-check bot adds twenty-two points of evacuation with Qwen and costs
nineteen with gpt-oss. And one finding we did not expect: asked to write rumours, the
small Llama model refused two times out of three, while gpt-oss wrote every one. So a
misinformation study run on a refusing model quietly studies less misinformation. The
model is part of the result. This is one seed per cell, and the hosted and local runs
differ in budgets, so treat the sizes as leads, not findings. Every call here went
through Otari, and all twenty runs replay exactly from the repository."

## If something goes wrong on camera

- **First local call is slow (20–40 s):** a model is loading. Run step 2 of the
  preparation again and retake.
- **`hosted` fails with "OTARI_API_KEY is not set":** add your otari.ai key to `.env`
  (HOSTED.md).
- **`budget` never refuses:** the per-run budget was changed in `otari/config.yml`
  (`r2r-run-amplifier`). The script stops after 39 calls.
- **Dashboards show no data:** they replay the recorded runs in `data/cache/` and
  `results/sweeps/`, which ship with the repository. Check that both folders are
  intact (`git status`), or use the public page, which needs neither.
