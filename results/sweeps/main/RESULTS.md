# Sweep results

20 runs: 4 model families x 5 scenarios x 1 seed(s).

> One seed per cell: differences are single realisations, not estimates with error bars.

Gateway per family: gemma3 on self-hosted Otari, gptoss-hosted on otari.ai, llama-hosted on otari.ai, qwen3 on self-hosted Otari.

> Hosted and local families are not a like-for-like comparison: on otari.ai the cohorts share one key with no per-run budget, so the amplifiers never run out, and no misinformation guardrail is configured there (tag rate 0).

## Outcomes by model family and intervention

|                                     |   evacuated_at_risk_share |   caught |   unneeded_evacuations |   rumour_believers_share |   rumours |   refusals |   misinfo_tag_rate |
|:------------------------------------|--------------------------:|---------:|-----------------------:|-------------------------:|----------:|-----------:|-------------------:|
| ('gemma3', 'all')                   |                     0.812 |        6 |                      3 |                    0.3   |         7 |          0 |              0     |
| ('gemma3', 'baseline')              |                     0.531 |       15 |                      0 |                    0.325 |         6 |          0 |              0.034 |
| ('gemma3', 'fact_check')            |                     0.656 |       11 |                      0 |                    0.238 |         7 |          0 |              0.015 |
| ('gemma3', 'local_language')        |                     0.5   |       16 |                      1 |                    0.262 |         7 |          0 |              0.047 |
| ('gemma3', 'sms_alert')             |                     0.594 |       13 |                      0 |                    0.25  |         7 |          0 |              0.046 |
| ('gptoss-hosted', 'all')            |                     0.656 |       11 |                      8 |                    0.012 |        36 |          0 |              0     |
| ('gptoss-hosted', 'baseline')       |                     0.531 |       15 |                      0 |                    0.1   |        36 |          0 |              0     |
| ('gptoss-hosted', 'fact_check')     |                     0.344 |       21 |                      0 |                    0.025 |        36 |          0 |              0     |
| ('gptoss-hosted', 'local_language') |                     0.562 |       14 |                      2 |                    0.112 |        36 |          0 |              0     |
| ('gptoss-hosted', 'sms_alert')      |                     0.469 |       17 |                      0 |                    0.062 |        36 |          0 |              0     |
| ('llama-hosted', 'all')             |                     0.469 |       17 |                      9 |                    0.2   |        12 |         24 |              0     |
| ('llama-hosted', 'baseline')        |                     0.375 |       20 |                      0 |                    0.15  |        14 |         22 |              0     |
| ('llama-hosted', 'fact_check')      |                     0.344 |       21 |                      0 |                    0.05  |        10 |         26 |              0     |
| ('llama-hosted', 'local_language')  |                     0.469 |       17 |                      0 |                    0.05  |        13 |         23 |              0     |
| ('llama-hosted', 'sms_alert')       |                     0.594 |       13 |                      0 |                    0.075 |        11 |         25 |              0     |
| ('qwen3', 'all')                    |                     0.75  |        8 |                      0 |                    0.012 |         7 |          0 |              0.059 |
| ('qwen3', 'baseline')               |                     0.531 |       15 |                      0 |                    0     |         8 |          0 |              0.015 |
| ('qwen3', 'fact_check')             |                     0.75  |        8 |                      0 |                    0     |         7 |          0 |              0.03  |
| ('qwen3', 'local_language')         |                     0.656 |       11 |                      0 |                    0.038 |         7 |          0 |              0.016 |
| ('qwen3', 'sms_alert')              |                     0.719 |        9 |                      0 |                    0     |         7 |          0 |              0.015 |

## Does the intervention ranking depend on the model?

- **gemma3**: all > fact_check > sms_alert > baseline > local_language
- **gptoss-hosted**: all > local_language > baseline > sms_alert > fact_check
- **llama-hosted**: sms_alert > all > local_language > baseline > fact_check
- **qwen3**: all > fact_check > sms_alert > local_language > baseline

- gemma3 vs gptoss-hosted: Kendall tau = -0.2; best intervention agrees (all vs all)
- gemma3 vs llama-hosted: Kendall tau = 0.0; best intervention DIFFERS (all vs sms_alert)
- gemma3 vs qwen3: Kendall tau = 0.8; best intervention agrees (all vs all)
- gptoss-hosted vs llama-hosted: Kendall tau = 0.4; best intervention DIFFERS (all vs sms_alert)
- gptoss-hosted vs qwen3: Kendall tau = 0.0; best intervention agrees (all vs all)
- llama-hosted vs qwen3: Kendall tau = 0.2; best intervention DIFFERS (sms_alert vs all)

## Effect of each intervention vs baseline (evacuation share, points)

| scenario_name   |   gemma3 |   gptoss-hosted |   llama-hosted |   qwen3 |
|:----------------|---------:|----------------:|---------------:|--------:|
| all             |     28.1 |            12.5 |            9.4 |    21.9 |
| fact_check      |     12.5 |           -18.7 |           -3.1 |    21.9 |
| local_language  |     -3.1 |             3.1 |            9.4 |    12.5 |
| sms_alert       |      6.3 |            -6.2 |           21.9 |    18.8 |

## Otari usage per run (mean)

|                            |       gemma3 |   gptoss-hosted |   llama-hosted |        qwen3 |
|:---------------------------|-------------:|----------------:|---------------:|-------------:|
| tokens_villager            | 14418.8      |    46588.8      |   15783.4      | 15152.6      |
| tokens_official            |  3520.4      |     4707.4      |    3715.6      |  3603.6      |
| tokens_amplifier           |  1359.4      |     9546.8      |    5252.6      |  1376.8      |
| tokens_ddo                 |   113        |     3356.2      |    4523.8      |   296.8      |
| cost_villager              |     0.000305 |        0.002687 |       0.003472 |     0.00034  |
| cost_official              |     0.000243 |        0.000379 |       0.002674 |     0.000247 |
| cost_amplifier             |     4.1e-05  |        0.000707 |       0.001156 |     3.7e-05  |
| cost_ddo                   |     7e-06    |        0.010249 |       0.010378 |     2e-05    |
| otari_latency_ms_villager  |  1740.72     |      908.056    |     306.802    |  1234.01     |
| otari_latency_ms_official  |  6496.28     |     2342.43     |     679.51     |  4871.83     |
| otari_latency_ms_amplifier |  3058.6      |     1235.34     |     422.133    |  2670.11     |
| otari_latency_ms_ddo       |  5479        |    18484.6      |   25184.9      |  5437.92     |
| otari_tool_calls_villager  |   nan        |        0        |       0        |   nan        |
| otari_tool_calls_official  |   nan        |        0        |       0        |   nan        |
| otari_tool_calls_amplifier |   nan        |        0        |       0        |   nan        |
| otari_tool_calls_ddo       |   nan        |        1        |       1        |   nan        |
| llm_calls                  |   100        |      217.4      |     144        |   105.4      |
| cache_hits                 |   117.2      |      122.2      |     122.4      |   119.2      |
| budget_events              |     1        |        0        |       0        |     1        |
| fallbacks                  |     0        |        0        |       0        |     0        |
| live_wall_s                |   528.04     |      357.92     |     150.24     |   224.88     |

Cohorts that ran out of their per-run Otari budget: gemma3: amplifier; gptoss-hosted: none; llama-hosted: none; qwen3: amplifier

Costs for local families are notional (local models priced per size class in otari/config.yml), not real charges. Costs for hosted families are what otari.ai billed.
