/**
 * @file rdma_context.cpp
 * @brief Verbs resource setup, QP connection, and RDMA operation posting.
 * @ingroup networking
 */

#include "net/rdma_context.hpp"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <iostream>

namespace net {

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

bool RdmaContext::init(const char* device_name) {
    // Enumerate RDMA devices visible to the process.
    int num_devices = 0;
    ibv_device** dev_list = ibv_get_device_list(&num_devices);
    if (!dev_list || num_devices == 0) {
        std::cerr << "rdma: no RDMA devices found. Is rdma-core installed and a NIC present?\n";
        return false;
    }

    // Pick the requested device, or default to the first one found.
    ibv_device* dev = nullptr;
    for (int i = 0; i < num_devices; ++i) {
        if (device_name == nullptr ||
            std::strcmp(ibv_get_device_name(dev_list[i]), device_name) == 0) {
            dev = dev_list[i];
            break;
        }
    }
    ibv_free_device_list(dev_list);

    if (!dev) {
        std::cerr << "rdma: device '" << device_name << "' not found\n";
        return false;
    }

    std::cerr << "rdma: opening device " << ibv_get_device_name(dev) << '\n';

    // Open the device to obtain a context for all subsequent operations.
    ctx_ = ibv_open_device(dev);
    if (!ctx_) {
        std::cerr << "rdma: ibv_open_device failed\n";
        return false;
    }

    // A protection domain groups memory registrations and queue pairs so that
    // remote operations across PDs are rejected by the hardware.
    pd_ = ibv_alloc_pd(ctx_);
    if (!pd_) {
        std::cerr << "rdma: ibv_alloc_pd failed\n";
        destroy();
        return false;
    }

    // A single completion queue is shared by the send and receive paths.
    // 64 entries is enough for the pipelined workloads used in this project.
    cq_ = ibv_create_cq(ctx_, 64, nullptr, nullptr, 0);
    if (!cq_) {
        std::cerr << "rdma: ibv_create_cq failed\n";
        destroy();
        return false;
    }

    // Create a Reliable Connected queue pair.  RC provides in-order, lossless
    // delivery, matching the semantics of the TCP baseline.
    ibv_qp_init_attr qp_attr = {};
    qp_attr.send_cq          = cq_;
    qp_attr.recv_cq          = cq_;
    qp_attr.qp_type          = IBV_QPT_RC;
    qp_attr.sq_sig_all       = 0; // only generate completions for signaled WRs
    qp_attr.cap.max_send_wr  = 32;
    qp_attr.cap.max_recv_wr  = 32;
    qp_attr.cap.max_send_sge = 1;
    qp_attr.cap.max_recv_sge = 1;
    // Inline sends skip the MR for small messages; 64 bytes covers most keys.
    qp_attr.cap.max_inline_data = 64;

    qp_ = ibv_create_qp(pd_, &qp_attr);
    if (!qp_) {
        std::cerr << "rdma: ibv_create_qp failed\n";
        destroy();
        return false;
    }

    // Register the send and receive buffers with the protection domain.
    // LOCAL_WRITE is required for receive buffers.
    send_mr_ = ibv_reg_mr(pd_, send_buf_, sizeof(send_buf_),
                          IBV_ACCESS_LOCAL_WRITE);
    if (!send_mr_) {
        std::cerr << "rdma: ibv_reg_mr(send) failed\n";
        destroy();
        return false;
    }

    recv_mr_ = ibv_reg_mr(pd_, recv_buf_, sizeof(recv_buf_),
                          IBV_ACCESS_LOCAL_WRITE);
    if (!recv_mr_) {
        std::cerr << "rdma: ibv_reg_mr(recv) failed\n";
        destroy();
        return false;
    }

    // Transition QP: RESET -> INIT.
    // The QP must reach INIT before any receive work requests can be posted.
    ibv_qp_attr attr    = {};
    attr.qp_state        = IBV_QPS_INIT;
    attr.port_num        = port_num_;
    attr.pkey_index      = 0;
    // Grant remote peers read, write, and atomic access so one-sided operations
    // issued against this QP's memory regions are permitted.
    attr.qp_access_flags = IBV_ACCESS_REMOTE_READ |
                           IBV_ACCESS_REMOTE_WRITE |
                           IBV_ACCESS_REMOTE_ATOMIC;
    int flags = IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS;
    if (ibv_modify_qp(qp_, &attr, flags) != 0) {
        std::cerr << "rdma: modify_qp INIT failed: " << std::strerror(errno) << '\n';
        destroy();
        return false;
    }

    return true;
}

// ---------------------------------------------------------------------------
// Local info and exported region
// ---------------------------------------------------------------------------

QpInfo RdmaContext::local_info() const {
    QpInfo info  = {};
    info.qp_num  = qp_->qp_num;

    // Query port attributes to get the LID (zero on pure RoCE networks).
    ibv_port_attr port_attr = {};
    ibv_query_port(ctx_, port_num_, &port_attr);
    info.lid = port_attr.lid;

    // GID index 0 holds the GID derived from the NIC's MAC address and is
    // the correct choice for RoCE v2 on CloudLab nodes.
    ibv_gid gid;
    ibv_query_gid(ctx_, port_num_, 0, &gid);
    std::memcpy(info.gid, gid.raw, 16);

    // Fill one-sided region metadata if set_exported_region() was called.
    info.rkey      = exported_rkey_;
    info.addr      = exported_addr_;
    info.num_slots = exported_num_slots_;

    return info;
}

void RdmaContext::set_exported_region(const ibv_mr* mr, uint64_t num_slots) {
    exported_rkey_      = mr->rkey;
    exported_addr_      = reinterpret_cast<uint64_t>(mr->addr);
    exported_num_slots_ = num_slots;
}

// ---------------------------------------------------------------------------
// QP state machine: INIT -> RTR -> RTS
// ---------------------------------------------------------------------------

bool RdmaContext::connect(const QpInfo& remote) {
    // INIT -> RTR (Ready to Receive).
    // The address handle (ah_attr) tells the NIC how to route packets to the
    // peer.  is_global = 1 enables GID-based routing required for RoCE.
    ibv_qp_attr attr           = {};
    attr.qp_state              = IBV_QPS_RTR;
    attr.path_mtu              = IBV_MTU_1024;
    attr.dest_qp_num           = remote.qp_num;
    attr.rq_psn                = 0; // initial receive packet sequence number
    attr.max_dest_rd_atomic    = 16; // allow up to 16 outstanding RDMA READs
    attr.min_rnr_timer         = 12; // RNR NAK timer (4.096 us * 2^12)
    attr.ah_attr.is_global     = 1;
    attr.ah_attr.grh.sgid_index = 0;
    attr.ah_attr.grh.hop_limit  = 1;
    attr.ah_attr.dlid           = remote.lid;
    attr.ah_attr.port_num       = port_num_;
    std::memcpy(attr.ah_attr.grh.dgid.raw, remote.gid, 16);

    int flags = IBV_QP_STATE     | IBV_QP_AV          | IBV_QP_PATH_MTU |
                IBV_QP_DEST_QPN  | IBV_QP_RQ_PSN      |
                IBV_QP_MAX_DEST_RD_ATOMIC              | IBV_QP_MIN_RNR_TIMER;
    if (ibv_modify_qp(qp_, &attr, flags) != 0) {
        std::cerr << "rdma: modify_qp RTR failed: " << std::strerror(errno) << '\n';
        return false;
    }

    // RTR -> RTS (Ready to Send).
    ibv_qp_attr rts        = {};
    rts.qp_state           = IBV_QPS_RTS;
    rts.timeout            = 14;  // local ACK timeout (4.096 us * 2^14 ~ 67 ms)
    rts.retry_cnt          = 7;   // max retransmissions before error
    rts.rnr_retry          = 7;   // 7 = retry forever on RNR NAK
    rts.sq_psn             = 0;   // initial send packet sequence number
    rts.max_rd_atomic      = 16;  // outstanding RDMA READ / atomic initiator ops
    flags = IBV_QP_STATE     | IBV_QP_TIMEOUT   | IBV_QP_RETRY_CNT |
            IBV_QP_RNR_RETRY | IBV_QP_SQ_PSN    | IBV_QP_MAX_QP_RD_ATOMIC;
    if (ibv_modify_qp(qp_, &rts, flags) != 0) {
        std::cerr << "rdma: modify_qp RTS failed: " << std::strerror(errno) << '\n';
        return false;
    }

    return true;
}

// ---------------------------------------------------------------------------
// Two-sided: post_recv / post_send
// ---------------------------------------------------------------------------

bool RdmaContext::post_recv() {
    ibv_sge sge  = {};
    sge.addr     = reinterpret_cast<uint64_t>(recv_buf_);
    sge.length   = sizeof(recv_buf_);
    sge.lkey     = recv_mr_->lkey;

    ibv_recv_wr wr  = {};
    wr.wr_id        = 0;
    wr.sg_list      = &sge;
    wr.num_sge      = 1;

    ibv_recv_wr* bad = nullptr;
    if (ibv_post_recv(qp_, &wr, &bad) != 0) {
        std::cerr << "rdma: ibv_post_recv failed: " << std::strerror(errno) << '\n';
        return false;
    }
    return true;
}

bool RdmaContext::post_send(std::string_view data) {
    std::size_t len = (data.size() < sizeof(send_buf_)) ? data.size() : sizeof(send_buf_);
    std::memcpy(send_buf_, data.data(), len);

    ibv_sge sge  = {};
    sge.addr     = reinterpret_cast<uint64_t>(send_buf_);
    sge.length   = static_cast<uint32_t>(len);
    sge.lkey     = send_mr_->lkey;

    ibv_send_wr wr   = {};
    wr.wr_id         = 1;
    wr.sg_list       = &sge;
    wr.num_sge       = 1;
    wr.opcode        = IBV_WR_SEND;
    wr.send_flags    = IBV_SEND_SIGNALED; // generate a send completion

    ibv_send_wr* bad = nullptr;
    if (ibv_post_send(qp_, &wr, &bad) != 0) {
        std::cerr << "rdma: ibv_post_send failed: " << std::strerror(errno) << '\n';
        return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// One-sided: post_rdma_read / post_rdma_write / post_fetch_and_add
// ---------------------------------------------------------------------------

bool RdmaContext::post_rdma_read(uint64_t remote_addr, uint32_t remote_rkey,
                                 void* local_dst, uint32_t len,
                                 uint32_t local_lkey) {
    // local_lkey must come from an ibv_mr registered by the caller that covers
    // local_dst.  The caller must keep that MR registered until after
    // poll_completion() returns, because the NIC DMAs into local_dst
    // asynchronously after this function returns.
    ibv_sge sge  = {};
    sge.addr     = reinterpret_cast<uint64_t>(local_dst);
    sge.length   = len;
    sge.lkey     = local_lkey;

    ibv_send_wr wr         = {};
    wr.wr_id               = 2;
    wr.sg_list             = &sge;
    wr.num_sge             = 1;
    wr.opcode              = IBV_WR_RDMA_READ;
    wr.send_flags          = IBV_SEND_SIGNALED;
    wr.wr.rdma.remote_addr = remote_addr;
    wr.wr.rdma.rkey        = remote_rkey;

    ibv_send_wr* bad = nullptr;
    if (ibv_post_send(qp_, &wr, &bad) != 0) {
        std::cerr << "rdma: ibv_post_send(RDMA_READ) failed: " << std::strerror(errno) << '\n';
        return false;
    }
    return true;
}

bool RdmaContext::post_rdma_write(uint64_t remote_addr, uint32_t remote_rkey,
                                  const void* local_src, uint32_t len,
                                  uint32_t local_lkey) {
    // local_lkey must come from an ibv_mr registered by the caller that covers
    // local_src.  The caller must keep that MR registered until after
    // poll_completion() returns, because the NIC reads from local_src
    // asynchronously after this function returns.
    ibv_sge sge  = {};
    sge.addr     = reinterpret_cast<uint64_t>(const_cast<void*>(local_src));
    sge.length   = len;
    sge.lkey     = local_lkey;

    ibv_send_wr wr         = {};
    wr.wr_id               = 4;
    wr.sg_list             = &sge;
    wr.num_sge             = 1;
    wr.opcode              = IBV_WR_RDMA_WRITE;
    wr.send_flags          = IBV_SEND_SIGNALED;
    wr.wr.rdma.remote_addr = remote_addr;
    wr.wr.rdma.rkey        = remote_rkey;

    ibv_send_wr* bad = nullptr;
    if (ibv_post_send(qp_, &wr, &bad) != 0) {
        std::cerr << "rdma: ibv_post_send(RDMA_WRITE) failed: " << std::strerror(errno) << '\n';
        return false;
    }
    return true;
}

bool RdmaContext::post_fetch_and_add(uint64_t remote_addr, uint32_t remote_rkey,
                                     void* local_dst, uint64_t add_val,
                                     uint32_t local_lkey) {
    // FETCH_AND_ADD requires 8-byte alignment on both remote_addr and local_dst.
    // local_lkey must remain valid until after poll_completion() returns.
    ibv_sge sge  = {};
    sge.addr     = reinterpret_cast<uint64_t>(local_dst);
    sge.length   = sizeof(uint64_t);
    sge.lkey     = local_lkey;

    ibv_send_wr wr              = {};
    wr.wr_id                    = 3;
    wr.sg_list                  = &sge;
    wr.num_sge                  = 1;
    wr.opcode                   = IBV_WR_ATOMIC_FETCH_AND_ADD;
    wr.send_flags               = IBV_SEND_SIGNALED;
    wr.wr.atomic.remote_addr    = remote_addr;
    wr.wr.atomic.rkey           = remote_rkey;
    wr.wr.atomic.compare_add    = add_val;

    ibv_send_wr* bad = nullptr;
    if (ibv_post_send(qp_, &wr, &bad) != 0) {
        std::cerr << "rdma: ibv_post_send(FETCH_AND_ADD) failed: " << std::strerror(errno) << '\n';
        return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Completion polling (busy-spin)
// ---------------------------------------------------------------------------

bool RdmaContext::poll_completion() {
    ibv_wc wc  = {};
    int    ret = 0;

    // Busy-spin until the NIC deposits a completion entry.
    // This is the standard approach for low-latency RDMA; sleeping here would
    // add scheduling jitter that would distort the benchmark measurements.
    while ((ret = ibv_poll_cq(cq_, 1, &wc)) == 0) {}

    if (ret < 0) {
        std::cerr << "rdma: ibv_poll_cq returned error\n";
        return false;
    }
    if (wc.status != IBV_WC_SUCCESS) {
        std::cerr << "rdma: work completion error: "
                  << ibv_wc_status_str(wc.status) << '\n';
        return false;
    }

    // Record how many bytes arrived for receive completions.
    if (wc.opcode == IBV_WC_RECV) {
        recv_len_ = wc.byte_len;
    }

    return true;
}

// ---------------------------------------------------------------------------
// Memory registration helper
// ---------------------------------------------------------------------------

ibv_mr* RdmaContext::reg_mr(void* buf, std::size_t len, int access_flags) {
    ibv_mr* mr = ibv_reg_mr(pd_, buf, len, access_flags);
    if (!mr) {
        std::cerr << "rdma: ibv_reg_mr failed: " << std::strerror(errno) << '\n';
    }
    return mr;
}

std::string_view RdmaContext::recv_data() const {
    return {recv_buf_, recv_len_};
}

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------

void RdmaContext::destroy() {
    // Deregister in the reverse order of allocation.
    if (send_mr_) { ibv_dereg_mr(send_mr_);  send_mr_ = nullptr; }
    if (recv_mr_) { ibv_dereg_mr(recv_mr_);  recv_mr_ = nullptr; }
    if (qp_)      { ibv_destroy_qp(qp_);     qp_      = nullptr; }
    if (cq_)      { ibv_destroy_cq(cq_);     cq_      = nullptr; }
    if (pd_)      { ibv_dealloc_pd(pd_);     pd_      = nullptr; }
    if (ctx_)     { ibv_close_device(ctx_);  ctx_     = nullptr; }
}

} // namespace net
