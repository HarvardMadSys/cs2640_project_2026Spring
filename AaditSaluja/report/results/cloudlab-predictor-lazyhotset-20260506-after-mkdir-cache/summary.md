# CloudLab Predictor Lazy Hotset After mkdir Cache 2026-05-06

| Case | Workload | Storage | Ops/s | Seconds | Promotions | Hot dirs | Native writes | Packed writes |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `lazy_read` | `oracle_hotcold_mix` | `predictive_cold_segments` | 4143.12 | 1.255 | 0 | 4 | 300 | 800 |
| `lazy_statread` | `oracle_hotcold_mix` | `predictive_cold_segments` | 4026.73 | 1.291 | 0 | 28 | 300 | 800 |
