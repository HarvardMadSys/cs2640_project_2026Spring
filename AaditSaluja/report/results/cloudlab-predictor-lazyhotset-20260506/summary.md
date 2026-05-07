# CloudLab Predictor Lazy Hotset 2026-05-06

| Case | Workload | Storage | Ops/s | Seconds | Promotions | Hot dirs | Native writes | Packed writes |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `lazy_read` | `oracle_hotcold_mix` | `predictive_cold_segments` | 3689.28 | 1.409 | 0 | 4 | 300 | 800 |
| `lazy_statread` | `oracle_hotcold_mix` | `predictive_cold_segments` | 3958.62 | 1.314 | 0 | 28 | 300 | 800 |
| `lazy_statread_e4d2` | `oracle_hotcold_mix` | `predictive_cold_segments` | 3842.07 | 1.353 | 0 | 32 | 300 | 800 |
