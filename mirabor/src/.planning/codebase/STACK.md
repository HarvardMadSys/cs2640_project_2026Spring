# Technology Stack

**Analysis Date:** 2026-04-16

## Languages

**Primary:**
- C++17 — simulator core. Source in `src/` and headers in `include/`. Standard specified by `CXXFLAGS := -std=c++17` in `Makefile`.
- Python 3 — tooling for trace collection (`scripts/collect_trace.py`) and result plotting (`scripts/plot_results.py`). Invoked via `python3` from `Makefile` target `plots`.

**Secondary:**
- LaTeX — project write-up artifacts (`progress.tex`, `v7.tex`). Not part of the runtime; output PDFs (`progress.pdf`) are committed. `.gitignore` excludes `*.aux`, `*.fdb_latexmk`, `*.fls`, `*.synctex.gz`, `progress.log`.
- Make — build driver (`Makefile`).
- CSV — data interchange format for traces (`traces/*.csv`) and simulation outputs (`results/*.csv`). No schema tooling; headers are hand-written (e.g. `timestamp,key,size`).

## Runtime

**Environment:**
- C++17-conformant toolchain. `Makefile` uses `CXX := g++` but any C++17 compiler works (clang++ on macOS is the default substitute given `Darwin 25.2.0`).
- Python 3 interpreter available as `python3` on PATH. No explicit minimum version pinned; all scripts use standard-library features compatible with 3.8+.
- Virtualenvs `/.venv/` and `/.venv13/` present (gitignored).

**Package Manager:**
- C++: none — single `Makefile`, no package manager or submodules.
- Python: `pip` via `requirements.txt`. No lockfile (no `requirements.lock`, no `poetry.lock`, no `Pipfile`). `pyproject.toml` absent.

## Frameworks

**Core:**
- C++ STL only — containers (`std::list`, `std::unordered_map`, `std::set`, `std::deque`), `<chrono>`, `<random>`, `<fstream>`. See `include/cache.h`, `src/trace_gen.cpp`. No external C++ frameworks (Boost, Abseil, etc.).
- `requests` — HTTP client used in `scripts/collect_trace.py` (`import requests`, `requests.Session`).
- `matplotlib` (with `Agg` backend) — figure rendering in `scripts/plot_results.py` (`matplotlib.use("Agg")`).
- `numpy` — numerical arrays for plotting (`scripts/plot_results.py`).
- `pandas` — CSV ingestion/manipulation in `scripts/plot_results.py` (`pd.read_csv`).

**Testing:**
- None detected. No `test/`, `tests/`, `*_test.*`, `*_test.cpp`, `test_*.py`, or framework markers (catch2, gtest, pytest, unittest) found. Validation artifacts live in `results/validation/` but are simulator outputs, not unit tests.

**Build/Dev:**
- GNU Make — `Makefile` with targets `all`, `clean`, `run`, `run-sweep`, `plots`.
- Compiler flags: `-std=c++17 -O2 -Wall -Wextra -Iinclude -MMD -MP` (auto-dep generation via `-MMD -MP`; linker inputs collected from `build/*.d`).
- No linter or formatter config detected (no `.clang-format`, `.clang-tidy`, `.flake8`, `.ruff.toml`, `pyproject.toml`, `.editorconfig`).
- No CI configuration (no `.github/`, `.gitlab-ci.yml`, `.circleci/`, `travis`).

## Key Dependencies

**Critical (Python, declared in `requirements.txt`):**
- `requests` — Congress.gov HTTP client. Used in `scripts/collect_trace.py`.
- `matplotlib` — plotting backend. Used in `scripts/plot_results.py`.
- `numpy` — numeric array support for plots. Used in `scripts/plot_results.py` (`np.arange`).
- `pandas` — CSV parsing and dataframe operations. Used in `scripts/plot_results.py`.
- `scipy` — listed in `requirements.txt` but not currently imported in any script (declared-but-unused).

**Critical (C++, header-only / stdlib):**
- `<list>`, `<unordered_map>`, `<set>`, `<deque>`, `<vector>` — backing structures for five cache policies (`include/cache.h`).
- `<random>` — Mersenne Twister `std::mt19937_64`, `std::lognormal_distribution`, `std::uniform_real_distribution` for synthetic trace generation (`src/trace_gen.cpp`).
- `<chrono>` — wall-clock timing of SHARDS runs (`src/main.cpp`).
- `<map>` — ordered stack-distance histograms for SHARDS (`include/shards.h`, `src/shards.cpp`).

**Infrastructure:**
- None — the project has no runtime server, database, or cloud client.

## Configuration

**Environment:**
- `CONGRESS_API_KEY` — **required** by `scripts/collect_trace.py`. Script exits with `Error: set CONGRESS_API_KEY environment variable` at `scripts/collect_trace.py:53` if unset. Read via `os.environ.get("CONGRESS_API_KEY")`.
- `.env` file exists at repo root (listed in `.gitignore`). Contents not read; presumably holds `CONGRESS_API_KEY` for local development. Not auto-loaded by any script — user must `export` it manually (per `README.md:64`).

**Build:**
- `Makefile` — single source of build configuration. No `CMakeLists.txt`, `meson.build`, `BUILD.bazel`, or `configure.ac`.
- No `tsconfig.json`, `package.json`, `Cargo.toml`, `go.mod`, or `pyproject.toml`.

**CLI flags (simulator, parsed in `src/main.cpp`):**
- `--trace <file>` — replay a CSV trace instead of synthetic generation.
- `--cache-sizes <list>` — comma-separated fractions; default `0.001,0.005,0.01,0.02,0.05,0.1`.
- `--policies <list>` — subset of `lru,fifo,clock,s3fifo,sieve`.
- `--output-dir <dir>` — default `results`.
- `--num-requests <n>` — default `500000`.
- `--num-objects <n>` — default `50000`.
- `--alpha <a>` — Zipf alpha; default `0.8`.
- `--alpha-sweep` / `--shards` / `--shards-exact` / `--replay-zipf` — boolean mode toggles.

**CLI flags (collector, parsed in `scripts/collect_trace.py`):**
- `--requests` (default 5000), `--duration` (default 14400s), `--output` (default `traces/congress_trace.csv`), `--seed` (default 42), `--append`.

## Platform Requirements

**Development:**
- macOS (observed: Darwin 25.2.0) or any POSIX-compatible OS with `make`, `g++` (or `clang++` aliased), and `python3`.
- Disk: trace files (`traces/`) and simulation outputs (`results/`) are gitignored and regenerable.
- Network: only required when running `scripts/collect_trace.py` (hits `https://api.congress.gov/v3`).

**Production:**
- Not applicable — this is an offline research/simulation project. No deployment target, no service, no containerization (no `Dockerfile`, no `docker-compose.yml`). The executable `cache_sim` is a one-shot command-line tool that writes CSVs.

---

*Stack analysis: 2026-04-16*
