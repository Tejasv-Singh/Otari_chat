# Calibration

The movement and belief rules are hand-written, so their constants need values.
They are **not fitted to observed evacuation data**: there is no public per-village
evacuation record for a Kullu flash flood. Instead the constants were chosen so that
the *official warning channel* behaves plausibly, against targets written down
before the search.

| Target | Meaning |
|---|---|
| T1 | Baseline (no interventions): 35–65% of at-risk villagers evacuate before the flood |
| T2 | An official SMS alert raises that share by at least 15 points |
| T3 | Baseline: at most 25% of villagers outside the flood zone evacuate anyway |

**What is deliberately not calibrated:** rumour spread, fact-checking, the
local-language effect and anything about model choice. Tuning those until they
"matter" would be tuning towards the research answer.

## Replay mode

Calibration and the main sweep run the model layer in **replay mode**: they make
no model calls. Every live call through Otari is recorded in
`data/cache/llm/prompts.jsonl`, tagged with its run id. That covers answers,
budget refusals and upstream outages. Replaying a run serves that run's records
prompt by prompt, in the order they happened. Rule-based dynamics can therefore
be re-run offline over the exact answers the models gave live.

Checked with `scripts/check_replay.py`:

* `--recorded recorded`: all 10 live runs of the recorded sweep (2 model families
  × 5 scenarios) replay with a call log *identical* to the live one, status and text
  of every call. This includes the gemma3 baseline's two upstream outages and every
  amplifier budget refusal.
* `--live`: a fresh live run with the calibrated defaults (B) against replay of
  its own calls (C): identical outcomes, 0 misses.

The first version of replay was not exact: a 502 on the first try of a prompt
followed by a later success was replayed as a success. Outages are now recorded too.

**Sampling noise.** The models sample, so two live runs of the same scenario
differ. Baseline, qwen3, seed 42, calibrated parameters:

| | evacuated at risk | caught | LLM calls |
|---|---|---|---|
| replay of the recorded live run | 0.53 | 15 | 103 |
| a second, fresh live run | 0.59 | 13 | 99 |

A difference of about 6 points between single runs can come from sampling alone.
Single-seed differences of that size are not evidence of an effect.

## Method

```bash
uv run python -m r2r.calibrate      # writes results/calibration.json
```

* A grid of 72 configurations over `relay_reach` {0.3, 0.45, 0.6}, `rising_rate`
  {0.02, 0.04, 0.06}, `far_relevance` {0.3, 0.6}, `flood_tick` {12, 14} and
  `threshold_mean` {0.6, 0.7}. `doubt_gain` is fixed at 0.2, an assumption that
  is not searched.
* Each configuration replays the qwen3 baseline and sms_alert live runs of
  `results/sweeps/recorded` (seed 42). That makes 144 replays in about 2 minutes,
  with 0 replay misses.
* The score is the total distance outside the targets (0 means all met). Ties go
  to the baseline evacuation share closest to 50%.

## Result

4 of 72 configurations meet every target. Chosen and now the defaults in
`r2r.model.Scenario`:

| Parameter | Before | Calibrated |
|---|---|---|
| relay_reach | 0.6 | 0.6 |
| rising_rate | 0.08 | **0.04** |
| far_relevance | 1.0 | **0.3** |
| flood_tick | 15 | **14** |
| threshold_mean | 0.6 | 0.6 |
| doubt_gain | 0.0 | 0.2 (assumed) |

qwen3 replay with these values: baseline evacuation 0.53, SMS alert 0.72
(+19 points), unneeded evacuations 0%.

Before calibration, almost every at-risk villager evacuated in every scenario
(97–100%). That ceiling hid every intervention effect.

## Limits

* Calibrated on one model family (qwen3) and one seed. gemma3 runs use the same
  constants unchanged, which is the point: only the model differs.
* Only 4 of 72 configurations meet the targets. Near them the outcome is
  sensitive: an earlier pass, which mixed answers from different runs, picked a
  different configuration.
* The targets are plausibility judgements, not data. Rankings of interventions are
  conditional on them.
