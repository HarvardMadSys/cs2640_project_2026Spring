/**
 * @file rdma_store.hpp
 * @brief Fixed-layout hash table exported through an RDMA memory region.
 * @ingroup storage
 *
 * `RdmaStore` is the one-sided RDMA storage backend.  Unlike
 * `KeyValueStore`, it cannot use dynamically allocated strings because remote
 * clients need stable, computable addresses.  The store is therefore a flat
 * open-addressing table of fixed-size slots.
 */
#pragma once

#include <infiniband/verbs.h>

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace kvstore {

/// Maximum bytes reserved for a key, including the null terminator.
static constexpr std::size_t RDMA_KEY_MAX = 112;

/// Maximum bytes reserved for a value, including the null terminator.
static constexpr std::size_t RDMA_VALUE_MAX = 896;

/**
 * @brief Number of slots in the remotely readable hash table.
 *
 * This value must remain a power of two because both server and client compute
 * probe positions with `hash & (RDMA_NUM_SLOTS - 1)`.
 */
static constexpr std::size_t RDMA_NUM_SLOTS = 4096;

/**
 * @brief One remotely readable cache object slot.
 * @ingroup storage
 *
 * The layout is part of the wire/storage contract between server and client:
 *
 * | Offset | Size | Field |
 * |-------:|-----:|-------|
 * | 0      | 1    | `occupied` state: 0 empty, 1 valid, 2 tombstone |
 * | 1      | 7    | padding |
 * | 8      | 8    | `access_count` metadata counter |
 * | 16     | 112  | null-terminated key |
 * | 128    | 896  | null-terminated value |
 *
 * The 1024-byte size and alignment make remote offset arithmetic simple.  The
 * `access_count` field is intentionally at offset 8 so RDMA atomics can target
 * an 8-byte-aligned address.
 */
struct alignas(1024) RdmaSlot {
    /// Slot state: 0 empty, 1 valid, 2 tombstone.
    uint8_t occupied;

    /// Padding that places `access_count` at byte offset 8.
    uint8_t _pad[7];

    /// Metadata counter updated by RDMA `FETCH_AND_ADD` in the metadata run.
    uint64_t access_count;

    /// Null-terminated key storage, up to 111 visible characters.
    char key[RDMA_KEY_MAX];

    /// Null-terminated value storage, up to 895 visible characters.
    char value[RDMA_VALUE_MAX];
};

static_assert(sizeof(RdmaSlot) == 1024, "RdmaSlot must be exactly 1024 bytes");
static_assert(offsetof(RdmaSlot, access_count) == 8,
              "access_count must be at offset 8 for atomic alignment");

/**
 * @brief Flat open-addressing hash table for one-sided RDMA access.
 * @ingroup storage
 *
 * The server mutates the table through `set()`, `get()`, and `erase()`.  After
 * `register_mr()` succeeds, clients can read the table directly with RDMA
 * READs by reproducing the FNV-1a hash and linear-probing sequence.
 *
 * @par Remote access model
 * `register_mr()` grants remote read, write, and atomic permissions.  The
 * benchmark uses remote reads for GET and optional atomics for metadata.  The
 * interactive client also has a one-sided write path, but concurrent one-sided
 * writes are not a complete production update protocol.
 */
class RdmaStore {
public:
    /**
     * @brief Construct an empty table with all slots zeroed.
     */
    RdmaStore();

    /**
     * @brief Insert or overwrite a key-value pair.
     *
     * @param key Key to store.  Must fit in `RDMA_KEY_MAX - 1` bytes.
     * @param value Value to store.  Must fit in `RDMA_VALUE_MAX - 1` bytes.
     * @return `true` on success, or `false` if the key/value is too large or
     *         the table has no available slot.
     */
    bool set(std::string_view key, std::string_view value);

    /**
     * @brief Look up a key in the local server copy of the RDMA table.
     *
     * @param key Key to read.
     * @return Stored value if present, or `std::nullopt` if missing.
     */
    std::optional<std::string> get(std::string_view key) const;

    /**
     * @brief Mark a key as deleted with a tombstone.
     *
     * @param key Key to remove.
     * @return `true` if the key existed and was tombstoned.
     */
    bool erase(std::string_view key);

    /**
     * @brief Register the slot array with an RDMA protection domain.
     *
     * @param pd Protection domain from the RDMA context that will export this
     *        memory.
     * @return Newly registered memory region, or `nullptr` on failure.
     *
     * @warning The returned `ibv_mr*` is owned by the caller and must be
     *          deregistered with `ibv_dereg_mr()` after all remote operations
     *          have stopped.
     */
    ibv_mr* register_mr(ibv_pd* pd);

    /**
     * @brief Return a read-only pointer to the first slot.
     */
    const RdmaSlot* slots() const { return slots_; }

    /**
     * @brief Return a mutable pointer to the first slot.
     */
    RdmaSlot* slots() { return slots_; }

    /**
     * @brief Total number of bytes in the registered slot array.
     */
    static constexpr std::size_t region_size() {
        return RDMA_NUM_SLOTS * sizeof(RdmaSlot);
    }

private:
    /**
     * @brief Compute the initial slot index for a key.
     *
     * The client-side one-sided path mirrors this FNV-1a hash exactly, so this
     * function is part of the cross-machine address contract even though it is
     * private in the server implementation.
     */
    static std::size_t slot_index(std::string_view key);

    /// Contiguous hash table slots exported as one memory region.
    RdmaSlot slots_[RDMA_NUM_SLOTS] = {};
};

}  // namespace kvstore
