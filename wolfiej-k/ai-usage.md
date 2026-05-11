# AI Usage

## What worked well

We used LLMs extensively (particularly Gemini 3.1 Pro and Opus 4.7 via Claude
Code) to implement the project. While we found that LLMs are not effective at
making high-level architectural decisions such as how to load processes, what
hash table to use, etc., they are very efficient once given concrete guidelines.
For example, drop-in lock-free hash table code is difficult to find online, but
with Claude it was straightforward to integrate existing implementations into
the project. Of course, it is difficult to verify correctness, so we
hand-verified the code as much as possible and generated-and-verified a large
suite of test cases. More testing is certainly required before a codebase of
this complexity could be used in production.

We would estimate that while the first few hundred lines of code were
predominately written by hand, the code for the later sections of the project
were almost exclusively AI-generated. We were able to do this both due to
increased familiarity (this was our first time using Claude Code) and project
clarity (we had more concrete ideas to dictate to the agent).

One suggestion when using LLMs is to provide implementation references while
possble. While model harnesses are fairly good at searching the internet, we
found that it is much faster (and more token efficient) to provide specific
GitHub repositories or blog posts up-front. This is particularly relevant for
lock-free code, which has many short references online.

## What did not work well

In general, the models we tested are very poor at *profiling* and *designing
experiments*. When asked to debug, Claude Code is excellent at running shell
scripts, reproducing the error, and iterating on itself; however, when asked to
find performance bottlenecks, it almost always falls back onto parsing code
line-by-line. Using profilers manually (e.g., `perf`) was far more efficient.
Likewise, LLMs are very poor at designing experiments. We frequently encountered
category errors such as averaging latency percentiles that we had to manually
fix (and, ultimately, verify the entire benchmarking suite).
