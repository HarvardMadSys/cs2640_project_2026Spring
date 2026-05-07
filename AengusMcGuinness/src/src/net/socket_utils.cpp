/**
 * @file socket_utils.cpp
 * @brief POSIX socket setup and blocking line-I/O helpers.
 * @ingroup networking
 */

// Bring in the helper declarations.
#include "net/socket_utils.hpp"

// Standard networking headers for POSIX sockets.
#include <arpa/inet.h>
// `errno` is inspected for recoverable system-call failures.
#include <cerrno>
// `cstring` gives us `std::strerror` elsewhere and matches the socket code style.
#include <cstring>
// `fcntl` is used to toggle nonblocking mode.
#include <fcntl.h>
// `netdb` is used for DNS and address resolution.
#include <netdb.h>
// `netinet/in.h` defines IPv4 socket address structures.
#include <netinet/in.h>
// `sys/socket.h` gives access to socket, bind, listen, connect, send, and recv.
#include <sys/socket.h>
// `unistd.h` gives us close().
#include <unistd.h>

// Namespace for the socket helpers.
namespace net {
// Keep one helper private because only this file needs it.
namespace {

// Enable SO_REUSEADDR so restarting the server is less annoying during testing.
bool set_reuseaddr(int fd) {
    // `1` means the option is turned on.
    int enabled = 1;
    // Ask the kernel to allow reusing the address quickly after shutdown.
    return ::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled)) == 0;
}

}  // namespace

// Turn on O_NONBLOCK for a descriptor.
bool set_non_blocking(int fd) {
    // Query the current file status flags.
    int flags = ::fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        // If the query failed, we cannot safely modify the descriptor.
        return false;
    }

    // Write the old flags back with O_NONBLOCK added.
    return ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

// Create a listening TCP socket on the requested port.
int create_server_socket(std::uint16_t port, int backlog) {
    // Create an IPv4 stream socket.
    int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }

    // Make quick restarts easier by reusing the address.
    if (!set_reuseaddr(fd)) {
        close_socket(fd);
        return -1;
    }

    // Prepare the server address structure.
    sockaddr_in addr {};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(port);

    // Bind the socket to the requested port.
    if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        close_socket(fd);
        return -1;
    }

    // Mark the socket as a listening socket.
    if (::listen(fd, backlog) < 0) {
        close_socket(fd);
        return -1;
    }

    // The server listens in nonblocking mode so the event loop can poll it.
    if (!set_non_blocking(fd)) {
        close_socket(fd);
        return -1;
    }

    // Return the ready-to-use listening descriptor.
    return fd;
}

// Resolve a host name and connect to the first viable address.
int create_client_socket(const std::string& host, std::uint16_t port) {
    // Fill out the address hints for `getaddrinfo`.
    addrinfo hints {};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    // `getaddrinfo` writes its answer into this pointer.
    addrinfo* result = nullptr;
    // Convert the numeric port to a string because `getaddrinfo` expects one.
    const std::string port_string = std::to_string(port);
    // Resolve the host and port into a linked list of address candidates.
    if (::getaddrinfo(host.c_str(), port_string.c_str(), &hints, &result) != 0) {
        return -1;
    }

    // Try each candidate until one connects successfully.
    int client_fd = -1;
    for (addrinfo* current = result; current != nullptr; current = current->ai_next) {
        // Create a socket matching this address candidate.
        client_fd = ::socket(current->ai_family, current->ai_socktype, current->ai_protocol);
        if (client_fd < 0) {
            continue;
        }

        // Attempt the connection.
        if (::connect(client_fd, current->ai_addr, current->ai_addrlen) == 0) {
            break;
        }

        // Close the failed socket before trying the next candidate.
        close_socket(client_fd);
        client_fd = -1;
    }

    // Release the address list returned by `getaddrinfo`.
    ::freeaddrinfo(result);
    // Return either a connected descriptor or -1 on failure.
    return client_fd;
}

// Read a single newline-delimited command from the socket.
bool read_line(int fd, std::string& line) {
    // Clear any previous contents before reading a fresh line.
    line.clear();

    // Reuse a one-byte buffer to keep the logic simple and exact.
    char ch = '\0';
    while (true) {
        // Read one byte at a time until a newline arrives.
        const ssize_t bytes_read = ::recv(fd, &ch, 1, 0);
        if (bytes_read == 0) {
            // EOF is acceptable only if we already collected some data.
            return !line.empty();
        }
        if (bytes_read < 0) {
            // Retry if the call was interrupted by a signal.
            if (errno == EINTR) {
                continue;
            }
            // Any other error terminates the read.
            return false;
        }

        // A newline marks the end of the request line.
        if (ch == '\n') {
            return true;
        }
        // Ignore carriage returns so CRLF also works.
        if (ch != '\r') {
            line.push_back(ch);
        }
    }
}

// Send the entire buffer, retrying until every byte is written.
bool write_all(int fd, std::string_view data) {
    // Track how much of the buffer we have already sent.
    std::size_t total_written = 0;
    while (total_written < data.size()) {
        // Try to send the remaining bytes in one call.
        const ssize_t bytes_written = ::send(
            fd,
            data.data() + total_written,
            data.size() - total_written,
            0
        );

        if (bytes_written < 0) {
            // Interrupted writes should simply be retried.
            if (errno == EINTR) {
                continue;
            }
            // Any other error means the write failed.
            return false;
        }

        // Advance the cursor by the number of bytes the kernel accepted.
        total_written += static_cast<std::size_t>(bytes_written);
    }

    // At this point the full buffer has been sent.
    return true;
}

// Close a socket only if the descriptor looks valid.
void close_socket(int fd) {
    if (fd >= 0) {
        ::close(fd);
    }
}

// Close the namespace.
}  // namespace net
