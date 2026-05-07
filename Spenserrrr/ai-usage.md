# AI Usage Report

I used AI assistance mainly as a brainstorming helper and programming partner. The AI helped me in the following ways:

- **Brainstorm preliminary ideas.** Early in the project, I used AI to compare possible directions, such as whether to focus on exact Spark baselines, Spark's built-in approximation, direct DataSketches UDAFs, or sketch materialization. This helped me narrow the project into a benchmark with a clear systems question.

- **Make the code cleaner and more modular.** I used AI to help organize the benchmark runner, configuration parsing, query definitions, metrics code, synthetic data generation, and materialization code into separate modules. I still checked the design myself, but AI was useful for keeping repetitive Scala/Spark code consistent.

- **Debug implementation issues.** AI helped with several Spark and DataSketches integration problems. One important example was the slow direct UDAF path: instead of only treating it as a bug, the debugging process helped identify UDAF serialization and aggregation overhead as a real systems issue, which motivated partition-level sketching and later sketch materialization.

- **Improve the presentation and writing.** For the presentation, AI gave useful suggestions on how to structure the story: motivation, what sketches are, why direct UDAFs were not enough, why materialization matters, and how to explain warm-cache versus cold-cache results. It also helped turn benchmark tables and plots into clearer claims.

What I learned from using AI is that it is most useful when I ask it to compare design alternatives and explain tradeoffs, not merely to write code. In this project, the best AI interactions were the ones where I gave concrete constraints, asked for competing explanations, and then checked the suggestions against benchmark results.

What surprised me is that approximate sketches did not automatically beat Spark exact. Spark's native operators and caching were much stronger than I initially expected. The AI was useful in pushing me to treat this as a systems result rather than a failure: sketches help under specific physical-design conditions, especially repeated cold queries over compact materialized summaries.

Some useful tips from this experience:

- AI is especially helpful for tasks that are conceptually easy but require using unfamiliar or verbose libraries, such as organizing benchmark CSV outputs, aggregating results, and generating plots.
- For larger code changes, it works better to write the skeleton or interface myself first and then ask AI to fill in details. This keeps the design under my control while still using AI for implementation speed.
- Specific prompts matter. AI does not work as well when asked vaguely to "implement this feature." It works much better when the inputs, expected outputs, constraints, and edge cases are stated clearly.
- AI-generated code still needs verification. I found it important to inspect benchmark numbers, rerun experiments when needed, and make sure the final claims matched the actual data.
