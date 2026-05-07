# CloudLab Predictor Diagnosis 2026-05-06

| Case | Workload | Storage | Ops/s | Seconds | Promotions | Hot dirs | Native writes | Packed writes |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `native` | `oracle_hotcold_mix` | `native` | 727.46 | 7.148 |  |  |  |  |
| `oracle` | `oracle_hotcold_mix` | `oracle_cold_segments` | 4593.31 | 1.132 |  |  | 380 | 720 |
| `pred_read1` | `oracle_hotcold_mix` | `predictive_cold_segments` | 2731.04 | 1.904 | 141 | 29 | 441 | 800 |
| `pred_statonly4` | `oracle_hotcold_mix` | `predictive_cold_segments` | 2063.80 | 2.520 | 86 | 4 | 386 | 800 |
| `pred_statread2` | `oracle_hotcold_mix` | `predictive_cold_segments` | 1633.38 | 3.184 | 134 | 28 | 434 | 800 |
| `pred_statread4` | `oracle_hotcold_mix` | `predictive_cold_segments` | 2148.56 | 2.420 | 87 | 5 | 387 | 800 |
