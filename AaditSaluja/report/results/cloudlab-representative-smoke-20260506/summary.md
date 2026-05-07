# CloudLab Representative Smoke 2026-05-06

| Case | Workload | Storage | Ops/s | Seconds | Promotions | Hot dirs | Native writes | Packed writes |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `filebench_native` | `filebench_varmail_like` | `native` | 767.11 | 2.305 |  |  |  |  |
| `filebench_predictor` | `filebench_varmail_like` | `predictive_cold_segments` | 1761.48 | 1.004 | 3 | 3 | 11 | 696 |
| `hotcold_native` | `oracle_hotcold_mix` | `native` | 728.79 | 4.458 |  |  |  |  |
| `hotcold_oracle` | `oracle_hotcold_mix` | `oracle_cold_segments` | 4929.31 | 0.659 |  |  | 237 | 450 |
| `hotcold_predictor` | `oracle_hotcold_mix` | `predictive_cold_segments` | 2263.33 | 1.435 | 54 | 5 | 241 | 500 |
| `hotdirs_native` | `hotdirs_zipf` | `native` | 2356.67 | 0.636 |  |  |  |  |
| `hotdirs_predictor` | `hotdirs_zipf` | `predictive_cold_segments` | 9786.16 | 0.153 | 0 | 0 | 0 | 500 |
| `mdtest_native` | `mdtest_tree` | `native` | 2433.20 | 0.822 |  |  |  |  |
| `mdtest_predictor` | `mdtest_tree` | `predictive_cold_segments` | 6499.98 | 0.308 | 0 | 0 | 0 | 500 |
