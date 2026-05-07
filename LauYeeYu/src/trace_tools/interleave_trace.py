#!/usr/bin/env python3
"""
Interleave requests from a long trace onto a shorter period to boost concurrency.

Takes a JSONL request trace (each line has a "timestamp" field in seconds) and
produces a new trace in which every request's timestamp is taken modulo a fold
period. For example `--range 7d --period 1d` keeps requests from the first 7
days of the input trace and collapses them onto a single day: a request
originally at day 4, 10:30 becomes a request at day 1, 10:30. The time-of-day
pattern is preserved while instantaneous concurrency scales with the fold
ratio.

Output records are re-sorted by the new (folded) timestamp so the output is
chronological. Note this can reorder turns of the same chat if that chat
spanned a fold-period boundary in the original trace; a warning is emitted
when this happens.

Example:
    python trace_tools/interleave_trace.py \\
        /netscratch/shared/juncheng/2026_chutes_requests/per_model_hash/Qwen_Qwen3-14B_hash16.jsonl \\
        ../datasets/chutes/Qwen_Qwen3-14B_hash16_7din1d.jsonl \\
        --range 7d --period 1d
"""

import argparse
import json
import sys
from collections import defaultdict


def parse_duration(s):
    """Parse duration like '7d', '24h', '90m', '3600s' into seconds."""
    s = s.strip().lower()
    units = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
    if s and s[-1] in units:
        return float(s[:-1]) * units[s[-1]]
    return float(s)


def interleave(input_path, output_path, range_seconds, period_seconds):
    records = []
    skipped_out_of_range = 0
    total_in = 0

    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_in += 1
            r = json.loads(line)
            ts = r['timestamp']
            if range_seconds is not None and ts >= range_seconds:
                skipped_out_of_range += 1
                continue
            r['timestamp'] = ts % period_seconds
            records.append(r)

    # Detect chats whose turns straddle a fold-period boundary: their turn
    # order will be scrambled by sorting on the folded timestamp.
    straddled_chats = 0
    if 'chat_id' in records[0] if records else False:
        by_chat = defaultdict(list)
        for r in records:
            by_chat[r['chat_id']].append(r.get('turn', 0))
        # Can't tell straddling from folded data alone; do a second pass on
        # the raw file to count chats that span period boundaries.
        chat_buckets = defaultdict(set)
        with open(input_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                ts = r['timestamp']
                if range_seconds is not None and ts >= range_seconds:
                    continue
                chat_buckets[r['chat_id']].add(int(ts // period_seconds))
        straddled_chats = sum(1 for b in chat_buckets.values() if len(b) > 1)

    records.sort(key=lambda r: r['timestamp'])

    with open(output_path, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')

    print(f'Read     {total_in} records from {input_path}')
    print(f'Kept     {len(records)} records (range < {range_seconds}s)')
    print(f'Dropped  {skipped_out_of_range} records outside range')
    print(f'Folded   onto period {period_seconds}s; wrote {output_path}')
    if straddled_chats:
        print(f'WARNING  {straddled_chats} chat_ids span fold-period '
              f'boundaries; their turns may be reordered after sorting.',
              file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='Input JSONL trace file')
    ap.add_argument('output', help='Output JSONL trace file')
    ap.add_argument('--range', dest='range_', required=True,
                    help='Keep only requests with timestamps < RANGE '
                         '(e.g. 7d, 24h, 3600s)')
    ap.add_argument('--period', default='1d',
                    help='Fold period: timestamps are taken modulo PERIOD '
                         '(default: 1d)')
    args = ap.parse_args()

    range_s = parse_duration(args.range_)
    period_s = parse_duration(args.period)
    if period_s <= 0:
        ap.error('--period must be positive')
    if range_s <= 0:
        ap.error('--range must be positive')

    interleave(args.input, args.output, range_s, period_s)


if __name__ == '__main__':
    main()
