/**
 * @file kv_store.cpp
 * @brief Implementation of the mutex-protected request/response store.
 * @ingroup storage
 */

// Include the declaration of the store methods we are implementing here.
#include "kvstore/kv_store.hpp"

// Put the implementation in the same namespace as the declaration.
namespace kvstore {

// `set` takes ownership of the key and value strings so callers can move into it.
bool KeyValueStore::set(std::string key, std::string value) {
    // Lock the map for the entire mutation so concurrent clients stay safe.
    std::lock_guard<std::mutex> lock(mutex_);
    // Insert the pair if it is new, or replace the old value if the key exists.
    auto [it, inserted] = entries_.insert_or_assign(std::move(key), std::move(value));
    // `it` is not used, but binding it makes the intent of `insert_or_assign` clear.
    (void)it;
    // Return whether this call created a new key.
    return inserted;
}

// `get` returns the value if it exists, otherwise it returns `std::nullopt`.
std::optional<std::string> KeyValueStore::get(const std::string& key) const {
    // Lock the map before reading so a concurrent writer cannot race us.
    std::lock_guard<std::mutex> lock(mutex_);
    // Find the key in the hash table.
    auto it = entries_.find(key);
    // If the iterator is at the end, the key is missing.
    if (it == entries_.end()) {
        return std::nullopt;
    }
    // Return a copy of the stored value.
    return it->second;
}

// Remove the key if present and report whether anything was erased.
bool KeyValueStore::erase(const std::string& key) {
    // Lock the map before mutating it.
    std::lock_guard<std::mutex> lock(mutex_);
    // `erase` returns the number of removed entries, so compare it to zero.
    return entries_.erase(key) > 0;
}

// Return the current number of entries in the map.
std::size_t KeyValueStore::size() const {
    // Lock the map so the count is consistent with concurrent updates.
    std::lock_guard<std::mutex> lock(mutex_);
    // Ask the unordered_map for its current size.
    return entries_.size();
}

// Close the namespace after all methods are defined.
}  // namespace kvstore
