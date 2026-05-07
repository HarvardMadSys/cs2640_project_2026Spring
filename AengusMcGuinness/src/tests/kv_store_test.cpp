// The key-value store is the unit under test here.
#include "kvstore/kv_store.hpp"

// Standard exit codes for the test executable.
#include <cstdlib>
// Console output reports failures and the final success banner.
#include <iostream>
// String is included because the test stores literal values in the database.
#include <string>

// Keep helper functions local to the test file.
namespace {

// Simple assertion helper that prints a message and returns nonzero on failure.
int expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "test failed: " << message << '\n';
        return 1;
    }
    return 0;
}

}  // namespace

// Exercise the basic store operations one by one.
int main() {
    // Start with an empty database instance.
    kvstore::KeyValueStore store;

    // A freshly constructed store should have no entries.
    if (int rc = expect(store.size() == 0, "new store should be empty"); rc != 0) {
        return rc;
    }

    // Insert the first key/value pair.
    store.set("alpha", "one");
    // The size should increase after the insertion.
    if (int rc = expect(store.size() == 1, "store size should update after set"); rc != 0) {
        return rc;
    }

    // Fetch the value we just inserted.
    auto value = store.get("alpha");
    // The key should now exist.
    if (int rc = expect(value.has_value(), "value should exist after set"); rc != 0) {
        return rc;
    }
    // The stored payload should match the inserted string exactly.
    if (int rc = expect(*value == "one", "retrieved value should match"); rc != 0) {
        return rc;
    }

    // Overwrite the existing key with a new value.
    store.set("alpha", "two");
    // Verify that the overwrite took effect.
    value = store.get("alpha");
    if (int rc = expect(value.has_value() && *value == "two", "set should overwrite existing values"); rc != 0) {
        return rc;
    }

    // Remove the key and confirm the delete reports success.
    if (int rc = expect(store.erase("alpha"), "erase should report success for existing keys"); rc != 0) {
        return rc;
    }
    // Deleted keys should no longer be readable.
    if (int rc = expect(!store.get("alpha").has_value(), "erased key should be missing"); rc != 0) {
        return rc;
    }
    // Deleting a missing key should return false instead of pretending success.
    if (int rc = expect(!store.erase("alpha"), "erase should fail for missing keys"); rc != 0) {
        return rc;
    }

    // Print a success banner so the test result is obvious when run directly.
    std::cout << "kv_store_test passed\n";
    return EXIT_SUCCESS;
}
/**
 * @file kv_store_test.cpp
 * @brief Unit tests for basic `kvstore::KeyValueStore` behavior.
 * @ingroup tests
 */
