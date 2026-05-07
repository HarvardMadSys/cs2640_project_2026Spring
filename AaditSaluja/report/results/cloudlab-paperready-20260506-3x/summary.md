# CloudLab Paper-Ready Benchmark

Completed result files: 162

| Kind | Workload | Variant | Runs | Mean ops/s | Stdev | Mean p95 ms | vs native | vs oracle | Predictor dir precision |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| external | direct_filebench_fileserver | external | 5 | 458.58 | 58.73 |  |  |  |  |
| external | direct_filebench_varmail | external | 5 | 343.90 | 34.07 |  |  |  |  |
| external | direct_ior | external | 5 | 29284.53 | 1215.17 |  |  |  |  |
| external | direct_ior_512m | external | 5 | 23389.33 | 587.98 |  |  |  |  |
| external | direct_mdtest | external | 5 | 2252.49 | 180.70 |  |  |  |  |
| external | direct_mdtest_10k | external | 5 | 2046.26 | 80.52 |  |  |  |  |
| inrepo | hotcold_cold90_access10 | native | 5 | 490.49 | 256.33 | 81.745375 | 1.0 | 0.446633 |  |
| inrepo | hotcold_cold90_access10 | oracle | 5 | 1098.20 | 514.67 | 61.534467 | 2.238976 | 1.0 |  |
| inrepo | hotcold_cold90_access10 | predictor | 5 | 1316.19 | 210.80 | 7.425978 | 2.683401 | 1.198495 | 0.304664 |
| inrepo | hotcold_cold90_access10 | predictor_nolearn | 5 | 10066.89 | 595.35 | 0.693321 | 20.524013 | 9.166695 | 0.0 |
| inrepo | predictor_false_hot_churn | native | 5 | 140.34 | 15.63 | 109.947341 | 1.0 | 0.003841 |  |
| inrepo | predictor_false_hot_churn | oracle | 5 | 36537.80 | 4069.38 | 0.790776 | 260.3531 | 1.0 |  |
| inrepo | predictor_false_hot_churn | predictor | 5 | 188.96 | 5.26 | 96.825364 | 1.34643 | 0.005172 | 0.0 |
| inrepo | predictor_false_hot_churn | predictor_nolearn | 5 | 29632.48 | 1765.30 | 0.910546 | 211.148645 | 0.811009 | 0.0 |
| inrepo | recreated_filebench_varmail_like | native | 5 | 89.83 | 8.56 | 19.04863 | 1.0 | 0.09038 |  |
| inrepo | recreated_filebench_varmail_like | oracle | 5 | 993.89 | 51.79 | 3.476338 | 11.064455 | 1.0 |  |
| inrepo | recreated_filebench_varmail_like | predictor | 5 | 289.65 | 135.10 | 9.672781 | 3.224555 | 0.291434 | 0.0 |
| inrepo | recreated_filebench_varmail_like | predictor_nolearn | 5 | 985.69 | 25.25 | 3.484392 | 10.973127 | 0.991746 | 0.0 |
| inrepo | recreated_mdtest_tree | native | 5 | 446.08 | 15.57 | 4.795969 | 1.0 | 0.038438 |  |
| inrepo | recreated_mdtest_tree | oracle | 5 | 11605.16 | 584.80 | 0.8156 | 26.015868 | 1.0 |  |
| inrepo | recreated_mdtest_tree | predictor | 5 | 11121.33 | 398.10 | 0.849306 | 24.931238 | 0.958309 | 0.0 |
| inrepo | recreated_mdtest_tree | predictor_nolearn | 5 | 11225.39 | 276.14 | 0.846017 | 25.164518 | 0.967276 | 0.0 |
| inrepo | scaled_hotcold_cold90_access10_20k | native | 3 | 334.60 | 21.32 | 152.164224 | 1.0 | 0.53416 |  |
| inrepo | scaled_hotcold_cold90_access10_20k | oracle | 3 | 626.40 | 36.02 | 108.050086 | 1.872097 | 1.0 |  |
| inrepo | scaled_hotcold_cold90_access10_20k | predictor | 3 | 791.92 | 113.53 | 100.69443 | 2.366756 | 1.264227 | 0.031414 |
| inrepo | scaled_hotcold_cold90_access10_20k | predictor_nolearn | 3 | 8389.15 | 292.44 | 0.83277 | 25.072214 | 13.39258 | 0.0 |
| inrepo | ycsb_hotspot_file_skew | native | 5 | 236.50 | 21.87 | 120.432671 | 1.0 | 0.43424 |  |
| inrepo | ycsb_hotspot_file_skew | oracle | 5 | 544.62 | 130.60 | 69.60903 | 2.302872 | 1.0 |  |
| inrepo | ycsb_hotspot_file_skew | predictor | 5 | 566.64 | 90.92 | 80.589792 | 2.39595 | 1.040418 | 0.0625 |
| inrepo | ycsb_hotspot_file_skew | predictor_nolearn | 5 | 7572.32 | 422.52 | 0.756435 | 32.018605 | 13.903773 | 0.0 |
| inrepo | ycsb_zipfian_file_skew | native | 5 | 223.63 | 38.93 | 145.290586 | 1.0 | 0.498832 |  |
| inrepo | ycsb_zipfian_file_skew | oracle | 5 | 448.31 | 99.53 | 111.693368 | 2.004683 | 1.0 |  |
| inrepo | ycsb_zipfian_file_skew | predictor | 5 | 414.50 | 45.51 | 124.859471 | 1.853481 | 0.924575 | 0.0625 |
| inrepo | ycsb_zipfian_file_skew | predictor_nolearn | 5 | 7923.45 | 209.47 | 0.720558 | 35.430818 | 17.674024 | 0.0 |
