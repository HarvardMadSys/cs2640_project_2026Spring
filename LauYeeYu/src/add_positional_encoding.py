#!/usr/bin/env python3
"""
Script to add positional encoding to hash IDs in QWen trace files.

This script transforms trace files to ensure that blocks with the same hash_id
but different prefixes get unique identifiers. This solves the issue where a block
with the same hash_id can have different prefixes across different requests.

The positional encoding is sequential, starting from 1 for each trace file.
"""

import json
import argparse
import os
from pathlib import Path
import sys

def add_positional_encoding(data, start_id=1):
    """
    Add positional encoding to hash IDs.
    
    This function transforms hash IDs to include their prefix context:
    - For sequence [h1, h2, h3], new IDs become [H(h1), H(h1,h2), H(h1,h2,h3)]
    - Ensures blocks with same hash_id but different prefixes get unique IDs
    - Uses sequential numbering starting from start_id
    
    Args:
        data: List of trace requests
        start_id: Starting ID for sequential numbering (default: 1)
    
    Returns:
        tuple: (transformed_data, next_id)
    """
    print("Adding positional encoding to hash IDs...")
    transformed_data = []
    current_id = start_id
    
    # Track mapping from (prefix_context, original_hash) to new_id
    hash_mapping = {}
    
    for req_idx, req in enumerate(data):
        new_req = req.copy()
        original_hash_ids = new_req.get('hash_ids', [])
        if not original_hash_ids:
            transformed_data.append(new_req)
            continue

        new_hash_ids = []
        prefix_context = tuple()  # Empty tuple for no prefix
        
        for hash_id in original_hash_ids:
            # Create a unique key for this (prefix_context, original_hash) combination
            hash_key = (prefix_context, hash_id)
            
            # Check if we've seen this exact combination before
            if hash_key not in hash_mapping:
                hash_mapping[hash_key] = current_id
                new_hash_ids.append(current_id)
                current_id += 1
            else:
                new_hash_ids.append(hash_mapping[hash_key])
            
            # Update prefix context for next hash_id
            prefix_context = prefix_context + (hash_id,)
        
        new_req['hash_ids'] = new_hash_ids
        transformed_data.append(new_req)
    
    print(f"Positional encoding complete. Generated {len(hash_mapping)} unique hash IDs.")
    print(f"ID range: {start_id} to {current_id - 1}")
    
    return transformed_data, current_id

def process_trace_file(input_file, output_file, start_id=1):
    """
    Process a single trace file to add positional encoding.
    
    Args:
        input_file: Path to input JSONL trace file
        output_file: Path to output JSONL trace file
        start_id: Starting ID for sequential numbering
    
    Returns:
        int: Next available ID for continuation across files
    """
    print(f"\nProcessing trace file: {input_file}")
    print(f"Output file: {output_file}")
    
    # Load trace data
    data = []
    line_count = 0
    
    try:
        with open(input_file, 'r') as f:
            for line in f:
                line_count += 1
                try:
                    data.append(json.loads(line.strip()))
                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping malformed line {line_count}: {e}")
                    continue
    except FileNotFoundError:
        print(f"Error: Input file {input_file} not found")
        return start_id
    except Exception as e:
        print(f"Error reading file {input_file}: {e}")
        return start_id
    
    print(f"Loaded {len(data)} requests from {line_count} lines")
    
    if not data:
        print("No data to process")
        return start_id
    
    # Add positional encoding
    transformed_data, next_id = add_positional_encoding(data, start_id)
    
    # Write transformed data
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w') as f:
            for req in transformed_data:
                f.write(json.dumps(req) + '\n')
        print(f"Successfully wrote {len(transformed_data)} requests to {output_file}")
    except Exception as e:
        print(f"Error writing to {output_file}: {e}")
        return start_id
    
    return next_id

def main():
    parser = argparse.ArgumentParser(
        description='Add positional encoding to hash IDs in QWen trace files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process single file
  python add_positional_encoding.py qwen_traceA_blksz_16.jsonl qwen_traceA_blksz_16_pos.jsonl
  
  # Process multiple files with sequential IDs
  python add_positional_encoding.py trace1.jsonl trace2.jsonl -o output1.jsonl output2.jsonl
  
  # Process files in a directory
  python add_positional_encoding.py traces/*.jsonl -o encoded_traces/
        """
    )
    
    parser.add_argument('input_files', nargs='+', 
                       help='Input trace JSONL file(s) to process')
    parser.add_argument('-o', '--output', nargs='+',
                       help='Output file(s). If single value and multiple inputs, '
                            'treated as directory prefix.')
    parser.add_argument('--start-id', type=int, default=1,
                       help='Starting ID for sequential numbering (default: 1)')
    parser.add_argument('--suffix', default='_pos',
                       help='Suffix to add to output files when using default naming')
    
    args = parser.parse_args()
    
    # Validate input files exist
    for input_file in args.input_files:
        if not os.path.exists(input_file):
            print(f"Error: Input file {input_file} not found")
            sys.exit(1)
    
    current_id = args.start_id
    
    if args.output:
        # User specified output files
        if len(args.output) == 1 and len(args.input_files) > 1:
            # Single output argument with multiple inputs - treat as directory
            output_dir = Path(args.output[0])
            output_dir.mkdir(parents=True, exist_ok=True)
            
            for input_file in args.input_files:
                input_path = Path(input_file)
                output_file = output_dir / f"{input_path.stem}{args.suffix}.jsonl"
                current_id = process_trace_file(input_file, str(output_file), current_id)
        else:
            # One-to-one mapping of input to output files
            if len(args.output) != len(args.input_files):
                print("Error: Number of output files must match number of input files")
                sys.exit(1)
            
            for input_file, output_file in zip(args.input_files, args.output):
                current_id = process_trace_file(input_file, output_file, current_id)
    else:
        # Default: create output files in same directory with suffix
        for input_file in args.input_files:
            input_path = Path(input_file)
            output_file = input_path.parent / f"{input_path.stem}{args.suffix}.jsonl"
            current_id = process_trace_file(input_file, str(output_file), current_id)
    
    print(f"\n✅ Positional encoding complete!")
    print(f"📊 Final ID range: {args.start_id} to {current_id - 1}")
    print(f"🔢 Total unique hash IDs generated: {current_id - args.start_id}")

if __name__ == '__main__':
    main()
