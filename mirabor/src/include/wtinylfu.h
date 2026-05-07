#pragma once
#include <cstdint>
#include <string>
#include <list>
#include <unordered_map>
#include <utility>
#include <algorithm>
#include "count_min_sketch.h"
#include "doorkeeper.h"  // Plan 04-05 D-08: embedded sibling to cms_ when use_doorkeeper_=true
// cache.h is NOT re-included: this header is itself included FROM cache.h,
// so CachePolicy and CacheStats are already in scope in the translation unit.

// ==================== W-TinyLFU ====================
// Window LRU (1% bytes) -> Main SLRU (99% bytes; 80% protected / 20% probation).
// Admission: CountMinSketch frequency comparison on main-region contention.
//
// Mirrors Caffeine WindowTinyLfuPolicy per
// .planning/phases/02-w-tinylfu-core/02-01-CAFFEINE-NOTES.md §4 (D-08a..D-08e)
// and §7 (summary of locked values).
//
// Hill-climbing adaptive window-size tuner OMITTED per CONTEXT.md L-8 /
// STACK.md §Rejected. Static 1%/99% window/main split.
//
// Stats single-source invariant (CONTEXT.md L-12): only the outer access()
// may call record(hit, size). Private helpers (evict_window_if_needed_,
// admit_candidate_to_main_, demote_protected_lru_to_probation_mru_) MUST
// NOT touch `stats` — doing so would double-count. Grep-enforced:
//   grep -cE "record\(\s*(true|false)" include/wtinylfu.h  ==  4
// (three hit returns in access() + one miss fall-through).
//
// Deliberate deviations from Caffeine (see 02-01-CAFFEINE-NOTES.md §6):
//   - D-08a short-circuit (empty probation -> admit unconditionally) is a
//     simplification; Caffeine escalates to protected/window victims instead.
//   - D-08e strict `>` comparison without 1/128 hash-DoS random admission;
//     no adversarial threat model in an offline research simulator.
//   - Update rule / sample_size / hash scheme differences are encapsulated
//     in CountMinSketch and do not appear in this header.
//   - Phase 4 D-05 (Plan 04-05): optional paper-faithful Doorkeeper pre-CMS
//     record filter gated by ctor flag `use_doorkeeper` (default false =
//     Phase 2 bit-identical). When true, the FIRST touch of a key is absorbed
//     by an embedded Doorkeeper Bloom filter and NOT recorded into cms_;
//     subsequent touches record as in baseline. Admission logic is UNCHANGED
//     per D-10 ("same admission, different counting"). Caffeine omits
//     Doorkeeper in production (CAFFEINE-NOTES §6); our DK is ablation-only.
class WTinyLFUCache : public CachePolicy {
public:
    WTinyLFUCache(uint64_t capacity_bytes, uint64_t n_objects_hint,
                  bool use_doorkeeper = false)
        : window_capacity_(std::max<uint64_t>(1, capacity_bytes / 100)),
          main_capacity_(capacity_bytes > window_capacity_
                             ? capacity_bytes - window_capacity_
                             : 0),
          protected_capacity_((main_capacity_ * 80) / 100),
          cms_(std::max<uint64_t>(1, n_objects_hint)),
          use_doorkeeper_(use_doorkeeper),
          // D-06 sizing: 4 × n_objects_hint bits. When use_doorkeeper_=false
          // we still allocate the minimum (1 element = 64 bits of packed bits_
          // vector) so the member is always validly constructed — the 64 bits
          // of overhead on baseline wtinylfu is noise. The member is never
          // read/written when the flag is false (access() branch skips it).
          doorkeeper_(use_doorkeeper ? std::max<uint64_t>(1, n_objects_hint) : 1)
    {
        // total_capacity_bytes = window_capacity_ + main_capacity_ and
        // probation_capacity_bytes = main_capacity_ - protected_capacity_
        // are implied by the invariants and not stored — they aren't
        // referenced elsewhere in the policy (main_capacity_ is the
        // effective admission budget; probation is bounded by "main - protected").
        (void)capacity_bytes;

        // D-09 (Plan 04-05): register the DK reset callback on CMS aging.
        // Only when DK is on — baseline wtinylfu leaves on_age_cb_ default-
        // empty, and CMS's halve_all_() `if (on_age_cb_)` check is then
        // a branch-predictable no-op (zero overhead). The lambda captures
        // `this` so the reference to doorkeeper_ stays valid for the life of
        // the cache.
        if (use_doorkeeper_) {
            cms_.set_on_age_cb([this]{ doorkeeper_.clear(); });
        }
    }

    bool access(const std::string& key, uint64_t size) override {
        // CMS records on EVERY access — Caffeine semantics per
        // 02-01-CAFFEINE-NOTES.md §4 "onAccess" L1775-L1796 and §7 "admission
        // test runs on main-region contention" (both rely on record happening
        // before any branch).
        //
        // D-05 (Plan 04-05): when use_doorkeeper_=true, apply the Einziger-
        // Friedman §4.3 paper-faithful pre-CMS filter — a key's FIRST touch
        // is absorbed into the Doorkeeper bit array and NOT recorded into
        // CMS; subsequent touches (DK hit) record into CMS as in baseline.
        // When use_doorkeeper_=false, behavior is IDENTICAL to Phase 2: the
        // else-branch's cms_.record(key) fires unconditionally.
        //
        // L-12 stats single-source invariant preserved: this if/else wraps
        // a SINGLE existing cms_.record(key) — it does NOT introduce
        // duplicate stats records. The four record-hit/miss calls below at
        // the three hit-path returns and the one miss fall-through are
        // UNCHANGED in count and position (grep `record\(\s*(true|false)`
        // still returns 4, same as Phase 2).
        if (use_doorkeeper_) {
            if (doorkeeper_.contains(key)) {
                cms_.record(key);  // DK hit: subsequent touch -> record as in baseline
            } else {
                doorkeeper_.add(key);
                // DK miss: first touch absorbed by DK; skip cms_.record() per D-05.
            }
        } else {
            cms_.record(key);  // Phase 2 baseline path — bit-identical
        }

        // Hit-path: probe window -> protected -> probation.
        if (auto it = window_map_.find(key); it != window_map_.end()) {
            window_size_ -= it->second->second;
            it->second->second = size;
            window_size_ += size;
            window_list_.splice(window_list_.end(), window_list_, it->second);
            evict_window_if_needed_();
            record(true, size);
            return true;
        }
        if (auto it = protected_map_.find(key); it != protected_map_.end()) {
            protected_size_ -= it->second->second;
            it->second->second = size;
            protected_size_ += size;
            protected_list_.splice(protected_list_.end(), protected_list_,
                                   it->second);
            while (protected_size_ > protected_capacity_
                   && !protected_list_.empty()) {
                demote_protected_lru_to_probation_mru_();
            }
            record(true, size);
            return true;
        }
        if (auto it = probation_map_.find(key); it != probation_map_.end()) {
            // D-08c: ANY hit while in probation promotes to protected MRU.
            // Caffeine verbatim per 02-01-CAFFEINE-NOTES.md §4 (reorderProbation
            // at L1798-L1815). No "second hit" guard; first hit promotes.
            Entry entry = *it->second;
            probation_size_ -= entry.second;
            probation_list_.erase(it->second);
            probation_map_.erase(it);
            entry.second = size;
            while (protected_size_ + size > protected_capacity_
                   && !protected_list_.empty()) {
                demote_protected_lru_to_probation_mru_();
            }
            protected_list_.push_back(entry);
            protected_map_[key] = std::prev(protected_list_.end());
            protected_size_ += size;
            record(true, size);
            return true;
        }

        // Miss: insert into window MRU (tail); enforce window budget, which
        // may push evicted window entries into the admission test.
        window_list_.push_back({key, size});
        window_map_[key] = std::prev(window_list_.end());
        window_size_ += size;
        evict_window_if_needed_();

        record(false, size);
        return false;
    }

    // Plan 04-05 (D-08): display name tracks the Doorkeeper ctor flag so CSV
    // `policy` rows carry "W-TinyLFU+DK" for the ablation variant while
    // baseline stays "W-TinyLFU" (Phase 2 bit-identical alias).
    std::string name() const override {
        return use_doorkeeper_ ? "W-TinyLFU+DK" : "W-TinyLFU";
    }

    void reset() override {
        window_list_.clear();    window_map_.clear();    window_size_ = 0;
        protected_list_.clear(); protected_map_.clear(); protected_size_ = 0;
        probation_list_.clear(); probation_map_.clear(); probation_size_ = 0;
        cms_.reset();
        // Plan 04-05 (D-09): full wipe matches CMS reset. Baseline wtinylfu
        // skips this; the DK variant clears to keep freshness window aligned.
        if (use_doorkeeper_) doorkeeper_.clear();
        stats = {};
    }

private:
    using Entry = std::pair<std::string, uint64_t>;
    using List  = std::list<Entry>;
    using Map   = std::unordered_map<std::string, List::iterator>;

    uint64_t window_capacity_;
    uint64_t main_capacity_;
    uint64_t protected_capacity_;

    List window_list_;    Map window_map_;    uint64_t window_size_ = 0;
    List protected_list_; Map protected_map_; uint64_t protected_size_ = 0;
    List probation_list_; Map probation_map_; uint64_t probation_size_ = 0;

    CountMinSketch cms_;

    // Plan 04-05 (D-08/D-09): optional Doorkeeper sibling, gated by ctor flag.
    // use_doorkeeper_ is the runtime predicate for the access()/reset() branches
    // and the name() selector. doorkeeper_ is always constructed (minimum 1
    // element = 64 bits of overhead when the flag is false) so the member is
    // always validly initialized regardless of flag state.
    bool use_doorkeeper_;
    Doorkeeper doorkeeper_;

    // Evict window LRU-head entries until the window is under its byte
    // budget. Each evicted entry becomes a candidate for admission into
    // main. Mirrors Caffeine evictFromWindow -> evictFromMain transition.
    void evict_window_if_needed_() {
        while (window_size_ > window_capacity_ && !window_list_.empty()) {
            Entry cand = window_list_.front();
            window_list_.pop_front();
            window_map_.erase(cand.first);
            window_size_ -= cand.second;
            admit_candidate_to_main_(cand);
        }
    }

    // Admission decision for a window-evicted candidate:
    //   D-08a: probation empty -> admit unconditionally (port simplification;
    //          CAFFEINE-NOTES §6 row 4 justifies — warmup-only condition).
    //   D-08b: main has spare byte capacity -> admit unconditionally (no CMS
    //          comparison). Matches Caffeine's
    //          `while (weightedSize() > maximum())` gate.
    //   else:  compare freq(candidate) vs freq(victim = probation LRU front).
    //          Admit iff freq(candidate) > freq(victim). Strict `>` only;
    //          ties reject candidate (D-08e, no randomization — see
    //          CAFFEINE-NOTES §6 row 5 for the deliberate omission of
    //          Caffeine's 1/128 hash-DoS random admit).
    //
    // Admitted candidates land at probation MRU (list tail). If main is
    // over-budget after admission, evict probation LRU entries until fit.
    void admit_candidate_to_main_(const Entry& cand) {
        bool admit = false;

        if (probation_list_.empty()) {
            admit = true;                                                // D-08a
        } else if (probation_size_ + protected_size_ + cand.second
                   <= main_capacity_) {
            admit = true;                                                // D-08b
        } else {
            const Entry& victim = probation_list_.front();
            uint32_t fc = cms_.estimate(cand.first);
            uint32_t fv = cms_.estimate(victim.first);
            admit = (fc > fv);                                           // D-08e
        }

        if (!admit) return;

        while (probation_size_ + protected_size_ + cand.second > main_capacity_
               && !probation_list_.empty()) {
            Entry v = probation_list_.front();
            probation_list_.pop_front();
            probation_map_.erase(v.first);
            probation_size_ -= v.second;
        }
        if (probation_size_ + protected_size_ + cand.second > main_capacity_) {
            return;  // candidate larger than entire main budget; drop.
        }

        probation_list_.push_back(cand);
        probation_map_[cand.first] = std::prev(probation_list_.end());
        probation_size_ += cand.second;
    }

    // D-08d: protected overflow -> demote the protected LRU-tail (list
    // front in our orientation) to probation MRU (probation back).
    // Mirrors Caffeine's demoteFromMainProtected at L1248-L1271.
    // MUST use push_back (MRU), not push_front.
    void demote_protected_lru_to_probation_mru_() {
        if (protected_list_.empty()) return;
        Entry demoted = protected_list_.front();
        protected_list_.pop_front();
        protected_map_.erase(demoted.first);
        protected_size_ -= demoted.second;
        while (probation_size_ + protected_size_ + demoted.second
                   > main_capacity_
               && !probation_list_.empty()) {
            Entry v = probation_list_.front();
            probation_list_.pop_front();
            probation_map_.erase(v.first);
            probation_size_ -= v.second;
        }
        if (probation_size_ + protected_size_ + demoted.second
            > main_capacity_) {
            return;  // demoted entry larger than what probation can hold; drop.
        }

        probation_list_.push_back(demoted);
        probation_map_[demoted.first] = std::prev(probation_list_.end());
        probation_size_ += demoted.second;
    }
};
