# CloudLab Comprehensive Benchmark

Completed JSON runs: 54

| Workload | Variant | Runs | Mean ops/s | Stdev | Mean phase p95 ms | vs native | vs oracle |
|---|---|---:|---:|---:|---:|---:|---:|
| filebench_varmail_like | native | 3 | 69.61 | 24.98 | 39.863 | 1.0 | 0.062901 |
| filebench_varmail_like | oracle | 3 | 1106.62 | 42.35 | 3.435 | 15.897947 | 1.0 |
| filebench_varmail_like | predictor | 3 | 248.96 | 46.74 | 10.432 | 3.576659 | 0.224976 |
| hotcold_cold70_access10 | native | 3 | 320.57 | 85.02 | 114.158 | 1.0 | 0.316696 |
| hotcold_cold70_access10 | oracle | 3 | 1012.25 | 540.91 | 55.455 | 3.157598 | 1.0 |
| hotcold_cold70_access10 | predictor | 3 | 1506.55 | 56.24 | 1.869 | 4.699527 | 1.488323 |
| hotcold_cold90_access10 | native | 3 | 334.01 | 87.36 | 118.917 | 1.0 | 0.299528 |
| hotcold_cold90_access10 | oracle | 3 | 1115.13 | 420.96 | 39.889 | 3.338581 | 1.0 |
| hotcold_cold90_access10 | predictor | 3 | 1208.52 | 175.74 | 33.561 | 3.618202 | 1.083754 |
| hotcold_cold90_access20 | native | 3 | 337.89 | 98.46 | 144.193 | 1.0 | 0.429149 |
| hotcold_cold90_access20 | oracle | 3 | 787.34 | 887.13 | 331.410 | 2.330192 | 1.0 |
| hotcold_cold90_access20 | predictor | 3 | 1053.40 | 484.10 | 50.535 | 3.117626 | 1.337927 |
| ior_mdtest_tree | native | 3 | 2412.22 | 1114.00 | 2.528 | 1.0 | 0.188882 |
| ior_mdtest_tree | oracle | 3 | 12771.00 | 1157.80 | 0.753 | 5.2943 | 1.0 |
| ior_mdtest_tree | predictor | 3 | 12461.66 | 1197.06 | 0.777 | 5.166064 | 0.975778 |
| ycsb_zipf_hotdirs | native | 3 | 321.59 | 46.46 | 26.419 | 1.0 | 0.799492 |
| ycsb_zipf_hotdirs | oracle | 3 | 402.25 | 19.92 | 4.261 | 1.250794 | 1.0 |
| ycsb_zipf_hotdirs | predictor | 3 | 14825.19 | 995.32 | 0.604 | 46.099011 | 36.855787 |
