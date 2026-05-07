| workload | variant | ops/s | speedup | mean ms | p95 ms | pins | md MB | md objects | data objects | segments |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| sprite_lfs_smallfile | default | 259.726 | 1.0 | 24.135 | 47.612 | 0 | 11.25 | 4 | 5000 | 0 |
| sprite_lfs_smallfile | static_top | 217.319 | 0.837 | 33.038 | 74.023 | 32 | 48.25 | 7 | 5000 | 0 |
| sprite_lfs_smallfile | prepin_hotset | 228.785 | 0.881 | 28.13 | 76.44 | 1 | 0.00 | 16 | 5000 | 0 |
| sprite_lfs_smallfile | predictive_safe | 293.206 | 1.129 | 24.928 | 46.376 | 0 | 0.56 | 0 | 5000 | 0 |
| sprite_lfs_smallfile | append_segments | 175.304 | 0.675 | 45.343 | 62.244 | 0 | 20.31 | 1 | 3 | 1 |
| hotdirs_zipf | default | 288.748 | 1.0 | 27.116 | 46.637 | 0 | 12.56 | 2 | 5000 | 0 |
| hotdirs_zipf | static_top | 90.88 | 0.315 | 87.122 | 178.401 | 64 | -32.75 | -3 | 5000 | 0 |
| hotdirs_zipf | prepin_hotset | 104.75 | 0.363 | 75.602 | 188.12 | 1 | 21.56 | 1 | 5000 | 0 |
| hotdirs_zipf | predictive_safe | 138.98 | 0.481 | 56.281 | 170.375 | 1 | -2.62 | 0 | 5000 | 0 |
| hotdirs_zipf | append_segments | 189.89 | 0.658 | 41.854 | 60.348 | 0 | 12.12 | 1 | 2 | 1 |
