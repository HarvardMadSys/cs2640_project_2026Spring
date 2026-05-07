/**
 * @file rdma_context.hpp
 * @brief RAII-style wrapper around one reliable-connected RDMA queue pair.
 * @ingroup networking
 *
 * `RdmaContext` owns the verbs resources needed by one peer connection:
 * device context, protection domain, completion queue, queue pair, and pinned
 * send/receive buffers.  It supports both two-sided send/receive and one-sided
 * read/write/atomic operations.
 */
#pragma once

#include <infiniband/verbs.h>

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace net {

/**
 * @brief Queue-pair and memory-region metadata exchanged before RDMA traffic.
 * @ingroup networking
 *
 * The project exchanges this structure over a normal TCP side channel.  Both
 * peers need QP routing information to connect.  One-sided clients also need
 * the server's exported memory address and remote key.
 */
struct QpInfo {
    /// Queue-pair number on the sending side.
    uint32_t qp_num;

    /// Local identifier.  This is commonly zero on pure RoCE fabrics.
    uint16_t lid;

    /// GID in IPv6 raw-byte form; index 0 is used by this project.
    uint8_t gid[16];

    /// Remote key for the exported one-sided memory region.
    uint32_t rkey;

    /// Base virtual address of the exported one-sided memory region.
    uint64_t addr;

    /// Number of `kvstore::RdmaSlot` entries in the exported region.
    uint64_t num_slots;
};

/// Maximum payload bytes in the reusable two-sided send/receive buffers.
static constexpr std::size_t RDMA_MSG_SIZE = 4096;

/**
 * @brief Owns one RDMA reliable-connected queue pair and its resources.
 * @ingroup networking
 *
 * Typical two-sided flow:
 *
 * 1. `init()`
 * 2. exchange `local_info()` with the peer over TCP
 * 3. `connect(remote_info)`
 * 4. `post_recv()`, `post_send()`, and `poll_completion()`
 *
 * Typical one-sided client flow:
 *
 * 1. complete the same QP handshake
 * 2. register a local destination/source buffer with `reg_mr()`
 * 3. call `post_rdma_read()`, `post_rdma_write()`, or
 *    `post_fetch_and_add()`
 * 4. call `poll_completion()` before reusing or deregistering the buffer
 *
 * @warning The class is not movable or copyable because registered buffers and
 *          verbs objects are tied to stable addresses.
 */
class RdmaContext {
public:
    RdmaContext() = default;

    /// Release all verbs resources in reverse-allocation order.
    ~RdmaContext() { destroy(); }

    RdmaContext(const RdmaContext&) = delete;
    RdmaContext& operator=(const RdmaContext&) = delete;

    /**
     * @brief Open an RDMA device and create the queue-pair resources.
     *
     * @param device_name Optional verbs device name such as `mlx5_3`.  Passing
     *        `nullptr` selects the first visible device.
     * @return `true` if the context reached QP state `INIT`.
     *
     * @post On success, send/receive buffers are registered and receive work
     *       requests may be posted.
     */
    bool init(const char* device_name = nullptr);

    /**
     * @brief Return local metadata to send to the peer.
     *
     * @return QP routing information plus exported-region metadata if
     *         `set_exported_region()` has been called.
     */
    QpInfo local_info() const;

    /**
     * @brief Attach one registered memory region to `local_info()` output.
     *
     * @param mr Memory region exported to one-sided clients.
     * @param num_slots Number of fixed-size slots in the exported region.
     *
     * @pre `mr` must remain valid for as long as peers may issue one-sided
     *      operations against the exported address/rkey.
     */
    void set_exported_region(const ibv_mr* mr, uint64_t num_slots);

    /**
     * @brief Transition the local QP from `INIT` through `RTR` to `RTS`.
     *
     * @param remote Metadata received from the peer.
     * @return `true` if both state transitions succeed.
     */
    bool connect(const QpInfo& remote);

    /**
     * @brief Post one receive work request for a future two-sided send.
     *
     * @return `true` if the receive was accepted by the verbs provider.
     */
    bool post_recv();

    /**
     * @brief Post one two-sided RDMA send.
     *
     * @param data Bytes to copy into the internal send buffer.
     * @return `true` if the send work request was posted.
     *
     * @note Payloads larger than `RDMA_MSG_SIZE` are truncated by this helper.
     */
    bool post_send(std::string_view data);

    /**
     * @brief Post one one-sided RDMA read.
     *
     * @param remote_addr Remote virtual address to read from.
     * @param remote_rkey Remote key authorizing access to `remote_addr`.
     * @param local_dst Registered local destination buffer.
     * @param len Number of bytes to read.
     * @param local_lkey Local key for the memory region covering `local_dst`.
     * @return `true` if the work request was posted.
     *
     * @pre `local_dst` must remain registered and alive until a successful
     *      `poll_completion()` observes this operation's completion.
     */
    bool post_rdma_read(uint64_t remote_addr, uint32_t remote_rkey,
                        void* local_dst, uint32_t len, uint32_t local_lkey);

    /**
     * @brief Post one one-sided RDMA write.
     *
     * @param remote_addr Remote virtual address to write to.
     * @param remote_rkey Remote key authorizing access to `remote_addr`.
     * @param local_src Registered local source buffer.
     * @param len Number of bytes to write.
     * @param local_lkey Local key for the memory region covering `local_src`.
     * @return `true` if the work request was posted.
     *
     * @pre `local_src` must remain registered and alive until completion.
     */
    bool post_rdma_write(uint64_t remote_addr, uint32_t remote_rkey,
                         const void* local_src, uint32_t len, uint32_t local_lkey);

    /**
     * @brief Post one RDMA atomic fetch-and-add.
     *
     * @param remote_addr 8-byte-aligned remote counter address.
     * @param remote_rkey Remote key authorizing atomic access.
     * @param local_dst Registered 8-byte local buffer that receives the old value.
     * @param add_val Value to add to the remote counter.
     * @param local_lkey Local key for the memory region covering `local_dst`.
     * @return `true` if the atomic work request was posted.
     *
     * @pre `remote_addr` and `local_dst` must both be 8-byte aligned.
     */
    bool post_fetch_and_add(uint64_t remote_addr, uint32_t remote_rkey,
                            void* local_dst, uint64_t add_val, uint32_t local_lkey);

    /**
     * @brief Busy-poll the completion queue until one completion appears.
     *
     * @return `true` for a successful work completion; `false` for CQ errors or
     *         failed work completions.
     *
     * @note Busy-polling is intentional for the benchmark because sleeping in
     *       the data path would add scheduler jitter to latency measurements.
     */
    bool poll_completion();

    /**
     * @brief Register an additional local memory buffer.
     *
     * @param buf Buffer start address.
     * @param len Buffer length in bytes.
     * @param access_flags Verbs access flags such as `IBV_ACCESS_LOCAL_WRITE`.
     * @return Registered memory region, or `nullptr` on failure.
     *
     * @warning The caller owns the returned `ibv_mr*` and must deregister it.
     */
    ibv_mr* reg_mr(void* buf, std::size_t len, int access_flags);

    /**
     * @brief View the bytes received by the most recent receive completion.
     */
    std::string_view recv_data() const;

    /**
     * @brief Release all verbs resources owned by this context.
     *
     * Safe to call more than once.
     */
    void destroy();

    /**
     * @brief Expose the protection domain for registering external buffers.
     */
    ibv_pd* pd() const { return pd_; }

private:
    /// Opened RDMA device context.
    ibv_context* ctx_ = nullptr;

    /// Protection domain for queue pair and memory registrations.
    ibv_pd* pd_ = nullptr;

    /// Shared completion queue for send and receive completions.
    ibv_cq* cq_ = nullptr;

    /// Reliable Connected queue pair.
    ibv_qp* qp_ = nullptr;

    /// Memory region for `send_buf_`.
    ibv_mr* send_mr_ = nullptr;

    /// Memory region for `recv_buf_`.
    ibv_mr* recv_mr_ = nullptr;

    /// Reusable pinned send buffer for two-sided messages.
    char send_buf_[RDMA_MSG_SIZE] = {};

    /// Reusable pinned receive buffer for two-sided messages.
    char recv_buf_[RDMA_MSG_SIZE] = {};

    /// Valid byte count from the most recent receive completion.
    uint32_t recv_len_ = 0;

    /// Remote key for the exported one-sided memory region.
    uint32_t exported_rkey_ = 0;

    /// Base address for the exported one-sided memory region.
    uint64_t exported_addr_ = 0;

    /// Number of slots in the exported one-sided memory region.
    uint64_t exported_num_slots_ = 0;

    /// RDMA port number used on the selected device.
    uint8_t port_num_ = 1;
};

}  // namespace net
