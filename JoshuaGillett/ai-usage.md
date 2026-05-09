# AI usage report

AI, in this project, was predominantly done with Cursor, running Claude on the backend. Any work done agentically is also understood by myself. I have vetted the results and the experimental setup before writing it into the report.

# Main usage cases
- **Statistical work**: I am notably not a very good statistician. With this in mind, Claude was able to generate some very good statistical benchmarks using some stuff that I know about but would do a very slow job of implementing: Cohen's |d|, Pearson/Spearman correlations, permutation tests, LDA-CV, etc. I include converting CSVs into knowledgable data in this section.
- **Tracing kernel-source audit**: Navigating the Linux kernel source code is, as you can imagine or have experienced, just aweful. Claude did a fantastic job of doing my provided task of finding the relevant hot paths and enumerating the possible contention spots.
- **Generating experimental cases very quickly**: The experimentation for my covert channels is quite broad. With this in mind, being able to quickly generate some cases like `many_ring_pressure` and `independent_pair` for falsification was very helpful.
- **Brainstorming mitigation strategies**: I had a hard time thinking of mitigations beyond what RingGuard provides. With this in mind, Claude was able to think up some interesting possibilities that would be pretty low costs on the maintainers. This was probably the weakest link of AI usage however, as some of the ideas it had were quite outlandish, and most of the ideas still remained completely sourced by myself.
- **General code synthesis**: Claude was able to quickly, and in parallel, in fact, write code that befit the ideas that I had about how to do the covert channel. It makes a lot of stupid decisions first off, so looking through the code, and telling it directly what to change was very helpful. There also, of course, exists times, where I just changed it myself.
- **README.md explanation**: I had Claude tell me how to describe how to use my code in a README briefly. I think this is a particular thing most developers are not very good at, and using AI is relatively useful here. AI is quite good at going from code to natural language, even in times when the maintainer is not lol.
- **Python code**: I hate writing matplotlib, so Claude did this. I respect Python a lot, but I cannot make myself write this language.

# Things I Did Not Expect
It was really a long shot trying to get Claude to do the kernel source code audit. I thought that it was going to find something nonsensical, but I had it pull the relevant source code into a dependencies directory, and from there it did a very good job of finding what I needed. I expect this is due to the fact that Linux code hsa a lot of text about online, and also performing better when the code is within the working directory.

# Tips
I expected this, but this project really solidified my opinion of usage of AI in research. It was very good at generating my ideas into code very quickly when I gave it a detailed and thought-out idea, but it was really just aweful at coming up with ideas itself. Thus, I was able to test a lot of my ideas very quickly and with little time commitment for a particularly bad idea. Some of my negative results are included within the report, so I won't go into any extreme detail.

Also, having an agent do a source code audit of a particular system you are working with turns out to be quite impactful! This is especially true if you can rule out hallucinatory ideas very quickly (this will require some understanding of the code you're working in of course). 

Also, as I think Vlad mentioned in class, having it write to some `.txt` or `.md` file as a sort-of brainstorming cache was particularly helpful. I had it keep a CHANGELOG.md and a `FINDINGS.md` for some portions of the project, but got rid of them when I stopped the brainstorming part of the code development and moved to cleaning up.
