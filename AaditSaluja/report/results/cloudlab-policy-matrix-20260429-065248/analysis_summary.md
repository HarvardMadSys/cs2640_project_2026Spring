| workload | policy | ops/s | speedup | mean ms | p95 ms | max p99 ms | pins | failures | elapsed s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mdtest_tree | none | 166.786 | 1.0 | 23.774 | 68.992 | 307.353 | 0 | 0 | 34.858579 |
| mdtest_tree | static | 115.749 | 0.694 | 32.193 | 64.88 | 425.453 | 73 | 0 | 60.577504 |
| mdtest_tree | predictive | 227.418 | 1.364 | 17.434 | 58.789 | 265.833 | 0 | 0 | 25.810537 |
| mdtest_tree | LLM_policy | 205.604 | 1.233 | 19.279 | 78.676 | 255.876 | 0 | 0 | 27.536265 |
| sprite_lfs_smallfile | none | 126.634 | 1.0 | 27.771 | 68.976 | 275.09 | 0 | 0 | 57.798972 |
| sprite_lfs_smallfile | static | 63.427 | 0.501 | 40.635 | 112.538 | 259.872 | 17 | 0 | 116.352574 |
| sprite_lfs_smallfile | predictive | 188.557 | 1.489 | 15.356 | 53.787 | 251.71 | 0 | 0 | 39.422181 |
| sprite_lfs_smallfile | LLM_policy | 89.022 | 0.703 | 27.122 | 89.051 | 269.526 | 0 | 0 | 82.184733 |
| hotdirs_zipf | none | 336.583 | 1.0 | 11.766 | 17.203 | 32.95 | 0 | 0 | 16.720706 |
| hotdirs_zipf | static | 41.48 | 0.123 | 95.153 | 184.016 | 602.395 | 33 | 0 | 134.723253 |
| hotdirs_zipf | predictive | 28.999 | 0.086 | 135.947 | 298.792 | 685.818 | 1 | 0 | 186.772988 |
| hotdirs_zipf | LLM_policy | 35.099 | 0.104 | 112.695 | 232.323 | 769.119 | 1 | 0 | 154.431242 |
| filebench_varmail_like | none | 55.952 | 1.0 | 17.862 | 76.366 | 171.759 | 0 | 0 | 76.979111 |
| filebench_varmail_like | static | 17.169 | 0.307 | 58.236 | 114.161 | 1697.201 | 49 | 0 | 253.240627 |
| filebench_varmail_like | predictive | 74.803 | 1.337 | 13.358 | 44.168 | 213.079 | 0 | 0 | 57.682995 |
| filebench_varmail_like | LLM_policy | 34.649 | 0.619 | 28.85 | 111.002 | 213.915 | 0 | 0 | 123.532029 |
