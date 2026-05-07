# CloudLab Hot/Cold Matrix

Serial randomized benchmark matrix for `oracle_hotcold_mix`.

## Fairness Controls

- Same cluster, mount, benchmark root, file size, file count, directory count, worker count, and operation count for every variant.
- Same seeds are reused across variants within each scenario/repeat.
- Run order is randomized globally, then executed serially.
- Each benchmark includes create, access, hot churn, and cleanup phases.
- Ceph health is checked before each run.

## Summary

| Scenario | Variant | Runs | Mean ops/s | Speedup | Stdev ops/s | Mean namespace entries | Layout applied | Failure runs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| cold70_access0 | hybrid | 3 | 540.88 | 1.496356 | 101.17 | 2409.0 | 0.0 | 0 |
| cold70_access0 | hybrid_layout | 3 | 391.02 | 1.081773 | 155.24 | 2409.0 | 6.0 | 0 |
| cold70_access0 | native | 3 | 361.46 | 1.0 | 40.72 | 4565.0 | 0.0 | 0 |
| cold70_access10 | hybrid | 3 | 398.07 | 1.191883 | 356.92 | 2409.0 | 0.0 | 0 |
| cold70_access10 | hybrid_layout | 3 | 726.85 | 2.17634 | 548.87 | 2409.0 | 6.0 | 0 |
| cold70_access10 | native | 3 | 333.98 | 1.0 | 30.62 | 4565.0 | 0.0 | 0 |
| cold70_access20 | hybrid | 3 | 1090.59 | 3.426533 | 524.31 | 2409.0 | 0.0 | 0 |
| cold70_access20 | hybrid_layout | 3 | 424.67 | 1.334281 | 184.70 | 2409.0 | 6.0 | 0 |
| cold70_access20 | native | 3 | 318.28 | 1.0 | 110.11 | 4565.0 | 0.0 | 0 |
| cold90_access0 | hybrid | 3 | 1002.16 | 2.901644 | 276.03 | 1809.0 | 0.0 | 0 |
| cold90_access0 | hybrid_layout | 3 | 593.16 | 1.717431 | 117.40 | 1809.0 | 6.0 | 0 |
| cold90_access0 | native | 3 | 345.38 | 1.0 | 62.32 | 4565.0 | 0.0 | 0 |
| cold90_access10 | hybrid | 3 | 1969.90 | 5.007496 | 1661.70 | 1809.0 | 0.0 | 0 |
| cold90_access10 | hybrid_layout | 3 | 1254.27 | 3.188356 | 650.64 | 1809.0 | 6.0 | 0 |
| cold90_access10 | native | 3 | 393.39 | 1.0 | 51.28 | 4565.0 | 0.0 | 0 |
| cold90_access20 | hybrid | 3 | 860.88 | 2.185571 | 494.54 | 1809.0 | 0.0 | 0 |
| cold90_access20 | hybrid_layout | 3 | 888.02 | 2.254468 | 748.45 | 1809.0 | 6.0 | 0 |
| cold90_access20 | native | 3 | 393.89 | 1.0 | 25.74 | 4565.0 | 0.0 | 0 |

## Phase Summary

| Scenario | Variant | Operation | Mean ops/s | Mean p95 ms | Runs |
|---|---|---|---:|---:|---:|
| cold70_access0 | hybrid | bulk_create | 909.11 | 3.00 | 3 |
| cold70_access0 | hybrid | cleanup_delete | 443.75 | 58.75 | 3 |
| cold70_access0 | hybrid | oracle_access_read | 5555.58 | 1.89 | 3 |
| cold70_access0 | hybrid | oracle_access_stat | 8453.60 | 1.24 | 3 |
| cold70_access0 | hybrid | oracle_hot_churn_create | 82.58 | 153.02 | 3 |
| cold70_access0 | hybrid | oracle_hot_churn_delete | 123.50 | 109.77 | 3 |
| cold70_access0 | hybrid_layout | bulk_create | 582.37 | 2.81 | 3 |
| cold70_access0 | hybrid_layout | cleanup_delete | 301.74 | 138.14 | 3 |
| cold70_access0 | hybrid_layout | oracle_access_read | 5480.97 | 2.01 | 3 |
| cold70_access0 | hybrid_layout | oracle_access_stat | 6360.44 | 1.40 | 3 |
| cold70_access0 | hybrid_layout | oracle_hot_churn_create | 98.22 | 78.70 | 3 |
| cold70_access0 | hybrid_layout | oracle_hot_churn_delete | 64.95 | 184.13 | 3 |
| cold70_access0 | native | bulk_create | 153.74 | 5.99 | 3 |
| cold70_access0 | native | cleanup_delete | 308.49 | 105.51 | 3 |
| cold70_access0 | native | oracle_access_read | 5195.99 | 1.93 | 3 |
| cold70_access0 | native | oracle_access_stat | 4679.40 | 1.70 | 3 |
| cold70_access0 | native | oracle_hot_churn_create | 65.03 | 239.09 | 3 |
| cold70_access0 | native | oracle_hot_churn_delete | 161.43 | 108.60 | 3 |
| cold70_access10 | hybrid | bulk_create | 399.32 | 2.87 | 3 |
| cold70_access10 | hybrid | cleanup_delete | 2642.98 | 77.62 | 3 |
| cold70_access10 | hybrid | oracle_access_read | 5633.76 | 2.09 | 3 |
| cold70_access10 | hybrid | oracle_access_stat | 8380.97 | 1.36 | 3 |
| cold70_access10 | hybrid | oracle_hot_churn_create | 99.29 | 2.62 | 3 |
| cold70_access10 | hybrid | oracle_hot_churn_delete | 367.76 | 141.76 | 3 |
| cold70_access10 | hybrid_layout | bulk_create | 571.02 | 2.96 | 3 |
| cold70_access10 | hybrid_layout | cleanup_delete | 617.46 | 104.38 | 3 |
| cold70_access10 | hybrid_layout | oracle_access_read | 5415.45 | 2.04 | 3 |
| cold70_access10 | hybrid_layout | oracle_access_stat | 7480.10 | 1.40 | 3 |
| cold70_access10 | hybrid_layout | oracle_hot_churn_create | 116.44 | 107.90 | 3 |
| cold70_access10 | hybrid_layout | oracle_hot_churn_delete | 1506.38 | 136.93 | 3 |
| cold70_access10 | native | bulk_create | 100.96 | 288.45 | 3 |
| cold70_access10 | native | cleanup_delete | 375.93 | 88.39 | 3 |
| cold70_access10 | native | oracle_access_read | 4905.51 | 2.19 | 3 |
| cold70_access10 | native | oracle_access_stat | 3396.37 | 1.62 | 3 |
| cold70_access10 | native | oracle_hot_churn_create | 68.68 | 265.61 | 3 |
| cold70_access10 | native | oracle_hot_churn_delete | 206.18 | 98.25 | 3 |
| cold70_access20 | hybrid | bulk_create | 625.51 | 3.48 | 3 |
| cold70_access20 | hybrid | cleanup_delete | 5995.47 | 19.51 | 3 |
| cold70_access20 | hybrid | oracle_access_read | 5559.85 | 2.19 | 3 |
| cold70_access20 | hybrid | oracle_access_stat | 10790.88 | 1.18 | 3 |
| cold70_access20 | hybrid | oracle_hot_churn_create | 127.08 | 77.27 | 3 |
| cold70_access20 | hybrid | oracle_hot_churn_delete | 2984.29 | 38.22 | 3 |
| cold70_access20 | hybrid_layout | bulk_create | 534.72 | 7.53 | 3 |
| cold70_access20 | hybrid_layout | cleanup_delete | 445.91 | 92.47 | 3 |
| cold70_access20 | hybrid_layout | oracle_access_read | 5568.23 | 1.91 | 3 |
| cold70_access20 | hybrid_layout | oracle_access_stat | 8241.37 | 1.41 | 3 |
| cold70_access20 | hybrid_layout | oracle_hot_churn_create | 86.14 | 139.45 | 3 |
| cold70_access20 | hybrid_layout | oracle_hot_churn_delete | 110.71 | 156.37 | 3 |
| cold70_access20 | native | bulk_create | 122.57 | 4.11 | 3 |
| cold70_access20 | native | cleanup_delete | 345.68 | 117.84 | 3 |
| cold70_access20 | native | oracle_access_read | 5139.82 | 2.07 | 3 |
| cold70_access20 | native | oracle_access_stat | 5208.46 | 1.70 | 3 |
| cold70_access20 | native | oracle_hot_churn_create | 61.73 | 287.89 | 3 |
| cold70_access20 | native | oracle_hot_churn_delete | 146.70 | 181.05 | 3 |
| cold90_access0 | hybrid | bulk_create | 3553.25 | 2.58 | 3 |
| cold90_access0 | hybrid | cleanup_delete | 2604.97 | 11.12 | 3 |
| cold90_access0 | hybrid | oracle_access_read | 6012.83 | 1.77 | 3 |
| cold90_access0 | hybrid | oracle_access_stat | 8426.09 | 1.22 | 3 |
| cold90_access0 | hybrid | oracle_hot_churn_create | 94.23 | 23.13 | 3 |
| cold90_access0 | hybrid | oracle_hot_churn_delete | 2194.82 | 38.62 | 3 |
| cold90_access0 | hybrid_layout | bulk_create | 2531.55 | 2.67 | 3 |
| cold90_access0 | hybrid_layout | cleanup_delete | 959.28 | 25.41 | 3 |
| cold90_access0 | hybrid_layout | oracle_access_read | 4298.65 | 1.74 | 3 |
| cold90_access0 | hybrid_layout | oracle_access_stat | 7593.16 | 1.38 | 3 |
| cold90_access0 | hybrid_layout | oracle_hot_churn_create | 74.33 | 159.98 | 3 |
| cold90_access0 | hybrid_layout | oracle_hot_churn_delete | 188.55 | 93.87 | 3 |
| cold90_access0 | native | bulk_create | 174.17 | 100.05 | 3 |
| cold90_access0 | native | cleanup_delete | 425.63 | 65.83 | 3 |
| cold90_access0 | native | oracle_access_read | 5203.64 | 2.01 | 3 |
| cold90_access0 | native | oracle_access_stat | 6791.83 | 1.45 | 3 |
| cold90_access0 | native | oracle_hot_churn_create | 55.89 | 276.20 | 3 |
| cold90_access0 | native | oracle_hot_churn_delete | 126.61 | 139.33 | 3 |
| cold90_access10 | hybrid | bulk_create | 3697.20 | 2.65 | 3 |
| cold90_access10 | hybrid | cleanup_delete | 19187.92 | 6.74 | 3 |
| cold90_access10 | hybrid | oracle_access_read | 6191.96 | 2.00 | 3 |
| cold90_access10 | hybrid | oracle_access_stat | 8751.42 | 1.28 | 3 |
| cold90_access10 | hybrid | oracle_hot_churn_create | 265.85 | 65.30 | 3 |
| cold90_access10 | hybrid | oracle_hot_churn_delete | 1686.70 | 32.26 | 3 |
| cold90_access10 | hybrid_layout | bulk_create | 2506.39 | 2.71 | 3 |
| cold90_access10 | hybrid_layout | cleanup_delete | 6293.99 | 12.12 | 3 |
| cold90_access10 | hybrid_layout | oracle_access_read | 5784.19 | 1.86 | 3 |
| cold90_access10 | hybrid_layout | oracle_access_stat | 8115.92 | 1.41 | 3 |
| cold90_access10 | hybrid_layout | oracle_hot_churn_create | 131.29 | 73.49 | 3 |
| cold90_access10 | hybrid_layout | oracle_hot_churn_delete | 1932.00 | 31.30 | 3 |
| cold90_access10 | native | bulk_create | 137.26 | 193.27 | 3 |
| cold90_access10 | native | cleanup_delete | 744.04 | 59.68 | 3 |
| cold90_access10 | native | oracle_access_read | 4987.88 | 2.05 | 3 |
| cold90_access10 | native | oracle_access_stat | 4288.25 | 1.56 | 3 |
| cold90_access10 | native | oracle_hot_churn_create | 73.64 | 246.94 | 3 |
| cold90_access10 | native | oracle_hot_churn_delete | 194.80 | 107.49 | 3 |
| cold90_access20 | hybrid | bulk_create | 4169.64 | 2.54 | 3 |
| cold90_access20 | hybrid | cleanup_delete | 6667.48 | 37.69 | 3 |
| cold90_access20 | hybrid | oracle_access_read | 6360.55 | 2.06 | 3 |
| cold90_access20 | hybrid | oracle_access_stat | 9938.23 | 1.25 | 3 |
| cold90_access20 | hybrid | oracle_hot_churn_create | 86.19 | 65.79 | 3 |
| cold90_access20 | hybrid | oracle_hot_churn_delete | 1883.32 | 97.76 | 3 |
| cold90_access20 | hybrid_layout | bulk_create | 2205.07 | 2.70 | 3 |
| cold90_access20 | hybrid_layout | cleanup_delete | 1977.15 | 15.02 | 3 |
| cold90_access20 | hybrid_layout | oracle_access_read | 6100.41 | 1.71 | 3 |
| cold90_access20 | hybrid_layout | oracle_access_stat | 9221.79 | 1.35 | 3 |
| cold90_access20 | hybrid_layout | oracle_hot_churn_create | 118.96 | 3.30 | 3 |
| cold90_access20 | hybrid_layout | oracle_hot_churn_delete | 1691.53 | 87.19 | 3 |
| cold90_access20 | native | bulk_create | 135.20 | 95.22 | 3 |
| cold90_access20 | native | cleanup_delete | 820.42 | 41.39 | 3 |
| cold90_access20 | native | oracle_access_read | 3609.44 | 2.47 | 3 |
| cold90_access20 | native | oracle_access_stat | 6214.72 | 1.55 | 3 |
| cold90_access20 | native | oracle_hot_churn_create | 65.97 | 246.15 | 3 |
| cold90_access20 | native | oracle_hot_churn_delete | 170.96 | 121.12 | 3 |
