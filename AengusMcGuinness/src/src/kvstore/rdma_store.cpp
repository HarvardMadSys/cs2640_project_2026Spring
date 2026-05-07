/**
 * @file rdma_store.cpp
 * @brief Implementation of the fixed-layout one-sided RDMA hash table.
 * @ingroup storage
 */

#include "kvstore/rdma_store.hpp"

#include <cstring>
#include <iostream>

namespace kvstore {

// ---------------------------------------------------------------------------
// FNV-1a hash
// ---------------------------------------------------------------------------

// FNV-1a is used both here and by the client-side benchmark to compute slot
// indices.  The client replicates this exact function so that hash(key) always
// maps to the same slot index on both sides, without any server involvement.
std::size_t RdmaStore::slot_index(std::string_view key) {
    uint32_t hash = 2166136261u; // FNV offset basis
    for (unsigned char c : key) {
        hash ^= c;
        hash *= 16777619u; // FNV prime
    }
    // Mask to a valid index.  RDMA_NUM_SLOTS is a power of two, so this is
    // equivalent to hash % RDMA_NUM_SLOTS but avoids division.
    return static_cast<std::size_t>(hash) & (RDMA_NUM_SLOTS - 1);
}

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------

RdmaStore::RdmaStore() {
    // Zero-initialise all slots so occupied == 0 everywhere before any inserts.
    std::memset(slots_, 0, sizeof(slots_));
}

// ---------------------------------------------------------------------------
// set
// ---------------------------------------------------------------------------

bool RdmaStore::set(std::string_view key, std::string_view value) {
    if (key.size() >= RDMA_KEY_MAX) {
        std::cerr << "rdma_store: key too long (" << key.size() << " >= " << RDMA_KEY_MAX << ")\n";
        return false;
    }
    if (value.size() >= RDMA_VALUE_MAX) {
        std::cerr << "rdma_store: value too long (" << value.size() << " >= " << RDMA_VALUE_MAX << ")\n";
        return false;
    }

    std::size_t idx   = slot_index(key);
    std::size_t first_tombstone = RDMA_NUM_SLOTS; // track reusable tombstone slot

    for (std::size_t i = 0; i < RDMA_NUM_SLOTS; ++i) {
        std::size_t probe = (idx + i) & (RDMA_NUM_SLOTS - 1);
        RdmaSlot&   slot  = slots_[probe];

        if (slot.occupied == 1 && std::strncmp(slot.key, key.data(), RDMA_KEY_MAX) == 0) {
            // Overwrite an existing entry.
            std::memset(slot.value, 0, RDMA_VALUE_MAX);
            std::memcpy(slot.value, value.data(), value.size());
            return true;
        }

        if (slot.occupied == 2 && first_tombstone == RDMA_NUM_SLOTS) {
            first_tombstone = probe; // prefer reusing a tombstone over an empty slot
        }

        if (slot.occupied == 0) {
            // Use the tombstone if we found one earlier; otherwise use this empty slot.
            std::size_t target = (first_tombstone != RDMA_NUM_SLOTS) ? first_tombstone : probe;
            RdmaSlot& dst = slots_[target];
            dst.occupied = 1;
            dst.access_count = 0;
            std::memset(dst.key,   0, RDMA_KEY_MAX);
            std::memset(dst.value, 0, RDMA_VALUE_MAX);
            std::memcpy(dst.key,   key.data(),   key.size());
            std::memcpy(dst.value, value.data(), value.size());
            return true;
        }
    }

    std::cerr << "rdma_store: table full\n";
    return false;
}

// ---------------------------------------------------------------------------
// get
// ---------------------------------------------------------------------------

std::optional<std::string> RdmaStore::get(std::string_view key) const {
    std::size_t idx = slot_index(key);

    for (std::size_t i = 0; i < RDMA_NUM_SLOTS; ++i) {
        std::size_t      probe = (idx + i) & (RDMA_NUM_SLOTS - 1);
        const RdmaSlot&  slot  = slots_[probe];

        if (slot.occupied == 0) {
            // Empty slot: the key cannot be further along the probe chain.
            return std::nullopt;
        }
        if (slot.occupied == 2) {
            // Tombstone: skip but continue probing.
            continue;
        }
        if (std::strncmp(slot.key, key.data(), RDMA_KEY_MAX) == 0) {
            return std::string(slot.value);
        }
    }

    return std::nullopt;
}

// ---------------------------------------------------------------------------
// erase
// ---------------------------------------------------------------------------

bool RdmaStore::erase(std::string_view key) {
    std::size_t idx = slot_index(key);

    for (std::size_t i = 0; i < RDMA_NUM_SLOTS; ++i) {
        std::size_t probe = (idx + i) & (RDMA_NUM_SLOTS - 1);
        RdmaSlot&   slot  = slots_[probe];

        if (slot.occupied == 0) return false; // not found
        if (slot.occupied == 2) continue;     // tombstone, keep probing

        if (std::strncmp(slot.key, key.data(), RDMA_KEY_MAX) == 0) {
            // Mark as tombstone so the probe chain for other keys remains intact.
            slot.occupied = 2;
            return true;
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// Memory registration
// ---------------------------------------------------------------------------

ibv_mr* RdmaStore::register_mr(ibv_pd* pd) {
    // Grant all remote access flags so clients can:
    //   - RDMA READ  slots for GET operations
    //   - RDMA WRITE slots for SET operations
    //   - FETCH_AND_ADD access_count to simulate LRU metadata overhead
    int flags = IBV_ACCESS_LOCAL_WRITE  |
                IBV_ACCESS_REMOTE_READ  |
                IBV_ACCESS_REMOTE_WRITE |
                IBV_ACCESS_REMOTE_ATOMIC;

    ibv_mr* mr = ibv_reg_mr(pd, slots_, region_size(), flags);
    if (!mr) {
        std::cerr << "rdma_store: ibv_reg_mr failed\n";
    }
    return mr;
}

} // namespace kvstore
