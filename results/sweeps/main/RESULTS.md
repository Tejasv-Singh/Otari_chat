# Sweep results

10 runs: 2 model families x 5 scenarios x 1 seed(s).

> One seed per cell: differences are single realisations, not estimates with error bars.

## Outcomes by model family and intervention

|                              |   evacuated_at_risk_share |   caught |   unneeded_evacuations |   rumour_believers_share |   misinfo_tag_rate |
|:-----------------------------|--------------------------:|---------:|-----------------------:|-------------------------:|-------------------:|
| ('gemma3', 'all')            |                     0.812 |        6 |                      3 |                    0.3   |              0     |
| ('gemma3', 'baseline')       |                     0.531 |       15 |                      0 |                    0.325 |              0.034 |
| ('gemma3', 'fact_check')     |                     0.656 |       11 |                      0 |                    0.238 |              0.015 |
| ('gemma3', 'local_language') |                     0.5   |       16 |                      1 |                    0.262 |              0.047 |
| ('gemma3', 'sms_alert')      |                     0.594 |       13 |                      0 |                    0.25  |              0.046 |
| ('qwen3', 'all')             |                     0.75  |        8 |                      0 |                    0.012 |              0.059 |
| ('qwen3', 'baseline')        |                     0.531 |       15 |                      0 |                    0     |              0.015 |
| ('qwen3', 'fact_check')      |                     0.75  |        8 |                      0 |                    0     |              0.03  |
| ('qwen3', 'local_language')  |                     0.656 |       11 |                      0 |                    0.038 |              0.016 |
| ('qwen3', 'sms_alert')       |                     0.719 |        9 |                      0 |                    0     |              0.015 |

## Does the intervention ranking depend on the model?

- **gemma3**: all > fact_check > sms_alert > baseline > local_language
- **qwen3**: all > fact_check > sms_alert > local_language > baseline

- gemma3 vs qwen3: Kendall tau = 0.8; best intervention agrees (all vs all)

## Effect of each intervention vs baseline (evacuation share, points)

| scenario_name   |   gemma3 |   qwen3 |
|:----------------|---------:|--------:|
| all             |     28.1 |    21.9 |
| fact_check      |     12.5 |    21.9 |
| local_language  |     -3.1 |    12.5 |
| sms_alert       |      6.3 |    18.8 |

## Otari usage per run (mean)

|                            |       gemma3 |        qwen3 |
|:---------------------------|-------------:|-------------:|
| tokens_villager            | 14418.8      | 15152.6      |
| tokens_official            |  3520.4      |  3603.6      |
| tokens_amplifier           |  1359.4      |  1376.8      |
| tokens_ddo                 |   113        |   296.8      |
| cost_villager              |     0.000305 |     0.00034  |
| cost_official              |     0.000243 |     0.000247 |
| cost_amplifier             |     4.1e-05  |     3.7e-05  |
| cost_ddo                   |     7e-06    |     2e-05    |
| otari_latency_ms_villager  |  1740.72     |  1234.01     |
| otari_latency_ms_official  |  6496.28     |  4871.83     |
| otari_latency_ms_amplifier |  3058.6      |  2670.11     |
| otari_latency_ms_ddo       |  5479        |  5437.92     |
| llm_calls                  |   100        |   105.4      |
| cache_hits                 |   117.2      |   119.2      |
| budget_events              |     1        |     1        |
| fallbacks                  |     0        |     0        |
| live_wall_s                |   528.04     |   224.88     |

Cohorts that ran out of their per-run Otari budget: gemma3: amplifier; qwen3: amplifier

Costs are notional (local models priced per size class in otari/config.yml), not real charges.
