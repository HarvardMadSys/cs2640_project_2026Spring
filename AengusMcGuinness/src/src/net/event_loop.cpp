/**
 * @file event_loop.cpp
 * @brief Implementation of the coroutine scheduler used by the TCP server.
 * @ingroup networking
 */

// The implementation of the coroutine scheduler declared in the header.
#include "net/event_loop.hpp"

// `cerrno` provides access to errno after system calls fail.
#include <cerrno>
// `exception` is needed because coroutine failures terminate the process.
#include <exception>
// `poll.h` gives us poll(2), which is the core readiness primitive here.
#include <poll.h>
// `stdexcept` is used for runtime_error when poll fails unexpectedly.
#include <stdexcept>
// `utility` provides std::exchange for move operations.
#include <utility>
// `vector` is used to build a temporary pollfd array every loop iteration.
#include <vector>

// Put the implementation in the networking namespace from the header.
namespace net {

// Construct a Task from an already-created coroutine handle.
EventLoop::Task::Task(handle_type handle) noexcept : handle_(handle) {}

// Move construction transfers the coroutine handle and clears the source.
EventLoop::Task::Task(Task&& other) noexcept : handle_(std::exchange(other.handle_, nullptr)) {}

// Move assignment first destroys any existing frame, then steals the other task's handle.
EventLoop::Task& EventLoop::Task::operator=(Task&& other) noexcept {
    if (this != &other) {
        if (handle_ != nullptr) {
            handle_.destroy();
        }
        handle_ = std::exchange(other.handle_, nullptr);
    }
    return *this;
}

// Destroy the coroutine frame if this task still owns one.
EventLoop::Task::~Task() {
    if (handle_ != nullptr) {
        handle_.destroy();
    }
}

// Attach the task to the loop and queue it for initial execution.
void EventLoop::Task::start(EventLoop& loop) {
    if (handle_ == nullptr) {
        return;
    }

    // Store the owning loop in the promise so the final suspend path can clean up correctly.
    handle_.promise().loop = &loop;
    // Enqueue the task so run() can resume it on the next dispatch cycle.
    loop.schedule(handle_);
    // The Task object no longer owns the handle after start() hands it to the loop.
    handle_ = nullptr;
}

// Return the Task wrapper object from the coroutine frame.
EventLoop::Task EventLoop::Task::promise_type::get_return_object() noexcept {
    return Task{std::coroutine_handle<promise_type>::from_promise(*this)};
}

// Start suspended so the creator can register the task before it runs.
std::suspend_always EventLoop::Task::promise_type::initial_suspend() noexcept {
    return {};
}

// The final awaiter always suspends so the scheduler can destroy the frame explicitly.
bool EventLoop::Task::promise_type::FinalAwaiter::await_ready() const noexcept {
    return false;
}

// Return the final awaiter object used to complete coroutine shutdown.
EventLoop::Task::promise_type::FinalAwaiter EventLoop::Task::promise_type::final_suspend() noexcept {
    return {};
}

// This coroutine type returns no result value.
void EventLoop::Task::promise_type::return_void() noexcept {}

// Any uncaught exception aborts the process because this demo server does not try to recover.
void EventLoop::Task::promise_type::unhandled_exception() {
    std::terminate();
}

// A read awaiter never completes immediately in this implementation.
bool EventLoop::ReadAwaiter::await_ready() const noexcept {
    return false;
}

// Suspend the coroutine and register it with the loop's read waiters.
void EventLoop::ReadAwaiter::await_suspend(std::coroutine_handle<> handle) const noexcept {
    loop->wait_read(fd, handle);
}

// A write awaiter also always suspends until the file descriptor is ready.
bool EventLoop::WriteAwaiter::await_ready() const noexcept {
    return false;
}

// Suspend the coroutine and register it with the loop's write waiters.
void EventLoop::WriteAwaiter::await_suspend(std::coroutine_handle<> handle) const noexcept {
    loop->wait_write(fd, handle);
}

// Helper that creates a read awaiter bound to this loop and the requested descriptor.
EventLoop::ReadAwaiter EventLoop::readable(int fd) noexcept {
    return ReadAwaiter{this, fd};
}

// Helper that creates a write awaiter bound to this loop and the requested descriptor.
EventLoop::WriteAwaiter EventLoop::writable(int fd) noexcept {
    return WriteAwaiter{this, fd};
}

// Queue a coroutine task for later execution.
void EventLoop::spawn(Task&& task) {
    task.start(*this);
}

// Add a coroutine handle to the ready queue.
void EventLoop::schedule(std::coroutine_handle<> handle) {
    ready_.push_back(handle);
}

// Request a graceful stop after the current batch of ready coroutines is drained.
void EventLoop::stop() {
    stopping_ = true;
}

// Destroy a coroutine frame that has reached its final suspend point.
void EventLoop::complete(std::coroutine_handle<> handle) {
    handle.destroy();
}

// Register a coroutine that should resume when fd becomes readable.
void EventLoop::wait_read(int fd, std::coroutine_handle<> handle) {
    read_waiters_[fd] = handle;
}

// Register a coroutine that should resume when fd becomes writable.
void EventLoop::wait_write(int fd, std::coroutine_handle<> handle) {
    write_waiters_[fd] = handle;
}

// Main dispatch loop: resume ready coroutines, then sleep in poll() for new readiness events.
void EventLoop::run() {
    while (!stopping_) {
        // First, run anything already ready without entering the kernel again.
        while (!ready_.empty()) {
            auto handle = ready_.front();
            ready_.pop_front();
            if (handle != nullptr && !handle.done()) {
                handle.resume();
            }
        }

        // If stop() was called during a coroutine resume, exit cleanly now.
        if (stopping_) {
            break;
        }

        // If nothing is waiting on any descriptor, there is no more work to do.
        if (read_waiters_.empty() && write_waiters_.empty()) {
            break;
        }

        // Rebuild the pollfd array from the current waiter maps on each iteration.
        std::vector<pollfd> poll_fds;
        poll_fds.reserve(read_waiters_.size() + write_waiters_.size());

        // Add every descriptor waiting for readability.
        for (const auto& [fd, _] : read_waiters_) {
            pollfd pfd {};
            pfd.fd = fd;
            pfd.events |= POLLIN;
            // If the same descriptor also has a writer waiting, ask for both events.
            if (write_waiters_.find(fd) != write_waiters_.end()) {
                pfd.events |= POLLOUT;
            }
            poll_fds.push_back(pfd);
        }

        // Add any write-only descriptors that were not already included above.
        for (const auto& [fd, _] : write_waiters_) {
            if (read_waiters_.find(fd) != read_waiters_.end()) {
                continue;
            }
            pollfd pfd {};
            pfd.fd = fd;
            pfd.events = POLLOUT;
            poll_fds.push_back(pfd);
        }

        // If there are no descriptors to poll, the loop cannot make forward progress.
        if (poll_fds.empty()) {
            break;
        }

        // Sleep in the kernel until some descriptor becomes ready.
        const int rc = ::poll(poll_fds.data(), poll_fds.size(), -1);
        if (rc < 0) {
            if (errno == EINTR) {
                // A signal interrupted poll; rebuild the descriptor list and try again.
                continue;
            }
            // Any other poll failure is unexpected in this small event loop.
            throw std::runtime_error("poll failed");
        }

        // Collect resumed coroutines first so we can mutate the wait maps safely afterward.
        std::vector<std::coroutine_handle<>> ready_handles;

        // Inspect every descriptor returned by poll.
        for (const pollfd& pfd : poll_fds) {
            if (pfd.revents == 0) {
                continue;
            }

            // Treat error, hangup, and invalid states as readiness so the coroutine can decide how to react.
            const bool readable = (pfd.revents & (POLLIN | POLLERR | POLLHUP | POLLNVAL)) != 0;
            // Treat the same error conditions as writable too, because the waiting coroutine must wake up.
            const bool writable = (pfd.revents & (POLLOUT | POLLERR | POLLHUP | POLLNVAL)) != 0;

            if (readable) {
                auto it = read_waiters_.find(pfd.fd);
                if (it != read_waiters_.end()) {
                    ready_handles.push_back(it->second);
                    read_waiters_.erase(it);
                }
            }

            if (writable) {
                auto it = write_waiters_.find(pfd.fd);
                if (it != write_waiters_.end()) {
                    ready_handles.push_back(it->second);
                    write_waiters_.erase(it);
                }
            }
        }

        // Move all awakened coroutines back to the ready queue.
        for (auto handle : ready_handles) {
            schedule(handle);
        }
    }
}

// End of the networking namespace.
}  // namespace net
