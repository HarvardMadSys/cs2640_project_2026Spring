#pragma once
#include <cstdint>
#include <string>
#include <unordered_map>
#include <list>
#include <vector>
#include <algorithm>
#include <unordered_set>
#include <deque>
#include <random>

struct TraceEntry {
    uint64_t timestamp;
    std::string key;
    uint64_t size; // object size in bytes
};

struct CacheStats {
    uint64_t hits = 0;
    uint64_t misses = 0;
    uint64_t byte_hits = 0;
    uint64_t byte_misses = 0;

    double miss_ratio() const {
        uint64_t total = hits + misses;
        return total == 0 ? 0.0 : (double)misses / total;
    }
    double byte_miss_ratio() const {
        uint64_t total = byte_hits + byte_misses;
        return total == 0 ? 0.0 : (double)byte_misses / total;
    }
};

// Abstract base class
class CachePolicy {
public:
    virtual ~CachePolicy() = default;
    virtual bool access(const std::string& key, uint64_t size) = 0; // returns true on hit
    virtual std::string name() const = 0;
    virtual void reset() = 0;
    
    CacheStats stats;
    
    void record(bool hit, uint64_t size) {
        if (hit) { stats.hits++; stats.byte_hits += size; }
        else     { stats.misses++; stats.byte_misses += size; }
    }
};

// ==================== LRU ====================
class LRUCache : public CachePolicy {
    uint64_t capacity_;
    uint64_t current_size_ = 0;
    std::list<std::pair<std::string, uint64_t>> order_; // key, size
    std::unordered_map<std::string, std::list<std::pair<std::string, uint64_t>>::iterator> map_;

public:
    LRUCache(uint64_t capacity) : capacity_(capacity) {}
    
    bool access(const std::string& key, uint64_t size) override {
        auto it = map_.find(key);
        if (it != map_.end()) {
            current_size_ -= it->second->second;
            it->second->second = size;
            current_size_ += size;
            order_.splice(order_.end(), order_, it->second);
            record(true, size);
            return true;
        }
        // Miss: insert
        while (current_size_ + size > capacity_ && !order_.empty()) {
            current_size_ -= order_.front().second;
            map_.erase(order_.front().first);
            order_.pop_front();
        }
        order_.push_back({key, size});
        map_[key] = std::prev(order_.end());
        current_size_ += size;
        record(false, size);
        return false;
    }
    
    std::string name() const override { return "LRU"; }
    void reset() override { order_.clear(); map_.clear(); current_size_ = 0; stats = {}; }
};

// ==================== FIFO ====================
class FIFOCache : public CachePolicy {
    uint64_t capacity_;
    uint64_t current_size_ = 0;
    std::list<std::pair<std::string, uint64_t>> queue_;
    std::unordered_map<std::string, std::list<std::pair<std::string, uint64_t>>::iterator> map_;

public:
    FIFOCache(uint64_t capacity) : capacity_(capacity) {}
    
    bool access(const std::string& key, uint64_t size) override {
        auto it = map_.find(key);
        if (it != map_.end()) {
            current_size_ -= it->second->second;
            it->second->second = size;
            current_size_ += size;
            record(true, size);
            return true;
        }
        while (current_size_ + size > capacity_ && !queue_.empty()) {
            current_size_ -= queue_.front().second;
            map_.erase(queue_.front().first);
            queue_.pop_front();
        }
        queue_.push_back({key, size});
        map_[key] = std::prev(queue_.end());
        current_size_ += size;
        record(false, size);
        return false;
    }
    
    std::string name() const override { return "FIFO"; }
    void reset() override { queue_.clear(); map_.clear(); current_size_ = 0; stats = {}; }
};

// ==================== CLOCK ====================
class CLOCKCache : public CachePolicy {
    uint64_t capacity_;
    uint64_t current_size_ = 0;
    
    struct Entry {
        std::string key;
        uint64_t size;
        bool referenced;
    };
    
    std::vector<Entry> buffer_;
    std::unordered_map<std::string, size_t> map_; // key -> index
    size_t hand_ = 0;
    size_t count_ = 0;

public:
    CLOCKCache(uint64_t capacity) : capacity_(capacity) {
        buffer_.reserve(1024);
    }
    
    bool access(const std::string& key, uint64_t size) override {
        auto it = map_.find(key);
        if (it != map_.end()) {
            current_size_ -= buffer_[it->second].size;
            buffer_[it->second].size = size;
            current_size_ += size;
            buffer_[it->second].referenced = true;
            record(true, size);
            return true;
        }
        // Evict until enough space
        while (current_size_ + size > capacity_ && count_ > 0) {
            evict_one();
        }
        // Insert
        if (count_ < buffer_.size()) {
            // Find an empty slot (from eviction)
            size_t idx = hand_;
            for (size_t i = 0; i < buffer_.size(); i++) {
                size_t pos = (hand_ + i) % buffer_.size();
                if (buffer_[pos].key.empty()) { idx = pos; break; }
            }
            buffer_[idx] = {key, size, false};
            map_[key] = idx;
        } else {
            buffer_.push_back({key, size, false});
            map_[key] = buffer_.size() - 1;
        }
        current_size_ += size;
        count_++;
        record(false, size);
        return false;
    }
    
    void evict_one() {
        while (true) {
            if (hand_ >= buffer_.size()) hand_ = 0;
            auto& e = buffer_[hand_];
            if (!e.key.empty()) {
                if (e.referenced) {
                    e.referenced = false;
                } else {
                    current_size_ -= e.size;
                    map_.erase(e.key);
                    e.key.clear();
                    count_--;
                    hand_ = (hand_ + 1) % buffer_.size();
                    return;
                }
            }
            hand_ = (hand_ + 1) % buffer_.size();
        }
    }
    
    std::string name() const override { return "CLOCK"; }
    void reset() override { buffer_.clear(); map_.clear(); hand_ = 0; count_ = 0; current_size_ = 0; stats = {}; }
};

// ==================== S3-FIFO ====================
// Small FIFO (10%) -> Main FIFO (90%), with ghost filter
// Per Yang et al. (SOSP '23): items enter small on miss (unless in ghost,
// then go directly to main). On eviction from small, items with freq > 0
// promote to main; freq == 0 items go to ghost.
class S3FIFOCache : public CachePolicy {
    uint64_t total_capacity_;
    uint64_t small_capacity_;
    uint64_t main_capacity_;
    // Phase 4 Axis C (D-11 / ABLA-01): small-queue-ratio ablation params.
    // small_frac_ records the ratio the instance was built with (0.05 / 0.10 /
    // 0.20). name_ is the display string written into CSV `policy` columns —
    // default "S3-FIFO" preserves Phase 1 back-compat; the ablation factory
    // branches in make_policy override it via the delegating ctor below.
    double small_frac_;
    std::string name_;

    // Small FIFO queue with frequency tracking
    struct SmallEntry {
        std::string key;
        uint64_t size;
        uint8_t freq; // incremented on hit while in small
    };
    std::list<SmallEntry> small_queue_;
    std::unordered_map<std::string, std::list<SmallEntry>::iterator> small_map_;
    uint64_t small_size_ = 0;

    // Main FIFO queue with frequency bits
    struct MainEntry {
        std::string key;
        uint64_t size;
        uint8_t freq; // 0-3
    };
    std::list<MainEntry> main_queue_;
    std::unordered_map<std::string, std::list<MainEntry>::iterator> main_map_;
    uint64_t main_size_ = 0;

    // Ghost filter: FIFO-ordered for correct eviction
    std::deque<std::string> ghost_queue_;
    std::unordered_set<std::string> ghost_set_;

public:
    // Phase 4 Axis C primary ctor (D-11): small_frac defaults to 0.1 to
    // preserve Phase 1 behavior. The legacy `s3fifo` make_policy branch calls
    // this with no second arg; the `capacity / 10` integer-truncation formula
    // is kept on the default-arg path so re-runs produce bit-identical CSVs.
    // The 0.05 / 0.20 ablation paths use the FP formula. name_ defaults to
    // "S3-FIFO" (the legacy display string); the delegating ctor below
    // overrides it for the ablation variants.
    S3FIFOCache(uint64_t capacity, double small_frac = 0.1)
        : total_capacity_(capacity), small_frac_(small_frac), name_("S3-FIFO") {
        small_capacity_ = std::max<uint64_t>(1,
            (small_frac == 0.1)
                ? capacity / 10
                : static_cast<uint64_t>(static_cast<double>(capacity) * small_frac));
        main_capacity_ = capacity - small_capacity_;
    }

    // Phase 4 Axis C delegating ctor (D-11): explicit-name overload used by
    // the s3fifo-5 / s3fifo-10 / s3fifo-20 branches in make_policy. Routes
    // through the primary ctor for sizing, then overrides name_ so CSV rows
    // carry the ablation display string (e.g. "S3-FIFO-10") rather than the
    // legacy "S3-FIFO" alias.
    S3FIFOCache(uint64_t capacity, double small_frac, const std::string& name_override)
        : S3FIFOCache(capacity, small_frac) {
        name_ = name_override;
    }

    // Phase 4 Axis C (D-11): test / introspection getter for the small-queue
    // ratio the instance was built with. Lets ablation-verification code
    // confirm each variant received its intended small_frac without grepping
    // CSV rows. Also consumes small_frac_ so -Wunused-private-field stays
    // clean under -Wall -Wextra.
    double small_frac() const { return small_frac_; }

    bool access(const std::string& key, uint64_t size) override {
        // Check main
        auto mit = main_map_.find(key);
        if (mit != main_map_.end()) {
            main_size_ -= mit->second->size;
            mit->second->size = size;
            main_size_ += size;
            if (mit->second->freq < 3) mit->second->freq++;
            record(true, size);
            return true;
        }
        // Check small — increment freq on hit
        auto sit = small_map_.find(key);
        if (sit != small_map_.end()) {
            small_size_ -= sit->second->size;
            sit->second->size = size;
            small_size_ += size;
            if (sit->second->freq < 3) sit->second->freq++;
            record(true, size);
            return true;
        }
        // Miss: if in ghost, insert into main; otherwise into small
        if (ghost_set_.count(key)) {
            ghost_set_.erase(key);
            // Remove from ghost queue (lazy: just leave stale entries, cleaned on trim)
            while (main_size_ + size > main_capacity_ && !main_queue_.empty()) {
                evict_main();
            }
            main_queue_.push_back({key, size, 0});
            main_map_[key] = std::prev(main_queue_.end());
            main_size_ += size;
        } else {
            while (small_size_ + size > small_capacity_ && !small_queue_.empty()) {
                evict_small();
            }
            small_queue_.push_back({key, size, 0});
            small_map_[key] = std::prev(small_queue_.end());
            small_size_ += size;
        }
        record(false, size);
        return false;
    }

    void evict_small() {
        auto& front = small_queue_.front();
        std::string key = front.key;
        uint64_t sz = front.size;
        uint8_t freq = front.freq;
        small_map_.erase(key);
        small_size_ -= sz;
        small_queue_.pop_front();

        if (freq > 0) {
            // Re-accessed while in small: promote to main
            while (main_size_ + sz > main_capacity_ && !main_queue_.empty()) {
                evict_main();
            }
            main_queue_.push_back({key, sz, 0});
            main_map_[key] = std::prev(main_queue_.end());
            main_size_ += sz;
        } else {
            // Not re-accessed: add to ghost
            ghost_set_.insert(key);
            ghost_queue_.push_back(key);
            // Trim ghost FIFO from the front (oldest first)
            size_t ghost_max = total_capacity_ / 100 + 1000;
            while (ghost_queue_.size() > ghost_max) {
                ghost_set_.erase(ghost_queue_.front());
                ghost_queue_.pop_front();
            }
        }
    }

    void evict_main() {
        while (!main_queue_.empty()) {
            auto& front = main_queue_.front();
            if (front.freq > 0) {
                front.freq--;
                main_queue_.splice(main_queue_.end(), main_queue_, main_queue_.begin());
            } else {
                main_size_ -= front.size;
                main_map_.erase(front.key);
                main_queue_.pop_front();
                return;
            }
        }
    }

    // Phase 4 Axis C (D-11): delegates to name_ member so ablation variants
    // emit "S3-FIFO-5" / "S3-FIFO-10" / "S3-FIFO-20" while the legacy
    // `s3fifo` policy keeps the default "S3-FIFO" display string.
    std::string name() const override { return name_; }
    void reset() override {
        small_queue_.clear(); small_map_.clear(); small_size_ = 0;
        main_queue_.clear(); main_map_.clear(); main_size_ = 0;
        ghost_queue_.clear(); ghost_set_.clear(); stats = {};
    }
};

// ==================== SIEVE ====================
// SIEVE: lazy promotion with a hand pointer
class SIEVECache : public CachePolicy {
    uint64_t capacity_;
    uint64_t current_size_ = 0;
    
    struct Node {
        std::string key;
        uint64_t size;
        bool visited;
    };
    
    std::list<Node> queue_;
    std::unordered_map<std::string, std::list<Node>::iterator> map_;
    std::list<Node>::iterator hand_;
    bool hand_valid_ = false;
    // Phase 4 Axis D (D-12 / ABLA-02): SIEVE visited-bit ablation. When
    // promote_on_hit_ is true (Phase 1 default), the access() hit-path sets
    // visited=true exactly as before → bit-identical to pre-Phase-4 outputs.
    // When false (the `sieve-noprom` variant), the set is skipped; every
    // entry's visited bit stays false from insertion (line 409 inserts with
    // visited=false) and evict_one()'s hand finds its first target on the
    // first sweep → SIEVE collapses to FIFO-with-hand. The gap between the
    // two variants at high skew IS the measured value of lazy promotion.
    bool promote_on_hit_;
    std::string name_;

public:
    SIEVECache(uint64_t capacity, bool promote_on_hit = true)
        : capacity_(capacity),
          promote_on_hit_(promote_on_hit),
          name_(promote_on_hit ? "SIEVE" : "SIEVE-NoProm") {}

    bool access(const std::string& key, uint64_t size) override {
        auto it = map_.find(key);
        if (it != map_.end()) {
            current_size_ -= it->second->size;
            it->second->size = size;
            current_size_ += size;
            if (promote_on_hit_) it->second->visited = true;  // D-12: guarded for SIEVE-NoProm ablation
            record(true, size);
            return true;
        }
        // Miss: evict until space
        while (current_size_ + size > capacity_ && !queue_.empty()) {
            evict_one();
        }
        // Insert at head
        queue_.push_front({key, size, false});
        map_[key] = queue_.begin();
        current_size_ += size;
        record(false, size);
        return false;
    }
    
    void evict_one() {
        if (!hand_valid_ || hand_ == queue_.end()) {
            hand_ = std::prev(queue_.end()); // tail
            hand_valid_ = true;
        }
        while (hand_->visited) {
            hand_->visited = false;
            if (hand_ == queue_.begin()) {
                hand_ = std::prev(queue_.end());
            } else {
                --hand_;
            }
        }
        auto victim = hand_;
        // Move hand before erasing
        if (hand_ == queue_.begin()) {
            hand_ = std::prev(queue_.end());
            // But if queue will be size 1, handle after erase
            if (hand_ == victim) hand_valid_ = false;
        } else {
            --hand_;
        }
        current_size_ -= victim->size;
        map_.erase(victim->key);
        queue_.erase(victim);
        if (queue_.empty()) hand_valid_ = false;
    }
    
    std::string name() const override { return name_; }
    void reset() override { queue_.clear(); map_.clear(); current_size_ = 0; hand_valid_ = false; stats = {}; }
};

// ==================== GDSF (Greedy Dual-Size-Frequency) ====================
// Cherkasova, "Improving WWW Proxies Performance with GDSF Caching Policy",
// HP Labs Tech Report, 1998. Per-key priority H = freq * cost(size) / size + L
// where L is a monotone-non-decreasing clock variable advanced on each
// eviction to the evicted item's H. We provide two cost models, selected
// at construction:
//   - kUnit     : cost(size) = 1 (the v3 paper's GDSF; size-only-aware)
//   - kEmpirical: cost(size) = 530 + 0.00254 * size_bytes (ms), the linear
//                 fit from scripts/fit_cost_vs_size.py against our two
//                 collected traces. Captures the realistic deployment shape
//                 where origin fetch latency grows with object size.
//
// Eviction is min-heap-based (O(log n) per eviction). The lazy-deletion
// pattern handles the fact that hits modify an entry's H without removing
// it from the heap; the heap may contain stale entries with obsolete H,
// which we filter at pop-time by comparing the popped H against the entry's
// current H in map_ (mismatch = stale = discard and pop again).
enum class GDSFCost { kUnit, kEmpirical };

class GDSFCache : public CachePolicy {
    uint64_t capacity_;
    uint64_t current_size_ = 0;
    double L_ = 0.0;
    GDSFCost cost_mode_;

    // Empirical fit: cost_ms = COST_INTERCEPT + COST_SLOPE_PER_BYTE * size
    static constexpr double COST_INTERCEPT_MS = 530.0;
    static constexpr double COST_SLOPE_MS_PER_BYTE = 0.0025366;

    struct Entry {
        uint64_t size;
        uint64_t freq;
        double H;
    };
    std::unordered_map<std::string, Entry> map_;

    // Min-heap of (H, key). May contain stale entries with H != map_[key].H;
    // pop_min() lazily discards stale entries.
    struct HeapNode {
        double H;
        std::string key;
        bool operator>(const HeapNode& o) const { return H > o.H; }
    };
    std::vector<HeapNode> heap_;

    double cost_for_size(uint64_t size) const {
        if (cost_mode_ == GDSFCost::kUnit) return 1.0;
        return COST_INTERCEPT_MS + COST_SLOPE_MS_PER_BYTE * (double)size;
    }

    void heap_push(double H, const std::string& key) {
        heap_.push_back({H, key});
        std::push_heap(heap_.begin(), heap_.end(), std::greater<HeapNode>{});
    }

    // Pop until top is non-stale, then return the popped (H, key).
    // Returns true if found a valid victim, false if heap empty.
    bool heap_pop_valid(double& out_H, std::string& out_key) {
        while (!heap_.empty()) {
            std::pop_heap(heap_.begin(), heap_.end(), std::greater<HeapNode>{});
            HeapNode top = heap_.back();
            heap_.pop_back();
            auto it = map_.find(top.key);
            if (it == map_.end()) continue;             // already evicted
            if (it->second.H != top.H) continue;        // stale H
            out_H = top.H; out_key = top.key;
            return true;
        }
        return false;
    }

public:
    GDSFCache(uint64_t capacity, GDSFCost cost_mode = GDSFCost::kUnit)
        : capacity_(capacity), cost_mode_(cost_mode) {}

    bool access(const std::string& key, uint64_t size) override {
        auto it = map_.find(key);
        if (it != map_.end()) {
            current_size_ = current_size_ - it->second.size + size;
            it->second.size = size;
            it->second.freq += 1;
            double new_H = L_ + (double)it->second.freq * cost_for_size(size) / (double)size;
            it->second.H = new_H;
            heap_push(new_H, key);   // old heap entry becomes stale; popped+discarded later
            record(true, size);
            return true;
        }
        // Miss: evict until space
        while (current_size_ + size > capacity_ && !map_.empty()) {
            double victim_H;
            std::string victim_key;
            if (!heap_pop_valid(victim_H, victim_key)) break;
            L_ = victim_H;
            auto v = map_.find(victim_key);
            if (v != map_.end()) {
                current_size_ -= v->second.size;
                map_.erase(v);
            }
        }
        double H_new = L_ + (double)1 * cost_for_size(size) / (double)size;
        Entry e{size, 1, H_new};
        map_[key] = e;
        heap_push(H_new, key);
        current_size_ += size;
        record(false, size);
        return false;
    }

    std::string name() const override {
        return cost_mode_ == GDSFCost::kEmpirical ? "GDSF-Cost" : "GDSF";
    }
    void reset() override {
        map_.clear(); heap_.clear();
        current_size_ = 0; L_ = 0.0; stats = {};
    }
};

// ==================== LHD (Least Hit Density) ====================
// Beckmann, Chen, Cidon. "LHD: Improving Cache Hit Rate by Maximizing
// Hit Density." NSDI 2018. The modern size-aware-AND-frequency-aware
// policy that v4's conclusion flagged as the most consequential missing
// baseline. Core idea: each cached item gets a hit-density score
// HD(item) = P(hit before eviction) / E[remaining bytes-time], evict
// the item with lowest HD on miss.
//
// Faithful sample-based implementation:
//   - Per-item: size, frequency, last-access age (in units of trace
//     accesses since insert).
//   - Age classes: 16 log-spaced buckets covering ages 1, 2, 4, ..., 32K.
//   - Per-class running statistics (EWMA-decayed): hits and evictions.
//   - hit_density(class) = hits / (hits + evictions). This is a simple
//     approximation; the full LHD paper uses a more elaborate residual
//     reuse-distance model but this captures the essential signal.
//   - Per-item HD = hit_density(class(age)) / size(item).
//   - On eviction: sample K=64 random items, evict the min-HD one.
//     Sampling avoids the O(n) per-eviction cost of a full scan; the
//     paper notes K=64 is sufficient for cache sizes up to ~10^6.
//
// The cost of LHD is implementation complexity (~150 lines vs GDSF's 50)
// and bookkeeping per access. We measure throughput in the focal sweep
// to verify it stays competitive with W-TinyLFU.
class LHDCache : public CachePolicy {
    uint64_t capacity_;
    uint64_t current_size_ = 0;
    uint64_t global_age_ = 0;        // counts every access; per-item age = global_age - last_access_age
    std::mt19937_64 rng_;            // proper RNG carried as state, not reseeded per-access
    static constexpr int N_CLASSES = 16;
    static constexpr int SAMPLE_K = 64;
    static constexpr double EWMA_DECAY = 0.9999;  // per-access decay; ~10K-access half-life

    struct Entry {
        uint64_t size;
        uint64_t freq;
        uint64_t last_access_global_age;
    };
    std::unordered_map<std::string, Entry> map_;
    std::vector<std::string> keys_;          // for sampling
    std::unordered_map<std::string, size_t> key_idx_;  // key -> position in keys_

    // Per-class running stats: hits, evictions, total bytes
    double class_hits_[N_CLASSES] = {};
    double class_evicts_[N_CLASSES] = {};

    // Class index from age (in units of accesses)
    int class_for_age(uint64_t age) const {
        if (age == 0) return 0;
        // log-spaced: class i covers ages [2^i, 2^(i+1))
        int b = 0;
        uint64_t a = age;
        while (a > 1 && b < N_CLASSES - 1) { a >>= 1; ++b; }
        return b;
    }

    double hit_density(int cls) const {
        double h = class_hits_[cls];
        double e = class_evicts_[cls];
        // Smoothed: avoid divide-by-zero, prior = 0.5
        return (h + 0.5) / (h + e + 1.0);
    }

    void decay_classes() {
        for (int i = 0; i < N_CLASSES; ++i) {
            class_hits_[i]   *= EWMA_DECAY;
            class_evicts_[i] *= EWMA_DECAY;
        }
    }

    void remove_from_keys_vec(const std::string& key) {
        auto it = key_idx_.find(key);
        if (it == key_idx_.end()) return;
        size_t i = it->second;
        size_t last = keys_.size() - 1;
        if (i != last) {
            keys_[i] = keys_[last];
            key_idx_[keys_[i]] = i;
        }
        keys_.pop_back();
        key_idx_.erase(it);
    }

public:
    LHDCache(uint64_t capacity, uint64_t seed = 12345)
        : capacity_(capacity), rng_(seed) {}

    bool access(const std::string& key, uint64_t size) override {
        ++global_age_;
        decay_classes();

        auto it = map_.find(key);
        if (it != map_.end()) {
            uint64_t age_at_hit = global_age_ - it->second.last_access_global_age;
            int cls = class_for_age(age_at_hit);
            class_hits_[cls] += 1.0;

            current_size_ = current_size_ - it->second.size + size;
            it->second.size = size;
            it->second.freq += 1;
            it->second.last_access_global_age = global_age_;
            record(true, size);
            return true;
        }
        // Miss: evict until space, sampling K candidates and picking min HD
        while (current_size_ + size > capacity_ && !map_.empty()) {
            // sample min(K, |cache|) random items, score by HD/size, pick min
            size_t n = keys_.size();
            size_t k = std::min((size_t)SAMPLE_K, n);
            std::string victim_key;
            double victim_score = 1e30;
            for (size_t i = 0; i < k; ++i) {
                size_t idx = std::uniform_int_distribution<size_t>(0, n-1)(rng_);
                const std::string& kk = keys_[idx];
                auto v = map_.find(kk);
                if (v == map_.end()) continue;
                uint64_t age = global_age_ - v->second.last_access_global_age;
                int cls = class_for_age(age);
                double hd = hit_density(cls);
                double score = hd / (double)v->second.size;  // higher = keep, lower = evict
                if (score < victim_score) { victim_score = score; victim_key = kk; }
            }
            if (victim_key.empty()) break;
            auto v = map_.find(victim_key);
            uint64_t age = global_age_ - v->second.last_access_global_age;
            int cls = class_for_age(age);
            class_evicts_[cls] += 1.0;
            current_size_ -= v->second.size;
            map_.erase(v);
            remove_from_keys_vec(victim_key);
        }
        Entry e{size, 1, global_age_};
        map_[key] = e;
        key_idx_[key] = keys_.size();
        keys_.push_back(key);
        current_size_ += size;
        record(false, size);
        return false;
    }

    std::string name() const override { return "LHD"; }
    void reset() override {
        map_.clear(); keys_.clear(); key_idx_.clear();
        current_size_ = 0; global_age_ = 0;
        for (int i = 0; i < N_CLASSES; ++i) { class_hits_[i] = 0; class_evicts_[i] = 0; }
        stats = {};
    }
};

// ==================== W-TinyLFU ====================
// Subordinate header; defined after CachePolicy so WTinyLFUCache : public CachePolicy
// has the base class in scope. See .planning/phases/02-w-tinylfu-core/02-CONTEXT.md L-11.

// ==================== LHDFull (residual reuse distance) ====================
// Faithful implementation of Beckmann/Chen/Cidon NSDI 2018, including the
// residual-reuse-distance model that LHDCache (lhd-lite, above) omits.
//
// Core formula (Eq. 3 of NSDI'18):
//
//   HD(class, age) = P(hit | survived to age, class) /
//                    E[remaining lifetime | survived to age, class]
//
// where a "lifetime event" is either a hit (reuse) or an eviction.
// Items with high HD should be retained; items with low HD evicted.
//
// Per class c, we maintain two histograms over discretized ages:
//
//   p_reuse[c][b]  = recent freq of HITS at age in bin b
//   p_evict[c][b]  = recent freq of EVICTIONS at age in bin b
//
// (EWMA-decayed every access, decay 0.9999.) On a hit at age a, we
// increment p_reuse[c][bin(a)] and reset the item's age to 0; on eviction
// at age a, we increment p_evict[c][bin(a)] and remove the item.
//
// On miss-evict, we sample K=64 random cached items, compute HD for each,
// and evict the minimum.
//
// Class assignment: 2-D (age_bin, freq_bin). Per Section 4.1 of NSDI'18,
// LHD's class is (app_id, age_bin, freq_bin); we don't have app_id from
// our traces, so we use (age_bin, freq_bin) — the canonical reduction
// when app_tags aren't available. Frequency buckets: {1, 2-4, 5-15, 16+}.
class LHDFullCache : public CachePolicy {
    uint64_t capacity_;
    uint64_t current_size_ = 0;
    uint64_t global_age_ = 0;
    std::mt19937_64 rng_;
    static constexpr int N_AGE_BINS = 16;
    static constexpr int N_FREQ_BINS = 4;     // {1, 2-4, 5-15, 16+}
    static constexpr int SAMPLE_K = 64;
    static constexpr double EWMA_DECAY = 0.9999;
    static constexpr double SMOOTHING = 0.5;

    static int freq_bin(uint64_t f) {
        if (f <= 1) return 0;
        if (f <= 4) return 1;
        if (f <= 15) return 2;
        return 3;
    }

    struct Entry {
        uint64_t size;
        uint64_t freq;
        uint64_t last_access_global_age;
    };
    std::unordered_map<std::string, Entry> map_;
    std::vector<std::string> keys_;
    std::unordered_map<std::string, size_t> key_idx_;

    // 2-D histograms: [freq_bin][age_bin]
    double p_reuse_[N_FREQ_BINS][N_AGE_BINS] = {};
    double p_evict_[N_FREQ_BINS][N_AGE_BINS] = {};

    int bin_for_age(uint64_t age) const {
        if (age == 0) return 0;
        int b = 0;
        uint64_t a = age;
        while (a > 1 && b < N_AGE_BINS - 1) { a >>= 1; ++b; }
        return b;
    }

    // Discrete representative-age (mid-of-bin): age 2^b for bin b.
    double bin_age(int b) const {
        return std::ldexp(1.0, b);  // 2^b
    }

    // HD = P(reuse | survived to age) / E[remaining lifetime | survived]
    // where:
    //   P(reuse) = sum_{b' >= bin(age)} p_reuse[b'] / sum_{b' >= bin(age)} (p_reuse[b'] + p_evict[b'])
    //   E[remaining_lifetime] = (sum_{b' >= bin(age)} bin_age(b') * (p_reuse[b'] + p_evict[b']) /
    //                            sum_{b' >= bin(age)} (p_reuse[b'] + p_evict[b'])) - bin_age(bin(age))
    //
    // 2-D HD: classes are (freq_bin, age_bin). To avoid divide-by-zero and
    // numerical noise on cold classes, we add a Laplace-style smoothing prior.
    double compute_hd(uint64_t age, uint64_t freq) const {
        int fb = freq_bin(freq);
        int ab = bin_for_age(age);
        double total_above = 0.0, reuse_above = 0.0, lifetime_above = 0.0;
        for (int b2 = ab; b2 < N_AGE_BINS; ++b2) {
            double r = p_reuse_[fb][b2] + SMOOTHING / (N_AGE_BINS * N_FREQ_BINS);
            double e = p_evict_[fb][b2] + SMOOTHING / (N_AGE_BINS * N_FREQ_BINS);
            double t = r + e;
            total_above += t;
            reuse_above += r;
            lifetime_above += bin_age(b2) * t;
        }
        if (total_above < 1e-12) return 1e30;
        double p_reuse = reuse_above / total_above;
        double e_lifetime = (lifetime_above / total_above) - bin_age(ab);
        if (e_lifetime <= 0) e_lifetime = bin_age(ab);
        return p_reuse / e_lifetime;
    }

    void decay() {
        for (int f = 0; f < N_FREQ_BINS; ++f)
            for (int i = 0; i < N_AGE_BINS; ++i) {
                p_reuse_[f][i] *= EWMA_DECAY;
                p_evict_[f][i] *= EWMA_DECAY;
            }
    }

    void remove_from_keys(const std::string& key) {
        auto it = key_idx_.find(key);
        if (it == key_idx_.end()) return;
        size_t i = it->second;
        size_t last = keys_.size() - 1;
        if (i != last) {
            keys_[i] = keys_[last];
            key_idx_[keys_[i]] = i;
        }
        keys_.pop_back();
        key_idx_.erase(it);
    }

public:
    LHDFullCache(uint64_t capacity, uint64_t seed = 12345)
        : capacity_(capacity), rng_(seed) {}

    bool access(const std::string& key, uint64_t size) override {
        ++global_age_;
        decay();

        auto it = map_.find(key);
        if (it != map_.end()) {
            uint64_t age_at_hit = global_age_ - it->second.last_access_global_age;
            int ab = bin_for_age(age_at_hit);
            int fb = freq_bin(it->second.freq);  // freq BEFORE this hit (the class the item was in)
            p_reuse_[fb][ab] += 1.0;

            current_size_ = current_size_ - it->second.size + size;
            it->second.size = size;
            it->second.freq += 1;
            it->second.last_access_global_age = global_age_;
            record(true, size);
            return true;
        }
        // Miss: evict until space, sample K candidates, evict min HD/size
        uint64_t victim_freq = 0;
        while (current_size_ + size > capacity_ && !map_.empty()) {
            size_t n = keys_.size();
            size_t k = std::min((size_t)SAMPLE_K, n);
            std::string victim_key;
            double victim_score = 1e30;
            uint64_t victim_age = 0;
            for (size_t i = 0; i < k; ++i) {
                size_t idx = std::uniform_int_distribution<size_t>(0, n-1)(rng_);
                const std::string& kk = keys_[idx];
                auto v = map_.find(kk);
                if (v == map_.end()) continue;
                uint64_t age = global_age_ - v->second.last_access_global_age;
                double hd = compute_hd(age, v->second.freq);
                double score = hd / (double)v->second.size;
                if (score < victim_score) {
                    victim_score = score;
                    victim_key = kk;
                    victim_age = age;
                    victim_freq = v->second.freq;
                }
            }
            if (victim_key.empty()) break;
            int ab = bin_for_age(victim_age);
            int fb = freq_bin(victim_freq);
            p_evict_[fb][ab] += 1.0;
            auto v = map_.find(victim_key);
            current_size_ -= v->second.size;
            map_.erase(v);
            remove_from_keys(victim_key);
        }
        Entry e{size, 1, global_age_};
        map_[key] = e;
        key_idx_[key] = keys_.size();
        keys_.push_back(key);
        current_size_ += size;
        record(false, size);
        return false;
    }

    std::string name() const override { return "LHD-Full"; }
    void reset() override {
        map_.clear(); keys_.clear(); key_idx_.clear();
        current_size_ = 0; global_age_ = 0;
        for (int f = 0; f < N_FREQ_BINS; ++f)
            for (int i = 0; i < N_AGE_BINS; ++i) { p_reuse_[f][i] = 0; p_evict_[f][i] = 0; }
        stats = {};
    }
};

#include "wtinylfu.h"
