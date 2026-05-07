# AI Usage Report

## Overview

I used AI assistance throughout this project as a brainstorming partner,
debugging assistant, pair programmer, data-analysis helper, and writing
reviewer.  The project still required me to choose the research question,
understand the RDMA programming model, run the CloudLab experiments, debug the
cluster environment, interpret the results, and decide what conclusions were
fair to claim.  AI was most useful when I treated it as a technical collaborator
that could speed up iteration, but whose output needed to be checked against
the code, the experimental data, and the actual behavior of the cluster.

## How I Used AI


### Coding Assistance

I used AI as a pair-programming assistant for several parts of the codebase,
including:

- the TCP key-value server and benchmark harness,
- the RDMA server and client paths,
- the metadata atomic benchmark mode,
- experiment runner scripts,
- CPU and network metric collection scripts,
- plotting and aggregation scripts, and
- Doxygen configuration and source-code documentation.

The AI helped draft code, find likely causes of build or
runtime errors, and make repetitive script changes faster.  I still had to run
the code, inspect compiler errors, debug CloudLab-specific RDMA behavior, and
decide whether the implementation matched the intended experiment.

### Debugging and Experiment Execution

AI was useful for diagnosing problems that came up while running on CloudLab.
Examples included:

- fixing build and link errors after code changes,
- choosing the correct RDMA device for the private experiment network,
- understanding why SSH-based metric collection did not work in my setup,
- switching to direct server-side metric collection, and
- identifying why Linux netdev byte counters were not valid for RDMA traffic.

### Data Analysis and Plotting

I used AI to help create scripts that aggregate multiple trials and generate plots
with error bars.  AI also helped identify an invalid measurement: the network byte counters reported plausible values for TCP but near-zero bytes per operation for RDMA.
Since that is physically impossible for RDMA reads and messages, I treated the
network-byte results as inconclusive instead of using them as evidence.

### Report and Presentation

I used AI to help organize the final report and revise the writing.

## What I Learned

I learned that AI is good at creating automation around experiments.  The
runner scripts, aggregation scripts, and plotting workflow made it much easier
to repeat experiments and avoid manual copy-paste mistakes.  This was one of
the biggest practical benefits of AI assistance.
