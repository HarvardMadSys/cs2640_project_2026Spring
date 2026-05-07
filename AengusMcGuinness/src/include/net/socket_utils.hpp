/**
 * @file socket_utils.hpp
 * @brief Thin POSIX socket helpers shared by clients, servers, and benchmarks.
 * @ingroup networking
 *
 * These helpers centralize repetitive socket setup and line-oriented blocking
 * I/O.  The coroutine TCP server uses the descriptor creation helpers but does
 * its own nonblocking reads/writes in `src/server/main.cpp`.
 */
#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace net {

/**
 * @brief Create a nonblocking TCP listening socket.
 *
 * @param port Local TCP port to bind.
 * @param backlog Listen backlog passed to `listen(2)`.
 * @return Listening file descriptor on success, or `-1` on failure.
 *
 * @post The returned descriptor has `SO_REUSEADDR` and `O_NONBLOCK` enabled.
 * @warning The caller owns the descriptor and must eventually call
 *          `close_socket()`.
 */
int create_server_socket(std::uint16_t port, int backlog = 16);

/**
 * @brief Resolve a host and establish a blocking TCP client connection.
 *
 * @param host Host name or numeric address.
 * @param port TCP port.
 * @return Connected file descriptor on success, or `-1` on failure.
 *
 * @warning The caller owns the descriptor and must eventually call
 *          `close_socket()`.
 */
int create_client_socket(const std::string& host, std::uint16_t port);

/**
 * @brief Enable nonblocking mode on a file descriptor.
 *
 * @param fd File descriptor to modify.
 * @return `true` if `O_NONBLOCK` was set successfully.
 */
bool set_non_blocking(int fd);

/**
 * @brief Read one newline-delimited line from a socket.
 *
 * @param fd Connected socket descriptor.
 * @param line Output string that is cleared before reading.
 * @return `true` if a line or partial EOF line was read, `false` on EOF before
 *         any bytes or on unrecoverable error.
 *
 * @note This helper performs blocking one-byte reads and is intended for the
 *       simple interactive client and benchmark control path, not the
 *       nonblocking event-loop server.
 */
bool read_line(int fd, std::string& line);

/**
 * @brief Write an entire buffer to a connected socket.
 *
 * @param fd Connected socket descriptor.
 * @param data Bytes to send.
 * @return `true` if every byte was written, `false` on unrecoverable error.
 *
 * @note Interrupted writes are retried.  Other write errors are returned to
 *       the caller.
 */
bool write_all(int fd, std::string_view data);

/**
 * @brief Close a socket descriptor if it is valid.
 *
 * @param fd Descriptor to close.  Negative descriptors are ignored.
 */
void close_socket(int fd);

}  // namespace net
