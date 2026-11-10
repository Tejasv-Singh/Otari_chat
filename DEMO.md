# Demo script (3 minutes)

What to show, in order, with what to say. Timings add up to 3:00. Everything runs
locally; nothing in the demo costs money.

Otari page names below follow the Otari docs at commit `e74a036`
(`docs/dashboard.md`, `docs/routing.md`, `docs/budgets.md`). Those docs say the running
dashboard is the source of truth for exact labels, so do one rehearsal pass and adjust
the wording if a label differs.

## Before recording

1. Start the stack and check it:

   ```bash
   docker compose up -d
   ```

   ```bash
   uv run python spike/prove.py
   ```

2. Warm both models so the first on-camera call is fast:

   ```bash
   uv run python scripts/demo_otari.py failover
   ```

3. Sign in to Otari's dashboard at `http://localhost:8000/` with the master key from
   your `.env` (per Otari `docs/run-the-dashboard.md`, "First-run walkthrough"). Do
   this off camera, and never show `.env` or the key on screen.
4. Open three browser tabs: the Otari dashboard, the Solara dashboard
   (`uv run solara run dashboard/app.py`), and the static page `docs/index.html`.
5. Keep one terminal visible, in the repository root, with a large font.
6. Optional, for a live failover: put a free Groq key in `.env` as `GROQ_API_KEY` and
   run `docker compose up -d` again. Without it, use the "no key" lines in step 5.

## The script

### 1. The question (0:00–0:25)

**Show:** the static page, top of the page, then drag the tick slider from 0 to 14.

**Say:** "A flash flood in Kullu. Eighty villagers, ten panchayat officials, four
rumour amplifiers and the district disaster office all talk through small open-weight
models. We compare three interventions: an SMS alert, a fact-check bot and
local-language messaging. Our question is whether the answer changes when you swap
the model that plays the people."

### 2. Otari is the control plane (0:25–0:55)

**Show:** Otari dashboard, **API keys** page: four keys, one per cohort, with the
Budget column. Then the **Routing** page: open the `villager` policy.

**Say:** "Every model call goes through a self-hosted Otari gateway. Each agent
cohort has its own virtual key and its own budget. Agents never name a model: they
call a routing policy. The `villager` policy is a small local model with a cloud
backup; `official` is a larger one. Swapping the model family from Qwen to Gemma is a
routing change in Otari's config, with no change to our code."

*(The policies come from `otari/config.yml`, so the page shows them read-only with
their full spec.)*

### 3. A budget runs out, and it is a model event (0:55–1:35)

**Run:**

```bash
uv run python scripts/demo_otari.py budget
```

**Show:** the calls counting up, then `budget_exhausted` on about the 14th call. Open
the printed `http://localhost:8000/requests?request=...` link: the refused request in
Otari's **Requests** tab.

**Say:** "The rumour amplifiers have a deliberately small per-run token budget. Here
Otari refuses the key with a 403. Our client does not crash and does not retry: the
cohort goes silent for the rest of the run. In the simulation that means the rumour
mill runs dry. It happened in all ten recorded runs."

**Show:** Solara dashboard, step a run past tick 9: the header reads
"silent cohorts: amplifier".

### 4. Guardrails tag misinformation (1:35–2:05)

**Run:**

```bash
uv run python scripts/demo_otari.py guardrail
```

**Show:** the rumour line with `tagged=True score=1.0`, the official alert with
`tagged=False`. Then the "Guardrail tag rate" chart on the static page.

**Say:** "Otari checks each message against our own flood-misinformation guardrail, a
small service Otari calls for us, covering English, Hinglish and Hindi. It runs in
monitor mode: nothing is blocked, the verdict comes back in a response header, and the
tag rate becomes a variable of the model."

### 5. The local model dies (2:05–2:35)

**Run:**

```bash
docker compose stop ollama
```

```bash
uv run python scripts/demo_otari.py failover
```

**With a Groq key, show:** `served_by=groq:...  fallback=True`, then the request in
Otari's Requests tab, which lists the models that were attempted.

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

### 6. The answer (2:35–3:00)

**Show:** the static page, "Does the model change the conclusion?" section: the two
bar charts and the ranking.

**Say:** "Both model families pick the same best intervention, and the rankings mostly
agree. The sizes do not: the SMS alert adds nineteen points with Qwen and six with
Gemma. And with Gemma a quarter to a third of villagers believe a rumour, against
almost none with Qwen, because the small Qwen model writes Hindi rumours nobody finds
credible. So the model is part of the result. This is one seed per cell and single
runs move by about six points, so treat the effect sizes as leads, not findings.
Everything here ran on a laptop CPU, for free, through self-hosted Otari."

## If something goes wrong on camera

- **First call is slow (20–40 s):** a model is loading. Run step 2 of the preparation
  again and retake.
- **`budget` never refuses:** the per-run budget was changed in `otari/config.yml`
  (`r2r-run-amplifier`). The script stops after 39 calls.
- **Dashboards show no data:** they replay the recorded runs in `data/cache/` and
  `results/sweeps/`, which ship with the repository. Check that both folders are
  intact (`git status`), or use `docs/index.html`, which needs neither.
