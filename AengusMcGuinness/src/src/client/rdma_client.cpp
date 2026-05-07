/**
 * @file rdma_client.cpp
 * @brief Interactive and benchmark RDMA key-value client.
 * @ingroup executables
 *
 * Supports two operational modes (`--mode`) and two execution styles
 * (interactive vs. `--benchmark`):
 *
 * - `--mode two-sided`: use RDMA send/receive.  Interactive by default; add
 *   `--benchmark` for throughput and latency sweeps.
 * - `--mode one-sided`: use RDMA READ/WRITE directly against the server's
 *   exported `kvstore::RdmaStore`.  In benchmark mode this runs GET-only
 *   reads, optionally with one `FETCH_AND_ADD` metadata atomic after each read.
 *
 * The benchmark CSV schema intentionally matches the TCP benchmark so plotting
 * scripts can compare transports on common axes.
 */

#include "kvstore/rdma_store.hpp"
#include "net/rdma_context.hpp"
#include "protocol/text_protocol.hpp"

#include <algorithm>
#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <netdb.h>
#include <numeric>
#include <random>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <vector>

using Clock        = std::chrono::steady_clock;
using Microseconds = std::chrono::microseconds;

namespace {

// ---------------------------------------------------------------------------
// TCP helpers for the QP-info handshake
// ---------------------------------------------------------------------------

int tcp_connect(const char* host, uint16_t port) {
    addrinfo hints    = {};
    hints.ai_family   = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    addrinfo*   res      = nullptr;
    std::string port_str = std::to_string(port);
    if (::getaddrinfo(host, port_str.c_str(), &hints, &res) != 0) return -1;

    int fd = -1;
    for (addrinfo* p = res; p; p = p->ai_next) {
        fd = ::socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        if (::connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        ::close(fd);
        fd = -1;
    }
    ::freeaddrinfo(res);
    return fd;
}

bool write_all(int fd, const void* buf, std::size_t len) {
    const char* p = static_cast<const char*>(buf);
    while (len > 0) {
        ssize_t n = ::write(fd, p, len);
        if (n <= 0) { if (n < 0 && errno == EINTR) continue; return false; }
        p += n; len -= static_cast<std::size_t>(n);
    }
    return true;
}

bool read_all(int fd, void* buf, std::size_t len) {
    char* p = static_cast<char*>(buf);
    while (len > 0) {
        ssize_t n = ::read(fd, p, len);
        if (n <= 0) { if (n < 0 && errno == EINTR) continue; return false; }
        p += n; len -= static_cast<std::size_t>(n);
    }
    return true;
}

bool connect_rdma_session(const char* host,
                          uint16_t    port,
                          const char* device_name,
                          net::RdmaContext& ctx,
                          net::QpInfo* server_info_out = nullptr)
{
    int tcp_fd = tcp_connect(host, port);
    if (tcp_fd < 0) {
        std::cerr << "kv_client_rdma: cannot connect to " << host << ':' << port << '\n';
        return false;
    }

    if (!ctx.init(device_name)) {
        ::close(tcp_fd);
        return false;
    }

    net::QpInfo server_info = {};
    net::QpInfo local       = ctx.local_info();
    if (!read_all(tcp_fd, &server_info, sizeof(server_info)) ||
        !write_all(tcp_fd, &local,      sizeof(local))) {
        std::cerr << "kv_client_rdma: QP info exchange failed\n";
        ::close(tcp_fd);
        return false;
    }
    ::close(tcp_fd);

    if (!ctx.connect(server_info)) {
        std::cerr << "kv_client_rdma: QP connect failed\n";
        return false;
    }

    if (server_info_out) {
        *server_info_out = server_info;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

struct BenchmarkResult {
    std::vector<int64_t> latencies_us;
    std::size_t measured_ok_responses = 0;
    std::size_t measured_errors = 0;
    std::size_t warmup_ok_responses = 0;
    std::size_t warmup_errors = 0;
};

// FNV-1a hash -- must match RdmaStore::slot_index exactly so the client
// computes the same slot index as the server without any server involvement.
std::size_t fnv1a(std::string_view key) {
    uint32_t hash = 2166136261u;
    for (unsigned char c : key) {
        hash ^= c;
        hash *= 16777619u;
    }
    return static_cast<std::size_t>(hash) & (kvstore::RDMA_NUM_SLOTS - 1);
}

// Produce the same key strings the server uses when preloading the store.
// rdma_main.cpp preloads with "key" + std::to_string(i).
std::string make_key(std::size_t i) {
    return "key" + std::to_string(i);
}

std::string make_benchmark_key(std::size_t index) {
    char buffer[32];
    std::snprintf(buffer, sizeof(buffer), "key_%08zu", index);
    return std::string(buffer);
}

std::string make_value(std::size_t size, std::size_t seed) {
    std::string value(size, 'x');
    for (std::size_t i = 0; i < size; ++i) {
        value[i] = static_cast<char>('a' + ((seed + i) % 26));
    }
    return value;
}

std::vector<double> build_zipf_cdf(std::size_t key_count, double s) {
    std::vector<double> cdf(key_count, 0.0);
    if (key_count == 0) return cdf;

    double total = 0.0;
    for (std::size_t i = 0; i < key_count; ++i) {
        total += 1.0 / std::pow(static_cast<double>(i + 1), s);
    }

    double cumulative = 0.0;
    for (std::size_t i = 0; i < key_count; ++i) {
        cumulative += (1.0 / std::pow(static_cast<double>(i + 1), s)) / total;
        cdf[i] = cumulative;
    }
    cdf.back() = 1.0;
    return cdf;
}

std::size_t sample_key(std::mt19937_64& rng, const std::vector<double>& zipf_cdf) {
    if (zipf_cdf.empty()) return 0;

    std::uniform_real_distribution<double> dist(0.0, 1.0);
    const double sample = dist(rng);
    auto it = std::lower_bound(zipf_cdf.begin(), zipf_cdf.end(), sample);
    if (it == zipf_cdf.end()) return zipf_cdf.size() - 1;
    return static_cast<std::size_t>(std::distance(zipf_cdf.begin(), it));
}

bool response_starts_with(const std::string& response, std::string_view prefix) {
    return response.size() >= prefix.size() &&
           std::equal(prefix.begin(), prefix.end(), response.begin());
}

bool slot_key_equals(const char* slot_key, std::string_view key) {
    std::size_t slot_len = 0;
    while (slot_len < kvstore::RDMA_KEY_MAX && slot_key[slot_len] != '\0') {
        ++slot_len;
    }
    return slot_len == key.size() &&
           std::memcmp(slot_key, key.data(), key.size()) == 0;
}

// Perform one complete one-sided GET via RDMA READs.
// slot_lkey and atomic_lkey must be the lkeys of MRs registered by the caller
// that cover local_slot and atomic_result respectively.  Both MRs must remain
// registered for the duration of this call (and until poll_completion returns).
bool one_sided_get(net::RdmaContext&      ctx,
                   const net::QpInfo&     server_info,
                   kvstore::RdmaSlot&     local_slot,
                   uint32_t               slot_lkey,
                   std::string_view       key,
                   bool                   simulate_metadata,
                   uint64_t&              atomic_result,
                   uint32_t               atomic_lkey)
{
    for (std::size_t i = 0; i < kvstore::RDMA_NUM_SLOTS; ++i) {
        std::size_t idx    = (fnv1a(key) + i) & (kvstore::RDMA_NUM_SLOTS - 1);
        uint64_t    offset = static_cast<uint64_t>(idx) * sizeof(kvstore::RdmaSlot);
        uint64_t    addr   = server_info.addr + offset;

        // Post the RDMA READ and poll its completion before inspecting the slot.
        // The MR (slot_lkey) must remain registered across both calls.
        if (!ctx.post_rdma_read(addr, server_info.rkey,
                                &local_slot, sizeof(local_slot), slot_lkey))
            return false;
        if (!ctx.poll_completion()) return false;

        if (local_slot.occupied == 0) return false; // empty slot: key absent
        if (local_slot.occupied == 2) continue;     // tombstone: keep probing

        if (slot_key_equals(local_slot.key, key)) {
            if (simulate_metadata) {
                // RDMA FETCH_AND_ADD on access_count simulates LRU bookkeeping.
                // The server NIC performs the atomic; the server CPU stays idle.
                uint64_t count_addr = addr + offsetof(kvstore::RdmaSlot, access_count);
                if (!ctx.post_fetch_and_add(count_addr, server_info.rkey,
                                            &atomic_result, 1, atomic_lkey)) {
                    return false;
                }
                if (!ctx.poll_completion()) return false;
            }
            return true;
        }
    }
    return false;
}

bool prepare_slot(kvstore::RdmaSlot& slot,
                  std::string_view   key,
                  std::string_view   value,
                  uint64_t           access_count,
                  std::string&       error)
{
    if (key.size() >= kvstore::RDMA_KEY_MAX) {
        error = "key too long";
        return false;
    }
    if (value.size() >= kvstore::RDMA_VALUE_MAX) {
        error = "value too long";
        return false;
    }

    std::memset(&slot, 0, sizeof(slot));
    slot.occupied     = 1;
    slot.access_count = access_count;
    std::memcpy(slot.key,   key.data(),   key.size());
    std::memcpy(slot.value, value.data(), value.size());
    return true;
}

// Perform one complete one-sided SET: probe the remote slot array with RDMA
// READs, then write a complete RdmaSlot into either the existing key's slot,
// the first tombstone, or the first empty slot in the probe chain.
// This preserves the report's single-writer assumption for one-sided SETs;
// concurrent writers can still race at slot granularity.
bool one_sided_set(net::RdmaContext&  ctx,
                   const net::QpInfo& server_info,
                   kvstore::RdmaSlot& local_slot,
                   uint32_t           slot_lkey,
                   std::string_view   key,
                   std::string_view   value,
                   std::string&       error)
{
    if (key.size() >= kvstore::RDMA_KEY_MAX) {
        error = "key too long";
        return false;
    }
    if (value.size() >= kvstore::RDMA_VALUE_MAX) {
        error = "value too long";
        return false;
    }

    const std::size_t start = fnv1a(key);
    bool     saw_tombstone        = false;
    uint64_t first_tombstone_addr = 0;

    for (std::size_t i = 0; i < kvstore::RDMA_NUM_SLOTS; ++i) {
        std::size_t idx    = (start + i) & (kvstore::RDMA_NUM_SLOTS - 1);
        uint64_t    offset = static_cast<uint64_t>(idx) * sizeof(kvstore::RdmaSlot);
        uint64_t    addr   = server_info.addr + offset;

        if (!ctx.post_rdma_read(addr, server_info.rkey,
                                &local_slot, sizeof(local_slot), slot_lkey)) {
            error = "RDMA READ failed while probing";
            return false;
        }
        if (!ctx.poll_completion()) {
            error = "RDMA READ completion failed while probing";
            return false;
        }

        if (local_slot.occupied == 1 && slot_key_equals(local_slot.key, key)) {
            const uint64_t access_count = local_slot.access_count;
            if (!prepare_slot(local_slot, key, value, access_count, error)) return false;

            if (!ctx.post_rdma_write(addr, server_info.rkey,
                                     &local_slot, sizeof(local_slot), slot_lkey)) {
                error = "RDMA WRITE failed";
                return false;
            }
            if (!ctx.poll_completion()) {
                error = "RDMA WRITE completion failed";
                return false;
            }
            return true;
        }

        if (local_slot.occupied == 2 && !saw_tombstone) {
            saw_tombstone        = true;
            first_tombstone_addr = addr;
            continue;
        }

        if (local_slot.occupied == 0) {
            const uint64_t target_addr = saw_tombstone ? first_tombstone_addr : addr;
            if (!prepare_slot(local_slot, key, value, 0, error)) return false;

            if (!ctx.post_rdma_write(target_addr, server_info.rkey,
                                     &local_slot, sizeof(local_slot), slot_lkey)) {
                error = "RDMA WRITE failed";
                return false;
            }
            if (!ctx.poll_completion()) {
                error = "RDMA WRITE completion failed";
                return false;
            }
            return true;
        }
    }

    if (saw_tombstone) {
        if (!prepare_slot(local_slot, key, value, 0, error)) return false;
        if (!ctx.post_rdma_write(first_tombstone_addr, server_info.rkey,
                                 &local_slot, sizeof(local_slot), slot_lkey)) {
            error = "RDMA WRITE failed";
            return false;
        }
        if (!ctx.poll_completion()) {
            error = "RDMA WRITE completion failed";
            return false;
        }
        return true;
    }

    error = "remote table full";
    return false;
}

// ---------------------------------------------------------------------------
// CSV output (same column layout as kv_benchmark for plot_benchmark.py)
// ---------------------------------------------------------------------------

void write_csv_row(const std::string& path,
                   const char*        host,
                   uint16_t           port,
                   std::size_t        clients,
                   std::size_t        ops,
                   std::size_t        key_count,
                   std::size_t        warmup,
                   bool               metadata,
                   double             elapsed_s,
                   double             throughput,
                   double             mean_us,
                   double             p50_us,
                   double             p95_us,
                   double             p99_us,
                   std::size_t        ok_ops,
                   std::size_t        errors,
                   std::size_t        warmup_ok,
                   std::size_t        warmup_errors)
{
    std::ifstream probe(path);
    bool write_header = !probe.good() ||
                        probe.peek() == std::ifstream::traits_type::eof();

    std::ofstream out(path, std::ios::app);
    if (!out) {
        std::cerr << "kv_client_rdma: cannot open csv: " << path << '\n';
        return;
    }

    if (write_header) {
        out << "host,port,clients,operations,keys,value_size,get_ratio,zipf_s,"
               "warmup,prefill,elapsed_seconds,throughput_rps,mean_latency_us,"
               "p50_latency_us,p95_latency_us,p99_latency_us,"
               "measured_ok_responses,measured_errors,"
               "warmup_ok_responses,warmup_errors\n";
    }

    // Fixed fields for one-sided RDMA: pure GET, no zipf skew.
    out << host           << ','   // host
        << port           << ','   // port
        << clients        << ','   // clients
        << ops            << ','   // operations
        << key_count      << ','   // keys
        << 0              << ','   // value_size (not tracked in one-sided path)
        << 1.0            << ','   // get_ratio (always 1.0 -- pure GET)
        << 0.0            << ','   // zipf_s (uniform random key selection)
        << warmup         << ','   // warmup
        << 1              << ','   // prefill (server always preloads)
        << elapsed_s      << ','
        << throughput     << ','
        << mean_us        << ','
        << p50_us         << ','
        << p95_us         << ','
        << p99_us         << ','
        << ok_ops         << ','   // measured_ok_responses
        << errors         << ','   // measured_errors
        << warmup_ok      << ','   // warmup_ok_responses
        << warmup_errors  << '\n'; // warmup_errors
}

void write_benchmark_csv_row(const std::string& path,
                             const char*        host,
                             uint16_t           port,
                             std::size_t        clients,
                             std::size_t        ops,
                             std::size_t        key_count,
                             std::size_t        value_size,
                             double             get_ratio,
                             double             zipf_s,
                             std::size_t        warmup,
                             bool               prefill,
                             double             elapsed_s,
                             double             throughput,
                             double             mean_us,
                             double             p50_us,
                             double             p95_us,
                             double             p99_us,
                             std::size_t        measured_ok,
                             std::size_t        measured_errors,
                             std::size_t        warmup_ok,
                             std::size_t        warmup_errors)
{
    std::ifstream probe(path);
    bool write_header = !probe.good() ||
                        probe.peek() == std::ifstream::traits_type::eof();

    std::ofstream out(path, std::ios::app);
    if (!out) {
        std::cerr << "kv_client_rdma: cannot open csv: " << path << '\n';
        return;
    }

    if (write_header) {
        out << "host,port,clients,operations,keys,value_size,get_ratio,zipf_s,"
               "warmup,prefill,elapsed_seconds,throughput_rps,mean_latency_us,"
               "p50_latency_us,p95_latency_us,p99_latency_us,"
               "measured_ok_responses,measured_errors,"
               "warmup_ok_responses,warmup_errors\n";
    }

    out << host << ','
        << port << ','
        << clients << ','
        << ops << ','
        << key_count << ','
        << value_size << ','
        << get_ratio << ','
        << zipf_s << ','
        << warmup << ','
        << (prefill ? 1 : 0) << ','
        << elapsed_s << ','
        << throughput << ','
        << mean_us << ','
        << p50_us << ','
        << p95_us << ','
        << p99_us << ','
        << measured_ok << ','
        << measured_errors << ','
        << warmup_ok << ','
        << warmup_errors << '\n';
}

bool two_sided_request_response(net::RdmaContext& ctx,
                                std::string_view  command,
                                std::string&      response)
{
    std::string payload(command);
    payload.push_back('\n');

    if (!ctx.post_recv()) return false;
    if (!ctx.post_send(payload)) return false;
    if (!ctx.poll_completion()) return false;
    if (!ctx.poll_completion()) return false;

    std::string_view received = ctx.recv_data();
    response.assign(received.data(), received.size());
    while (!response.empty() && (response.back() == '\n' || response.back() == '\r')) {
        response.pop_back();
    }
    return true;
}

BenchmarkResult run_two_sided_worker(const char*                      host,
                                     uint16_t                         port,
                                     const char*                      device_name,
                                     const std::vector<std::string>&  keys,
                                     const std::vector<double>&       zipf_cdf,
                                     std::size_t                      worker_index,
                                     std::size_t                      worker_ops,
                                     std::size_t                      warmup_ops,
                                     std::size_t                      value_size,
                                     double                           get_ratio,
                                     double                           zipf_s)
{
    BenchmarkResult result;
    result.latencies_us.reserve(worker_ops);

    net::RdmaContext ctx;
    if (!connect_rdma_session(host, port, device_name, ctx)) {
        result.measured_errors += worker_ops;
        result.warmup_errors += warmup_ops;
        return result;
    }

    std::mt19937_64 rng(0xC0FFEEULL + worker_index * 9973ULL);
    std::uniform_real_distribution<double> ratio_dist(0.0, 1.0);
    std::uniform_int_distribution<std::size_t> uniform_key_dist(0, keys.size() - 1);

    auto run_phase = [&](std::size_t count, bool measure) {
        for (std::size_t i = 0; i < count; ++i) {
            const bool do_get = ratio_dist(rng) < get_ratio;
            const std::size_t key_index =
                zipf_s > 0.0 ? sample_key(rng, zipf_cdf) : uniform_key_dist(rng);
            const std::string& key = keys[key_index];

            std::string command;
            if (do_get) {
                command = "GET " + key;
            } else {
                command = "SET " + key + " " +
                          make_value(value_size, worker_index * 1315423911ULL + i);
            }

            const auto start = Clock::now();
            std::string response;
            const bool ok = two_sided_request_response(ctx, command, response);
            const auto end = Clock::now();

            bool valid = false;
            if (ok && do_get) {
                valid = response_starts_with(response, "VALUE ") || response == "NOT_FOUND";
            } else if (ok) {
                valid = response == "OK";
            }

            if (measure) {
                if (valid) ++result.measured_ok_responses;
                else       ++result.measured_errors;
                result.latencies_us.push_back(
                    std::chrono::duration_cast<Microseconds>(end - start).count());
            } else {
                if (valid) ++result.warmup_ok_responses;
                else       ++result.warmup_errors;
            }
        }
    };

    run_phase(warmup_ops, false);
    run_phase(worker_ops, true);

    std::string quit_response;
    two_sided_request_response(ctx, "QUIT", quit_response);
    return result;
}

bool run_two_sided_prefill(const char*                     host,
                           uint16_t                        port,
                           const char*                     device_name,
                           const std::vector<std::string>& keys,
                           std::size_t                     value_size)
{
    net::RdmaContext ctx;
    if (!connect_rdma_session(host, port, device_name, ctx)) {
        return false;
    }

    for (std::size_t i = 0; i < keys.size(); ++i) {
        std::string response;
        const std::string command = "SET " + keys[i] + " " + make_value(value_size, i);
        if (!two_sided_request_response(ctx, command, response) || response != "OK") {
            std::cerr << "kv_client_rdma: two-sided prefill failed for key "
                      << keys[i] << '\n';
            return false;
        }
    }
    std::string quit_response;
    two_sided_request_response(ctx, "QUIT", quit_response);
    return true;
}

bool run_two_sided_benchmark(const char*        host,
                             uint16_t           port,
                             const char*        device_name,
                             std::size_t        clients,
                             std::size_t        ops,
                             std::size_t        warmup_count,
                             std::size_t        key_count,
                             std::size_t        value_size,
                             double             get_ratio,
                             double             zipf_s,
                             bool               prefill,
                             const std::string& csv_path)
{
    if (clients == 0 || ops == 0 || key_count == 0 || value_size == 0 ||
        value_size + 128 > net::RDMA_MSG_SIZE ||
        get_ratio < 0.0 || get_ratio > 1.0 || zipf_s < 0.0 || clients > ops) {
        std::cerr << "kv_client_rdma: invalid two-sided benchmark arguments\n";
        return false;
    }

    std::cout << "Two-sided RDMA benchmark\n"
              << "  clients=" << clients
              << "  ops=" << ops
              << "  warmup=" << warmup_count
              << "  keys=" << key_count
              << "  value_size=" << value_size
              << "  get_ratio=" << get_ratio
              << "  zipf_s=" << zipf_s << '\n';

    std::vector<std::string> keys;
    keys.reserve(key_count);
    for (std::size_t i = 0; i < key_count; ++i) {
        keys.push_back(make_benchmark_key(i));
    }
    const std::vector<double> zipf_cdf = build_zipf_cdf(key_count, zipf_s);

    if (prefill) {
        std::cout << "  pre-filling...\n";
        if (!run_two_sided_prefill(host, port, device_name, keys, value_size)) {
            return false;
        }
    }

    const std::size_t base_ops = ops / clients;
    const std::size_t remainder = ops % clients;
    const std::size_t warmup_base = warmup_count / clients;
    const std::size_t warmup_remainder = warmup_count % clients;

    std::vector<std::thread> workers;
    std::vector<BenchmarkResult> results(clients);
    workers.reserve(clients);

    std::cout << "  measuring...\n";
    const auto wall_start = Clock::now();
    for (std::size_t i = 0; i < clients; ++i) {
        const std::size_t worker_ops = base_ops + (i < remainder ? 1 : 0);
        const std::size_t worker_warmup = warmup_base + (i < warmup_remainder ? 1 : 0);

        workers.emplace_back([&, i, worker_ops, worker_warmup]() {
            results[i] = run_two_sided_worker(host, port, device_name, keys, zipf_cdf,
                                              i, worker_ops, worker_warmup,
                                              value_size, get_ratio, zipf_s);
        });
    }

    for (auto& worker : workers) {
        worker.join();
    }
    const auto wall_end = Clock::now();

    std::vector<int64_t> latencies;
    std::size_t measured_ok = 0;
    std::size_t measured_errors = 0;
    std::size_t warmup_ok = 0;
    std::size_t warmup_errors = 0;

    for (const auto& result : results) {
        measured_ok += result.measured_ok_responses;
        measured_errors += result.measured_errors;
        warmup_ok += result.warmup_ok_responses;
        warmup_errors += result.warmup_errors;
        latencies.insert(latencies.end(), result.latencies_us.begin(), result.latencies_us.end());
    }

    std::sort(latencies.begin(), latencies.end());
    const double elapsed_s = std::chrono::duration_cast<
        std::chrono::duration<double>>(wall_end - wall_start).count();
    const double throughput = static_cast<double>(ops) / elapsed_s;
    const double mean_us = latencies.empty() ? 0.0
        : static_cast<double>(std::accumulate(latencies.begin(), latencies.end(), int64_t{0}))
          / static_cast<double>(latencies.size());

    auto percentile = [&](double frac) -> double {
        if (latencies.empty()) return 0.0;
        const std::size_t idx = std::min<std::size_t>(
            latencies.size() - 1,
            static_cast<std::size_t>(std::floor(frac * static_cast<double>(latencies.size() - 1))));
        return static_cast<double>(latencies[idx]);
    };

    const double p50 = percentile(0.50);
    const double p95 = percentile(0.95);
    const double p99 = percentile(0.99);

    std::cout << "\nResults\n"
              << "  elapsed_s:              " << elapsed_s << '\n'
              << "  throughput_rps:         " << throughput << '\n'
              << "  mean_latency_us:        " << mean_us << '\n'
              << "  p50_latency_us:         " << p50 << '\n'
              << "  p95_latency_us:         " << p95 << '\n'
              << "  p99_latency_us:         " << p99 << '\n'
              << "  measured_ok_responses:  " << measured_ok << '\n'
              << "  measured_errors:        " << measured_errors << '\n'
              << "  warmup_ok_responses:    " << warmup_ok << '\n'
              << "  warmup_errors:          " << warmup_errors << '\n';

    if (!csv_path.empty()) {
        write_benchmark_csv_row(csv_path, host, port, clients, ops, key_count,
                                value_size, get_ratio, zipf_s, warmup_count,
                                prefill, elapsed_s, throughput, mean_us, p50,
                                p95, p99, measured_ok, measured_errors,
                                warmup_ok, warmup_errors);
        std::cout << "  csv: " << csv_path << '\n';
    }

    return true;
}

// ---------------------------------------------------------------------------
// Two-sided interactive loop
// ---------------------------------------------------------------------------

void run_two_sided(net::RdmaContext& ctx) {
    std::cout << "Connected (two-sided RDMA).\n"
              << "Commands: GET <k>  SET <k> <v>  DEL <k>  QUIT\n";

    std::string line;
    while (std::cout << "> " && std::getline(std::cin, line)) {
        if (line.empty()) continue;

        if (!ctx.post_recv()) { std::cerr << "post_recv failed\n"; break; }
        if (!ctx.post_send(line + "\n")) { std::cerr << "post_send failed\n"; break; }
        if (!ctx.poll_completion()) { std::cerr << "send completion error\n"; break; }
        if (!ctx.poll_completion()) { std::cerr << "recv completion error\n"; break; }

        std::cout << ctx.recv_data() << '\n';
        if (ctx.recv_data().find("BYE") != std::string_view::npos) break;
    }
}

// ---------------------------------------------------------------------------
// One-sided interactive loop
// ---------------------------------------------------------------------------

void run_one_sided_interactive(net::RdmaContext&   ctx,
                               const net::QpInfo&  server_info)
{
    std::cout << "Connected (one-sided RDMA).\n"
              << "  rkey=0x"  << std::hex << server_info.rkey
              << "  base=0x"  << server_info.addr
              << std::dec     << "  slots=" << server_info.num_slots << '\n'
              << "Commands: GET <key>   GET_META <key>   SET <key> <value>   QUIT\n";

    kvstore::RdmaSlot local_slot = {};
    ibv_mr* slot_mr = ctx.reg_mr(&local_slot, sizeof(local_slot),
                                 IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE);
    if (!slot_mr) { std::cerr << "reg_mr failed\n"; return; }

    uint64_t atomic_result = 0;
    ibv_mr*  atomic_mr = ctx.reg_mr(&atomic_result, sizeof(atomic_result),
                                    IBV_ACCESS_LOCAL_WRITE);
    if (!atomic_mr) { ibv_dereg_mr(slot_mr); std::cerr << "reg_mr failed\n"; return; }

    std::string line;
    while (std::cout << "> " && std::getline(std::cin, line)) {
        if (line.empty()) continue;
        if (line == "QUIT") break;

        if (line.rfind("GET_META ", 0) == 0) {
            std::string key = line.substr(9);
            bool found = one_sided_get(ctx, server_info, local_slot, slot_mr->lkey,
                                       key, true,
                                       atomic_result, atomic_mr->lkey);
            if (found) {
                std::cout << "VALUE " << local_slot.value << '\n';
                std::cout << "  [access_count was " << atomic_result << "]\n";
            } else {
                std::cout << "NOT_FOUND\n";
            }
            continue;
        }

        protocol::Request req = protocol::parse_request(line);
        if (req.type == protocol::RequestType::Get) {
            bool found = one_sided_get(ctx, server_info, local_slot, slot_mr->lkey,
                                       req.key, false,
                                       atomic_result, atomic_mr->lkey);
            if (found) {
                std::cout << "VALUE " << local_slot.value << '\n';
            } else {
                std::cout << "NOT_FOUND\n";
            }
        } else if (req.type == protocol::RequestType::Set) {
            std::string error;
            bool ok = one_sided_set(ctx, server_info, local_slot, slot_mr->lkey,
                                    req.key, req.value, error);
            if (ok) {
                std::cout << "OK\n";
            } else {
                std::cout << "ERROR " << error << '\n';
            }
        } else if (req.type == protocol::RequestType::Quit) {
            break;
        } else {
            std::cout << "ERROR " << req.error << '\n';
        }
    }

    ibv_dereg_mr(slot_mr);
    ibv_dereg_mr(atomic_mr);
}

// ---------------------------------------------------------------------------
// One-sided benchmark loop
// ---------------------------------------------------------------------------

BenchmarkResult run_one_sided_worker(const char* host,
                                     uint16_t    port,
                                     const char* device_name,
                                     std::size_t worker_index,
                                     std::size_t worker_ops,
                                     std::size_t warmup_ops,
                                     std::size_t key_count,
                                     bool        simulate_metadata)
{
    BenchmarkResult result;
    result.latencies_us.reserve(worker_ops);

    net::RdmaContext ctx;
    net::QpInfo server_info = {};
    if (!connect_rdma_session(host, port, device_name, ctx, &server_info)) {
        result.measured_errors += worker_ops;
        result.warmup_errors += warmup_ops;
        return result;
    }

    if (server_info.addr == 0 ||
        server_info.num_slots != kvstore::RDMA_NUM_SLOTS) {
        std::cerr << "kv_client_rdma: server did not export the expected one-sided region\n";
        result.measured_errors += worker_ops;
        result.warmup_errors += warmup_ops;
        return result;
    }

    // Register a local slot buffer for RDMA READ destinations.
    kvstore::RdmaSlot local_slot = {};
    ibv_mr* slot_mr = ctx.reg_mr(&local_slot, sizeof(local_slot),
                                 IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE);
    if (!slot_mr) {
        std::cerr << "reg_mr(slot) failed\n";
        result.measured_errors += worker_ops;
        result.warmup_errors += warmup_ops;
        return result;
    }

    // Register an atomic result buffer for optional FETCH_AND_ADD.
    uint64_t atomic_result = 0;
    ibv_mr*  atomic_mr     = nullptr;
    if (simulate_metadata) {
        atomic_mr = ctx.reg_mr(&atomic_result, sizeof(atomic_result),
                               IBV_ACCESS_LOCAL_WRITE);
        if (!atomic_mr) {
            ibv_dereg_mr(slot_mr);
            std::cerr << "reg_mr(atomic) failed\n";
            result.measured_errors += worker_ops;
            result.warmup_errors += warmup_ops;
            return result;
        }
    }

    // Use a seeded RNG for reproducible key selection.
    std::mt19937_64 rng(0xC0FFEEULL + worker_index * 9973ULL);
    std::uniform_int_distribution<std::size_t> key_dist(0, key_count - 1);

    auto run_phase = [&](std::size_t count, bool measure) {
        for (std::size_t i = 0; i < count; ++i) {
            std::string key = make_key(key_dist(rng));

            const auto t0 = Clock::now();
            bool ok = one_sided_get(ctx, server_info, local_slot, slot_mr->lkey,
                                    key, simulate_metadata,
                                    atomic_result,
                                    atomic_mr ? atomic_mr->lkey : 0u);
            const auto t1 = Clock::now();

            if (measure) {
                if (ok) ++result.measured_ok_responses;
                else    ++result.measured_errors;
                result.latencies_us.push_back(
                    std::chrono::duration_cast<Microseconds>(t1 - t0).count());
            } else {
                if (ok) ++result.warmup_ok_responses;
                else    ++result.warmup_errors;
            }
        }
    };

    run_phase(warmup_ops, false);
    run_phase(worker_ops, true);

    if (atomic_mr) ibv_dereg_mr(atomic_mr);
    ibv_dereg_mr(slot_mr);
    return result;
}

bool run_one_sided_benchmark(const char*        host,
                             uint16_t           port,
                             const char*        device_name,
                             std::size_t        clients,
                             std::size_t        ops,
                             std::size_t        warmup_count,
                             std::size_t        key_count,
                             bool               simulate_metadata,
                             const std::string& csv_path)
{
    if (clients == 0 || ops == 0 || key_count == 0 || clients > ops) {
        std::cerr << "kv_client_rdma: invalid one-sided benchmark arguments\n";
        return false;
    }

    std::cout << "One-sided RDMA benchmark\n"
              << "  clients=" << clients
              << "  ops="     << ops
              << "  warmup="  << warmup_count
              << "  keys="    << key_count
              << "  metadata=" << (simulate_metadata ? "yes" : "no") << '\n';

    const std::size_t base_ops = ops / clients;
    const std::size_t remainder = ops % clients;
    const std::size_t warmup_base = warmup_count / clients;
    const std::size_t warmup_remainder = warmup_count % clients;

    std::vector<std::thread> workers;
    std::vector<BenchmarkResult> results(clients);
    workers.reserve(clients);

    std::cout << "  measuring...\n";
    const auto wall_start = Clock::now();
    for (std::size_t i = 0; i < clients; ++i) {
        const std::size_t worker_ops = base_ops + (i < remainder ? 1 : 0);
        const std::size_t worker_warmup = warmup_base + (i < warmup_remainder ? 1 : 0);

        workers.emplace_back([&, i, worker_ops, worker_warmup]() {
            results[i] = run_one_sided_worker(host, port, device_name,
                                              i, worker_ops, worker_warmup,
                                              key_count, simulate_metadata);
        });
    }

    for (auto& worker : workers) {
        worker.join();
    }

    const auto wall_end = Clock::now();
    double elapsed_s = std::chrono::duration_cast<
        std::chrono::duration<double>>(wall_end - wall_start).count();
    double throughput = static_cast<double>(ops) / elapsed_s;

    std::vector<int64_t> latencies;
    std::size_t ok_count = 0;
    std::size_t error_count = 0;
    std::size_t warmup_ok = 0;
    std::size_t warmup_errors = 0;

    for (const auto& worker_result : results) {
        ok_count += worker_result.measured_ok_responses;
        error_count += worker_result.measured_errors;
        warmup_ok += worker_result.warmup_ok_responses;
        warmup_errors += worker_result.warmup_errors;
        latencies.insert(latencies.end(),
                         worker_result.latencies_us.begin(),
                         worker_result.latencies_us.end());
    }

    // Compute statistics.
    std::sort(latencies.begin(), latencies.end());
    double mean_us = latencies.empty() ? 0.0
        : static_cast<double>(
              std::accumulate(latencies.begin(), latencies.end(), int64_t{0}))
          / static_cast<double>(latencies.size());

    auto percentile = [&](double frac) -> double {
        if (latencies.empty()) return 0.0;
        std::size_t idx = std::min<std::size_t>(
            latencies.size() - 1,
            static_cast<std::size_t>(frac * (latencies.size() - 1)));
        return static_cast<double>(latencies[idx]);
    };

    double p50 = percentile(0.50);
    double p95 = percentile(0.95);
    double p99 = percentile(0.99);

    // Print human-readable summary.
    std::cout << "\nResults\n"
              << "  elapsed_s:        " << elapsed_s  << '\n'
              << "  throughput_rps:   " << throughput  << '\n'
              << "  mean_latency_us:  " << mean_us     << '\n'
              << "  p50_latency_us:   " << p50         << '\n'
              << "  p95_latency_us:   " << p95         << '\n'
              << "  p99_latency_us:   " << p99         << '\n'
              << "  ok_ops:           " << ok_count    << '\n'
              << "  errors:           " << error_count << '\n'
              << "  warmup_ok:        " << warmup_ok << '\n'
              << "  warmup_errors:    " << warmup_errors << '\n';

    // Write CSV row if a path was given.
    if (!csv_path.empty()) {
        write_csv_row(csv_path, host, port, clients, ops, key_count, warmup_count,
                      simulate_metadata, elapsed_s, throughput,
                      mean_us, p50, p95, p99, ok_count, error_count,
                      warmup_ok, warmup_errors);
        std::cout << "  csv: " << csv_path << '\n';
    }

    return error_count == 0 && warmup_errors == 0;
}

} // namespace

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char* argv[]) {
    const char* host              = "localhost";
    uint16_t    port              = 9091;
    bool        one_sided         = false;
    const char* device_name       = nullptr;
    bool        benchmark_mode    = false;
    std::size_t ops               = 10000;
    std::size_t warmup_count      = 1000;
    std::size_t key_count         = 1024;
    std::size_t clients           = 1;
    std::size_t value_size        = 64;
    double      get_ratio         = 0.95;
    double      zipf_s            = 0.8;
    bool        prefill           = true;
    bool        simulate_metadata = false;
    std::string csv_path;

    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        if (arg == "--host"     && i + 1 < argc) host              = argv[++i];
        if (arg == "--port"     && i + 1 < argc) port              = static_cast<uint16_t>(std::stoi(argv[++i]));
        if (arg == "--mode"     && i + 1 < argc) one_sided         = (std::string(argv[++i]) == "one-sided");
        if (arg == "--device"   && i + 1 < argc) device_name       = argv[++i];
        if (arg == "--benchmark")                benchmark_mode    = true;
        if (arg == "--ops"      && i + 1 < argc) ops               = static_cast<std::size_t>(std::stoul(argv[++i]));
        if (arg == "--warmup"   && i + 1 < argc) warmup_count      = static_cast<std::size_t>(std::stoul(argv[++i]));
        if (arg == "--keys"     && i + 1 < argc) key_count         = static_cast<std::size_t>(std::stoul(argv[++i]));
        if (arg == "--clients"  && i + 1 < argc) clients           = static_cast<std::size_t>(std::stoul(argv[++i]));
        if (arg == "--value-size" && i + 1 < argc) value_size      = static_cast<std::size_t>(std::stoul(argv[++i]));
        if (arg == "--get-ratio" && i + 1 < argc) get_ratio        = std::stod(argv[++i]);
        if (arg == "--zipf-s"   && i + 1 < argc) zipf_s            = std::stod(argv[++i]);
        if (arg == "--no-prefill")                prefill           = false;
        if (arg == "--metadata")                 simulate_metadata = true;
        if (arg == "--csv"      && i + 1 < argc) csv_path          = argv[++i];
    }

    if (!one_sided && benchmark_mode) {
        return run_two_sided_benchmark(host, port, device_name,
                                       clients, ops, warmup_count, key_count,
                                       value_size, get_ratio, zipf_s,
                                       prefill, csv_path)
             ? EXIT_SUCCESS
             : EXIT_FAILURE;
    }

    if (one_sided && benchmark_mode) {
        return run_one_sided_benchmark(host, port, device_name,
                                       clients, ops, warmup_count, key_count,
                                       simulate_metadata, csv_path)
             ? EXIT_SUCCESS
             : EXIT_FAILURE;
    }

    net::RdmaContext ctx;
    net::QpInfo server_info = {};
    if (!connect_rdma_session(host, port, device_name, ctx, &server_info)) {
        return EXIT_FAILURE;
    }

    if (one_sided &&
        (server_info.addr == 0 ||
         server_info.num_slots != kvstore::RDMA_NUM_SLOTS)) {
        std::cerr << "kv_client_rdma: server did not export the expected one-sided region\n";
        return EXIT_FAILURE;
    }

    if (one_sided) {
        run_one_sided_interactive(ctx, server_info);
    } else {
        run_two_sided(ctx);
    }

    return EXIT_SUCCESS;
}
