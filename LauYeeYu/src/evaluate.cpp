#include <libCacheSim.h>

#include <cstring>
#include <cstdlib>
#include <vector>
#include <cmath>
#include <iostream>
#include <utility>

#include "parser.h"

// #define RECORD_EVICTION_PROCESS 1

#define RECORD_MISSES 1
#define RECORD_HOLES 1

// Compute intensity transform function: 13823 + 2*i
// This transforms raw compute intensity values before they are used in eviction decisions
double compute_intensity_transform(double raw_intensity) {
  return 863.0 + 2.0 * raw_intensity;
}

// Function to create cache with different algorithms
cache_t* create_cache(const char* algorithm, common_cache_params_t& cc_params) {
  cache_t* cache = nullptr;

  if (strcmp(algorithm, "LRU") == 0) {
    cache = LRU_init(cc_params, NULL);
  } else if (strcmp(algorithm, "FIFO") == 0) {
    cache = FIFO_init(cc_params, NULL);
  } else if (strcmp(algorithm, "LFU") == 0) {
    cache = LFU_init(cc_params, NULL);
  } else if (strcmp(algorithm, "Clock") == 0) {
    cache = Clock_init(cc_params, NULL);
  } else if (strcmp(algorithm, "ARC") == 0) {
    cache = ARC_init(cc_params, NULL);
  } else if (strcmp(algorithm, "S3FIFO") == 0) {
    cache = S3FIFO_init(cc_params, NULL);
  } else if (strcmp(algorithm, "Sieve") == 0) {
    cache = Sieve_init(cc_params, NULL);
  } else if (strcmp(algorithm, "TwoQ") == 0) {
    cache = TwoQ_init(cc_params, NULL);
  } else if (strcmp(algorithm, "LeCaR") == 0) {
    cache = LeCaR_init(cc_params, NULL);
  } else if (strcmp(algorithm, "Belady") == 0) {
    cache = Belady_init(cc_params, NULL);
  } else if (strcmp(algorithm, "BeladyCompute") == 0) {
    cache = BeladyCompute_init(cc_params, NULL);
  } else if (strcmp(algorithm, "RandomCompute") == 0) {
    cache = RandomCompute_init(cc_params, NULL);
  } else if (strcmp(algorithm, "RandomQuickDemotion") == 0) {
    cache = RandomQuickDemotion_init(cc_params, NULL);
  } else if (strcmp(algorithm, "GDSF_compute") == 0) {
    cache = GDSF_compute_init(cc_params, NULL);
  } else if (strcmp(algorithm, "LHD_compute") == 0) {
    cache = LHD_compute_init(cc_params, NULL);
  } else if (strcmp(algorithm, "LHD") == 0) {
    cache = LHD_init(cc_params, NULL);
  } else if (strcmp(algorithm, "GDSF") == 0) {
    cache = GDSF_init(cc_params, NULL);
  } else {
    printf("Unknown algorithm: %s, using LRU\n", algorithm);
    cache = LRU_init(cc_params, NULL);
  }

  return cache;
}

int ensure_space(cache_t *cache, int64_t obj_size,
                      const request_t *req) {
  if (obj_size > cache->cache_size) {
    // object is too large to fit in the cache
    return 1;
  }

  int64_t free_space = cache->cache_size - cache->get_occupied_byte(cache);
  // printf("Ensuring space for object of size %ld, free space %ld for request %d\n",
  //        (long)obj_size, (long)free_space, req->features[2]);
// #ifdef RECORD_EVICTION_PROCESS
//   if (free_space < obj_size) {
//     char buffer[1024];
//     snprintf(buffer, sizeof(buffer), "Req %d for %ld blks",
//              (int)req->features[2], (long)obj_size - free_space);
//     print_eviction_debug_message(buffer);
//   }
// #endif /* RECORD_EVICTION_PROCESS */
  while (free_space < obj_size) {
    cache->evict(cache, req);
    free_space = cache->cache_size - cache->get_occupied_byte(cache);
  }
  return 0;
}

// Helper function to determine the bin for a given compute intensity
int get_bin_for_intensity(int intensity) {
    if (intensity < 0) return -1;
    if (intensity == 0) return 0; // Bin index 0 for intensity 0
    if (intensity == 1) return 1; // Bin index 1 for intensity 1
    // For intensity >= 2, use log base 2
    // This maps intensity 2-3 to bin 2, 4-7 to bin 3, and so on.
    return static_cast<int>(floor(log2(intensity))) + 1;
}

// Helper function to get the intensity range for a given bin index
std::pair<int, int> get_range_for_bin(int bin_index) {
    if (bin_index == 0) {
        return {0, 0};
    }
    if (bin_index == 1) {
        return {1, 1};
    }
    // For bin_index >= 2
    int start = 1 << (bin_index - 1); // 2^(bin_index - 1)
    int end = (1 << bin_index) - 1;   // 2^bin_index - 1
    return {start, end};
}


void run_simulation(cache_t *cache, const std::vector<request_t*>& requests, const char* algorithm, const char* trace_file) {
  int64_t actual_compute = 0;
  int64_t saved_compute = 0;
  bool skip_this_request = false;

  const int num_bins = 32;
  std::vector<int64_t> saved_compute_bins(num_bins, 0);
  std::vector<int64_t> total_compute_bins(num_bins, 0);

#ifdef RECORD_HOLES
  std::vector<std::pair<int, int>> current_request_gaps;
  int current_gap_length = 0;
  int current_gap_start_index = -1;
  int current_block_index = 0;
  bool in_gap = false;
  int current_request_id = -1;
  FILE* hole_file = nullptr;
  if (algorithm != nullptr) {
      char filename[512];
      const char* base_name = trace_file ? strrchr(trace_file, '/') : nullptr;
      base_name = base_name ? base_name + 1 : (trace_file ? trace_file : "unknown");
      
      // Ensure the holes_logs directory exists
      system("mkdir -p holes_logs");
      
      snprintf(filename, sizeof(filename), "holes_logs/holes_%s_cache%llu_%s.txt", base_name, (unsigned long long)cache->cache_size, algorithm);
      hole_file = fopen(filename, "w");
  }
#endif

  for (const auto& req : requests) {
    if (req->features[1] > 0) { // First block of the request
#ifdef RECORD_HOLES
      if (current_request_id != -1) {
          if (in_gap && current_gap_length > 0) {
              current_request_gaps.push_back({current_gap_start_index, current_gap_length});
          }
          if (hole_file) {
              fprintf(hole_file, "Request %d:", current_request_id);
              for (const auto& gap : current_request_gaps) {
                  fprintf(hole_file, " (idx: %d, len: %d)", gap.first, gap.second);
              }
              fprintf(hole_file, "\n");
          }
      }
      current_request_id = req->features[2];
      current_request_gaps.clear();
      current_gap_length = 0;
      current_gap_start_index = -1;
      current_block_index = 0;
      in_gap = false;
#endif

      skip_this_request = false;
      if (ensure_space(cache, req->features[1], req) == 1) {
        // object too large to fit in cache, skip
        skip_this_request = true;
#ifdef RECORD_HOLES
        current_request_id = -1; // Don't record skipped requests
#endif
        continue;
      }
#ifdef RECORD_EVICTION_PROCESS
      char buffer[1024];
      snprintf(buffer, sizeof(buffer), "Request %d of size %d",
               (int)req->features[2], (int)req->features[1]);
      print_eviction_debug_message(buffer);
#endif /* RECORD_EVICTION_PROCESS */
    } else if (skip_this_request) {
      // previous request was too large to fit in cache, skip this one too
      continue;
    }

    // Get raw compute intensity from request
    int raw_compute_cost = req->features[0];

    // Apply transform if the cache algorithm uses one
    double compute_cost = compute_intensity_transform((double)raw_compute_cost);

    // Use raw compute cost for binning (to show original intensity distribution)
    int bin = get_bin_for_intensity(raw_compute_cost);

    if (bin >= 0 && bin < num_bins) {
        total_compute_bins[bin] += compute_cost;
    }

    // I think the get function already adds the request to the cache if it's a miss
    if (cache->get(cache, req) == false) {
      // cache miss
      actual_compute += compute_cost;
#ifdef RECORD_HOLES
      if (!in_gap) {
          in_gap = true;
          current_gap_length = 1;
          current_gap_start_index = current_block_index;
      } else {
          current_gap_length++;
      }
#endif
#ifdef RECORD_EVICTION_PROCESS
      char buffer[1024];
      snprintf(buffer, sizeof(buffer), "Miss: %lu, reuse distance %d",
               (unsigned long)req->obj_id, req->features[3]);
      print_eviction_debug_message(buffer);
#endif /* RECORD_EVICTION_PROCESS */
    } else {
      // cache hit
      saved_compute += compute_cost;
      if (bin >= 0 && bin < num_bins) {
          saved_compute_bins[bin] += compute_cost;
      }
#ifdef RECORD_HOLES
      if (in_gap) {
          current_request_gaps.push_back({current_gap_start_index, current_gap_length});
          in_gap = false;
          current_gap_length = 0;
      }
#endif
#ifdef RECORD_EVICTION_PROCESS
      char buffer[1024];
      snprintf(buffer, sizeof(buffer), "Hit: %lu, reuse distance %d",
               (unsigned long)req->obj_id, req->features[3]);
      print_eviction_debug_message(buffer);
#endif /* RECORD_EVICTION_PROCESS */
    }
#ifdef RECORD_HOLES
    current_block_index++;
#endif
  }
#ifdef RECORD_HOLES
  if (current_request_id != -1) {
      if (in_gap && current_gap_length > 0) {
          current_request_gaps.push_back({current_gap_start_index, current_gap_length});
      }
      if (hole_file) {
          fprintf(hole_file, "Request %d:", current_request_id);
          for (const auto& gap : current_request_gaps) {
              fprintf(hole_file, " (idx: %d, len: %d)", gap.first, gap.second);
          }
          fprintf(hole_file, "\n");
      }
  }
  if (hole_file) {
      fclose(hole_file);
  }
#endif

  // print the ratio of saved compute to actual compute
  printf("Algorithm %s: Saved compute: %lld, Actual compute: %lld, Ratio: %.3lf\n",
         algorithm, saved_compute, actual_compute,
         (double)saved_compute / ((double)actual_compute + (double)saved_compute));
  
  printf("Algorithm %s: Compute savings by intensity bin:\n", algorithm);
  for (int i = 0; i < num_bins; ++i) {
      if (total_compute_bins[i] > 0) {
          std::pair<int, int> range = get_range_for_bin(i);
          if (range.first == range.second) {
              printf("  Bin %2d (Intensity %4d):       Saved %12lld / Total %12lld (Ratio: %.3lf)\n",
                     i + 1,
                     range.first,
                     saved_compute_bins[i],
                     total_compute_bins[i],
                     (double)saved_compute_bins[i] / (double)total_compute_bins[i]);
          } else {
              printf("  Bin %2d (Range %7d - %-7d): Saved %12lld / Total %12lld (Ratio: %.3lf)\n",
                     i + 1,
                     range.first,
                     range.second,
                     saved_compute_bins[i],
                     total_compute_bins[i],
                     (double)saved_compute_bins[i] / (double)total_compute_bins[i]);
          }
      }
  }
}

#ifdef RECORD_EVICTION_PROCESS
const char* algorithms[] = {
    "LRU",
    // "S3FIFO",
    "GDSF_compute",
    // "GDSF",
    "LHD_compute",
    "RandomCompute",
    "RandomQuickDemotion",
    "Belady",
    "BeladyCompute",
};
#else
const char* algorithms[] = {
    "LRU",
    // "FIFO",
    // "LFU",
    // "Clock",
    "ARC",
    "S3FIFO",
    // "Sieve",
    // "TwoQ",
    // "LeCaR",
    "GDSF",
    "GDSF_compute",
    "LHD",
    "LHD_compute",
    "RandomCompute",
    "RandomQuickDemotion",
    "Belady",
    "BeladyCompute",
};
#endif

void print_usage(const char* program_name) {
  printf("Usage: %s --trace-file <path> --cache-size <size>\n", program_name);
  printf("Options:\n");
  printf("  --trace-file <path>    Path to the trace file\n");
  printf("  --cache-size <size>    Cache size in bytes (supports suffixes: K, M, G)\n");
  printf("\nThe program will test all available algorithms: LRU, FIFO, LFU, Clock, ARC, S3FIFO, Sieve, TwoQ, LeCaR, Belady\n");
}

uint64_t parse_cache_size(const char* size_str) {
  char* endptr;
  double size = strtod(size_str, &endptr);
  
  if (endptr == size_str) {
    fprintf(stderr, "Error: Invalid cache size format\n");
    exit(1);
  }
  
  // Handle suffixes
  if (*endptr != '\0') {
    switch (*endptr) {
      case 'K':
      case 'k':
        size *= 1024;
        break;
      case 'M':
      case 'm':
        size *= 1024 * 1024;
        break;
      case 'G':
      case 'g':
        size *= 1024 * 1024 * 1024;
        break;
      default:
        fprintf(stderr, "Error: Unknown size suffix '%c'\n", *endptr);
        exit(1);
    }
  }
  
  return (uint64_t)size;
}

int main(int argc, char *argv[]) {
  const char* trace_file = NULL;
  uint64_t cache_size = 0;

  // Parse command line arguments
  for (int i = 1; i < argc; i++) {
    if (strcmp(argv[i], "--trace-file") == 0) {
      if (i + 1 < argc) {
        trace_file = argv[++i];
      } else {
        fprintf(stderr, "Error: --trace-file requires a value\n");
        print_usage(argv[0]);
        exit(1);
      }
    } else if (strcmp(argv[i], "--cache-size") == 0) {
      if (i + 1 < argc) {
        cache_size = parse_cache_size(argv[++i]);
      } else {
        fprintf(stderr, "Error: --cache-size requires a value\n");
        print_usage(argv[0]);
        exit(1);
      }
    } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
      print_usage(argv[0]);
      exit(0);
    } else {
      fprintf(stderr, "Error: Unknown argument '%s'\n", argv[i]);
      print_usage(argv[0]);
      exit(1);
    }
  }

  // Validate required arguments
  if (trace_file == NULL) {
    fprintf(stderr, "Error: --trace-file is required\n");
    print_usage(argv[0]);
    exit(1);
  }
  
  if (cache_size == 0) {
    fprintf(stderr, "Error: --cache-size is required\n");
    print_usage(argv[0]);
    exit(1);
  }

  // Load trace
  QWenTrace trace{trace_file};

  // Set up cache parameters
  common_cache_params_t cc_params = default_common_cache_params();
  cc_params.cache_size = cache_size;
  printf("Cache size: %llu bytes\n", cc_params.cache_size);
  printf("Trace file: %s\n", trace_file);

  const std::vector<request_t*>& requests = trace.get_requests();
  
  // Test all algorithms
  for (const auto& algo : algorithms) {
    cache_t *cache = create_cache(algo, cc_params);
#ifdef RECORD_EVICTION_PROCESS
    char eviction_process_file[512];
    char mk_directory_name[512];
    snprintf(mk_directory_name, sizeof(mk_directory_name), "mkdir -p eviction_logs/%s/%llu", trace_file,cc_params.cache_size);
    system(mk_directory_name);
    // make a directory named eviction_logs if not exists
    snprintf(eviction_process_file, sizeof(eviction_process_file), "eviction_logs/%s/%llu/%s.txt", trace_file, cc_params.cache_size, algo);
    set_new_record_eviction_process_file(eviction_process_file);
#endif
    run_simulation(cache, requests, algo, trace_file);
    cache->cache_free(cache);
  }
  
  return 0;
}
