// The protocol layer is the unit under test here.
#include "protocol/text_protocol.hpp"

// Standard exit codes for a standalone test executable.
#include <cstdlib>
// Console output is used for assertion failures and success banners.
#include <iostream>
// Strings are used in expected request and response payloads.
#include <string>

// Keep helper functions private to this file.
namespace {

// Small assertion helper that prints the failure and returns a nonzero code.
int expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "test failed: " << message << '\n';
        return 1;
    }
    return 0;
}

}  // namespace

// Validate parsing and serialization across the supported wire format.
int main() {
    // Bring the protocol enum names into local scope for readability in the checks below.
    using protocol::RequestType;
    using protocol::Response;
    using protocol::ResponseType;

    // Parse a simple GET request with a single key.
    {
        const auto request = protocol::parse_request("GET alpha");
        if (int rc = expect(request.type == RequestType::Get, "GET should parse"); rc != 0) {
            return rc;
        }
        if (int rc = expect(request.key == "alpha", "GET should capture key"); rc != 0) {
            return rc;
        }
    }

    // Parse SET, including extra surrounding whitespace and mixed case.
    {
        const auto request = protocol::parse_request("  set beta value  ");
        if (int rc = expect(request.type == RequestType::Set, "SET should parse case-insensitively"); rc != 0) {
            return rc;
        }
        if (int rc = expect(request.key == "beta", "SET should capture key"); rc != 0) {
            return rc;
        }
        if (int rc = expect(request.value == "value", "SET should capture value"); rc != 0) {
            return rc;
        }
    }

    // DEL should be accepted as a one-argument delete command.
    {
        const auto request = protocol::parse_request("DEL gamma");
        if (int rc = expect(request.type == RequestType::Del, "DEL should parse"); rc != 0) {
            return rc;
        }
    }

    // QUIT is the session-closing command.
    {
        const auto request = protocol::parse_request("quit");
        if (int rc = expect(request.type == RequestType::Quit, "QUIT should parse"); rc != 0) {
            return rc;
        }
    }

    // GET with too many arguments should be rejected.
    {
        const auto request = protocol::parse_request("GET a b");
        if (int rc = expect(request.type == RequestType::Invalid, "invalid GET should be rejected"); rc != 0) {
            return rc;
        }
    }

    // SET with too few arguments should also be rejected.
    {
        const auto request = protocol::parse_request("SET only-key");
        if (int rc = expect(request.type == RequestType::Invalid, "invalid SET should be rejected"); rc != 0) {
            return rc;
        }
    }

    // Verify each response kind serializes to the exact expected wire format.
    {
        if (int rc = expect(protocol::serialize_response({ResponseType::Ok, {}}) == "OK\n", "serialize OK"); rc != 0) {
            return rc;
        }
        if (int rc = expect(protocol::serialize_response({ResponseType::Value, "abc"}) == "VALUE abc\n", "serialize VALUE"); rc != 0) {
            return rc;
        }
        if (int rc = expect(protocol::serialize_response({ResponseType::NotFound, {}}) == "NOT_FOUND\n", "serialize NOT_FOUND"); rc != 0) {
            return rc;
        }
        if (int rc = expect(protocol::serialize_response({ResponseType::Error, "bad"}) == "ERROR bad\n", "serialize ERROR"); rc != 0) {
            return rc;
        }
        if (int rc = expect(protocol::serialize_response({ResponseType::Bye, {}}) == "BYE\n", "serialize BYE"); rc != 0) {
            return rc;
        }
    }

    // Print a success message so the test can be run manually without ambiguity.
    std::cout << "protocol_test passed\n";
    return EXIT_SUCCESS;
}
/**
 * @file protocol_test.cpp
 * @brief Unit tests for request parsing and response serialization.
 * @ingroup tests
 */
