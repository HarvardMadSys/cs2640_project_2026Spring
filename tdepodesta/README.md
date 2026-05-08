# trace-viz

By Trevor DePodesta. Interactive trace visualization with cache simulation overlays. README copied from source repo (https://github.com/tdepodesta/trace-viz).

The project has two apps:

- `frontend/` - React + Vite UI
- `backend/` - Flask API for trace ingest, aggregation, and simulation

## Prerequisites

- Node.js 18+ (20+ recommended) and npm
- Python 3.10+ (3.11 recommended)

## Quickstart

### 1) Start backend

From `backend/`:

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash
pip install -r requirements.txt
python app.py
```

For deployment, use Waitress instead: `python serve.py` from `backend/` (see [`backend/README.md`](backend/README.md)).

Backend default URL: `http://localhost:5000`

### 2) Start frontend

From `frontend/`:

```bash
npm install
npm run dev
```

Frontend default URL: `http://localhost:5173`

## Environment configuration

Frontend API target is controlled by `VITE_API_BASE_URL`.

Default:

```text
http://localhost:5000
```

To override, create `frontend/.env.local`:

```bash
VITE_API_BASE_URL=http://localhost:5000
```

## Common workflow

1. Run backend server.
2. Run frontend server.
3. Open the frontend URL from Vite.
4. Upload a trace file (`.csv`/`.txt`) or select an existing indexed trace.
5. Explore the heatmap and simulation controls.

## Repo structure

```text
trace-viz/
  backend/   Flask API + trace indexing/simulation
  frontend/  React Vite app
```

## Documentation

- Backend API, endpoint reference, trace storage layout: [`backend/README.md`](backend/README.md)
- Frontend setup and scripts: [`frontend/README.md`](frontend/README.md)
