| workload | storage | ops/s | speedup | p95 ms | md MB | data objects | segments | shards | max rec/shard | index MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| sprite_lfs_smallfile | native | 271.968 | 1.0 | 47.024 | -16.938 | 5000 | 0 | 0 | 0 | 0.0 |
| sprite_lfs_smallfile | append_segments | 197.048 | 0.725 | 62.024 | 21.188 | 3 | 1 | 0 | 0 | 1.7 |
| sprite_lfs_smallfile | sharded_directory | 111.929 | 0.412 | 158.951 | 24.375 | 64 | 32 | 32 | 157 | 1.998 |
| sprite_lfs_smallfile | sharded_hash8 | 115.009 | 0.423 | 174.212 | -4.688 | 16 | 8 | 8 | 667 | 1.876 |
| hotdirs_zipf | native | 267.566 | 1.0 | 47.873 | 1.5 | 5000 | 0 | 0 | 0 | 0.0 |
| hotdirs_zipf | append_segments | 187.39 | 0.7 | 60.59 | 8.188 | 2 | 1 | 0 | 0 | 1.629 |
| hotdirs_zipf | sharded_directory | 119.686 | 0.447 | 96.451 | 9.188 | 128 | 64 | 64 | 3996 | 1.931 |
| hotdirs_zipf | sharded_hash8 | 89.908 | 0.336 | 261.423 | 22.5 | 16 | 8 | 8 | 689 | 1.806 |
