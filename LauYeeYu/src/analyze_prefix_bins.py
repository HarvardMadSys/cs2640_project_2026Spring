#!/usr/bin/env python3
"""
Script to analyze QWen traces and group requests by prefix.
"""

import json
from collections import defaultdict
import argparse
from pathlib import Path

def load_trace_data(filepath, max_lines=None):
    """Load and parse QWen trace data from JSONL file."""
    data = []
    print(f"Loading trace data from {filepath}...")
    
    with open(filepath, 'r') as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            try:
                data.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                print(f"Skipping malformed line {i+1}")
                continue
    
    print(f"Loaded {len(data)} requests")
    return data

def main():
    parser = argparse.ArgumentParser(description='Analyze QWen traces by prefix bins.')
    parser.add_argument('trace_file', help='Path to QWen trace JSONL file')
    parser.add_argument('--output-file', help='Output file for the binned data (JSONL)')
    parser.add_argument('--prefix-length', type=int, default=5, help='Length of the prefix to group by')
    parser.add_argument('--filter-prefix', type=str, help='A comma-separated string of hash IDs to filter requests by. The length can be different from --prefix-length.')
    parser.add_argument('--max-lines', type=int, help='Maximum number of lines to process (for testing)')
    parser.add_argument('--min-length', type=int, help='Minimum length of the hash_ids sequence to include a request.')
    
    args = parser.parse_args()
    
    trace_path = Path(args.trace_file)
    if not trace_path.is_file():
        print(f"Error: Trace file not found at {trace_path}")
        return

    output_file = args.output_file
    if not output_file:
        output_file = trace_path.with_suffix('.binned.jsonl')

    # Load data
    data = load_trace_data(trace_path, args.max_lines)
    if not data:
        print(f"No data loaded from {trace_path}")
        return

    # Filter requests by a given prefix before binning
    if args.filter_prefix:
        try:
            # Convert the comma-separated string of hash IDs into a tuple of integers
            filter_p = tuple(map(int, args.filter_prefix.split(',')))
            filter_len = len(filter_p)
            print(f"Filtering for requests that start with prefix: {filter_p} (length: {filter_len})")
            
            original_count = len(data)
            # Keep only requests where the beginning of its hash_ids matches the filter prefix
            data = [req for req in data if tuple(req.get('hash_ids', [])[:filter_len]) == filter_p]
            print(f"Filtered {original_count} requests down to {len(data)}.")
        except ValueError:
            print("Error: --filter-prefix must be a comma-separated list of integers.")
            return
    
    # Filter requests by minimum hash_ids length
    if args.min_length is not None:
        print(f"Filtering for requests with hash_ids sequence length >= {args.min_length}...")
        original_count = len(data)
        data = [req for req in data if len(req.get('hash_ids', [])) >= args.min_length]
        print(f"Filtered {original_count} requests down to {len(data)}.")

    # Bin requests by prefix
    print(f"Binning requests by prefix of length {args.prefix_length}...")
    bins = defaultdict(list)
    for req in data:
        hash_ids = req.get('hash_ids', [])
        if len(hash_ids) >= args.prefix_length:
            prefix = tuple(hash_ids[:args.prefix_length])
            bins[prefix].append(req)
            
    print(f"Found {len(bins)} unique prefixes.")
    
    # Prepare bins for sorting
    sorted_bins_data = []
    for prefix, requests in bins.items():
        sorted_bins_data.append({
            'prefix': prefix,
            'count': len(requests),
            'requests': requests
        })
        
    # Sort bins by count
    sorted_bins_data.sort(key=lambda x: x['count'], reverse=True)
    
    # Write to output file
    print(f"Writing sorted bins to {output_file}...")
    with open(output_file, 'w') as f:
        for bin_data in sorted_bins_data:
            for req in bin_data['requests']:
                info = {
                    'timestamp': req.get('timestamp'),
                    'hash_ids': req.get('hash_ids', []),
                }
                f.write(json.dumps(info) + '\n')
            
    print("Analysis complete.")

if __name__ == '__main__':
    main()
