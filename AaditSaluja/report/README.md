# Report Source

USENIX-format LaTeX source for the CephFS metadata project report.

Files:

- `main.tex`: report source.
- `refs.bib`: bibliography.
- `figures/`: generated PDF/SVG figures imported by the report.
- `results/`: raw benchmark outputs and aggregate CSV/Markdown summaries.
- `usenix-2020-09.sty`: USENIX style file.
- `usenix2019_v3.1.tex`: official USENIX sample source, kept as a local reference.

Build:

```sh
cd report
make
```

The build writes `main.pdf` in this directory; the root-level submitted copy is
`../report.pdf`.
