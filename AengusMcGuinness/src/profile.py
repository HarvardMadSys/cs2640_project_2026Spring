"""
CloudLab profile for the RDMA cache prototype.

This profile provisions two Ubuntu 22.04 bare-metal nodes connected by a
private high-bandwidth experiment link. The intended use is to run the cache
server on one node and the benchmark client on the other while evaluating TCP,
two-sided RDMA, and one-sided RDMA paths.

The profile requests hardware with Mellanox RDMA-capable NICs. On the final
CloudLab xl170 experiment, the private experiment interface mapped to RDMA
device mlx5_3, while the public/control interface mapped to mlx5_0. Users
should verify the mapping on each allocation with:

    ip -br addr
    rdma link show
    ibv_devinfo

See the top-level README and final report for build, run, and evaluation
commands.
"""

import geni.portal as portal
import geni.rspec.pg as pg

pc = portal.Context()

pc.defineParameter(
    "nodeType",
    "Node Hardware Type",
    portal.ParameterType.NODETYPE,
    "xl170",
    longDescription="Choose a node type with a Mellanox NIC for RDMA. "
                    "xl170 (Utah), c220g5 (Wisconsin), or d6515 (Utah) all work."
)

params = pc.bindParameters()
request = pc.makeRequestRSpec()

# Two nodes: server and client
server = request.RawPC("server")
server.hardware_type = params.nodeType
server.disk_image = "urn:publicid:IDN+emulab.net+image+emulab-ops:UBUNTU22-64-STD"

client = request.RawPC("client")
client.hardware_type = params.nodeType
client.disk_image = "urn:publicid:IDN+emulab.net+image+emulab-ops:UBUNTU22-64-STD"

# Link between them (CloudLab will use the RDMA-capable interface if available)
link = request.Link("rdma-link")
link.addNode(server)
link.addNode(client)
link.bandwidth = 25000000  # 25 Gbps

pc.printRequestRSpec(request)
