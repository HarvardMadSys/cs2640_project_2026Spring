#pragma once
#include <array>
#include <cstdint>
#include <functional>  // D-09 (Plan 04-05): std::function for on_age_cb_ hook
#include <string>
#include <vector>
#include <algorithm>
#include "hash_util.h"

// Count-Min Sketch — 4-bit packed counters, depth=4, width=nextpow2(n_objects_hint).
//
// Periodic halving every 10*W accesses (W = width * depth), matching Caffeine's
// FrequencySketch.reset() cadence semantics (but in counter-operation units — see
// .planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md §3 + §6 row 2 for the
// sample_size formula deviation: our port uses 10 * width * depth where Caffeine's
// FrequencySketch.java:L96 uses 10 * maximumSize).
//
// Hash scheme: FNV-1a with four seeds from include/hash_util.h
// (FNV_SEED_A..D). One FNV-1a call per row, keyed on (seed, key_bytes). See
// 02-01-CAFFEINE-NOTES.md §5 + §6 row 6 for the deliberate deviation from
// Caffeine's single-hash-plus-SplitMix-mix pattern.
//
// Update rule: CONSERVATIVE — locked by REQUIREMENTS.md WTLFU-01 and ROADMAP.md
// §Phase 2 success-criterion 1 (both specify "conservative update" unconditionally).
// Conservative: find min across d rows, increment only rows currently at that min
// (cap at COUNTER_MAX=15). This deliberately deviates from Caffeine's observed
// behavior, which uses unconditional-all-rows increment; see 02-01-CAFFEINE-NOTES.md
// §2 + §6 row 1. The WTLFU-01 specification is authoritative; the Caffeine notes
// are reference material.
//
// Counters pack two 4-bit values per byte. Byte layout for counter at (row, col):
//   byte index  = (col >> 1)
//   nibble half = (col & 1)  // 0 -> low nibble, 1 -> high nibble
// Width is always a power of 2, so (hash & (width - 1)) picks the column.

class CountMinSketch {
public:
    static constexpr uint32_t DEPTH = 4;
    static constexpr uint8_t  COUNTER_MAX = 15;  // 4-bit saturation

    explicit CountMinSketch(uint64_t n_objects_hint) {
        // width = nextpow2(max(n_objects_hint, 1))
        uint64_t w = n_objects_hint < 1 ? 1 : n_objects_hint;
        uint64_t p = 1;
        while (p < w) p <<= 1;
        width_ = p;
        width_mask_ = width_ - 1;
        // Each row has width_ counters; pack two per byte -> (width_ + 1) / 2 bytes.
        // For width_ == 1, allocate 1 byte (holds one counter in the low nibble).
        uint64_t bytes_per_row = (width_ + 1) / 2;
        rows_.assign(DEPTH, std::vector<uint8_t>(bytes_per_row, 0));
        sample_size_ = 10ULL * width_ * DEPTH;
        sample_count_ = 0;
    }

    void record(const std::string& key) {
        std::array<uint64_t, DEPTH> cols;
        cols[0] = fnv1a_64(key, FNV_SEED_A) & width_mask_;
        cols[1] = fnv1a_64(key, FNV_SEED_B) & width_mask_;
        cols[2] = fnv1a_64(key, FNV_SEED_C) & width_mask_;
        cols[3] = fnv1a_64(key, FNV_SEED_D) & width_mask_;

        // CONSERVATIVE update per WTLFU-01: find min across all d rows,
        // then increment only the rows currently at that min (cap at COUNTER_MAX).
        // Locked by REQUIREMENTS.md WTLFU-01 — see file header for the authority
        // citation and for why this differs from Caffeine's observed behavior.
        uint8_t mn = COUNTER_MAX;
        for (uint32_t r = 0; r < DEPTH; ++r) {
            mn = std::min<uint8_t>(mn, get_counter(r, cols[r]));
        }
        if (mn < COUNTER_MAX) {
            for (uint32_t r = 0; r < DEPTH; ++r) {
                if (get_counter(r, cols[r]) == mn) {
                    set_counter(r, cols[r], static_cast<uint8_t>(mn + 1));
                }
            }
        }

        ++sample_count_;
        if (sample_count_ >= sample_size_) halve_all_();
    }

    uint32_t estimate(const std::string& key) const {
        uint8_t mn = COUNTER_MAX;
        mn = std::min<uint8_t>(mn, get_counter(0, fnv1a_64(key, FNV_SEED_A) & width_mask_));
        mn = std::min<uint8_t>(mn, get_counter(1, fnv1a_64(key, FNV_SEED_B) & width_mask_));
        mn = std::min<uint8_t>(mn, get_counter(2, fnv1a_64(key, FNV_SEED_C) & width_mask_));
        mn = std::min<uint8_t>(mn, get_counter(3, fnv1a_64(key, FNV_SEED_D) & width_mask_));
        return static_cast<uint32_t>(mn);
    }

    void reset() {
        for (auto& row : rows_) {
            std::fill(row.begin(), row.end(), static_cast<uint8_t>(0));
        }
        sample_count_ = 0;
    }

    // Test-only hook (D-10). Immediately halves all counters and resets sample_count_.
    // Used by tests/test_wtinylfu.cpp (02-04) to verify aging-cadence deterministically.
    void force_age() { halve_all_(); }

    // D-09 hook (Plan 04-05): register a callback fired at the end of halve_all_()
    // — the natural aging cadence. Default-empty std::function is a no-op (zero
    // overhead) for baseline wtinylfu (no callback registered); WTinyLFUCache
    // sets this to `[this]{ doorkeeper_.clear(); }` only when use_doorkeeper_=true
    // so the Doorkeeper bit array stays aligned with CMS freshness windows
    // (STACK.md §Doorkeeper: "aligned with the CMS so freshness is consistent").
    // Because force_age() calls halve_all_(), test code can deterministically
    // observe DK clears via the same hook.
    void set_on_age_cb(std::function<void()> cb) { on_age_cb_ = std::move(cb); }

    uint64_t width() const { return width_; }
    uint64_t sample_size() const { return sample_size_; }
    uint64_t sample_count() const { return sample_count_; }

private:
    uint64_t width_;
    uint64_t width_mask_;
    uint64_t sample_size_;
    uint64_t sample_count_;
    std::vector<std::vector<uint8_t>> rows_;  // rows_[row][byte]
    std::function<void()> on_age_cb_;  // D-09 (Plan 04-05): fired from halve_all_() if set

    uint8_t get_counter(uint32_t row, uint64_t col) const {
        uint8_t byte = rows_[row][col >> 1];
        return (col & 1u) ? static_cast<uint8_t>(byte >> 4)
                          : static_cast<uint8_t>(byte & 0x0F);
    }

    void set_counter(uint32_t row, uint64_t col, uint8_t val) {
        uint8_t& byte = rows_[row][col >> 1];
        if (col & 1u) {
            byte = static_cast<uint8_t>((byte & 0x0F) | ((val & 0x0F) << 4));
        } else {
            byte = static_cast<uint8_t>((byte & 0xF0) | (val & 0x0F));
        }
    }

    void halve_all_() {
        // For each byte, shift both nibbles right by 1 independently.
        //   Low nibble:  (b & 0x0F) >> 1
        //   High nibble: ((b >> 4) & 0x0F) >> 1
        // Combined: ((b >> 1) & 0x77) -- because b = HHHHLLLL,
        //   (b >> 1) = 0HHHHLLL, and masking with 0x77 = 0111 0111
        //   drops the cross-boundary bit (old bit 4 landing in bit 3).
        for (auto& row : rows_) {
            for (auto& byte : row) {
                byte = static_cast<uint8_t>((byte >> 1) & 0x77);
            }
        }
        sample_count_ = 0;
        // D-09 (Plan 04-05): notify subscribers of the aging event. The
        // default-empty std::function branch-predictable-skips for baseline
        // wtinylfu (no callback registered). WTinyLFUCache's Doorkeeper path
        // uses this to clear() the DK bit array in lockstep with CMS aging.
        if (on_age_cb_) on_age_cb_();
    }
};
