#!/usr/bin/env python3
"""
An interactive script to analyze QWen trace files.

This script loads a trace file, processes the hash IDs to make them
position-aware, and then allows the user to interactively query
information about specific hash IDs.
"""

import json
from collections import defaultdict
import argparse

def load_trace_data(filepath):
    """Load and parse QWen trace data from a JSONL file."""
    data = []
    print(f"Loading trace data from {filepath}...")
    try:
        with open(filepath, 'r') as f:
            for line in f:
                try:
                    data.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    print(f"Warning: Skipping malformed JSON line.")
                    continue
    except FileNotFoundError:
        print(f"Error: The file '{filepath}' was not found.")
        return None
    
    print(f"Loaded {len(data)} requests.")
    return data

def translate_hash_ids_with_prefix(data):
    """
    Translates original hash IDs to new IDs that incorporate their prefix context.
    This ensures that a block's ID is unique to its position in the sequence.
    For a sequence [h1, h2, h3], the new IDs become [H(h1), H(H(h1), h2), H(H(H(h1), h2), h3)].
    """
    print("Translating hash IDs to include prefix context...")
    translated_data = []
    for req in data:
        new_req = req.copy()
        original_hash_ids = new_req.get('hash_ids', [])
        if not original_hash_ids:
            translated_data.append(new_req)
            continue

        new_hash_ids = []
        cumulative_hash = 0
        for hash_id in original_hash_ids:
            # The hash() function in python is not stable across processes.
            # Using a simple cumulative hash function for demonstration.
            cumulative_hash = (cumulative_hash * 31 + hash_id)
            new_hash_ids.append(cumulative_hash)
        new_req['hash_ids'] = new_hash_ids
        translated_data.append(new_req)
    print("Translation complete.")
    return translated_data

def analyze_hashes(data):
    """Analyzes the translated hash IDs to get their counts and appearance indices."""
    hash_counts = defaultdict(int)
    hash_appearances = defaultdict(list)

    print("Analyzing hash IDs...")
    for req_idx, req in enumerate(data):
        for block_idx, hash_id in enumerate(req.get('hash_ids', [])):
            hash_counts[hash_id] += 1
            hash_appearances[hash_id].append((req_idx, block_idx))
    
    print("Analysis complete.")
    return hash_counts, hash_appearances

def interactive_session(hash_counts, hash_appearances):
    """Starts an interactive session to query hash ID information."""
    print("\nStarting interactive session.")
    print("Enter a number (hash ID) to get its information.")
    print("Type 'quit', 'exit', or press Ctrl+D to end the session.")

    while True:
        try:
            user_input = input("\nEnter a hash ID: ").strip()
        except EOFError:
            print("\nExiting interactive session.")
            break

        if user_input.lower() in ['quit', 'exit']:
            print("Exiting interactive session.")
            break

        try:
            hash_id = int(user_input)
        except ValueError:
            print("Invalid input. Please enter a valid number.")
            continue

        if hash_id in hash_counts:
            count = hash_counts[hash_id]
            appearances = hash_appearances[hash_id]
            first_seen = appearances[0]
            last_seen = appearances[-1]
            print(f"  -> Hash ID: {hash_id}")
            print(f"     - First seen at request index: {first_seen[0]}, index in hash_ids list: {first_seen[1]}")
            print(f"     - Last seen at request index: {last_seen[0]}, index in hash_ids list: {last_seen[1]}")
            print(f"     - Time window of appearance (request indices): [{first_seen[0]}, {last_seen[0]}]")
            print(f"     - Total usage count: {count}")
        else:
            print(f"  -> Hash ID {hash_id} not found in the trace.")

def main():
    """Main function to run the interactive trace analyzer."""
    parser = argparse.ArgumentParser(
        description='Interactively analyze QWen trace files.'
    )
    parser.add_argument(
        'trace_file',
        help='Path to the QWen trace JSONL file.'
    )
    args = parser.parse_args()

    # Load and process the trace data
    data = load_trace_data(args.trace_file)
    if data:
        # translated_data = translate_hash_ids_with_prefix(data)
        hash_counts, hash_appearances = analyze_hashes(data)
        interactive_session(hash_counts, hash_appearances)

if __name__ == '__main__':
    main()
