# CS2640 Final Project: NVMe Storage Benchmarking Framework
## RocksDB and MongoDB on CloudLab Bare-Metal NVMe

### Quick Start
```bash
# On CloudLab node after setup completes:
cd /local/repository
sudo ./benchmarks/run_all.sh
```

Note: some path names may not work since the repository was restructured for submission after the benchmarks were tested.

# Project Repository Overview
This project implements a comprehensive performance benchmarking framework for next-generation NVMe SSDs, focusing on Zoned Namespaces (ZNS) and Flexible Data Placement (FDP). The repository is structured to support both bare-metal experimentation on CloudLab and high-fidelity QEMU emulation.

## 📁 Repository Structure
```
cs2640-final-project/
├── benchmarks/                  # Core benchmarking logic
│   ├── run_all.sh             # Master script (CloudLab bare-metal)
│   ├── run_emulated.sh        # Master script (QEMU emulation)
│   ├── setup_qemu_vm.sh       # QEMU VM setup and configuration
│   ├── RocksDB/               # RocksDB-specific benchmarks
│   ├── mongodb/               # MongoDB-specific benchmarks
│   └── fio-workloads/         # Low-level I/O kernel-bypass tests
├── report/                    # LaTeX research papers
│   ├── usenix2019_v1.0.tex      # Original submission (Conference Style)
│   └── usenix2019_v3.1.tex      # Revised version (arXiv Style)
├── slides/                    # Presentation slides
│   ├── Final_Project_Slides.pptx
│   └── Final_Project_Slides_Draft.key
└── build_artifacts/           # Compiled software and system images
    ├── qemu_image/            # VM disk images (e.g., Ubuntu 22.04 + ZNS support)
    ├── RocksDB/               # Pre-compiled RocksDB binaries (db_bench)
    ├── mongodb/               # Pre-compiled MongoDB binaries
    ├── fio/                   # Pre-compiled fio toolchain
    └── fvm/                   # Flash Virtual Machine utilities
```

## 🚀 Benchmarking Workloads
The framework evaluates performance across three storage interfaces to highlight the trade-offs between control, flexibility, and ease of use:

1.  **ZNS (Zoned Namespaces)**:
    *   **Interface**: Strict host-managed sequential zones.
    *   **Implementation**: RocksDB with ZNS plugin, FIO raw device access.
    *   **Key Metric**: Measures WAF reduction by eliminating internal SSD garbage collection (GC).

2.  **FDP (Flexible Data Placement)**:
    *   **Interface**: Hint-based I/O placement with backward compatibility.
    *   **Implementation**: RocksDB FDP mode, MongoDB FDP integration.
    *   **Key Metric**: Compares WAF and latency against conventional SSDs under mixed workloads.

3.  **Conventional (CONV)**:
    *   **Interface**: Standard NVMe block interface (baseline).
    *   **Implementation**: Standard RocksDB, standard MongoDB.
    *   **Key Metric**: Establishes baseline WAF and performance metrics for comparison.

## 🔧 Execution Environments
The project supports two distinct environments to validate results across different abstraction levels:

### CloudLab Bare-Metal (Production)
- **Goal**: Native performance validation.
- **Hardware**: Real ZNS and FDP NVMe SSDs.
- **Script**: `benchmarks/run_all.sh`
- **Method**: Builds tools directly on the node or uses `build_artifacts/`.

### QEMU Emulation (Research)
- **Goal**: Reproducibility and hardware accessibility.
- **Hardware**: Host CPU with emulated NVMe controllers (NVMe-oF/TCP).
- **Script**: `benchmarks/run_emulated.sh`
- **Method**: Uses `build_artifacts/qemu_image/` to boot a pre-configured Ubuntu VM with ZNS and FDP drivers.

## Paper Artifacts
The repository includes complete research papers detailing the methodology and results:
- `report/usenix2019_v1.0.tex`: Original conference submission quality paper.
- `report/usenix2019_v3.1.tex`: Extended/final version with additional analysis and figures.

These documents contain extensive graphs and tables analyzing:
- Write Amplification Factors (WAF)
- Throughput (IOPS)
- Latency Percentiles (P50, P99, P100)
- Energy Efficiency

