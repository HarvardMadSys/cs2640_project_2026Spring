# Covert Channels in the `io_uring` Runtime

**Author:** Joshua Gillett &lt;joshuagillett@college.harvard.edu&gt;
**Course:** CS 2640 (Spring 2026)

## Abstract

Linux's `io_uring` subsystem is an asynchronous interface used to
bypass the context-switching overhead of I/O system calls. `io_uring`
uses shared-memory ring buffers between the user and the kernel to
provide performant I/O operations. However, this still entails some
amount of syscalls. In order to push a submission queue to the kernel,
a participant process must call `io_uring_enter()`. If, as an
ambitious systems programmer, you want to avoid this, `io_uring`
exposes the option for a dedicated polling kthread (`SQPOLL`) that
drains submissions without entering the calling task. Prior
side-channel work on `io_uring` has focused on the contention against
the storage device or the PCIe bus. We instead ask whether the
runtime itself — its scheduler, registered-resource tables, and
documented cross-ring primitives — can be used to communicate between
two processes that share no permitted IPC.

We answer this question in the affirmative with three different
layers of required capability for the attacker.

1. When two rings can share an `SQPOLL` backend via
   `IORING_SETUP_ATTACH_WQ`, a single shared kernel polling loop is
   enough to carry ≈ 86 bits/sec at a five-fold cross-validated bit
   error rate (BER) of 0.09 % on a commodity laptop. We attribute
   this signal directly to the `SQPOLL` inner-loop scan via a
   Cohen's-*d* versus ring-fan-in sweep.
2. When the rings cannot share an `SQPOLL` backend, but both call
   `IORING_REIGSTER_FILES` on a common backing `struct file` (e.g.,
   `/dev/null`), a sender can cause contention in
   `IORING_REGISTER_FILES_UPDATE` by modulating the `f_ref.refcnt`
   cache line, for which a receiver can load on every
   `io_file_get_fixed` call. Repetition coding allows this channel to
   transmit ≈ 0.625 bits/sec with a BER of 0.08.
3. When neither shared `SQPOLL` nor common registered resources are
   available, but the sender is able to hold a file descriptor to the
   receiver's ring, `IORING_OP_MSG_RING` provides a documented and
   kernel-approved cross-ring CQE delivery primitive that achieves a
   BER of 0.008 at ≈ 18 bits/sec.


## Goals achieved

- 75 % tier (testbench + a runtime channel exists): achieved.
- 100 % tier (capacity and BER without storage I/O): achieved
  (Channel I delivers ≈ 86 bits/sec at near-zero LDA-CV-BER without
  any storage I/O; Channels II and III are additional positive
  results not in the original proposal).
- 125 % tier (mitigations): partially achieved. We give a layered
  defenses analysis but have not built or benchmarked any kernel-side
  patch.

## Layout

```
report.pdf       compiled USENIX-format report
report/          LaTeX source of the report
src/             C++ binaries (one per experimental variant)
include/         shared C++ headers
scripts/         build, run, and analysis scripts
results/         per-experiment CSV / JSON / PNG output
deps/            kernel-source excerpts cited in the audit
CMakeLists.txt   build configuration
ai-usage.md      AI usage report
```

## Build

Install dependencies (Fedora):

```bash
sudo dnf install cmake gcc-c++ liburing-devel pkgconf-pkg-config
```

Debian / Ubuntu: `cmake gcc-c++ liburing-dev pkg-config`.

Then:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

If the system `liburing` is too old, build a local one:

```bash
./scripts/build_liburing_prefix.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_PREFIX_PATH="$PWD/deps/liburing-install"
cmake --build build -j
```

## Reproducing the report

| Section                    | Script                              |
| -------------------------- | ----------------------------------- |
| Channel I (Sec. 5)         | `scripts/run_champion.sh`           |
| Channel II (Sec. 6)        | `scripts/run_regfile_repetition.sh` |
| No-`ATTACH_WQ` sweep (Tab. 2) | `scripts/run_track_b_bench.sh`   |
| Attribution (Fig. 1)       | `scripts/attribution_sweep.sh`      |
| Sub-10 ms sweep (Tab. 1)   | `scripts/champion_sweep.sh`         |

Each script writes a timestamped directory under `results/`.
Pre-computed runs the report points at are kept under
`results/channel*_*` and `results/track_b/`.

The two report figures are regenerated with:

```bash
python3 scripts/analyze_attribution.py \
  --sweep-dir results/channel1_attribution \
  --plot-out  report/ch1_attribution.png

python3 scripts/plot_k_sweep.py \
  results/channel2_repetition/aggregate.json \
  --out report/ch2_k_sweep.png
```

## Building the report

```bash
cd report
pdflatex main && bibtex main && pdflatex main && pdflatex main
cp main.pdf ../report.pdf
```

