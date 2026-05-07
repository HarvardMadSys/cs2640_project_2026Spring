"""
CloudLab profile for CS2640 Final Project:
NVMe Storage Benchmarking -- Conventional vs ZNS vs FDP.

Requests a single bare-metal node with NVMe SSDs. Benchmarks run in two
modes:
  1. NATIVE -- db_bench / YCSB / cachebench directly on the physical NVMe
  2. EMULATED -- same workloads inside a KVM-accelerated QEMU VM against
     emulated ZNS and FDP NVMe namespaces (backed by the real NVMe)

This dual-mode approach produces the comprehensive result matrix comparing
conventional NVMe, ZNS, and FDP across all three database engines.
"""

import geni.portal as portal
import geni.rspec.pg as pg

# ---------------------------------------------------------------------------
# Portal context and parameters
# ---------------------------------------------------------------------------
pc = portal.Context()

pc.defineParameter(
    "hardware_type",
    "Hardware Type",
    portal.ParameterType.STRING,
    "c6620",
    [
        # --- Utah (recommended: best NVMe + KVM) ---
        ("c6620",    "c6620 - Utah, 28c Xeon, 2x800GB NVMe Gen4, 100Gb"),
        ("d760",     "d760 - Utah, 64c Xeon, 2x1.6TB NVMe Gen5, 100Gb"),
        ("d7615",    "d7615 - Utah, 32c EPYC, 2x1.6TB NVMe Gen5, 100Gb"),
        ("d760-hbm", "d760-hbm - Utah, 64c Xeon+HBM, 2x1.6TB NVMe Gen5"),
        ("c6525-100g", "c6525-100g - Utah, 24c EPYC, 2x1.6TB NVMe Gen4, 100Gb"),
        ("d750",     "d750 - Utah, 16c Xeon, Optane+NVMe Gen4, 25Gb"),
        # --- Wisconsin ---
        ("sm110p",   "sm110p - Wisc, 16c Xeon, 4x960GB Samsung NVMe Gen4"),
        ("sm220u",   "sm220u - Wisc, 32c Xeon, 8x960GB Samsung NVMe Gen4"),
        ("d7525",    "d7525 - Wisc, 32c EPYC, 1x1.6TB NVMe Gen4, A30 GPU"),
        ("d8545",    "d8545 - Wisc, 48c EPYC, 1x1.6TB NVMe Gen4, 4xA100"),
        # --- Clemson ---
        ("r650",     "r650 - Clem, 72c Xeon, 1x1.6TB NVMe Gen4, 100Gb"),
        ("r6525",    "r6525 - Clem, 64c EPYC, 1x1.6TB NVMe Gen4, 100Gb"),
        ("r6615",    "r6615 - Clem, 32c EPYC, 2x800GB NVMe Gen4, 100Gb"),
    ],
    "Bare-metal node type. Must have NVMe SSDs and KVM support (x86_64).",
)

pc.defineParameter(
    "os_image",
    "OS Image",
    portal.ParameterType.STRING,
    "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD",
    [
        ("urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD",
         "Ubuntu 22.04 (default)"),
        ("urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU24-64-STD",
         "Ubuntu 24.04"),
    ],
    "Disk image to load on the node.",
)

params = pc.bindParameters()

# ---------------------------------------------------------------------------
# RSpec
# ---------------------------------------------------------------------------
request = pc.makeRequestRSpec()

node = request.RawPC("node")
node.hardware_type = params.hardware_type
node.disk_image = params.os_image
node.routable_control_ip = True

# Run setup.sh from the cloned repository on first boot
node.addService(pg.Execute(shell="bash", command="/local/repository/setup.sh"))

pc.printRequestRSpec(request)
