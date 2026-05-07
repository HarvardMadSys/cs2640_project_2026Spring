/**
 * @file kv_store.hpp
 * @brief Mutex-protected in-memory key-value store used by TCP and two-sided RDMA.
 * @ingroup storage
 *
 * `KeyValueStore` is the semantic storage backend for request/response modes.
 * It deliberately stores ordinary `std::string` keys and values so TCP and
 * two-sided RDMA can share the same application behavior while changing only
 * the transport beneath it.
 */
#pragma once

#include <cstddef>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>

namespace kvstore {

/**
 * @brief Small thread-safe in-memory map from string keys to string values.
 * @ingroup storage
 *
 * The class is intentionally simple: every public method takes the same mutex
 * for the duration of the operation.  That keeps correctness easy to reason
 * about while still allowing the TCP server to service multiple connections
 * and the two-sided RDMA server to use the same request handler.
 *
 * @par Thread safety
 * All public methods are safe to call concurrently.  Returned values are
 * copies, so callers do not hold references into the protected map after the
 * mutex is released.
 */
class KeyValueStore {
public:
    /**
     * @brief Insert or overwrite a key-value pair.
     *
     * @param key Key to insert.  The store takes ownership of the string.
     * @param value Value to associate with `key`.  The store takes ownership of
     *        the string.
     * @return `true` if a new key was inserted, or `false` if an existing key
     *         was overwritten.
     */
    bool set(std::string key, std::string value);

    /**
     * @brief Look up a key.
     *
     * @param key Key to read.
     * @return The stored value if the key exists; `std::nullopt` otherwise.
     */
    std::optional<std::string> get(const std::string& key) const;

    /**
     * @brief Remove a key from the store.
     *
     * @param key Key to erase.
     * @return `true` if a key was removed, or `false` if it was not present.
     */
    bool erase(const std::string& key);

    /**
     * @brief Return the number of currently stored keys.
     *
     * @return Current map size observed while holding the store mutex.
     */
    std::size_t size() const;

private:
    /// Guards every access to `entries_`.
    mutable std::mutex mutex_;

    /// Backing hash table for request/response modes.
    std::unordered_map<std::string, std::string> entries_;
};

}  // namespace kvstore
