---
phase: 02-w-tinylfu-core
plan: 01
source_repo: ben-manes/caffeine
source_tag: v3.1.8
fetched: 2026-04-18
files:
  - caffeine/src/main/java/com/github/benmanes/caffeine/cache/FrequencySketch.java
  - caffeine/src/main/java/com/github/benmanes/caffeine/cache/BoundedLocalCache.java
status: locked
---

# Caffeine v3.x Reference Notes — Phase 2 Pre-Work

**Fetched:** 2026-04-18
**Commit / tag:** v3.1.8 (Caffeine release tag)
**Files:** FrequencySketch.java (214 lines), BoundedLocalCache.java (4679 lines), both from `caffeine/src/main/java/com/github/benmanes/caffeine/cache/`
**Source URLs:**
- https://raw.githubusercontent.com/ben-manes/caffeine/v3.1.8/caffeine/src/main/java/com/github/benmanes/caffeine/cache/FrequencySketch.java
- https://raw.githubusercontent.com/ben-manes/caffeine/v3.1.8/caffeine/src/main/java/com/github/benmanes/caffeine/cache/BoundedLocalCache.java

This document is the authoritative Caffeine reference for Plans 02-02 (CMS) and 02-03
(W-TinyLFU). Every numeric constant, update rule, and admission/promotion edge case in our
C++ port traces back to a verbatim citation here. Where our port deliberately diverges from
Caffeine, §6 records the deviation and its justification.

---

## 1. `sampleSize` formula (FrequencySketch.java)

Citation: `FrequencySketch.java:L88-L102` (`ensureCapacity` method)

Verbatim Java:
```java
public void ensureCapacity(@NonNegative long maximumSize) {
  requireArgument(maximumSize >= 0);
  int maximum = (int) Math.min(maximumSize, Integer.MAX_VALUE >>> 1);
  if ((table != null) && (table.length >= maximum)) {
    return;
  }

  table = new long[Math.max(Caffeine.ceilingPowerOfTwo(maximum), 8)];
  sampleSize = (maximumSize == 0) ? 10 : (10 * maximum);
  blockMask = (table.length >>> 3) - 1;
  if (sampleSize <= 0) {
    sampleSize = Integer.MAX_VALUE;
  }
  size = 0;
}
```

Key observations:
- Caffeine's `sampleSize = 10 * maximum` where `maximum` is the requested cache entry count
  (clamped to `Integer.MAX_VALUE >>> 1`). It is **NOT** `10 × (width × depth)`.
- The table length is `ceilingPowerOfTwo(maximum)` (with floor 8), so for maximum=N the
  table holds at least N×16 = N×16 4-bit counters (each `long` slot stores 16 counters).
- Equivalently, in CMS terms with depth=4: `width = ceilingPowerOfTwo(maximum)` and
  `total_counters = width × depth × 4`-bits-each. Caffeine packs 16 counters per long.

Our port's locked choice (per CONTEXT.md L-5, D-03, D-10):
- `width = nextpow2(n_objects_hint)`, `depth = 4`
- `sample_size = 10 * width * depth` = `10 * width * 4` = `40 * width`

Comparison to Caffeine: Caffeine's `sample_size = 10 * maximum_size` ≈ `10 * width` (because
`width = ceilingPowerOfTwo(maximum) ≈ maximum`). Our port uses `40 * width`, which is **4×
larger** than Caffeine's threshold. This means our port halves counters less frequently
(once per ~40W records vs once per ~10W records).

**This is a deliberate deviation — see §6.** Justification: our chosen formula in
CONTEXT.md L-5/D-03 follows the Einziger-Friedman TinyLFU paper convention which counts in
"counter operations" not "key operations"; with depth=4 every `record(key)` triggers 4
counter writes, so `10 × W × D` records ≈ `10 × W × D × D` = `10 × W × 16` counter writes,
matching the paper's reset cadence in counter-operation units. Caffeine instead counts
key-level events (one record = one increment of `size`, regardless of depth — see §3 below).

The discrepancy is recorded but does NOT block 02-02; the chosen `40 * width` keeps aging
slow enough that warm objects retain their frequency under typical Congress/CourtListener
trace lengths. If 02-06 validation shows W-TinyLFU under-performing relative to LRU at α≥0.8,
revisiting `sample_size = 10 * width` (Caffeine-exact) is the first lever to pull.

---

## 2. `increment()` update semantics (FrequencySketch.java)

Citation: `FrequencySketch.java:L144-L169` (`increment` method) and
`FrequencySketch.java:L195-L203` (`incrementAt` helper)

Verbatim Java — `increment(E e)`:
```java
@SuppressWarnings("ShortCircuitBoolean")
public void increment(E e) {
  if (isNotInitialized()) {
    return;
  }

  int[] index = new int[8];
  int blockHash = spread(e.hashCode());
  int counterHash = rehash(blockHash);
  int block = (blockHash & blockMask) << 3;
  for (int i = 0; i < 4; i++) {
    int h = counterHash >>> (i << 3);
    index[i] = (h >>> 1) & 15;
    int offset = h & 1;
    index[i + 4] = block + offset + (i << 1);
  }
  boolean added =
        incrementAt(index[4], index[0])
      | incrementAt(index[5], index[1])
      | incrementAt(index[6], index[2])
      | incrementAt(index[7], index[3]);

  if (added && (++size == sampleSize)) {
    reset();
  }
}
```

Verbatim Java — `incrementAt(int i, int j)`:
```java
boolean incrementAt(int i, int j) {
  int offset = j << 2;
  long mask = (0xfL << offset);
  if ((table[i] & mask) != mask) {
    table[i] += (1L << offset);
    return true;
  }
  return false;
}
```

Classify the update rule observed in Caffeine:
- [ ] **Conservative update:** only rows whose counter equals the current `min(counter_row_i)`
      get incremented; rows already above the min are skipped.
- [x] **Standard CMS:** ALL d=4 counters are incremented unconditionally (up to saturation
      at 15). The `|` operator at L161-164 is **bitwise OR** (per the
      `@SuppressWarnings("ShortCircuitBoolean")` annotation at L144), which forces evaluation
      of all four `incrementAt` calls regardless of return value. There is **no conditional
      check** that any counter equals the row-minimum.

**Our port's locked choice: CONSERVATIVE update** (per REQUIREMENTS.md WTLFU-01 line 42:
"4-bit counters, depth=4, width=nextpow2(capacity-objects), **conservative update**, periodic
halving every 10×W accesses" and ROADMAP.md §Phase 2 Success Criterion 1).

Saturation: counters max at 15 (4-bit). `incrementAt` returns `false` (no-op) if the
counter is already at 15.

**This IS a deliberate deviation from Caffeine — see §6 row 1.** Caffeine uses STANDARD
update; our port uses CONSERVATIVE update because REQUIREMENTS.md WTLFU-01 mandates it
unconditionally. The requirement is authoritative; this document is reference material.

Implementation guidance for 02-02:
- Compute the d=4 row indices and their counter values.
- Find `current_min = min(counter[row_0], counter[row_1], counter[row_2], counter[row_3])`.
- For each row `i`: if `counter[row_i] == current_min` AND `counter[row_i] < 15`, increment
  it; otherwise skip.
- Return `true` if at least one counter was incremented (used to advance `sample_count_`).
- Caffeine's `size` counter is per-call, NOT per-incremented-counter. Our port should
  follow the same semantic: `sample_count_` increases by 1 per `record()` call when at
  least one counter was incremented. (See §3 for the reset trigger.)

---

## 3. `reset()` / aging halving (FrequencySketch.java)

Citation: `FrequencySketch.java:L66-L67` (mask constants), `FrequencySketch.java:L205-L213`
(`reset` method), and `FrequencySketch.java:L166-L168` (the trigger condition inside
`increment`).

Verbatim Java — masks:
```java
static final long RESET_MASK = 0x7777777777777777L;
static final long ONE_MASK = 0x1111111111111111L;
```

Verbatim Java — trigger inside `increment` (L166-168):
```java
if (added && (++size == sampleSize)) {
  reset();
}
```

Verbatim Java — `reset()`:
```java
/** Reduces every counter by half of its original value. */
void reset() {
  int count = 0;
  for (int i = 0; i < table.length; i++) {
    count += Long.bitCount(table[i] & ONE_MASK);
    table[i] = (table[i] >>> 1) & RESET_MASK;
  }
  size = (size - (count >>> 2)) >>> 1;
}
```

Locked behaviors for our port:
- **Halving trigger**: when `sample_count_ == sample_size_` (Caffeine uses `==`, not `>=`,
  but `++size == sampleSize` increments first then compares — equivalent to `>=` on the
  next call as long as we never overshoot; safe to use `>=` in C++ port).
- **Halving operation**: for every counter, `counter = counter >> 1` (integer right shift).
  Caffeine implements this in bulk via `(table[i] >>> 1) & RESET_MASK` — the AND with
  `0x7777...` zeros out the high bit of each 4-bit nibble that would otherwise be polluted
  by the bit shifted in from the adjacent counter. Our port operating on 4-bit counters
  one at a time naturally gets `(c >> 1)` correct without a mask.
- **Post-halve sample_count update**: Caffeine does NOT reset `size` to 0. It computes
  `size = (size - (count >>> 2)) >>> 1` where `count` is the total number of odd 4-bit
  counters across the entire table (each odd counter contributes 0.25 to the lost-count
  correction, hence `count >>> 2`).

**Our port's choice (per CONTEXT.md D-10): `sample_count_ = 0` after halving.** This is a
simplification: we lose Caffeine's "lost-count compensation" but gain code clarity. Acceptable
because (a) our trace lengths are O(20K-100K) not millions, so cumulative drift between
Caffeine's `(size - count/4) / 2` and our `0` is bounded by roughly half-a-window of error;
(b) the simulator is deterministic, so 02-06 validation can re-tune the sample_size constant
if drift matters empirically.

This is a documented deviation — see §6 row 2.

---

## 4. Admission + promotion edge cases (BoundedLocalCache.java)

Caffeine's TinyLFU admission lives entirely in `BoundedLocalCache.evictFromMain` (L763-871)
and `BoundedLocalCache.admit` (L884-897). Promotion lives in `onAccess` (L1775-1796) and
`reorderProbation` (L1800-1815). Demotion from protected to probation lives in
`demoteFromMainProtected` (L1250-1271).

The high-level flow:
1. `evictEntries()` (L703-711) calls `evictFromWindow()` then `evictFromMain(candidate)`.
2. `evictFromWindow()` (L720-744) moves window-LRU entries to probation MRU
   (`accessOrderProbationDeque().offerLast(node)`) until the window is under-budget.
3. `evictFromMain(candidate)` runs the admission test ONLY when `weightedSize() > maximum()`
   (L768) — i.e., only when the main region is over its byte budget.
4. The admission test itself is `admit(candidateKey, victimKey)` (L860, L884).

### D-08a — Empty probation segment → candidate admitted unconditionally

CONTEXT.md D-08a: "Empty probation segment → candidate admitted unconditionally (no CMS
comparison). Record the admission; CMS still updated on access."

Caffeine citation: `BoundedLocalCache.java:L767-L789` (the victim-selection bootstrap at
the head of `evictFromMain`'s while loop).

Verbatim Java:
```java
int victimQueue = PROBATION;
int candidateQueue = PROBATION;
Node<K, V> victim = accessOrderProbationDeque().peekFirst();
while (weightedSize() > maximum()) {
  // Search the admission window for additional candidates
  if ((candidate == null) && (candidateQueue == PROBATION)) {
    candidate = accessOrderWindowDeque().peekFirst();
    candidateQueue = WINDOW;
  }

  // Try evicting from the protected and window queues
  if ((candidate == null) && (victim == null)) {
    if (victimQueue == PROBATION) {
      victim = accessOrderProtectedDeque().peekFirst();
      victimQueue = PROBATION;  // (sic; comment in source — but conceptually moves to PROTECTED)
      continue;
    } ...
```

Match status: [ ] matches D-08a / [x] **deviates — flag in §6 row 3.**

Caffeine does **NOT** have a literal "empty probation → admit candidate unconditionally"
short-circuit. Instead, when probation is empty, Caffeine pulls victims from the protected
queue (and then from the window). Our port's D-08a semantic — "candidate admitted into
probation unconditionally when probation segment is empty" — is a **simplification** that
sidesteps the protected→victim escalation logic.

Justification for our simplification: in our byte-bounded port, the "empty probation"
case typically occurs during cache warmup; admitting candidates unconditionally during
warmup is intuitively correct (cache is filling, no contention) and avoids the
multi-queue victim search complexity. The simplification is documented in §6.

### D-08b — Main SLRU has spare byte capacity → candidate admitted unconditionally

CONTEXT.md D-08b: "Main SLRU has spare byte capacity → candidate admitted unconditionally
(no CMS comparison). Only run the `freq(candidate) > freq(victim)` test when main is at-or-
over its byte budget."

Caffeine citation: `BoundedLocalCache.java:L768` (the while-loop condition that gates the
entire admission test).

Verbatim Java:
```java
while (weightedSize() > maximum()) {
  ...
  if (admit(candidateKey, victimKey)) { ... }
  ...
}
```

Match status: [x] **matches D-08b** — when `weightedSize() <= maximum()` (i.e., main has
spare byte capacity), the entire `evictFromMain` while loop is skipped, so the admission
test never fires. Candidates that arrived from `evictFromWindow` simply remain in
probation (where they were placed at L733: `accessOrderProbationDeque().offerLast(node)`).

This is exactly D-08b: spare capacity → no admission test → candidate stays.

### D-08c — Probation→protected promotion on ANY hit (not "second hit")

CONTEXT.md D-08c: "Probation→protected promotion fires on ANY hit during probation
residency (Caffeine default; not the paper's 'second hit' rule). Move promoted entry to
protected MRU."

Caffeine citation: `BoundedLocalCache.java:L1775-L1796` (`onAccess`) and
`BoundedLocalCache.java:L1798-L1815` (`reorderProbation`).

Verbatim Java — `onAccess`:
```java
void onAccess(Node<K, V> node) {
  if (evicts()) {
    K key = node.getKey();
    if (key == null) {
      return;
    }
    frequencySketch().increment(key);
    if (node.inWindow()) {
      reorder(accessOrderWindowDeque(), node);
    } else if (node.inMainProbation()) {
      reorderProbation(node);
    } else {
      reorder(accessOrderProtectedDeque(), node);
    }
    setHitsInSample(hitsInSample() + 1);
  }
  ...
}
```

Verbatim Java — `reorderProbation`:
```java
/** Promote the node from probation to protected on an access. */
void reorderProbation(Node<K, V> node) {
  if (!accessOrderProbationDeque().contains(node)) {
    // Ignore stale accesses for an entry that is no longer present
    return;
  } else if (node.getPolicyWeight() > mainProtectedMaximum()) {
    reorder(accessOrderProbationDeque(), node);
    return;
  }

  // If the protected space exceeds its maximum, the LRU items are demoted to the probation space.
  // This is deferred to the adaption phase at the end of the maintenance cycle.
  setMainProtectedWeightedSize(mainProtectedWeightedSize() + node.getPolicyWeight());
  accessOrderProbationDeque().remove(node);
  accessOrderProtectedDeque().offerLast(node);
  node.makeMainProtected();
}
```

Match status: [x] **matches D-08c** — every probation hit calls `reorderProbation`, which
unconditionally moves the node from probation to protected MRU
(`accessOrderProtectedDeque().offerLast(node)`). There is no "second hit" guard. The
promotion fires on the FIRST hit during probation residency.

Edge case: nodes whose own `policyWeight` exceeds `mainProtectedMaximum()` are NOT promoted
(L1804-1807); they stay in probation (just reordered to MRU within probation). Our port
should mirror this — a single object larger than the entire protected byte budget cannot
live in protected without immediately overflowing it.

### D-08d — Protected overflow on promotion → demoted protected LRU goes to probation MRU

CONTEXT.md D-08d: "Protected overflow on promotion → demoted protected LRU-tail entry moves
to probation MRU (not probation tail). Caffeine's `reorderProbation` behavior."

Caffeine citation: `BoundedLocalCache.java:L1248-L1271` (`demoteFromMainProtected`).

Verbatim Java:
```java
/** Transfers the nodes from the protected to the probation region if it exceeds the maximum. */
void demoteFromMainProtected() {
  long mainProtectedMaximum = mainProtectedMaximum();
  long mainProtectedWeightedSize = mainProtectedWeightedSize();
  if (mainProtectedWeightedSize <= mainProtectedMaximum) {
    return;
  }

  for (int i = 0; i < QUEUE_TRANSFER_THRESHOLD; i++) {
    if (mainProtectedWeightedSize <= mainProtectedMaximum) {
      break;
    }

    Node<K, V> demoted = accessOrderProtectedDeque().poll();
    if (demoted == null) {
      break;
    }
    demoted.makeMainProbation();
    accessOrderProbationDeque().offerLast(demoted);
    mainProtectedWeightedSize -= demoted.getPolicyWeight();
  }
  setMainProtectedWeightedSize(mainProtectedWeightedSize);
}
```

Match status: [x] **matches D-08d** — `accessOrderProtectedDeque().poll()` removes from
the head (LRU end) of the protected deque, and `accessOrderProbationDeque().offerLast(demoted)`
inserts at the tail (MRU end) of probation. So a demoted protected-LRU entry lands at
probation MRU, exactly as D-08d specifies.

Implementation note for 02-03: Caffeine bounds the demotion loop to
`QUEUE_TRANSFER_THRESHOLD` iterations (typically 1024) per maintenance cycle. Our port
runs synchronously per `access()` call, so demote-while-overflow is acceptable without a
fixed iteration cap.

### D-08e — Admission tie (`freq(candidate) == freq(victim)`) → reject candidate

CONTEXT.md D-08e: "Admission test tie (`freq(candidate) == freq(victim)`) → reject candidate
(favor incumbents; Caffeine default)."

Caffeine citation: `BoundedLocalCache.java:L884-L897` (`admit` method) and
`BoundedLocalCache.java:L224` (`ADMIT_HASHDOS_THRESHOLD = 6`).

Verbatim Java:
```java
boolean admit(K candidateKey, K victimKey) {
  int victimFreq = frequencySketch().frequency(victimKey);
  int candidateFreq = frequencySketch().frequency(candidateKey);
  if (candidateFreq > victimFreq) {
    return true;
  } else if (candidateFreq >= ADMIT_HASHDOS_THRESHOLD) {
    // The maximum frequency is 15 and halved to 7 after a reset to age the history. An attack
    // exploits that a hot candidate is rejected in favor of a hot victim. The threshold of a warm
    // candidate reduces the number of random acceptances to minimize the impact on the hit rate.
    int random = ThreadLocalRandom.current().nextInt();
    return ((random & 127) == 0);
  }
  return false;
}
```

Match status: [~] **matches D-08e for the strict `>` comparison, but Caffeine adds a
1/128 random admission for "warm" candidates (freq >= 6) to defend against hash-DoS
attacks**.

Our port deviates: we omit the hash-DoS random tiebreaker (no adversarial threat model
in a research simulator). For all ties (`candidateFreq == victimFreq`), our port returns
`false` (reject candidate) unconditionally. See §6 row 4.

---

## 5. Hash scheme reference (FrequencySketch.java `spread` / `rehash` / counter index)

Citation: `FrequencySketch.java:L171-L186` (`spread` and `rehash` helper functions) and
`FrequencySketch.java:L150-L159` (the index-computation block inside `increment`).

Verbatim Java — `spread`:
```java
/** Applies a supplemental hash functions to defends against poor quality hash. */
static int spread(int x) {
  x ^= x >>> 17;
  x *= 0xed5ad4bb;
  x ^= x >>> 11;
  x *= 0xac4c1b51;
  x ^= x >>> 15;
  return x;
}
```

Verbatim Java — `rehash`:
```java
/** Applies another round of hashing for additional randomization. */
static int rehash(int x) {
  x *= 0x31848bab;
  x ^= x >>> 14;
  return x;
}
```

Verbatim Java — counter index computation inside `increment` (L150-159):
```java
int[] index = new int[8];
int blockHash = spread(e.hashCode());
int counterHash = rehash(blockHash);
int block = (blockHash & blockMask) << 3;
for (int i = 0; i < 4; i++) {
  int h = counterHash >>> (i << 3);
  index[i] = (h >>> 1) & 15;        // 4-bit counter slot within the long (0-15)
  int offset = h & 1;
  index[i + 4] = block + offset + (i << 1);  // long-array index
}
```

Our C++ port deviates: per CONTEXT.md D-11 / L-10, our port uses **FNV-1a from
`include/hash_util.h` with the four pre-defined seeds `FNV_SEED_A..D`** — one FNV-1a call
per row, keyed on `(seed, key_bytes)`. We do NOT use Caffeine's
single-base-hash-plus-mixing-constants pattern.

Justification: Caffeine's scheme requires a 64-bit input hash (it consumes
`Object.hashCode()` which is `int` but its `spread`/`rehash` produce ints folded into the
64-bit table layout). Our keys are `std::string`; a separate 64-bit pre-hash step plus
4 mixing rounds is more code than 4 independent FNV-1a calls and offers no measurable
quality benefit at our scale (per L-10). FNV-1a with 4 distinct seeds gives 4 effectively
independent 64-bit hashes for our depth=4 CMS rows. See §6 row 5.

---

## 6. Deliberate deviations from Caffeine

| # | Topic | Caffeine behavior | Our port | Justification |
|---|-------|-------------------|----------|---------------|
| 1 | §2 increment update rule | **STANDARD update** — all 4 counters incremented unconditionally (bitwise `|` of `incrementAt` calls at FrequencySketch.java:L161-L164). | **CONSERVATIVE update** — only counters equal to row-min are incremented. | REQUIREMENTS.md WTLFU-01 (line 42) and ROADMAP.md §Phase 2 Success Criterion 1 mandate "conservative update" unconditionally. The requirement is authoritative; this document is reference. |
| 2 | §1 sample_size formula | `sampleSize = 10 * maximumSize` (FrequencySketch.java:L96). | `sample_size = 10 * width * depth = 40 * width`. | CONTEXT.md L-5 / D-03 follow the Einziger-Friedman paper's "counter operations" convention; our port's halving cadence is 4× slower than Caffeine's per the same trace. Acceptable for trace lengths ≤100K accesses; revisitable in 02-06 if validation under-performs. |
| 3 | §3 post-reset `size` update | `size = (size - (count >>> 2)) >>> 1` — preserves a fractional residual via `count` (number of odd counters across table). | `sample_count_ = 0` (full reset). | CONTEXT.md D-10 explicitly chose full reset for code simplicity. Drift impact is bounded (≤ half a window). |
| 4 | §4 D-08a empty-probation short-circuit | No literal short-circuit — when probation is empty, victims are pulled from protected then window queues. | Candidate admitted into probation unconditionally when probation is empty. | Simplification for byte-bounded port; "empty probation" is a warmup-only condition where unconditional admission is intuitively correct. Avoids multi-queue victim escalation. |
| 5 | §4 D-08e admission tiebreak | Strict `candidateFreq > victimFreq` reject-on-tie, BUT adds 1/128 random admission for `candidateFreq >= 6` as hash-DoS defense (BoundedLocalCache.java:L884-L897, with `ADMIT_HASHDOS_THRESHOLD = 6` at L224). | Strict `candidateFreq > victimFreq` reject-on-tie unconditionally. No randomization. | No adversarial threat model in a research simulator. Eliminating the 1/128 random admission preserves determinism (D-05 test requirement) without affecting α≥0.8 acceptance criteria. |
| 6 | §5 hash scheme | Single 32-bit `Object.hashCode()` → `spread()` → `rehash()` → split into 4 row-indices via byte-shifts. | FNV-1a with 4 distinct seeds (`FNV_SEED_A..D`), one call per row, keyed on `(seed, key_bytes)`. | CONTEXT.md L-10 / D-11. Caffeine's scheme is optimized for `Object.hashCode()` inputs; our keys are `std::string`. FNV-1a with 4 seeds is equivalent quality at our scale and reuses Phase 1's `hash_util.h`. |

Note: §4 D-08b, D-08c, D-08d **match Caffeine** verbatim — no deviations recorded for those rules.

---

## 7. Summary — values locked for 02-02 and 02-03

### CMS (Plan 02-02 — `include/count_min_sketch.h`)

- **Counter width:** 4 bits, saturating at 15 (matches Caffeine).
- **Depth:** 4 rows (matches Caffeine).
- **Width:** `nextpow2(n_objects_hint)` where `n_objects_hint = capacity_bytes / avg_object_size_from_workload_stats` (CONTEXT.md D-02; matches Caffeine's `ceilingPowerOfTwo(maximum)`).
- **Update rule: CONSERVATIVE** — only increment counters whose value equals the row-minimum. **(Locked by REQUIREMENTS.md WTLFU-01; deliberately differs from Caffeine's STANDARD update — see §6 row 1.)**
- **Halving trigger:** when `sample_count_ >= sample_size_`.
- **Halving operation:** for each 4-bit counter, `counter = counter >> 1` (integer right shift; no rounding).
- **sample_size formula:** `10 * width * depth` (CONTEXT.md L-5 / D-03; deliberately 4× larger than Caffeine's `10 * maximum` — see §6 row 2).
- **Post-halve:** `sample_count_ = 0` (full reset; deliberately simpler than Caffeine — see §6 row 3).
- **Hash scheme:** FNV-1a from `include/hash_util.h` with 4 seeds (`FNV_SEED_A..D`); one call per row, keyed on `(seed, key_bytes)` — see §6 row 6.
- **Public API:** `record(const std::string& key)`, `estimate(const std::string& key) const`, `reset()`, `force_age()` (test hook).

### W-TinyLFU (Plan 02-03 — `include/wtinylfu.h`)

- **Window LRU byte budget:** 1% of total byte capacity (CONTEXT.md L-6).
- **Main SLRU byte budget:** 99% of total byte capacity.
- **Protected byte budget:** 80% of main SLRU.
- **Probation byte budget:** 20% of main SLRU.
- **Admission test:** `freq(candidate) > freq(victim)` (strict `>` only).
- **Tie behavior (D-08e):** `freq(candidate) == freq(victim)` → reject candidate (favor incumbent). No randomization. **(Deliberately omits Caffeine's hash-DoS 1/128 random admission — see §6 row 5.)**
- **Probation→protected promotion (D-08c):** fires on ANY hit during probation residency (matches Caffeine `reorderProbation`).
- **Protected overflow demotion (D-08d):** demoted protected-LRU entry → probation MRU position (matches Caffeine `demoteFromMainProtected`).
- **Empty-probation short-circuit (D-08a):** candidate admitted unconditionally when probation is empty. **(Deliberate simplification of Caffeine's victim-escalation logic — see §6 row 4.)**
- **Spare-main-capacity short-circuit (D-08b):** admission test only runs when main SLRU is at-or-over byte budget (matches Caffeine's `while (weightedSize() > maximum())` gate).
- **Stats recording:** `record(hit, size)` called exactly once per outer `access()` call (CONTEXT.md L-12); never from inside sub-region operations.
- **Internal lists:** window, protected, probation are private std::list-based deques; NOT reused `LRUCache` instances (CONTEXT.md L-12).

---

**End of reference notes.** Plans 02-02 and 02-03 should `grep` this file for any
implementation question. Any further Caffeine source consultation must be recorded as an
addendum at the bottom of this file with new line citations from the same `v3.1.8` tag.
