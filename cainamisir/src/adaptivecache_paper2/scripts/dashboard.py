#!/usr/bin/env python3
"""Experiment dashboard — generates a live HTML dashboard with full drill-down.

Usage:
    python scripts/dashboard.py results/exp_haiku_5           # open in browser
    python scripts/dashboard.py results/exp_haiku_5 --watch   # auto-refresh every 10s
    python scripts/dashboard.py results/exp_haiku_5 --terminal # rich terminal view
"""

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from collections import defaultdict


def read_all_data(output_dir: Path) -> list[dict]:
    results = []
    for traj_file in sorted(output_dir.rglob("*.traj.json")):
        try:
            data = json.loads(traj_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        info = data.get("info", {})
        stats = info.get("model_stats", {})
        ct = data.get("cache_trace", [])
        instance_id = traj_file.parent.name
        policy = data.get("cache_policy", "?")
        budget = data.get("cache_budget", "?")
        messages = data.get("messages", [])

        results.append({
            "instance_id": instance_id,
            "policy": policy,
            "budget": budget,
            "config_key": f"{policy}/{budget}",
            "exit_status": info.get("exit_status", "unknown"),
            "has_patch": bool(info.get("submission", "")),
            "submission_preview": (info.get("submission", "") or "")[:500],
            "cost": stats.get("instance_cost", 0),
            "api_calls": stats.get("api_calls", 0),
            "cache_trace": ct,
            "num_messages": len(messages),
            "exception": info.get("exception_str", ""),
        })
    return results


def generate_html(output_dir: Path, auto_refresh: int = 0) -> str:
    results = read_all_data(output_dir)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    # Aggregate by config
    groups = defaultdict(list)
    for r in results:
        groups[r["config_key"]].append(r)

    # Aggregate by instance
    by_instance = defaultdict(list)
    for r in results:
        by_instance[r["instance_id"]].append(r)

    total_cost = sum(r["cost"] for r in results)

    refresh_tag = f'<meta http-equiv="refresh" content="{auto_refresh}">' if auto_refresh > 0 else ""

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
{refresh_tag}
<title>AdaptiveCache Dashboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'SF Mono', 'Menlo', 'Monaco', monospace; background: #0d1117; color: #c9d1d9; padding: 20px; font-size: 13px; }}
  h1 {{ color: #58a6ff; margin-bottom: 5px; font-size: 20px; }}
  h2 {{ color: #8b949e; font-size: 16px; margin: 25px 0 10px; border-bottom: 1px solid #21262d; padding-bottom: 5px; }}
  h3 {{ color: #58a6ff; font-size: 14px; margin: 15px 0 8px; cursor: pointer; }}
  h3:hover {{ color: #79c0ff; }}
  .meta {{ color: #8b949e; margin-bottom: 20px; }}
  .meta span {{ margin-right: 20px; }}
  .cost {{ color: #f0883e; }}
  .good {{ color: #3fb950; }}
  .warn {{ color: #d29922; }}
  .bad {{ color: #f85149; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  th {{ background: #161b22; color: #8b949e; text-align: left; padding: 8px 12px; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  td {{ padding: 6px 12px; border-bottom: 1px solid #21262d; }}
  tr:hover td {{ background: #161b22; }}
  .bar-container {{ width: 120px; height: 14px; background: #21262d; border-radius: 3px; display: inline-block; vertical-align: middle; }}
  .bar {{ height: 100%; border-radius: 3px; transition: width 0.3s; }}
  .bar-green {{ background: linear-gradient(90deg, #238636, #3fb950); }}
  .bar-yellow {{ background: linear-gradient(90deg, #9e6a03, #d29922); }}
  .bar-red {{ background: linear-gradient(90deg, #da3633, #f85149); }}
  .bar-blue {{ background: linear-gradient(90deg, #1f6feb, #58a6ff); }}
  .sparkline {{ font-size: 11px; letter-spacing: -1px; color: #3fb950; }}
  .collapsible {{ display: none; margin-left: 15px; }}
  .collapsible.open {{ display: block; }}
  .toggle {{ cursor: pointer; user-select: none; }}
  .toggle::before {{ content: "▶ "; font-size: 10px; }}
  .toggle.open::before {{ content: "▼ "; }}
  .step-row td {{ font-size: 12px; padding: 3px 8px; }}
  .step-row:nth-child(even) td {{ background: #0d1117; }}
  .pill {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
  .pill-green {{ background: #238636; color: #fff; }}
  .pill-red {{ background: #da3633; color: #fff; }}
  .pill-yellow {{ background: #9e6a03; color: #fff; }}
  .pill-blue {{ background: #1f6feb; color: #fff; }}
  .instance-section {{ background: #161b22; border: 1px solid #21262d; border-radius: 6px; padding: 12px; margin: 10px 0; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 10px; }}
  .metric-card {{ background: #0d1117; border: 1px solid #21262d; border-radius: 6px; padding: 12px; text-align: center; }}
  .metric-value {{ font-size: 28px; font-weight: bold; }}
  .metric-label {{ color: #8b949e; font-size: 11px; margin-top: 4px; }}
</style>
<script>
function toggle(id) {{
  var el = document.getElementById(id);
  var btn = document.getElementById('btn-' + id);
  if (el.classList.contains('open')) {{
    el.classList.remove('open');
    btn.classList.remove('open');
  }} else {{
    el.classList.add('open');
    btn.classList.add('open');
  }}
}}
</script>
</head><body>
<h1>AdaptiveCache Experiment Dashboard</h1>
<div class="meta">
  <span>{len(results)} trajectories</span>
  <span class="cost">${total_cost:.2f} total cost</span>
  <span>{ts}</span>
  <span>{output_dir.name}</span>
</div>
"""

    # --- Top-level metric cards ---
    total_steps = sum(len(r["cache_trace"]) for r in results)
    total_prompt = sum(sum(t.get("prompt_tokens", 0) for t in r["cache_trace"]) for r in results)
    total_cache = sum(sum(t.get("cache_read_tokens", 0) for t in r["cache_trace"]) for r in results)
    overall_hit = total_cache / total_prompt if total_prompt > 0 else 0

    html += f"""
<div class="grid" style="margin-bottom: 20px;">
  <div class="metric-card"><div class="metric-value">{len(results)}</div><div class="metric-label">Trajectories</div></div>
  <div class="metric-card"><div class="metric-value">{total_steps}</div><div class="metric-label">Total Steps</div></div>
  <div class="metric-card"><div class="metric-value cost">${total_cost:.2f}</div><div class="metric-label">Total Cost</div></div>
  <div class="metric-card"><div class="metric-value">{total_prompt:,}</div><div class="metric-label">Total Prompt Tokens</div></div>
  <div class="metric-card"><div class="metric-value {'good' if overall_hit > 0.5 else 'warn' if overall_hit > 0.1 else 'bad'}">{overall_hit:.1%}</div><div class="metric-label">Overall Cache Hit Rate</div></div>
</div>
"""

    # --- Config comparison table ---
    html += "<h2>Config Comparison</h2><table>"
    html += "<tr><th>Config</th><th>Instances</th><th>Patches</th><th>Avg Cost</th><th>Avg Steps</th><th>Avg Prompt Tok</th><th>Avg Cache Read</th><th>Cache Hit Rate</th><th>Evicted Msgs</th></tr>"

    for key in sorted(groups.keys()):
        runs = groups[key]
        n = len(runs)
        patches = sum(1 for r in runs if r["has_patch"])
        avg_cost = sum(r["cost"] for r in runs) / n
        avg_steps = sum(len(r["cache_trace"]) for r in runs) / n
        total_p = sum(sum(t.get("prompt_tokens", 0) for t in r["cache_trace"]) for r in runs)
        total_c = sum(sum(t.get("cache_read_tokens", 0) for t in r["cache_trace"]) for r in runs)
        avg_p = total_p / n
        avg_c = total_c / n
        hit = total_c / total_p if total_p > 0 else 0
        avg_evict = sum(sum(t.get("messages_evicted", 0) for t in r["cache_trace"]) for r in runs) / n

        hit_cls = "good" if hit > 0.5 else "warn" if hit > 0.1 else "bad"
        bar_cls = "bar-green" if hit > 0.5 else "bar-yellow" if hit > 0.1 else "bar-red"

        html += f"""<tr>
  <td><strong>{key}</strong></td>
  <td>{n}</td>
  <td>{'<span class="pill pill-green">' + str(patches) + '</span>' if patches else '<span class="pill pill-red">0</span>'}</td>
  <td class="cost">${avg_cost:.3f}</td>
  <td>{avg_steps:.0f}</td>
  <td>{avg_p:,.0f}</td>
  <td>{avg_c:,.0f}</td>
  <td><span class="{hit_cls}">{hit:.1%}</span> <div class="bar-container"><div class="bar {bar_cls}" style="width:{hit*100:.0f}%"></div></div></td>
  <td>{avg_evict:.0f}</td>
</tr>"""

    html += "</table>"

    # --- Per-instance comparison ---
    html += "<h2>Per-Instance Comparison</h2>"

    for instance_id in sorted(by_instance.keys()):
        runs = by_instance[instance_id]
        iid_short = instance_id[:40]
        section_id = instance_id.replace("__", "_").replace("-", "_")

        html += f"""
<div class="instance-section">
  <h3 class="toggle" id="btn-{section_id}" onclick="toggle('{section_id}')">{iid_short}</h3>
  <div class="collapsible" id="{section_id}">
    <table>
    <tr><th>Policy</th><th>Status</th><th>Steps</th><th>Cost</th><th>Prompt Tok</th><th>Cache Read</th><th>Hit Rate</th><th>Evicted</th><th>Cache Hit Sparkline</th></tr>
"""
        for r in sorted(runs, key=lambda x: x["policy"]):
            ct = r["cache_trace"]
            steps = len(ct)
            total_p = sum(t.get("prompt_tokens", 0) for t in ct)
            total_c = sum(t.get("cache_read_tokens", 0) for t in ct)
            hit = total_c / total_p if total_p > 0 else 0
            evicted = sum(t.get("messages_evicted", 0) for t in ct)
            hit_cls = "good" if hit > 0.5 else "warn" if hit > 0.1 else "bad"

            # Sparkline
            spark_vals = []
            for t in ct:
                p = t.get("prompt_tokens", 0)
                c = t.get("cache_read_tokens", 0)
                spark_vals.append(c / p if p > 0 else 0)
            blocks = " ▁▂▃▄▅▆▇█"
            spark = "".join(blocks[min(int(v * 8), 8)] for v in spark_vals[-30:])

            status_cls = "pill-green" if r["has_patch"] else "pill-yellow" if r["exit_status"] in ("LimitsExceeded", "Submitted") else "pill-red"
            status_text = "PATCH" if r["has_patch"] else r["exit_status"][:16]

            html += f"""<tr>
  <td><strong>{r['policy']}/{r['budget']}</strong></td>
  <td><span class="pill {status_cls}">{status_text}</span></td>
  <td>{steps}</td>
  <td class="cost">${r['cost']:.3f}</td>
  <td>{total_p:,}</td>
  <td>{total_c:,}</td>
  <td class="{hit_cls}">{hit:.1%}</td>
  <td>{evicted}</td>
  <td class="sparkline">{spark}</td>
</tr>"""

        html += "</table>"

        # Step-by-step trace for each policy
        for r in sorted(runs, key=lambda x: x["policy"]):
            ct = r["cache_trace"]
            if not ct:
                continue
            trace_id = f"{section_id}_{r['policy']}"
            html += f"""
    <h3 class="toggle" id="btn-{trace_id}" onclick="toggle('{trace_id}')" style="font-size:12px; color:#8b949e;">
      Step trace: {r['policy']}/{r['budget']}
    </h3>
    <div class="collapsible" id="{trace_id}">
      <table>
      <tr><th>Step</th><th>Msgs Sent</th><th>Evicted</th><th>Prompt Tok</th><th>Cache Read</th><th>Cache Create</th><th>Hit Rate</th><th>Cost</th></tr>
"""
            for t in ct:
                p = t.get("prompt_tokens", 0)
                c = t.get("cache_read_tokens", 0)
                cc = t.get("cache_creation_tokens", 0)
                hit = c / p if p > 0 else 0
                hit_cls = "good" if hit > 0.5 else "warn" if hit > 0.1 else "bad"
                ev = t.get("messages_evicted", 0)
                ev_cls = "bad" if ev > 0 else ""

                html += f"""<tr class="step-row">
  <td>{t.get('step', '?')}</td>
  <td>{t.get('sent_messages', '?')}</td>
  <td class="{ev_cls}">{ev}</td>
  <td>{p:,}</td>
  <td>{c:,}</td>
  <td>{cc:,}</td>
  <td class="{hit_cls}">{hit:.1%}</td>
  <td>${t.get('cost', 0):.4f}</td>
</tr>"""

            html += "</table></div>"

        # Exception info
        for r in runs:
            if r.get("exception"):
                html += f'<details style="margin-top:8px;"><summary style="color:#f85149;cursor:pointer;">Error: {r["policy"]}</summary><pre style="color:#8b949e;font-size:11px;overflow-x:auto;padding:8px;">{r["exception"][:1000]}</pre></details>'

        html += "</div></div>"

    html += "</body></html>"
    return html


def main():
    parser = argparse.ArgumentParser(description="Experiment dashboard")
    parser.add_argument("output_dir", help="Results directory")
    parser.add_argument("--watch", action="store_true", help="Auto-refresh HTML every 10s")
    parser.add_argument("--terminal", action="store_true", help="Rich terminal view instead of HTML")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    if args.terminal:
        # Fall back to simple Rich terminal view
        from rich.console import Console
        from rich.live import Live
        console = Console()
        # Import the old terminal dashboard logic if needed
        print("Use without --terminal for the full HTML dashboard.")
        return

    html_path = output_dir / "dashboard.html"
    refresh = 10 if args.watch else 0
    html = generate_html(output_dir, auto_refresh=refresh)
    html_path.write_text(html)
    print(f"Dashboard written to {html_path}")

    if not args.no_open:
        webbrowser.open(f"file://{html_path.resolve()}")

    if args.watch:
        print(f"Watching for changes (refresh every {refresh}s). Ctrl+C to stop.")
        try:
            while True:
                time.sleep(refresh)
                html = generate_html(output_dir, auto_refresh=refresh)
                html_path.write_text(html)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
