/**
 * @file main.cpp
 * @brief Interactive TCP client for manually exercising the text protocol.
 * @ingroup executables
 */

// Socket helpers for connection setup and line-oriented I/O.
#include "net/socket_utils.hpp"
// The same parser used by the server is reused to decide when QUIT was entered.
#include "protocol/text_protocol.hpp"

// Exit codes and general runtime support.
#include <cstdlib>
// Stream output is used for prompts, responses, and errors.
#include <iostream>
// std::string stores the command lines entered by the user.
#include <string>

// Keep the helper functions local to this translation unit.
namespace {

// Send one line to the server and print the server's single-line response.
bool send_request(int fd, const std::string& line) {
    // write_all guarantees the complete request line is transmitted unless an error occurs.
    if (!net::write_all(fd, line + "\n")) {
        std::cerr << "failed to send request\n";
        return false;
    }

    // The server replies with exactly one line per command.
    std::string response;
    if (!net::read_line(fd, response)) {
        std::cerr << "server closed the connection\n";
        return false;
    }

    // Show the raw protocol response so the client stays simple and transparent.
    std::cout << response << '\n';
    return true;
}

}  // namespace

// Connect to the server and run a tiny interactive command shell.
int main(int argc, char* argv[]) {
    // Default to localhost and the standard project port when no arguments are given.
    const std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    // Parse the port from argv if supplied, otherwise use 9090.
    const std::uint16_t port = static_cast<std::uint16_t>(argc > 2 ? std::stoi(argv[2]) : 9090);

    // Open a TCP client socket and connect to the server.
    int fd = net::create_client_socket(host, port);
    if (fd < 0) {
        std::cerr << "failed to connect to " << host << ':' << port << '\n';
        return EXIT_FAILURE;
    }

    // Tell the user the connection succeeded and explain the supported commands.
    std::cout << "connected to " << host << ':' << port << '\n';
    std::cout << "type commands like: SET key value | GET key | DEL key | QUIT\n";

    // Read lines from stdin until EOF, an error, or QUIT.
    std::string line;
    while (true) {
        // Prompt for the next command.
        std::cout << "> ";
        if (!std::getline(std::cin, line)) {
            break;
        }

        // Ignore blank lines so accidental enter presses do not generate traffic.
        if (line.empty()) {
            continue;
        }

        // Send the user-supplied line to the server and print its response.
        if (!send_request(fd, line)) {
            break;
        }

        // Reuse the parser locally so the client can stop on QUIT without waiting for another prompt.
        const protocol::Request request = protocol::parse_request(line);
        if (request.type == protocol::RequestType::Quit) {
            break;
        }
    }

    // Close the connection before exiting.
    net::close_socket(fd);
    return EXIT_SUCCESS;
}
