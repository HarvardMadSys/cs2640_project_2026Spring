# CloudLab Predictor Profile

Non-oracle predictive cold-packing strategies compared with oracle upper bound.

| Scenario | Variant | Runs | Mean ops/s | Rel. oracle | Stdev | Namespace | Promotions | Hot promos | Cold promos |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cold70_access20 | oracle | 2 | 530.77 | 1.0 | 385.18 | 2409.0 | 0.0 | 0.0 | 0.0 |
| cold70_access20 | pred_read1 | 2 | 1403.33 | 2.643923 | 193.62 | 1436.5 | 1367.5 | 844.5 | 523.0 |
| cold70_access20 | pred_statread2 | 2 | 1603.11 | 3.020323 | 1065.12 | 1862.5 | 1793.5 | 907.5 | 886.0 |
| cold70_access20 | pred_statread4 | 2 | 2608.79 | 4.915075 | 656.25 | 1085.5 | 1023.5 | 909.0 | 114.5 |
| cold90_access10 | oracle | 2 | 588.73 | 1.0 | 114.81 | 1809.0 | 0.0 | 0.0 | 0.0 |
| cold90_access10 | pred_read1 | 2 | 5714.55 | 9.70653 | 95.61 | 660.5 | 593.0 | 304.5 | 288.5 |
| cold90_access10 | pred_statread2 | 2 | 4932.44 | 8.378072 | 622.05 | 660.5 | 594.0 | 307.0 | 287.0 |
| cold90_access10 | pred_statread4 | 2 | 5291.78 | 8.988432 | 6.75 | 331.0 | 315.5 | 309.0 | 6.5 |
| cold90_access20 | oracle | 2 | 790.95 | 1.0 | 120.28 | 1809.0 | 0.0 | 0.0 | 0.0 |
| cold90_access20 | pred_read1 | 2 | 5492.51 | 6.944176 | 4658.43 | 919.0 | 850.0 | 303.5 | 546.5 |
| cold90_access20 | pred_statread2 | 2 | 2861.80 | 3.618179 | 1437.73 | 1218.0 | 1149.0 | 308.5 | 840.5 |
| cold90_access20 | pred_statread4 | 2 | 5259.36 | 6.649412 | 440.77 | 432.5 | 383.5 | 309.5 | 74.0 |
