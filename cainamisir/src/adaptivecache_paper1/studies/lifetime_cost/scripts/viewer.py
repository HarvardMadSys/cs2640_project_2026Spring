"""Minimal trajectory viewer.

Browses the JSONL trajectories under studies/lifetime_cost/out/ and renders
them step-by-step with role coloring, compaction events highlighted, and
[evicted:...] holes visible. Run:

    .venv/bin/python -m studies.lifetime_cost.scripts.viewer
    # then open http://127.0.0.1:5050

By default scans `studies/lifetime_cost/out/`. Pass --out_root to override.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from flask import Flask, abort, request, url_for


OUT_ROOT = Path("studies/lifetime_cost/out")
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def list_runs() -> list[dict]:
    """Each run = one out_dir. Return [{name, path, n_trajs, policies}]."""
    runs = []
    for d in sorted(OUT_ROOT.iterdir()):
        if not d.is_dir():
            continue
        traj_files = list(d.rglob("*.jsonl"))
        if not traj_files:
            continue
        policies = set()
        n_trajs = 0
        for p in traj_files:
            policies.add(p.stem)
            with open(p) as f:
                n_trajs += sum(1 for _ in f if _.strip())
        runs.append({
            "name": d.name,
            "path": str(d.relative_to(OUT_ROOT)),
            "n_trajs": n_trajs,
            "policies": sorted(policies),
            "n_traj_files": len(traj_files),
        })
    return runs


def load_run_index(run_name: str) -> list[dict]:
    """Return [{policy, task_id, traj_idx, file, resolved, n_steps, max_p, n_comp}]."""
    base = OUT_ROOT / run_name
    if not base.exists():
        abort(404, f"run not found: {run_name}")
    out = []
    for p in sorted(base.rglob("*.jsonl")):
        with open(p) as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                steps = d.get("steps", [])
                n_steps = len(steps)
                max_p = max((s["usage"]["prompt_tokens"] for s in steps), default=0)
                n_comp = sum(1 for s in steps if s.get("compaction_after"))
                out.append({
                    "policy": p.stem,
                    "task_id": d.get("task_id", "?"),
                    "traj_idx": idx,
                    "file": str(p.relative_to(OUT_ROOT)),
                    "resolved": d.get("resolved"),
                    "n_steps": n_steps,
                    "max_p": max_p,
                    "n_comp": n_comp,
                    "model": d.get("model", "?"),
                })
    return out


def load_trajectory(run_name: str, file_rel: str, traj_idx: int) -> dict:
    p = OUT_ROOT / file_rel
    if not p.exists() or not str(p).startswith(str(OUT_ROOT.resolve())):
        # Allow non-strict prefix match; we already constrained by file_rel
        pass
    if not p.exists():
        abort(404, f"trajectory file not found: {file_rel}")
    with open(p) as f:
        for idx, line in enumerate(f):
            if idx == traj_idx:
                return json.loads(line)
    abort(404, f"trajectory index out of range: {traj_idx}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

CSS = """
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, system-ui, Segoe UI, Helvetica, Arial, sans-serif;
         margin: 0; padding: 1rem 2rem; background: #fafafa; color: #222; }
  h1 { margin-top: 0; font-size: 1.4rem; }
  h2 { font-size: 1.05rem; margin: 1.2rem 0 .4rem; }
  a { color: #0a66c2; text-decoration: none; }
  a:hover { text-decoration: underline; }
  table { border-collapse: collapse; width: 100%; font-size: 0.86rem; background: white; }
  th, td { padding: 6px 10px; border-bottom: 1px solid #eee; text-align: left; vertical-align: top; }
  th { background: #f3f3f3; font-weight: 600; position: sticky; top: 0; }
  tr:hover { background: #f9f9f9; }
  .meta { color: #666; font-size: 0.85rem; }
  .ok { color: #1a7f37; font-weight: 600; }
  .fail { color: #cf222e; font-weight: 600; }
  .step { background: white; border: 1px solid #ddd; border-radius: 6px; margin: 14px 0;
          padding: 12px 16px; }
  .step-head { font-size: 0.85rem; color: #555; margin-bottom: 8px;
               display: flex; gap: 14px; flex-wrap: wrap; }
  .step-head .num { font-weight: 700; color: #222; font-size: 1rem; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 9px;
           font-size: 0.74rem; line-height: 1.5; }
  .badge.tok  { background: #eef; color: #225; }
  .badge.cache{ background: #efe; color: #252; }
  .badge.comp { background: #fef0e6; color: #8a3500; font-weight: 600; }
  .role { display: inline-block; min-width: 76px; padding: 2px 8px; margin-right: 8px;
          border-radius: 4px; font-size: 0.74rem; font-weight: 600;
          text-transform: uppercase; vertical-align: middle; }
  .role.system    { background: #e9e9e9; color: #444; }
  .role.user      { background: #d9e9ff; color: #1f4480; }
  .role.assistant { background: #def0d8; color: #295e1f; }
  .role.tool      { background: #fff1c1; color: #6a4f00; }
  .msg { background: #fcfcfc; border: 1px solid #e9e9e9; border-radius: 4px;
         padding: 8px 12px; margin: 6px 0; }
  .msg pre { white-space: pre-wrap; word-break: break-word; margin: 4px 0; font-family:
             ui-monospace, Menlo, Monaco, Consolas, monospace; font-size: 0.84rem;
             line-height: 1.4; max-height: 380px; overflow: auto; }
  .toolcall { background: #fffaf2; border-left: 3px solid #f5a623; padding: 6px 10px;
              margin: 4px 0; font-family: ui-monospace, Menlo, monospace; font-size: 0.84rem; }
  .evicted-marker { background: #fdecea; color: #8b0000; font-weight: 600; padding: 2px 4px; }
  .compaction-banner { background: #fff8eb; border: 1px solid #f5a623; border-radius: 6px;
                       padding: 10px 14px; margin: 12px 0; }
  .compaction-banner .arrow { font-size: 1.05rem; color: #8a3500; }
  .nav { margin-bottom: 1rem; }
  .nav a { margin-right: 12px; }
  details { margin: 4px 0; }
  summary { cursor: pointer; user-select: none; color: #555; }
  .filters { margin: 12px 0; }
  .filters select, .filters input { padding: 4px 8px; font-size: 0.9rem; margin-right: 8px; }
</style>
"""


def page(title: str, body_html: str, nav: str = "") -> str:
    return f"""<!doctype html><html><head>
<meta charset="utf-8"><title>{html.escape(title)}</title>{CSS}
</head><body>
<div class="nav"><a href="{url_for('index')}">← runs</a>{nav}</div>
<h1>{html.escape(title)}</h1>
{body_html}
</body></html>"""


@app.route("/")
def index():
    runs = list_runs()
    rows = ["<tr><th>run</th><th>trajectories</th><th>policies</th></tr>"]
    for r in runs:
        rows.append(
            f"<tr><td><a href='{url_for('run', run_name=r['name'])}'>{html.escape(r['name'])}</a></td>"
            f"<td>{r['n_trajs']}</td>"
            f"<td class='meta'>{', '.join(r['policies'])}</td></tr>"
        )
    body = f"<table>{''.join(rows)}</table>"
    return page("AdaptiveCache trajectory viewer", body)


@app.route("/run/<path:run_name>")
def run(run_name: str):
    rows = load_run_index(run_name)
    if not rows:
        return page(run_name, "<p>No trajectories.</p>")
    # group by task for easier scanning
    by_task = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r)

    parts = []
    for task_id in sorted(by_task):
        ts = sorted(by_task[task_id], key=lambda x: x["policy"])
        parts.append(f"<h2>{html.escape(task_id)}</h2>")
        parts.append("<table>")
        parts.append("<tr><th>policy</th><th>resolved</th><th>steps</th><th>max_prompt</th><th>compactions</th><th>view</th></tr>")
        for r in ts:
            res_html = (
                f"<span class='ok'>resolved</span>" if r["resolved"]
                else (f"<span class='fail'>fail</span>" if r["resolved"] is False
                      else "—")
            )
            link = url_for("trajectory", run_name=run_name, file=r["file"], idx=r["traj_idx"])
            parts.append(
                f"<tr><td>{html.escape(r['policy'])}</td>"
                f"<td>{res_html}</td>"
                f"<td>{r['n_steps']}</td>"
                f"<td>{r['max_p']:,}</td>"
                f"<td>{r['n_comp']}</td>"
                f"<td><a href='{link}'>open</a></td></tr>"
            )
        parts.append("</table>")
    return page(f"Run: {run_name}", "\n".join(parts))


@app.route("/run/<path:run_name>/trajectory")
def trajectory(run_name: str):
    file_rel = request.args.get("file", "")
    idx = int(request.args.get("idx", 0))
    d = load_trajectory(run_name, file_rel, idx)

    title = f"{d.get('policy','?')} / {d.get('task_id','?')}"
    nav = (f"<a href='{url_for('run', run_name=run_name)}'>← {html.escape(run_name)}</a>")

    parts = []
    parts.append("<div class='meta'>")
    parts.append(f"model: <b>{html.escape(d.get('model','?'))}</b> · ")
    parts.append(f"benchmark: {html.escape(d.get('benchmark','?'))} · ")
    res = d.get('resolved')
    res_html = "<span class='ok'>resolved=True</span>" if res else (
        "<span class='fail'>resolved=False</span>" if res is False else "resolved=?")
    parts.append(res_html + " · ")
    parts.append(f"steps: <b>{len(d.get('steps', []))}</b>")
    parts.append("</div>")

    # render system + initial user (from step 0 messages_in)
    if d.get("steps"):
        init_msgs = d["steps"][0].get("messages_in", [])
        parts.append("<h2>initial context</h2>")
        for m in init_msgs:
            parts.append(_render_message(m, label="init"))

    # walk steps
    for k, s in enumerate(d.get("steps", [])):
        parts.append(_render_step(k, s, d["steps"][k+1] if k+1 < len(d["steps"]) else None))

    return page(title, "\n".join(parts), nav=nav)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _is_evicted(content: str) -> bool:
    if not isinstance(content, str):
        return False
    return content.startswith("[evicted") or content.startswith("[microcompacted")


def _render_message(m: dict, label: str = "") -> str:
    role = m.get("role", "?")
    content = m.get("content")
    if not isinstance(content, str):
        content = json.dumps(content)
    tool_call_id = m.get("tool_call_id")
    name = m.get("name")
    extra_meta = []
    if tool_call_id:
        extra_meta.append(f"tool_call_id={html.escape(tool_call_id)}")
    if name:
        extra_meta.append(f"name={html.escape(name)}")
    extra_str = " · ".join(extra_meta)

    body_html = ""
    if _is_evicted(content):
        body_html = f"<pre><span class='evicted-marker'>{html.escape(content)}</span></pre>"
    else:
        body_html = f"<pre>{html.escape(content)}</pre>"

    # tool_calls if assistant
    tool_calls = m.get("tool_calls") or []
    tc_html = ""
    for tc in tool_calls:
        fn = tc.get("function") or {}
        nm = fn.get("name", "?")
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args, indent=2)
        tc_html += (f"<div class='toolcall'>→ <b>{html.escape(nm)}</b>"
                    f" <span class='meta'>id={html.escape(tc.get('id',''))}</span>"
                    f"<pre>{html.escape(args)}</pre></div>")

    return (f"<div class='msg'>"
            f"<span class='role {role}'>{role}</span>"
            f"<span class='meta'>{extra_str}</span>"
            f"{body_html}"
            f"{tc_html}"
            f"</div>")


def _render_step(k: int, s: dict, next_step: dict | None) -> str:
    u = s.get("usage", {})
    response = s.get("response", {}) or {}
    comp = s.get("compaction_after")

    head = (f"<div class='step-head'>"
            f"<span class='num'>step {k}</span>"
            f"<span class='badge tok'>prompt {u.get('prompt_tokens',0):,}</span>"
            f"<span class='badge cache'>cached {u.get('cached_tokens',0):,}</span>"
            f"<span class='badge tok'>completion {u.get('completion_tokens',0):,}</span>"
            f"<span class='badge tok'>wall {s.get('wallclock_ms',0)} ms</span>"
            f"</div>")

    # The agent's response itself
    asst_msg = {"role": "assistant",
                "content": response.get("content", ""),
                "tool_calls": response.get("tool_calls") or []}
    asst_html = _render_message(asst_msg)

    # The tool obs that came back this step (sit in next_step's messages_in tail)
    obs_html = ""
    if next_step is not None:
        prev_msgs = s.get("messages_in", [])
        next_msgs = next_step.get("messages_in", [])
        # Skip the assistant turn (1) plus what was in messages_in
        new = next_msgs[len(prev_msgs) + 1:]
        for m in new:
            obs_html += _render_message(m)

    comp_html = ""
    if comp:
        comp_html = (f"<div class='compaction-banner'>"
                     f"<b>compaction event</b> "
                     f"<span class='meta'>policy={html.escape(comp.get('policy','?'))}</span> "
                     f"<span class='arrow'>"
                     f"{comp.get('tokens_before','?'):,} → {comp.get('tokens_after','?'):,} content tokens "
                     f"({comp.get('msgs_before','?')} → {comp.get('msgs_after','?')} msgs)"
                     f"</span>")
        # cost components if present
        cs = []
        if comp.get('compaction_input_uncached_tokens'):
            cs.append(f"in_uncached={comp['compaction_input_uncached_tokens']:,}")
        if comp.get('compaction_input_cached_tokens'):
            cs.append(f"in_cached={comp['compaction_input_cached_tokens']:,}")
        if comp.get('compaction_output_tokens'):
            cs.append(f"output={comp['compaction_output_tokens']:,}")
        if cs:
            comp_html += f"<div class='meta'>scorer/summarizer call: {' · '.join(cs)}</div>"
        comp_html += "</div>"

    return f"<div class='step'>{head}{asst_html}{obs_html}{comp_html}</div>"


def main():
    global OUT_ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out_root", default=str(OUT_ROOT))
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    OUT_ROOT = Path(args.out_root).resolve()
    print(f"Serving from {OUT_ROOT} → http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
