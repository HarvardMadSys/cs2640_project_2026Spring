#!/usr/bin/env python3
"""Trajectory viewer — shows full conversation with eviction annotations.

Usage:
    python scripts/viewer.py results/live_q30          # all trajectories
    python scripts/viewer.py results/live_q30 --watch  # auto-refresh every 10s
"""

from __future__ import annotations

import argparse
import html
import json
import time
import webbrowser
from pathlib import Path


# ---------------------------------------------------------------------------
# Load trajectories
# ---------------------------------------------------------------------------

def load_trajs(root: Path) -> list[dict]:
    trajs = []
    for f in sorted(root.rglob("*.traj.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        d["_file"] = str(f)
        d["_policy"] = d.get("cache_policy", f.parts[-3].split("_")[0])
        d["_budget"] = d.get("cache_budget", "?")
        d["_instance"] = d.get("instance_id", f.parent.name)
        trajs.append(d)
    return trajs


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

ROLE_COLORS = {
    "system":    "#1f6feb",
    "user":      "#3fb950",
    "assistant": "#d29922",
    "tool":      "#8b949e",
    "exit":      "#f85149",
}

def esc(s: str) -> str:
    return html.escape(str(s))

def render_message(msg: dict, evicted_after: bool = False) -> str:
    role = msg.get("role", "?")
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    extra = msg.get("extra") or {}
    actions = extra.get("actions", [])
    color = ROLE_COLORS.get(role, "#888")

    parts = []

    # Role header
    parts.append(f'<div class="msg" style="border-left:3px solid {color}">')
    parts.append(f'<div class="msg-role" style="color:{color}">{esc(role.upper())}</div>')

    # Bash actions
    for a in actions:
        cmd = esc(str(a.get("command", "")))
        parts.append(f'<div class="bash"><span class="bash-prompt">$ </span><code>{cmd}</code></div>')

    # Think block (collapse by default)
    think_start = content.find("<think>")
    think_end = content.find("</think>")
    if think_start != -1 and think_end != -1:
        think = content[think_start+7:think_end].strip()
        rest = (content[:think_start] + content[think_end+8:]).strip()
        uid = f"think_{id(msg)}"
        parts.append(f'<details><summary style="color:#8b949e;cursor:pointer;font-size:11px">thinking ({len(think)} chars)</summary>'
                     f'<pre class="think">{esc(think[:2000])}{"..." if len(think)>2000 else ""}</pre></details>')
        content = rest

    # Main content
    if content.strip():
        parts.append(f'<pre class="content">{esc(content[:3000])}{"..." if len(content)>3000 else ""}</pre>')

    # Exit info
    if role == "exit":
        exit_status = extra.get("exit_status", "")
        submission = extra.get("submission", "")
        if exit_status:
            parts.append(f'<div class="exit-status">Exit: {esc(exit_status)}</div>')
        if submission:
            parts.append(f'<details><summary style="color:#3fb950;cursor:pointer">Patch submitted ({len(submission)} chars)</summary>'
                         f'<pre class="patch">{esc(submission[:3000])}</pre></details>')

    if evicted_after:
        parts.append('<div class="evict-marker">⚡ EVICTION FIRED after this step</div>')

    parts.append('</div>')
    return "".join(parts)


def render_traj(traj: dict, idx: int) -> str:
    policy = traj.get("_policy", "?")
    budget = traj.get("_budget", "?")
    instance = traj.get("_instance", "?")
    info = traj.get("info", {})
    msgs = traj.get("messages", [])
    ct = traj.get("cache_trace", [])
    exit_status = info.get("exit_status", "running")
    has_patch = bool(info.get("submission", ""))
    model_stats = info.get("model_stats", {})

    # Build step→eviction map from cache_trace
    eviction_at_step: dict[int, dict] = {}
    for entry in ct:
        if entry.get("messages_evicted", 0) > 0:
            eviction_at_step[entry["step"]] = entry

    status_color = "#3fb950" if has_patch else "#f85149" if exit_status and exit_status != "running" else "#d29922"
    uid = f"traj_{idx}"

    out = []
    out.append(f'<div class="traj" id="{uid}">')
    out.append(f'''<div class="traj-header" onclick="toggle('{uid}_body')">
        <span class="traj-title">{esc(instance)}</span>
        <span class="tag" style="background:{status_color}">{esc(exit_status or "running")}</span>
        <span class="tag tag-policy">{esc(policy)}/{budget}</span>
        <span class="traj-meta">
            {model_stats.get("api_calls", "?")} steps ·
            {"✅ patch" if has_patch else "❌ no patch"}
        </span>
    </div>''')

    out.append(f'<div class="traj-body" id="{uid}_body">')

    # Cache trace summary table
    if ct:
        out.append('<table class="ct-table"><tr><th>Step</th><th>Sent</th><th>Evicted</th><th>Prompt tok</th><th>Cache read</th><th>Hit%</th></tr>')
        for e in ct:
            evicted = e.get("messages_evicted", 0)
            ev_cls = ' style="color:#f85149;font-weight:bold"' if evicted > 0 else ""
            hit = e.get("cache_hit_rate", 0) * 100
            out.append(f'''<tr{ev_cls}>
                <td>{e.get("step","?")}</td>
                <td>{e.get("sent_messages","?")}</td>
                <td>{evicted}</td>
                <td>{e.get("prompt_tokens",0):,}</td>
                <td>{e.get("cache_read_tokens",0):,}</td>
                <td>{hit:.0f}%</td>
            </tr>''')
        out.append('</table>')

    # Full conversation
    out.append('<div class="conversation">')
    step = 0
    for i, msg in enumerate(msgs):
        role = msg.get("role", "")
        if role == "assistant":
            step += 1
        evicted_after = step in eviction_at_step
        out.append(render_message(msg, evicted_after=(evicted_after and role == "assistant")))
    out.append('</div>')

    out.append('</div></div>')
    return "".join(out)


def generate_html(root: Path, auto_refresh: int = 0) -> str:
    trajs = load_trajs(root)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    refresh_tag = f'<meta http-equiv="refresh" content="{auto_refresh}">' if auto_refresh else ""

    # Summary stats
    total = len(trajs)
    patches = sum(1 for t in trajs if t.get("info", {}).get("submission"))
    policies = sorted(set(t["_policy"] for t in trajs))

    html_parts = [f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">{refresh_tag}
<title>AdaptiveCache Trajectory Viewer</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'SF Mono',Menlo,monospace; background:#0d1117; color:#c9d1d9; padding:20px; font-size:13px; }}
h1 {{ color:#58a6ff; margin-bottom:8px; font-size:18px; }}
.meta {{ color:#8b949e; margin-bottom:20px; font-size:12px; }}
.filters {{ margin-bottom:16px; }}
.filters button {{ background:#21262d; border:1px solid #30363d; color:#c9d1d9; padding:4px 12px;
    border-radius:4px; cursor:pointer; margin-right:6px; font-size:12px; }}
.filters button:hover,.filters button.active {{ background:#1f6feb; border-color:#1f6feb; color:#fff; }}
.traj {{ border:1px solid #21262d; border-radius:6px; margin-bottom:12px; overflow:hidden; }}
.traj-header {{ background:#161b22; padding:10px 16px; cursor:pointer; display:flex; align-items:center; gap:10px; }}
.traj-header:hover {{ background:#1c2128; }}
.traj-title {{ font-weight:600; flex:1; }}
.traj-meta {{ color:#8b949e; font-size:11px; margin-left:auto; }}
.tag {{ padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; color:#fff; }}
.tag-policy {{ background:#6e40c9; }}
.traj-body {{ display:none; padding:16px; }}
.traj-body.open {{ display:block; }}
.ct-table {{ width:100%; border-collapse:collapse; margin-bottom:16px; font-size:12px; }}
.ct-table th {{ background:#161b22; padding:4px 8px; text-align:left; color:#8b949e; font-size:11px; }}
.ct-table td {{ padding:3px 8px; border-bottom:1px solid #21262d; }}
.conversation {{ display:flex; flex-direction:column; gap:8px; }}
.msg {{ background:#161b22; border-radius:4px; padding:10px 12px; }}
.msg-role {{ font-size:10px; font-weight:700; letter-spacing:1px; margin-bottom:6px; }}
.bash {{ background:#0d1117; border-radius:3px; padding:4px 8px; margin:4px 0; font-size:12px; }}
.bash-prompt {{ color:#3fb950; }}
.content {{ font-size:12px; white-space:pre-wrap; word-break:break-word; color:#e6edf3; max-height:400px; overflow-y:auto; }}
.think {{ font-size:11px; color:#8b949e; white-space:pre-wrap; padding:8px; background:#0d1117; margin:4px 0; max-height:200px; overflow-y:auto; }}
.patch {{ font-size:11px; color:#3fb950; white-space:pre-wrap; padding:8px; background:#0d1117; margin:4px 0; }}
.exit-status {{ color:#f85149; font-size:12px; margin-top:4px; }}
.evict-marker {{ background:#da3633; color:#fff; padding:4px 10px; border-radius:3px; font-size:11px; margin-top:6px; display:inline-block; }}
</style>
<script>
function toggle(id) {{
    var el = document.getElementById(id);
    el.classList.toggle('open');
}}
function filterPolicy(policy) {{
    document.querySelectorAll('.traj').forEach(function(t) {{
        var p = t.getAttribute('data-policy');
        t.style.display = (!policy || p === policy) ? '' : 'none';
    }});
    document.querySelectorAll('.filters button').forEach(function(b) {{
        b.classList.toggle('active', b.getAttribute('data-policy') === policy);
    }});
}}
</script>
</head><body>
<h1>AdaptiveCache Trajectory Viewer</h1>
<div class="meta">{total} trajectories · {patches} patches · {ts} · {root.name}</div>
<div class="filters">
    <button data-policy="" onclick="filterPolicy('')" class="active">All</button>
"""]

    for p in policies:
        html_parts.append(f'    <button data-policy="{p}" onclick="filterPolicy(\'{p}\')">{p}</button>\n')
    html_parts.append('</div>\n')

    for i, traj in enumerate(trajs):
        policy = traj.get("_policy", "?")
        traj_html = render_traj(traj, i)
        # inject data-policy for filtering
        traj_html = traj_html.replace('<div class="traj"', f'<div class="traj" data-policy="{policy}"', 1)
        html_parts.append(traj_html)

    html_parts.append('</body></html>')
    return "".join(html_parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", help="Results directory")
    parser.add_argument("--watch", action="store_true", help="Auto-refresh every 10s")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    out = root / "viewer.html"
    refresh = 10 if args.watch else 0

    content = generate_html(root, auto_refresh=refresh)
    out.write_text(content)
    print(f"Viewer: {out}")

    if not args.no_open:
        webbrowser.open(f"file://{out.resolve()}")

    if args.watch:
        print("Watching... Ctrl+C to stop.")
        try:
            while True:
                time.sleep(refresh)
                out.write_text(generate_html(root, auto_refresh=refresh))
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
