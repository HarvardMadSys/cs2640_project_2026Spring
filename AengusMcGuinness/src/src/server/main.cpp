/**
 * @file main.cpp
 * @brief TCP key-value server entry point and coroutine connection handlers.
 * @ingroup executables
 */

// Key-value storage implementation used to answer client requests.
#include "kvstore/kv_store.hpp"
// The coroutine event loop that drives nonblocking socket readiness.
#include "net/event_loop.hpp"
// Socket helpers for creating, configuring, and closing descriptors.
#include "net/socket_utils.hpp"
// Line-based request parsing and response serialization.
#include "protocol/text_protocol.hpp"

// Standard exit codes and argument conversion helpers.
#include <cstdlib>
// C string utilities are used for strerror output.
#include <cstring>
// errno is needed to interpret system call failures.
#include <cerrno>
// Exception support is needed because the event loop may throw on poll failure.
#include <exception>
// iostream is used for user-facing startup and error messages.
#include <iostream>
// string is used for inbound buffers and CLI parsing.
#include <string>

// Socket address types for accept(2).
#include <netinet/in.h>
// Socket system calls such as recv, send, and accept.
#include <sys/socket.h>
// close(2) and related descriptor helpers.
#include <unistd.h>

// This file contains the server entry point and the coroutine-based connection logic.
namespace {

// Make the event-loop type easier to reference inside helper functions.
using net::EventLoop;

// Translate a parsed protocol request into a structured response.
protocol::Response handle_request(kvstore::KeyValueStore& store, const protocol::Request& request) {
    // Pull the response enums into local scope so the switch is easier to read.
    using protocol::Response;
    using protocol::ResponseType;
    using protocol::RequestType;

    // Route each request type to the storage operation it represents.
    switch (request.type) {
    case RequestType::Get: {
        // Look up the requested key and either return VALUE or NOT_FOUND.
        auto value = store.get(request.key);
        if (!value) {
            return {ResponseType::NotFound, {}};
        }
        return {ResponseType::Value, *value};
    }
    case RequestType::Set:
        // SET updates the in-memory store and returns a simple success acknowledgment.
        store.set(request.key, request.value);
        return {ResponseType::Ok, {}};
    case RequestType::Del:
        // DEL reports whether the key was actually present.
        return store.erase(request.key) ? Response{ResponseType::Ok, {}} : Response{ResponseType::NotFound, {}};
    case RequestType::Quit:
        // QUIT tells the client the session is closing.
        return {ResponseType::Bye, {}};
    case RequestType::Invalid:
        // Malformed input is turned into an ERROR response with a human-readable message.
        return {ResponseType::Error, request.error};
    }

    // The compiler requires a fallback even though the enum cases above are exhaustive.
    return {ResponseType::Error, "unhandled request"};
}

// Coroutine that owns one client connection and processes requests until the client disconnects.
net::EventLoop::Task handle_client(EventLoop& loop, kvstore::KeyValueStore& store, int client_fd) {
    // Buffer for bytes that have arrived but have not yet been split into full lines.
    std::string inbound;
    // Pre-allocate a modest buffer to reduce reallocations for common request sizes.
    inbound.reserve(4096);
    // Temporary stack buffer for recv(2).
    char buffer[4096];

    while (true) {
        // Try to drain all currently readable bytes before sleeping again.
        while (true) {
            // recv on a nonblocking socket returns data, EOF, or EAGAIN/EWOULDBLOCK.
            const ssize_t bytes_read = ::recv(client_fd, buffer, sizeof(buffer), 0);
            if (bytes_read > 0) {
                // Append the newly received bytes to the line accumulator.
                inbound.append(buffer, static_cast<std::size_t>(bytes_read));
                continue;
            }

            if (bytes_read == 0) {
                // EOF means the peer closed the connection cleanly.
                net::close_socket(client_fd);
                co_return;
            }

            if (errno == EINTR) {
                // Interrupted system calls are retried immediately.
                continue;
            }

            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                // No more bytes are available right now, so suspend until the socket is readable again.
                break;
            }

            // Any other error ends the connection.
            net::close_socket(client_fd);
            co_return;
        }

        // Process every complete newline-delimited command currently buffered.
        std::size_t newline = std::string::npos;
        while ((newline = inbound.find('\n')) != std::string::npos) {
            // Extract one line and remove it from the buffer.
            std::string line = inbound.substr(0, newline);
            inbound.erase(0, newline + 1);

            // Parse, execute, and serialize one request-response round trip.
            const protocol::Request request = protocol::parse_request(line);
            const protocol::Response response = handle_request(store, request);
            const std::string serialized = protocol::serialize_response(response);

            // send may write only part of the response, so track progress explicitly.
            std::size_t offset = 0;
            while (offset < serialized.size()) {
                // Attempt to send the remaining bytes of the response.
                const ssize_t bytes_written = ::send(
                    client_fd,
                    serialized.data() + offset,
                    serialized.size() - offset,
                    0
                );

                if (bytes_written > 0) {
                    // Advance the write cursor by however many bytes the kernel accepted.
                    offset += static_cast<std::size_t>(bytes_written);
                    continue;
                }

                if (bytes_written < 0 && errno == EINTR) {
                    // Retry send after interruption.
                    continue;
                }

                if (bytes_written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                    // The socket is full right now, so suspend until it becomes writable.
                    co_await loop.writable(client_fd);
                    continue;
                }

                // Any other send error closes the connection.
                net::close_socket(client_fd);
                co_return;
            }

            if (request.type == protocol::RequestType::Quit) {
                // QUIT completes the request and then terminates the session.
                net::close_socket(client_fd);
                co_return;
            }
        }

        // If no complete line is buffered, suspend until more bytes arrive.
        co_await loop.readable(client_fd);
    }
}

// Coroutine that accepts new clients and spawns a handler for each one.
net::EventLoop::Task accept_loop(EventLoop& loop, kvstore::KeyValueStore& store, int server_fd) {
    while (true) {
        // Sleep until the listening socket indicates that at least one connection is ready.
        co_await loop.readable(server_fd);

        while (true) {
            // accept requires a storage buffer for the remote address, even if we do not inspect it.
            sockaddr_storage client_addr {};
            // The address buffer length must be initialized to the storage size.
            socklen_t client_len = sizeof(client_addr);
            // Try to accept one pending connection from the kernel backlog.
            const int client_fd = ::accept(server_fd, reinterpret_cast<sockaddr*>(&client_addr), &client_len);
            if (client_fd >= 0) {
                // Newly accepted sockets are switched to nonblocking mode before the handler sees them.
                if (!net::set_non_blocking(client_fd)) {
                    // If nonblocking setup fails, close the socket and move on to the next pending client.
                    net::close_socket(client_fd);
                    continue;
                }

                // Hand the connection off to a dedicated client coroutine.
                loop.spawn(handle_client(loop, store, client_fd));
                continue;
            }

            if (errno == EINTR) {
                // Interrupted accept calls can be retried immediately.
                continue;
            }

            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                // The accept queue is drained for now, so go back to waiting for readability.
                break;
            }

            // Unexpected accept failures are reported to stderr, but the loop keeps running.
            std::cerr << "accept failed: " << std::strerror(errno) << '\n';
            break;
        }
    }
}

}  // namespace

// Parse the port, start the server socket, and run the event loop.
int main(int argc, char* argv[]) {
    // Default to 9090 unless the user overrides the port on the command line.
    const std::uint16_t port = static_cast<std::uint16_t>(argc > 1 ? std::stoi(argv[1]) : 9090);

    // Create the listening TCP socket and bind it to the requested port.
    int server_fd = net::create_server_socket(port);
    if (server_fd < 0) {
        std::cerr << "failed to start server on port " << port << ": " << std::strerror(errno) << '\n';
        return EXIT_FAILURE;
    }

    // Print a startup banner so the user knows the server is ready.
    std::cout << "kv_server listening on port " << port << '\n';

    // The store is shared by all client coroutines and protected internally by a mutex.
    kvstore::KeyValueStore store;
    // The event loop owns scheduling and readiness notifications.
    EventLoop loop;
    // Start the accept coroutine and let it keep spawning client coroutines.
    loop.spawn(accept_loop(loop, store, server_fd));
    // Run until the loop naturally drains or an error stops it.
    loop.run();
    // Clean up the listening socket before exiting.
    net::close_socket(server_fd);
    return EXIT_SUCCESS;
}
