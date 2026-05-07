/**
 * @file text_protocol.cpp
 * @brief Parser and serializer implementation for the text key-value protocol.
 * @ingroup protocol
 */

// Bring in the protocol declarations we are implementing.
#include "protocol/text_protocol.hpp"

// `algorithm` is used for lowercasing the command name.
#include <algorithm>
// `cctype` gives us `std::isspace` and `std::tolower`.
#include <cctype>
// `sstream` is included because this file originally used stream-style helpers.
#include <sstream>

// Put the implementation in the same namespace as the header.
namespace protocol {
// Keep internal helpers out of the public API.
namespace {

// Trim whitespace from the left side of a string view.
std::string trim_left(std::string_view text) {
    // Start at the beginning of the input.
    std::size_t start = 0;
    // Skip leading whitespace one character at a time.
    while (start < text.size() && std::isspace(static_cast<unsigned char>(text[start])) != 0) {
        ++start;
    }
    // Return the remaining substring as a concrete string.
    return std::string(text.substr(start));
}

// Trim whitespace from the right side of a string view.
std::string trim_right(std::string_view text) {
    // An empty string needs no trimming.
    if (text.empty()) {
        return {};
    }

    // Start from the end and move left while whitespace remains.
    std::size_t end = text.size();
    while (end > 0 && std::isspace(static_cast<unsigned char>(text[end - 1])) != 0) {
        --end;
    }
    // Return the trimmed prefix.
    return std::string(text.substr(0, end));
}

// Trim both sides by composing the left and right helpers.
std::string trim(std::string_view text) {
    return trim_right(trim_left(text));
}

// Lowercase a string copy so command names are case-insensitive.
std::string lower_copy(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return text;
}

// Build a structured invalid request with a human-readable error message.
Request invalid_request(std::string message) {
    Request request;
    request.type = RequestType::Invalid;
    request.error = std::move(message);
    return request;
}

}  // namespace

// Parse one command line into a typed request.
Request parse_request(std::string_view line) {
    // Remove leading and trailing spaces first so parsing is predictable.
    const std::string cleaned = trim(line);
    // Reject empty lines early so the server can report a useful error.
    if (cleaned.empty()) {
        return invalid_request("empty request");
    }

    // Split the first token from the remainder.
    const std::size_t first_space = cleaned.find(' ');
    // Lowercase the command token so commands are case-insensitive.
    const std::string command = lower_copy(cleaned.substr(0, first_space));
    // Everything after the command is the argument area.
    const std::string remainder =
        first_space == std::string::npos ? std::string() : trim_left(cleaned.substr(first_space + 1));

    // Default-initialize the request; it will remain invalid unless a branch succeeds.
    Request request;

    // Parse GET, which requires exactly one key.
    if (command == "get") {
        if (remainder.empty()) {
            return invalid_request("GET requires a key");
        }
        if (remainder.find(' ') != std::string::npos) {
            return invalid_request("GET accepts exactly one key");
        }
        request.type = RequestType::Get;
        request.key = remainder;
        return request;
    }

    // Parse DEL, and allow the alias DELETE for convenience.
    if (command == "del" || command == "delete") {
        if (remainder.empty()) {
            return invalid_request("DEL requires a key");
        }
        if (remainder.find(' ') != std::string::npos) {
            return invalid_request("DEL accepts exactly one key");
        }
        request.type = RequestType::Del;
        request.key = remainder;
        return request;
    }

    // Parse SET, which needs a key and a value.
    if (command == "set") {
        // The first space after the command separates key from value.
        const std::size_t key_end = remainder.find(' ');
        if (key_end == std::string::npos) {
            return invalid_request("SET requires a key and a value");
        }

        request.type = RequestType::Set;
        request.key = remainder.substr(0, key_end);
        request.value = trim_left(remainder.substr(key_end + 1));

        // Disallow empty keys because they make debugging and benchmarking messy.
        if (request.key.empty()) {
            return invalid_request("SET requires a non-empty key");
        }
        // Disallow empty values for the same reason.
        if (request.value.empty()) {
            return invalid_request("SET requires a non-empty value");
        }
        return request;
    }

    // Parse QUIT and EXIT as session shutdown commands.
    if (command == "quit" || command == "exit") {
        request.type = RequestType::Quit;
        return request;
    }

    // Any other command is rejected explicitly.
    return invalid_request("unknown command: " + command);
}

// Convert a structured response back into the line-based wire format.
std::string serialize_response(const Response& response) {
    switch (response.type) {
    case ResponseType::Ok:
        return "OK\n";
    case ResponseType::Value:
        return "VALUE " + response.payload + "\n";
    case ResponseType::NotFound:
        return "NOT_FOUND\n";
    case ResponseType::Error:
        return "ERROR " + response.payload + "\n";
    case ResponseType::Bye:
        return "BYE\n";
    }

    // The switch should always be exhaustive, but keep a fallback for safety.
    return "ERROR invalid response\n";
}

// Close the namespace after the implementation is complete.
}  // namespace protocol
