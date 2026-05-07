/**
 * @file text_protocol.hpp
 * @brief Parser and serializer for the line-oriented key-value protocol.
 * @ingroup protocol
 *
 * The protocol is intentionally text based so that the TCP and two-sided RDMA
 * paths can share identical application semantics.  A request is one line:
 *
 * - `GET key`
 * - `SET key value`
 * - `DEL key`
 * - `QUIT`
 *
 * A response is also one line:
 *
 * - `OK`
 * - `VALUE payload`
 * - `NOT_FOUND`
 * - `ERROR message`
 * - `BYE`
 */
#pragma once

#include <string>
#include <string_view>

namespace protocol {

/**
 * @brief Request kinds supported by the text command parser.
 * @ingroup protocol
 */
enum class RequestType {
    /// Fetch the value associated with a key.
    Get,
    /// Insert or overwrite a key-value pair.
    Set,
    /// Delete a key.
    Del,
    /// Close the current client session cleanly.
    Quit,
    /// Malformed, empty, or unknown input.
    Invalid,
};

/**
 * @brief Response kinds emitted by server request handlers.
 * @ingroup protocol
 */
enum class ResponseType {
    /// Operation succeeded without returning a value.
    Ok,
    /// Operation succeeded and returned a payload.
    Value,
    /// The requested key was not found.
    NotFound,
    /// The request was invalid or could not be processed.
    Error,
    /// The session should close after this response.
    Bye,
};

/**
 * @brief Structured representation of one parsed request line.
 * @ingroup protocol
 */
struct Request {
    /// Parsed command kind.
    RequestType type = RequestType::Invalid;

    /// Key argument for `GET`, `SET`, and `DEL`.
    std::string key;

    /// Value argument for `SET`.
    std::string value;

    /// Human-readable explanation when `type == RequestType::Invalid`.
    std::string error;
};

/**
 * @brief Structured representation of one response line.
 * @ingroup protocol
 */
struct Response {
    /// Response kind to serialize.
    ResponseType type = ResponseType::Error;

    /// Value payload or error message, depending on `type`.
    std::string payload;
};

/**
 * @brief Parse one request line into a structured request.
 *
 * @param line Request text without requiring a trailing newline.  Leading and
 *        trailing whitespace are ignored; command names are case-insensitive.
 * @return Parsed request.  Malformed input returns `RequestType::Invalid`
 *         with `Request::error` set.
 *
 * @note `SET` treats the first argument as the key and the remaining
 *       non-leading text as the value, so values may contain spaces.
 */
Request parse_request(std::string_view line);

/**
 * @brief Serialize a structured response into one newline-terminated line.
 *
 * @param response Response object to encode.
 * @return Wire-format text ending in `\n`.
 */
std::string serialize_response(const Response& response);

}  // namespace protocol
