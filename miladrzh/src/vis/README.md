# Agent Log Visualizer (vis/)

This folder contains a small web app that visualizes agent execution logs (JSON) as a timeline.

- **UI:** React + TypeScript (Create React App)
- **Chart:** D3
- **Hosting target:** GitHub Pages-friendly (static build)

## Folder structure

- `vis/app/` — React app source
- `vis/logs/` — source logs (your canonical log store)
- `vis/app/public/logs/` — logs served by the dev server / production build (same-origin)

> Why are logs duplicated?
> 
> Create React App serves static files from `public/`. Fetching logs from the same origin avoids cross-origin (CORS) issues.

## Prerequisites

- Node.js 18+ recommended
- npm (comes with Node)

## Install dependencies

From the repo root:

```bash
cd vis/app
npm install
```

## Add / update logs

1. Put or update logs in:
   - `vis/logs/*.json`

The log list is now served dynamically from that folder, so adding/removing
files there is enough (no copy into `public/` needed).

## Run (development)

Use the helper script to run both the log server and the React app:

```bash
cd vis
./run.sh
```

Or run them manually. First start the log server (serves `/logs` and `/logs/index.json`):

```bash
cd vis
node server.js
```

Then start the React app:

```bash
cd vis/app
npm start
```

Then open the URL printed by the dev server (typically `http://localhost:3000`).

## Run tests

```bash
cd vis/app
npm test -- --watchAll=false
```

## Build (production)

```bash
cd vis/app
npm run build
```

This outputs a static site into `vis/app/build/`.

## Deploy

Because this is a static build, you can deploy `vis/app/build/` to:

- GitHub Pages
- Any static hosting (Netlify, Cloudflare Pages, S3, etc.)

### GitHub Pages notes

If you host under a subpath (e.g. `/agent-os/vis/`), you’ll likely want to set the CRA `homepage` field in `vis/app/package.json` accordingly.

## Troubleshooting

### Timeline is empty

- Ensure the selected JSON exists at:
  - `http://localhost:3000/logs/<file>.json`
- Confirm the JSON contains an `events` array.

### CORS errors

You shouldn’t see CORS errors when fetching from `/logs/...` because logs are served from the same origin via `public/`.
