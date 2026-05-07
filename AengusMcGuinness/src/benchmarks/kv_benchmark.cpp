/**
 * @file kv_benchmark.cpp
 * @brief TCP workload generator and CSV-producing benchmark driver.
 * @ingroup executables
 */

// The benchmark talks to the same TCP socket helpers used by the client and server.
#include "net/socket_utils.hpp"

// Standard algorithms for sorting, search, and aggregation.
#include <algorithm>
// Atomic is included because earlier versions used it; it remains harmless here.
#include <atomic>
// steady_clock gives us monotonic timing for latency and throughput measurement.
#include <chrono>
// Mathematical helpers are used for Zipf sampling and percentile calculation.
#include <cmath>
// Fixed-width integer types keep the benchmark outputs precise.
#include <cstdint>
// Exit codes and numeric conversion helpers.
#include <cstdlib>
// C string support for parsing and diagnostics.
#include <cstring>
// File I/O is used for optional CSV output.
#include <cstdio>
// CSV output is appended through standard streams.
#include <fstream>
// Console output reports benchmark results to the user.
#include <iostream>
// Numeric limits are available if we need sentinel values.
#include <limits>
// Accumulate computes latency averages.
#include <numeric>
// Random number generation drives workload selection.
#include <random>
// Strings store command arguments, keys, values, and paths.
#include <string>
// string_view avoids unnecessary copies for command parsing.
#include <string_view>
// Threads provide one worker per client connection in the benchmark.
#include <thread>
// Vectors hold workers, keys, latencies, and result objects.
#include <vector>

// Anonymous namespace keeps benchmark-only helpers out of the global API.
namespace {

// Use the same monotonic clock for all timing measurements.
using Clock = std::chrono::steady_clock;
// Latency is tracked in microseconds because that is a convenient human-readable unit.
using Microseconds = std::chrono::microseconds;

// Options captures every command-line knob that controls a benchmark run.
struct Options {
    // Target server host.
    std::string host = "127.0.0.1";
    // Target server port.
    std::uint16_t port = 9090;
    // Number of concurrent benchmark clients.
    std::size_t clients = 4;
    // Total number of measured operations to execute.
    std::size_t operations = 10000;
    // Size of the synthetic keyspace.
    std::size_t key_count = 1024;
    // Size of generated values in bytes.
    std::size_t value_size = 64;
    // Fraction of operations that should be GETs.
    double get_ratio = 0.95;
    // Zipf skew parameter; zero means uniform access.
    double zipf_s = 1.0;
    // Warmup operations are executed before measurement starts.
    std::size_t warmup = 1000;
    // Prefill populates the keyspace so GETs can hit valid values.
    bool prefill = true;
    // Optional CSV output path for graphing.
    std::string csv_path;
};

// Result stores the measured outcomes from one worker thread.
struct Result {
    // Latency samples for measured operations only.
    std::vector<std::int64_t> latencies_us;
    // Count of successful measured responses.
    std::size_t measured_ok_responses = 0;
    // Count of measured requests that failed validation or transport.
    std::size_t measured_errors = 0;
    // Count of warmup operations that succeeded.
    std::size_t warmup_ok_responses = 0;
    // Count of warmup operations that failed.
    std::size_t warmup_errors = 0;
};

// Print usage information and exit with failure if the command line is malformed.
[[noreturn]] void usage_and_exit(const char* program) {
    std::cerr << "usage: " << program
              << " [--host HOST] [--port PORT] [--clients N] [--ops N] [--keys N]"
              << " [--value-size N] [--get-ratio R] [--zipf-s S] [--warmup N] [--no-prefill]"
              << " [--csv PATH]\n";
    std::exit(EXIT_FAILURE);
}

// Parse a non-negative integer from text, returning false on malformed input.
bool parse_uint(const std::string& text, std::size_t& out) {
    try {
        // stoull gives us a simple decimal parser with range checking.
        std::size_t index = 0;
        const unsigned long long value = std::stoull(text, &index);
        if (index != text.size()) {
            return false;
        }
        out = static_cast<std::size_t>(value);
        return true;
    } catch (...) {
        return false;
    }
}

// Parse a TCP port number and make sure it fits in 16 bits.
bool parse_port(const std::string& text, std::uint16_t& out) {
    std::size_t value = 0;
    if (!parse_uint(text, value) || value > 65535) {
        return false;
    }
    out = static_cast<std::uint16_t>(value);
    return true;
}

// Parse a floating-point control knob such as get_ratio or zipf_s.
bool parse_double(const std::string& text, double& out) {
    try {
        // stod gives us the same kind of strict full-string parse as stoull above.
        std::size_t index = 0;
        const double value = std::stod(text, &index);
        if (index != text.size()) {
            return false;
        }
        out = value;
        return true;
    } catch (...) {
        return false;
    }
}

// Parse all benchmark options from argv.
Options parse_args(int argc, char* argv[]) {
    // Start from the defaults in the struct and override them from the command line.
    Options options;

    for (int i = 1; i < argc; ++i) {
        // Current argument token.
        const std::string arg = argv[i];
        // Helper that fetches the next argv entry or prints usage if it is missing.
        auto next_value = [&](const char* flag) -> std::string {
            if (i + 1 >= argc) {
                std::cerr << "missing value for " << flag << '\n';
                usage_and_exit(argv[0]);
            }
            return argv[++i];
        };

        // Each flag maps directly to one field in Options.
        if (arg == "--host") {
            options.host = next_value("--host");
            continue;
        }
        if (arg == "--port") {
            if (!parse_port(next_value("--port"), options.port)) {
                std::cerr << "invalid port\n";
                usage_and_exit(argv[0]);
            }
            continue;
        }
        if (arg == "--clients") {
            if (!parse_uint(next_value("--clients"), options.clients) || options.clients == 0) {
                std::cerr << "invalid clients value\n";
                usage_and_exit(argv[0]);
            }
            continue;
        }
        if (arg == "--ops") {
            if (!parse_uint(next_value("--ops"), options.operations) || options.operations == 0) {
                std::cerr << "invalid ops value\n";
                usage_and_exit(argv[0]);
            }
            continue;
        }
        if (arg == "--keys") {
            if (!parse_uint(next_value("--keys"), options.key_count) || options.key_count == 0) {
                std::cerr << "invalid keys value\n";
                usage_and_exit(argv[0]);
            }
            continue;
        }
        if (arg == "--value-size") {
            if (!parse_uint(next_value("--value-size"), options.value_size) || options.value_size == 0) {
                std::cerr << "invalid value-size value\n";
                usage_and_exit(argv[0]);
            }
            continue;
        }
        if (arg == "--get-ratio") {
            if (!parse_double(next_value("--get-ratio"), options.get_ratio) || options.get_ratio < 0.0 || options.get_ratio > 1.0) {
                std::cerr << "invalid get-ratio value\n";
                usage_and_exit(argv[0]);
            }
            continue;
        }
        if (arg == "--zipf-s") {
            if (!parse_double(next_value("--zipf-s"), options.zipf_s) || options.zipf_s < 0.0) {
                std::cerr << "invalid zipf-s value\n";
                usage_and_exit(argv[0]);
            }
            continue;
        }
        if (arg == "--warmup") {
            if (!parse_uint(next_value("--warmup"), options.warmup)) {
                std::cerr << "invalid warmup value\n";
                usage_and_exit(argv[0]);
            }
            continue;
        }
        if (arg == "--no-prefill") {
            options.prefill = false;
            continue;
        }
        if (arg == "--csv") {
            options.csv_path = next_value("--csv");
            continue;
        }

        std::cerr << "unknown argument: " << arg << '\n';
        usage_and_exit(argv[0]);
    }

    return options;
}

// Generate a stable synthetic key name for a given index.
std::string make_key(std::size_t index) {
    // Zero-pad keys so lexical order roughly matches numeric order.
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "key_%08zu", index);
    return std::string(buffer);
}

// Generate a deterministic value payload of the requested size.
std::string make_value(std::size_t size, std::size_t seed) {
    // Fill the string with repeating letters so the payload is easy to recognize.
    std::string value(size, 'x');
    for (std::size_t i = 0; i < size; ++i) {
        // The seed makes each value distinct without relying on randomness.
        value[i] = static_cast<char>('a' + ((seed + i) % 26));
    }
    return value;
}

// Build a cumulative distribution function for Zipfian key selection.
std::vector<double> build_zipf_cdf(std::size_t key_count, double s) {
    // The resulting vector maps random samples in [0,1] to key indices.
    std::vector<double> cdf(key_count, 0.0);
    if (key_count == 0) {
        return cdf;
    }

    // First compute the normalization constant for the Zipf distribution.
    double total = 0.0;
    for (std::size_t i = 0; i < key_count; ++i) {
        total += 1.0 / std::pow(static_cast<double>(i + 1), s);
    }

    // Then turn the probabilities into a monotonic cumulative distribution.
    double cumulative = 0.0;
    for (std::size_t i = 0; i < key_count; ++i) {
        cumulative += (1.0 / std::pow(static_cast<double>(i + 1), s)) / total;
        cdf[i] = cumulative;
    }

    // Force the final entry to exactly 1.0 to guard against floating-point drift.
    cdf.back() = 1.0;
    return cdf;
}

// Sample a key index from the Zipf CDF using inverse transform sampling.
std::size_t sample_key(std::mt19937_64& rng, const std::vector<double>& zipf_cdf) {
    if (zipf_cdf.empty()) {
        return 0;
    }

    // Draw a uniform sample and locate the first CDF entry that exceeds it.
    std::uniform_real_distribution<double> dist(0.0, 1.0);
    const double sample = dist(rng);
    auto it = std::lower_bound(zipf_cdf.begin(), zipf_cdf.end(), sample);
    if (it == zipf_cdf.end()) {
        return zipf_cdf.size() - 1;
    }
    return static_cast<std::size_t>(std::distance(zipf_cdf.begin(), it));
}

// Send one command and wait for one line of response.
bool request_response(int fd, std::string_view command, std::string& response) {
    if (!net::write_all(fd, std::string(command) + "\n")) {
        return false;
    }
    return net::read_line(fd, response);
}

// Check whether a response begins with the expected prefix.
bool response_starts_with(const std::string& response, std::string_view prefix) {
    return response.size() >= prefix.size() &&
        std::equal(prefix.begin(), prefix.end(), response.begin());
}

// Append a single benchmark result row to a CSV file, creating the header if needed.
void write_csv_row(
    const std::string& path,
    const Options& options,
    double elapsed_seconds,
    double throughput,
    double mean_latency,
    double p50_latency,
    double p95_latency,
    double p99_latency,
    std::size_t measured_ok_responses,
    std::size_t measured_errors,
    std::size_t warmup_ok_responses,
    std::size_t warmup_errors
) {
    // Probe the file first so we know whether to emit the CSV header.
    std::ifstream probe(path);
    const bool write_header = !probe.good() || probe.peek() == std::ifstream::traits_type::eof();
    // Open the output in append mode so repeated runs build up a table.
    std::ofstream out(path, std::ios::app);
    if (!out) {
        std::cerr << "failed to open csv output: " << path << '\n';
        std::exit(EXIT_FAILURE);
    }

    if (write_header) {
        // Keep the column order stable so plotting scripts can rely on it.
        out << "host,port,clients,operations,keys,value_size,get_ratio,zipf_s,warmup,prefill,elapsed_seconds,throughput_rps,mean_latency_us,p50_latency_us,p95_latency_us,p99_latency_us,measured_ok_responses,measured_errors,warmup_ok_responses,warmup_errors\n";
    }

    // Emit one row with all control knobs and all measured metrics.
    out << options.host << ','
        << options.port << ','
        << options.clients << ','
        << options.operations << ','
        << options.key_count << ','
        << options.value_size << ','
        << options.get_ratio << ','
        << options.zipf_s << ','
        << options.warmup << ','
        << (options.prefill ? 1 : 0) << ','
        << elapsed_seconds << ','
        << throughput << ','
        << mean_latency << ','
        << p50_latency << ','
        << p95_latency << ','
        << p99_latency << ','
        << measured_ok_responses << ','
        << measured_errors << ','
        << warmup_ok_responses << ','
        << warmup_errors << '\n';
}

// Run one client thread worth of operations against the server.
Result run_worker(
    const Options& options,
    const std::vector<std::string>& keys,
    const std::vector<double>& zipf_cdf,
    std::size_t worker_index,
    std::size_t worker_ops,
    std::size_t warmup_ops
) {
    // Each worker collects its own samples to avoid contention on shared counters.
    Result result;
    result.latencies_us.reserve(worker_ops);

    // Open one TCP connection for this worker.
    int fd = net::create_client_socket(options.host, options.port);
    if (fd < 0) {
        std::cerr << "failed to connect worker " << worker_index << '\n';
        result.measured_errors += worker_ops;
        return result;
    }

    // Seed the RNG deterministically so benchmark runs are reproducible.
    std::mt19937_64 rng(0xC0FFEEULL + worker_index * 9973ULL);
    // One distribution decides whether each request is a GET or SET.
    std::uniform_real_distribution<double> ratio_dist(0.0, 1.0);
    // Uniform fallback when Zipf skew is disabled.
    std::uniform_int_distribution<std::size_t> uniform_key_dist(0, keys.size() - 1);

    // run_phase executes either warmup or measured work.
    auto run_phase = [&](std::size_t count, bool measure) {
        for (std::size_t i = 0; i < count; ++i) {
            // Decide request type from the configured get_ratio.
            const bool do_get = ratio_dist(rng) < options.get_ratio;
            // Choose a key index using Zipf or uniform selection.
            const std::size_t key_index = options.zipf_s > 0.0 ? sample_key(rng, zipf_cdf) : uniform_key_dist(rng);
            // Reference the chosen key without copying.
            const std::string& key = keys[key_index];
            // Build the textual request command.
            std::string command;
            if (do_get) {
                command = "GET " + key;
            } else {
                command = "SET " + key + " " + make_value(options.value_size, worker_index * 1315423911ULL + i);
            }

            // Time the request as seen by the client thread.
            const auto start = Clock::now();
            std::string response;
            const bool ok = request_response(fd, command, response);
            const auto end = Clock::now();

            if (!ok) {
                // Transport failures count as errors in the active phase.
                if (measure) {
                    ++result.measured_errors;
                } else {
                    ++result.warmup_errors;
                }
                continue;
            }

            // Validate GET responses carefully because they can legitimately be VALUE or NOT_FOUND.
            if (do_get) {
                if (!response_starts_with(response, "VALUE ") && response != "NOT_FOUND") {
                    if (measure) {
                        ++result.measured_errors;
                    } else {
                        ++result.warmup_errors;
                    }
                } else {
                    if (measure) {
                        ++result.measured_ok_responses;
                    } else {
                        ++result.warmup_ok_responses;
                    }
                }
            } else {
                // SET requests must return a plain OK line.
                if (response != "OK") {
                    if (measure) {
                        ++result.measured_errors;
                    } else {
                        ++result.warmup_errors;
                    }
                } else {
                    if (measure) {
                        ++result.measured_ok_responses;
                    } else {
                        ++result.warmup_ok_responses;
                    }
                }
            }

            if (measure) {
                // Only measured-phase latencies should affect the graphs.
                result.latencies_us.push_back(
                    std::chrono::duration_cast<Microseconds>(end - start).count()
                );
            }
        }
    };

    // Burn through warmup operations before gathering statistics.
    run_phase(warmup_ops, false);
    // Execute the measured workload after warmup completes.
    run_phase(worker_ops, true);

    // Close the connection before returning to the caller.
    net::close_socket(fd);
    return result;
}

}  // namespace

// Parse the benchmark configuration, optionally prefill the store, then run all workers and summarize the results.
int main(int argc, char* argv[]) {
    // Translate command-line arguments into a structured Options object.
    const Options options = parse_args(argc, argv);

    // A benchmark with more client threads than total operations would not be meaningful.
    if (options.clients > options.operations) {
        std::cerr << "clients cannot exceed total operations\n";
        return EXIT_FAILURE;
    }

    // Precompute the keys so each worker can reuse them without string formatting overhead.
    std::vector<std::string> keys;
    keys.reserve(options.key_count);
    for (std::size_t i = 0; i < options.key_count; ++i) {
        keys.push_back(make_key(i));
    }

    // Build the Zipf sampler once and reuse it across workers.
    const std::vector<double> zipf_cdf = build_zipf_cdf(options.key_count, options.zipf_s);

    // Prefill ensures GETs have something to return before the measured phase begins.
    if (options.prefill) {
        // Use a dedicated connection so prefill traffic does not interfere with measured workers.
        int fd = net::create_client_socket(options.host, options.port);
        if (fd < 0) {
            std::cerr << "failed to connect for prefill\n";
            return EXIT_FAILURE;
        }

        // Populate every key with a deterministic initial value.
        for (std::size_t i = 0; i < keys.size(); ++i) {
            std::string response;
            const std::string command = "SET " + keys[i] + " " + make_value(options.value_size, i);
            if (!request_response(fd, command, response) || response != "OK") {
                std::cerr << "prefill failed for key " << keys[i] << '\n';
                net::close_socket(fd);
                return EXIT_FAILURE;
            }
        }

        net::close_socket(fd);
    }

    // Split the requested operations evenly across workers, distributing leftovers to the first few threads.
    const std::size_t base_ops = options.operations / options.clients;
    const std::size_t remainder = options.operations % options.clients;
    // Warmup is split the same way to keep per-thread behavior balanced.
    const std::size_t warmup_base = options.warmup / options.clients;
    const std::size_t warmup_remainder = options.warmup % options.clients;

    // Spawn one worker thread per client so the benchmark can drive concurrency.
    std::vector<std::thread> workers;
    // Each worker deposits its measurements into its own slot.
    std::vector<Result> results(options.clients);
    workers.reserve(options.clients);

    // Start the wall-clock timer for the measured phase.
    const auto start = Clock::now();
    for (std::size_t i = 0; i < options.clients; ++i) {
        // Give the early workers one extra operation if the division is uneven.
        const std::size_t worker_ops = base_ops + (i < remainder ? 1 : 0);
        // Give the early workers one extra warmup operation if needed.
        const std::size_t worker_warmup = warmup_base + (i < warmup_remainder ? 1 : 0);

        // Launch the worker lambda with copies of the per-thread operation counts.
        workers.emplace_back([&, i, worker_ops, worker_warmup]() {
            results[i] = run_worker(options, keys, zipf_cdf, i, worker_ops, worker_warmup);
        });
    }

    // Wait until every worker has finished before computing global statistics.
    for (auto& worker : workers) {
        worker.join();
    }
    // End the benchmark timer after all work is complete.
    const auto end = Clock::now();

    // Merge all worker-local measurements into aggregate summary vectors and counters.
    std::vector<std::int64_t> latencies;
    std::size_t measured_ok_responses = 0;
    std::size_t measured_errors = 0;
    std::size_t warmup_ok_responses = 0;
    std::size_t warmup_errors = 0;

    for (const Result& result : results) {
        // Add up each worker's success and error counts.
        measured_ok_responses += result.measured_ok_responses;
        measured_errors += result.measured_errors;
        warmup_ok_responses += result.warmup_ok_responses;
        warmup_errors += result.warmup_errors;
        // Concatenate all measured latency samples into one vector for percentile computation.
        latencies.insert(latencies.end(), result.latencies_us.begin(), result.latencies_us.end());
    }

    // Sort latencies so percentile lookups become simple index operations.
    std::sort(latencies.begin(), latencies.end());

    // Convert the elapsed wall-clock interval into seconds for throughput calculations.
    const double elapsed_seconds = std::chrono::duration_cast<std::chrono::duration<double>>(end - start).count();
    // Throughput is measured operations divided by elapsed time.
    const double throughput = static_cast<double>(options.operations) / elapsed_seconds;
    // Mean latency is the average measured operation time.
    const auto mean_latency = latencies.empty()
        ? 0.0
        : static_cast<double>(std::accumulate(latencies.begin(), latencies.end(), std::int64_t{0})) / latencies.size();

    // Helper for computing percentile values from the sorted latency vector.
    auto percentile = [&](double fraction) -> double {
        if (latencies.empty()) {
            return 0.0;
        }
        // Clamp the computed index so floating-point rounding cannot step past the end.
        const std::size_t index = std::min<std::size_t>(
            latencies.size() - 1,
            static_cast<std::size_t>(std::floor(fraction * static_cast<double>(latencies.size() - 1)))
        );
        return static_cast<double>(latencies[index]);
    };

    // Print the human-readable summary first so a terminal run is self-explanatory.
    std::cout << "Benchmark summary\n";
    std::cout << "  host: " << options.host << ':' << options.port << '\n';
    std::cout << "  clients: " << options.clients << '\n';
    std::cout << "  operations: " << options.operations << '\n';
    std::cout << "  keys: " << options.key_count << '\n';
    std::cout << "  get_ratio: " << options.get_ratio << '\n';
    std::cout << "  zipf_s: " << options.zipf_s << '\n';
    std::cout << "  throughput_rps: " << throughput << '\n';
    std::cout << "  mean_latency_us: " << mean_latency << '\n';
    std::cout << "  p50_latency_us: " << percentile(0.50) << '\n';
    std::cout << "  p95_latency_us: " << percentile(0.95) << '\n';
    std::cout << "  p99_latency_us: " << percentile(0.99) << '\n';
    std::cout << "  measured_ok_responses: " << measured_ok_responses << '\n';
    std::cout << "  measured_errors: " << measured_errors << '\n';
    std::cout << "  warmup_ok_responses: " << warmup_ok_responses << '\n';
    std::cout << "  warmup_errors: " << warmup_errors << '\n';

    // Optionally write the same data in machine-friendly CSV form for plotting.
    if (!options.csv_path.empty()) {
        write_csv_row(
            options.csv_path,
            options,
            elapsed_seconds,
            throughput,
            mean_latency,
            percentile(0.50),
            percentile(0.95),
            percentile(0.99),
            measured_ok_responses,
            measured_errors,
            warmup_ok_responses,
            warmup_errors
        );
    }

    // Report failure if any request, warmup or measured, produced an error.
    return (measured_errors + warmup_errors) == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
