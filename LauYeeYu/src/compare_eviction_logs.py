#!/usr/bin/env python3
"""
Compares the eviction logs of various cache algorithms.

This script analyzes which blocks were evicted by different cache algorithms.
It then looks up the overall popularity (total appearance count)
and in-request indices of those evicted blocks in the original trace.

Finally, it generates several plots to compare the eviction strategies:
1. A frequency count of the popularity of evicted blocks (log-log).
2. A CDF plot of the in-request index distribution of evicted blocks.
3. For specified pairs of policies (e.g., LRU vs. Belady), bar charts
   showing the raw count differences for popularity and index.
"""

import argparse
import json
import os
import re
from collections import defaultdict, Counter

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import pandas as pd
import numpy as np


def parse_compute_savings(log_filepath):
    binned_savings = defaultdict(dict)
    current_algo = None
    
    # Regexes for algorithm and bin lines
    algo_regex = re.compile(r"Algorithm (\S+): Compute savings by intensity bin:")
    # Capture group 1: Bin label (e.g. "Intensity 1" or "Range 2 - 3")
    # Capture group 2: Saved count
    # Capture group 3: Ratio (optional to capture, but useful for verifying line format)
    bin_regex = re.compile(r"Bin\s+\d+\s+\((.+)\):\s+Saved\s+(\d+)\s+/\s+Total\s+\d+\s+\(Ratio:\s+([\d.]+)\)")

    try:
        with open(log_filepath, 'r') as f:
            for line in f:
                algo_match = algo_regex.search(line)
                if algo_match:
                    current_algo = algo_match.group(1)
                    continue
                
                if current_algo:
                    bin_match = bin_regex.search(line)
                    if bin_match:
                        bin_label_raw = bin_match.group(1).strip()
                        # Normalize the label to be consistent
                        bin_label = bin_label_raw.replace("Intensity", "").replace("Range", "").replace(" ", "")
                        saved = int(bin_match.group(2))
                        binned_savings[current_algo][bin_label] = saved
    except FileNotFoundError:
        print(f"Error: Simulation log file not found at '{log_filepath}'")
        return None
        
    return binned_savings

def load_trace_data(filepath):
    """Load and parse QWen trace data from a JSONL file."""
    data = []
    print(f"Loading trace data from {filepath}...")
    if not os.path.exists(filepath):
        print(f"Error: The file '{filepath}' was not found.")
        return None
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


def analyze_trace(data):
    """Analyzes hash IDs to get their popularity and in-request indices."""
    hash_popularity = defaultdict(int)
    hash_indices = defaultdict(int)

    print("Analyzing hash IDs for popularity and indices...")
    for req_idx, req in enumerate(data):
        for block_idx, hash_id in enumerate(req.get('hash_ids', [])):
            hash_popularity[hash_id] += 1
            if hash_id in hash_indices:
                assert hash_indices[hash_id] == block_idx, \
                    f"Error: Inconsistent indices for hash ID {hash_id}."
            else:
                hash_indices[hash_id] = block_idx

    print("Analysis complete.")
    return hash_popularity, hash_indices


def parse_eviction_log_for_ids(filepath):
    """Parses an eviction log to get the list of evicted block IDs."""
    evicted_ids = []
    print(f"Parsing eviction log for IDs: {filepath}...")
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    evicted_ids.append(int(line))
    except FileNotFoundError:
        print(f"Warning: Eviction log '{filepath}' not found.")
        return None
    print(f"Found {len(evicted_ids)} evicted blocks.")
    return evicted_ids

def plot_cdf(ax, data, label, linestyle='-'):
    """Helper function to plot a CDF (Cumulative Distribution Function) onto a given axis as a step function."""
    if not data:
        print(f"Warning: No data available to plot for '{label}' CDF. Its line will not appear in the plot.")
        return
        
    # Sort the data
    x = np.sort(data)
    # Calculate the y-values for the CDF, ranging from 0 to (N-1)/N
    y = np.arange(len(x)) / len(x)
    
    # Plot as a step function. 'steps-post' means the step happens after the x-value.
    ax.plot(x, y, drawstyle='steps-post', label=label, linewidth=1.5, linestyle=linestyle)

def plot_frequency(ax, data, label, linestyle='-'):
    """Helper function to plot a frequency count on log-log axes."""
    if not data:
        print(f"Warning: No data available to plot for '{label}' frequency. Its line will not appear in the plot.")
        return

    counts = Counter(data)
    # Sort by popularity (the key of the counter)
    popularities = sorted(counts.keys())
    # Get the corresponding counts
    count_values = [counts[p] for p in popularities]
    
    ax.plot(popularities, count_values, label=label, linewidth=1.5, linestyle=linestyle)
    ax.set_xscale('log')
    ax.set_yscale('log')


def align_yaxis_zero(ax1, ax2):
    """Aligns the y-axes of two plots to have their zeros at the same height,
    expanding the axis limits to do so, not shrinking them.
    """
    y1_min, y1_max = ax1.get_ylim()
    y2_min, y2_max = ax2.get_ylim()

    # Determine the new limits to include 0, without shrinking.
    y1_min_new = min(y1_min, 0)
    y1_max_new = max(y1_max, 0)
    y2_min_new = min(y2_min, 0)
    y2_max_new = max(y2_max, 0)

    # Calculate the proportion of the range that is negative for each axis
    range1 = y1_max_new - y1_min_new
    prop_neg1 = -y1_min_new / range1 if range1 != 0 else 0

    range2 = y2_max_new - y2_min_new
    prop_neg2 = -y2_min_new / range2 if range2 != 0 else 0

    # Determine the largest negative proportion needed
    max_prop_neg = max(prop_neg1, prop_neg2)

    # Calculate the largest positive proportion needed
    prop_pos1 = y1_max_new / range1 if range1 != 0 else 0
    prop_pos2 = y2_max_new / range2 if range2 != 0 else 0
    max_prop_pos = max(prop_pos1, prop_pos2)
    
    # In case one of the axes is all positive or all negative, the sum of
    # max proportions can be > 1. We need to normalize them.
    if max_prop_neg + max_prop_pos > 1:
        total_prop = max_prop_neg + max_prop_pos
        max_prop_neg /= total_prop
        max_prop_pos /= total_prop

    # Calculate new total range for each axis based on its largest part and the required proportion
    new_range1 = max(y1_max_new / max_prop_pos if max_prop_pos > 0 else 0,
                     -y1_min_new / max_prop_neg if max_prop_neg > 0 else 0)
    
    new_range2 = max(y2_max_new / max_prop_pos if max_prop_pos > 0 else 0,
                     -y2_min_new / max_prop_neg if max_prop_neg > 0 else 0)

    # Set the new limits
    ax1.set_ylim(-new_range1 * max_prop_neg, new_range1 * max_prop_pos)
    ax2.set_ylim(-new_range2 * max_prop_neg, new_range2 * max_prop_pos)


def parse_eviction_log_for_misses(filepath):
    """Parses an eviction log for miss information on a per-request basis."""
    misses_by_req = defaultdict(dict)
    current_req_id = None

    req_regex = re.compile(r"Request (\d+) of size \d+")
    miss_regex = re.compile(r"Miss: (\d+), reuse distance (-?\d+)")

    print(f"Parsing eviction log for misses: {filepath}...")
    try:
        with open(filepath, 'r') as f:
            for line in f:
                req_match = req_regex.search(line)
                if req_match:
                    current_req_id = int(req_match.group(1))
                    continue

                if current_req_id is not None:
                    miss_match = miss_regex.search(line)
                    if miss_match:
                        block_id = int(miss_match.group(1))
                        reuse_dist = int(miss_match.group(2))
                        if reuse_dist != -1:
                            misses_by_req[current_req_id][block_id] = reuse_dist
    except FileNotFoundError:
        print(f"Warning: Eviction log '{filepath}' not found.")
        return None
    
    print(f"Found misses for {len(misses_by_req)} requests.")
    return misses_by_req


def plot_reuse_distance_diff(policy1_path, policy2_path, hash_indices_map, output_dir, trace_name, cache_size):
    """
    Compares two eviction logs to find blocks missed by policy1 but not policy2,
    and plots the reuse distance distribution of those blocks.
    """
    policy1_misses = parse_eviction_log_for_misses(policy1_path)
    policy2_misses = parse_eviction_log_for_misses(policy2_path)

    if policy1_misses is None or policy2_misses is None:
        print("Could not proceed with reuse distance comparison due to missing log files.")
        return

    reuse_distances = []

    # Find common request IDs
    common_req_ids = set(policy1_misses.keys()) & set(policy2_misses.keys())
    print(f"Comparing {len(common_req_ids)} common requests between the two policies.")

    for req_id in common_req_ids:
        p1_missed_blocks = policy1_misses[req_id]
        p2_missed_blocks = policy2_misses.get(req_id, {})

        # Find blocks missed in p1 but not in p2 (which means it was a hit in p2)
        diff_missed_blocks = set(p1_missed_blocks.keys()) - set(p2_missed_blocks.keys())

        for block_id in diff_missed_blocks:
            # Assuming block_id is the same as hash_id
            block_index = hash_indices_map.get(block_id)

            if block_index is not None and 64 <= block_index <= 512:
                reuse_dist = p1_missed_blocks[block_id]
                # The check for -1 is already in the parsing function.
                reuse_distances.append(reuse_dist)

    if not reuse_distances:
        print("No blocks found matching the criteria (missed by GDSF_compute, hit by BeladyCompute, index 64-512).")
        return

    # --- Create the Plot ---
    fig, ax = plt.subplots(figsize=(12, 8))
    # Set a lower zorder for the grid and a higher one for the bars.
    ax.grid(True, which="both", ls="--", zorder=0)
    ax.hist(reuse_distances, bins=50, edgecolor='black', zorder=2)
    
    p1_name = os.path.splitext(os.path.basename(policy1_path))[0]
    p2_name = os.path.splitext(os.path.basename(policy2_path))[0]
    
    ax.set_title(f'Reuse Distance of Blocks Missed by {p1_name} but Hit by {p2_name}\n(Block Index 64-512, Trace: {trace_name}, Cache Size: {cache_size})')
    ax.set_xlabel('Reuse Distance')
    ax.set_ylabel('Frequency')
    ax.set_yscale('log')

    plot_path = os.path.join(output_dir, f'reuse_dist_diff_{p1_name}_vs_{p2_name}.png')
    fig.savefig(plot_path, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved Reuse Distance Difference plot to {plot_path}")


def plot_miss_heatmap_by_reuse_dist_index(policy1_path, policy2_path, hash_indices_map, output_dir, trace_name, cache_size,
                                          title_fontsize=18, label_fontsize=14, tick_fontsize=12, annotation_fontsize=10, cbar_label_fontsize=14):
    """
    Plots a heatmap of blocks missed by policy1 but hit by policy2,
    with reuse distance on the x-axis and block index on the y-axis.
    """
    from matplotlib.colors import LogNorm
    p1_name = os.path.splitext(os.path.basename(policy1_path))[0]
    p2_name = os.path.splitext(os.path.basename(policy2_path))[0]

    policy1_misses = parse_eviction_log_for_misses(policy1_path)
    policy2_misses = parse_eviction_log_for_misses(policy2_path)

    if policy1_misses is None or policy2_misses is None:
        print("Could not proceed with reuse distance/index heatmap due to missing log files.")
        return

    reuse_distances_unfiltered = []
    block_indices_unfiltered = []

    common_req_ids = set(policy1_misses.keys()) & set(policy2_misses.keys())

    for req_id in common_req_ids:
        p1_missed_blocks = policy1_misses[req_id]
        p2_missed_blocks = policy2_misses.get(req_id, {})

        diff_missed_blocks = set(p1_missed_blocks.keys()) - set(p2_missed_blocks.keys())

        for block_id in diff_missed_blocks:
            block_index = hash_indices_map.get(block_id)
            if block_index is not None:
                reuse_dist = p1_missed_blocks[block_id]
                reuse_distances_unfiltered.append(reuse_dist)
                block_indices_unfiltered.append(block_index)
    
    # Convert to numpy arrays for easier filtering
    reuse_distances_unfiltered = np.array(reuse_distances_unfiltered)
    block_indices_unfiltered = np.array(block_indices_unfiltered)
    
    # Apply filters for specified ranges
    rd_mask = (reuse_distances_unfiltered >= 0) & (reuse_distances_unfiltered <= 2000)
    idx_mask = (block_indices_unfiltered >= 0) & (block_indices_unfiltered <= 2048)
    combined_mask = rd_mask & idx_mask
    
    reuse_distances = reuse_distances_unfiltered[combined_mask]
    block_indices = block_indices_unfiltered[combined_mask]

    if reuse_distances.size == 0:
        print(f"No blocks found missed by {p1_name} but hit by {p2_name} within the specified ranges (RD: 0-2000, Idx: 0-2048) to generate a heatmap.")
        return

    # --- Binning (Linear) ---
    # Reuse distance bins (x-axis)
    num_rd_bins = 20
    rd_bins = np.linspace(0, 2001, num=num_rd_bins + 1, dtype=int)
    rd_bin_labels = [f"{rd_bins[i]}-{rd_bins[i+1]-1}" for i in range(len(rd_bins)-1)]

    # Index bins (y-axis)
    num_idx_bins = 20
    idx_bins = np.linspace(0, 2049, num=num_idx_bins + 1, dtype=int)
    idx_bin_labels = [f"{idx_bins[i]}-{idx_bins[i+1]-1}" for i in range(len(idx_bins)-1)]

    # --- Create 2D histogram ---
    counts, _, _ = np.histogram2d(reuse_distances, block_indices, bins=[rd_bins, idx_bins])
    counts = counts.T

    # --- Plotting ---
    fig, ax = plt.subplots(figsize=(18, 14))

    # Use a logarithmic color scale. Replace 0 with NaN for plotting to avoid log(0).
    counts_for_plot = counts.astype(float)
    counts_for_plot[counts_for_plot == 0] = np.nan

    # Avoid LogNorm error with all-NaN data after filtering
    if np.all(np.isnan(counts_for_plot)):
        print(f"Skipping heatmap for {p1_name} vs {p2_name} as there is no data to plot after filtering.")
        plt.close(fig)
        return

    # Use LogNorm for the color scale. vmin=1 is important for count data.
    im = ax.imshow(counts_for_plot, cmap='viridis', aspect='auto', origin='lower',
                   norm=LogNorm(vmin=1, vmax=70000))

    cbar = ax.figure.colorbar(im, ax=ax)
    cbar.ax.set_ylabel('Frequency Count (Log Scale)', rotation=-90, va="bottom", fontsize=cbar_label_fontsize)

    # Add text annotations for counts
    for i in range(len(idx_bin_labels)):
        for j in range(len(rd_bin_labels)):
            if counts[i, j] > 0:
                ax.text(j, i, f'{counts[i, j]:.0f}', ha="center", va="center", color="black", fontsize=annotation_fontsize)

    ax.set_xticks(np.arange(len(rd_bin_labels)))
    ax.set_yticks(np.arange(len(idx_bin_labels)))
    ax.set_xticklabels(rd_bin_labels, rotation=45, ha="right", fontsize=tick_fontsize)
    ax.set_yticklabels(idx_bin_labels, fontsize=tick_fontsize)
    ax.set_xlabel("Reuse Distance in Requests (Binned)", fontsize=label_fontsize)
    ax.set_ylabel("Block Index in Request (Binned)", fontsize=label_fontsize)
    ax.set_title(f'Heatmap of Blocks Missed by {p1_name} but Hit by {p2_name}\nTrace: {trace_name}, Cache Size: {cache_size}', fontsize=title_fontsize)

    heatmap_path = os.path.join(output_dir, f'miss_heatmap_reuse_dist_index_{p1_name}_vs_{p2_name}.png')
    fig.savefig(heatmap_path, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved Reuse Distance/Index Heatmap to {heatmap_path}")



def main():
    """Main function to run the eviction log comparison."""
    parser = argparse.ArgumentParser(
        description='Compare eviction logs by analyzing the popularity and index of evicted blocks.'
    )
    parser.add_argument(
        '--log-dir',
        required=True,
        help='Directory containing eviction log files (e.g., LRU.txt).'
    )
    parser.add_argument(
        '--trace-file',
        required=True,
        help='Path to the QWen trace JSONL file.'
    )
    parser.add_argument(
        '--output-dir',
        default='plots',
        help='Directory to save the output plots.'
    )
    parser.add_argument(
        '--sim-log',
        required=True,
        help='Path to the simulation log file from evaluate.cpp.'
    )
    args = parser.parse_args()

    # --- 1. Set up paths and discover policies ---
    if not os.path.isdir(args.log_dir):
        print(f"Error: Log directory not found at '{args.log_dir}'")
        return

    trace_file_path = args.trace_file
    
    try:
        log_files = [f for f in os.listdir(args.log_dir) if f.endswith('.txt')]
        policies = sorted([os.path.splitext(f)[0] for f in log_files])
    except FileNotFoundError:
        print(f"Error: Cannot access log directory at '{args.log_dir}'")
        return

    if not policies:
        print(f"Error: No policy logs (.txt files) found in '{args.log_dir}'")
        return

    print(f"Found policies to analyze: {policies}")

    # --- 1.5. Parse simulation log for compute savings ---
    compute_savings = parse_compute_savings(args.sim_log)
    if not compute_savings:
        print("Warning: Could not parse any compute savings. Skipping savings plots.")


    # --- 2. Analyze the original trace ---
    trace_data = load_trace_data(trace_file_path)
    if not trace_data:
        return

    hash_popularity_map, hash_indices_map = analyze_trace(trace_data)

    # --- 3. Process logs for each policy ---
    results = defaultdict(dict)
    for policy in policies:
        print(f"\n--- Processing Policy: {policy} ---")
        log_path = os.path.join(args.log_dir, f"{policy}.txt")
        evicted_ids = parse_eviction_log_for_ids(log_path)

        if not evicted_ids:
            results[policy]['popularities'] = []
            results[policy]['indices'] = []
            continue

        popularities = []
        indices = []
        for block_id in evicted_ids:
            if block_id in hash_popularity_map:
                # We want all occurrences of this block's popularity and indices
                popularities.append(hash_popularity_map[block_id])
                indices.append(hash_indices_map[block_id])
        
        # We now have a list of popularity scores, one for each evicted block
        results[policy]['popularities'] = popularities
        results[policy]['indices'] = indices

    # --- 4. Print summary statistics ---
    for stat_name, stat_key in [("Popularity", "popularities"), ("In-Request Index", "indices")]:
        print(f"\n--- {stat_name} of Evicted Blocks ---")
        for policy in policies:
            data = results[policy][stat_key]
            if data:
                print(f"\n{policy} Evicted Blocks:")
                print(pd.Series(data).describe())

    # --- Sanity-Checking Processed Policy Data ---
    print("\n--- Sanity-Checking Processed Policy Data ---")
    if len(policies) > 1:
        # Check for identical popularity distributions
        p0 = policies[0]
        c0 = Counter(results[p0]['popularities'])
        for i in range(1, len(policies)):
            p_next = policies[i]
            c_next = Counter(results[p_next]['popularities'])
            if c0 and c0 == c_next:
                print(f"Warning: Popularity distribution of evicted blocks for '{p0}' and '{p_next}' are identical.")
                print("         This is unexpected and may indicate an issue with the input eviction logs.")
        
        # Check for identical index distributions
        c0_idx = Counter(results[p0]['indices'])
        for i in range(1, len(policies)):
            p_next = policies[i]
            c_next_idx = Counter(results[p_next]['indices'])
            if c0_idx and c0_idx == c_next_idx:
                print(f"Warning: In-request index distribution of evicted blocks for '{p0}' and '{p_next}' are identical.")
                print("         This is unexpected and may indicate an issue with the input eviction logs.")

    # --- 5. Visualize results ---
    trace_name = os.path.basename(args.trace_file)
    cache_size = os.path.basename(args.log_dir)
    plot_subdir = os.path.join(args.output_dir, f"{trace_name}_cache_{cache_size}")
    os.makedirs(plot_subdir, exist_ok=True)
    print(f"\nSaving plots to {plot_subdir}")
    
    # Define a cycle of line styles for plotting
    styles = ['-', '--', ':', '-.']
    
    # Plot 1: Popularity Frequency Count
    fig1, ax1 = plt.subplots(figsize=(12, 8))
    for i, policy in enumerate(policies):
        plot_frequency(ax1, results[policy]['popularities'], policy, linestyle=styles[i % len(styles)])
    
    ax1.set_title(f'Frequency of Evicted Block Popularity\nTrace: {trace_name}, Cache Size: {cache_size}')
    ax1.set_xlabel('Block Popularity (Total Appearances in Trace) - Log Scale')
    ax1.set_ylabel('Number of Evicted Blocks - Log Scale')
    ax1.legend()
    ax1.grid(True, which="both", ls="--")
    pop_freq_path = os.path.join(plot_subdir, 'evicted_block_popularity_counts.png')
    fig1.savefig(pop_freq_path, bbox_inches='tight')
    plt.close(fig1)
    print(f"Saved Popularity Frequency plot to {pop_freq_path}")

    # Plot 2: Popularity CDF
    fig2, ax2 = plt.subplots(figsize=(12, 8))
    for i, policy in enumerate(policies):
        plot_cdf(ax2, results[policy]['popularities'], policy, linestyle=styles[i % len(styles)])

    ax2.set_xscale('log')
    ax2.set_title(f'Popularity of Evicted Blocks (CDF)\nTrace: {trace_name}, Cache Size: {cache_size}')
    ax2.set_xlabel('Block Popularity - Log Scale')
    ax2.set_ylabel('Cumulative Probability')
    ax2.legend()
    ax2.grid(True, which="both", ls="--")
    pop_cdf_path = os.path.join(plot_subdir, 'evicted_block_popularity_cdf.png')
    fig2.savefig(pop_cdf_path, bbox_inches='tight')
    plt.close(fig2)
    print(f"Saved Popularity CDF plot to {pop_cdf_path}")

    # Plot 3: Index CDF
    fig3, ax3 = plt.subplots(figsize=(12, 8))
    for i, policy in enumerate(policies):
        plot_cdf(ax3, results[policy]['indices'], policy, linestyle=styles[i % len(styles)])

    ax3.set_xscale('log')
    ax3.set_title(f'In-Request Index of Evicted Blocks (CDF)\nTrace: {trace_name}, Cache Size: {cache_size}')
    ax3.set_xlabel('Block Index in Request - Log Scale')
    ax3.set_ylabel('Cumulative Probability')
    ax3.legend()
    ax3.grid(True, which="both", ls="--")
    idx_cdf_path = os.path.join(plot_subdir, 'evicted_block_index_cdf.png')
    fig3.savefig(idx_cdf_path, bbox_inches='tight')
    plt.close(fig3)
    print(f"Saved Index CDF plot to {idx_cdf_path}")

    # --- Difference Plots ---
    comparison_pairs = [('LRU', 'BeladyCompute'), ('LRU', 'Belady'), ('GDSF_compute', 'Belady'), ('GDSF_compute', 'BeladyCompute')]
    for policy1, policy2 in comparison_pairs:
        if policy1 not in results or policy2 not in results:
            print(f"\nSkipping difference plots for '{policy1}' vs '{policy2}' as one or both policies were not found.")
            continue
            
        print(f"\nGenerating comparison plots for {policy1} vs {policy2}...")

        if compute_savings and policy1 in compute_savings and policy2 in compute_savings:
            p1_savings = compute_savings[policy1]
            p2_savings = compute_savings[policy2]

            all_labels_set = set(p1_savings.keys()) | set(p2_savings.keys())
            
            if all_labels_set:
                def sort_key(label):
                    first_num = int(label.split('-')[0].strip())
                    return first_num
                
                sorted_labels = sorted(list(all_labels_set), key=sort_key)
                
                p1_values = [p1_savings.get(lbl, 0) for lbl in sorted_labels]
                p2_values = [p2_savings.get(lbl, 0) for lbl in sorted_labels]
                
                x = np.arange(len(sorted_labels))
                width = 0.35

                fig, ax = plt.subplots(figsize=(15, 8))
                rects1 = ax.bar(x - width/2, p1_values, width, label=policy1)
                rects2 = ax.bar(x + width/2, p2_values, width, label=policy2)

                ax.set_ylabel('Absolute Compute Savings')
                ax.set_title(f'Binned Compute Savings: {policy1} vs {policy2}')
                ax.set_xticks(x)
                ax.set_xticklabels(sorted_labels, rotation=45, ha='right')
                ax.legend()
                ax.grid(True, axis='y', linestyle='--')

                fig.tight_layout()
                
                binned_savings_path = os.path.join(plot_subdir, f'binned_compute_savings_{policy1}_vs_{policy2}.png')
                fig.savefig(binned_savings_path, bbox_inches='tight')
                plt.close(fig)
                print(f"Saved Binned Compute Savings plot to {binned_savings_path}")

        # Plot for Popularity Difference
        p1_counts = Counter(results[policy1]['popularities'])
        p2_counts = Counter(results[policy2]['popularities'])
        all_popularities = sorted(list(set(p1_counts.keys()) | set(p2_counts.keys())))
        
        if all_popularities:
            max_pop = max(all_popularities)
            num_bins = 20
            # Create bin edges
            bins = np.geomspace(1, max_pop + 1, num=num_bins)
            bins = np.unique(np.round(bins).astype(int))
            if bins[-1] <= max_pop:
                bins = np.append(bins, max_pop + 1)
            
            bin_labels = [f"{bins[i]}-{bins[i+1]-1}" if bins[i] < bins[i+1]-1 else f"{bins[i]}" for i in range(len(bins)-1)]
            
            # Bin the data using pandas
            p1_series = pd.Series(p1_counts)
            p2_series = pd.Series(p2_counts)
            
            p1_binned = p1_series.groupby(pd.cut(p1_series.index, bins, right=False, labels=bin_labels), observed=False).sum()
            p2_binned = p2_series.groupby(pd.cut(p2_series.index, bins, right=False, labels=bin_labels), observed=False).sum()
            
            binned_df = pd.concat([p1_binned, p2_binned], axis=1).fillna(0)
            binned_df.columns = ['p1', 'p2']
            
            differences = binned_df['p1'] - binned_df['p2']
            with np.errstate(divide='ignore', invalid='ignore'):
                relative_increases = (differences / binned_df['p2'])
                relative_increases.replace([np.inf, -np.inf], np.nan, inplace=True)
            
            x_pos = np.arange(len(binned_df.index))
            
            # Difference Plot
            fig, ax = plt.subplots(figsize=(15, 8))
            bar_plot = ax.bar(x_pos, differences.values, color='b', alpha=0.6, label=f'Absolute Difference ({policy1} - {policy2})')
            ax.axhline(0, color='grey', linewidth=0.8)
            ax.set_title(f'Eviction Count Difference (Popularity): {policy1} vs {policy2}\nTrace: {trace_name}, Cache Size: {cache_size}')
            ax.set_xlabel('Block Popularity (Binned)')
            ax.set_ylabel(f'Difference in Eviction Counts', color='b')
            ax.tick_params(axis='y', labelcolor='b')
            ax.grid(True, which="major", axis='y', linestyle='--')
            
            ax.set_xticks(x_pos)
            ax.set_xticklabels(binned_df.index, rotation=45, ha='right')

            ax_twin = ax.twinx()
            line_plot, = ax_twin.plot(x_pos, relative_increases.values, color='r', marker='o', linestyle='--', label=f'Relative Increase (vs {policy2})')
            ax_twin.set_ylabel('Relative Increase', color='r')
            ax_twin.tick_params(axis='y', labelcolor='r')
            ax_twin.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))
            align_yaxis_zero(ax, ax_twin)
            ax.legend(handles=[bar_plot, line_plot], loc='upper right')

            diff_path = os.path.join(plot_subdir, f'eviction_popularity_difference_{policy1}_vs_{policy2}.png')
            fig.savefig(diff_path, bbox_inches='tight')
            plt.close(fig)
            print(f"Saved Popularity Count Difference plot to {diff_path}")

            # Actual Counts Plot
            fig_actual, ax_actual = plt.subplots(figsize=(15, 8))
            width = 0.35
            rects1 = ax_actual.bar(x_pos - width/2, binned_df['p1'], width, label=policy1)
            rects2 = ax_actual.bar(x_pos + width/2, binned_df['p2'], width, label=policy2)
            ax_actual.set_ylabel('Eviction Counts')
            ax_actual.set_title(f'Eviction Counts (Popularity): {policy1} vs {policy2}\nTrace: {trace_name}, Cache Size: {cache_size}')
            ax_actual.set_xticks(x_pos)
            ax_actual.set_xticklabels(binned_df.index, rotation=45, ha='right')
            ax_actual.legend()
            ax_actual.grid(True, axis='y', linestyle='--')
            
            actual_path = os.path.join(plot_subdir, f'eviction_popularity_counts_{policy1}_vs_{policy2}.png')
            fig_actual.savefig(actual_path, bbox_inches='tight')
            plt.close(fig_actual)
            print(f"Saved Popularity Actual Counts plot to {actual_path}")

            # Weighted Cost Plot (Popularity)
            p1_pop_weights = defaultdict(float)
            for pop, idx in zip(results[policy1]['popularities'], results[policy1]['indices']):
                p1_pop_weights[pop] += (idx + 1)
            
            p2_pop_weights = defaultdict(float)
            for pop, idx in zip(results[policy2]['popularities'], results[policy2]['indices']):
                p2_pop_weights[pop] += (idx + 1)
            
            p1_series_w = pd.Series(p1_pop_weights)
            p2_series_w = pd.Series(p2_pop_weights)
            
            p1_binned_w = p1_series_w.groupby(pd.cut(p1_series_w.index, bins, right=False, labels=bin_labels), observed=False).sum()
            p2_binned_w = p2_series_w.groupby(pd.cut(p2_series_w.index, bins, right=False, labels=bin_labels), observed=False).sum()
            
            binned_df_w = pd.concat([p1_binned_w, p2_binned_w], axis=1).fillna(0)
            binned_df_w.columns = ['p1', 'p2']
            
            fig_w, ax_w = plt.subplots(figsize=(15, 8))
            ax_w.bar(x_pos - width/2, binned_df_w['p1'], width, label=policy1)
            ax_w.bar(x_pos + width/2, binned_df_w['p2'], width, label=policy2)
            ax_w.set_ylabel('Weighted Eviction Cost (Sum of Index+1)')
            ax_w.set_title(f'Weighted Eviction Cost by Popularity: {policy1} vs {policy2}\nTrace: {trace_name}, Cache Size: {cache_size}')
            ax_w.set_xticks(x_pos)
            ax_w.set_xticklabels(binned_df.index, rotation=45, ha='right')
            ax_w.legend()
            ax_w.grid(True, axis='y', linestyle='--')
            
            weighted_path = os.path.join(plot_subdir, f'eviction_weighted_cost_by_popularity_{policy1}_vs_{policy2}.png')
            fig_w.savefig(weighted_path, bbox_inches='tight')
            plt.close(fig_w)
            print(f"Saved Weighted Cost by Popularity plot to {weighted_path}")


        # Plot for Index Difference
        p1_counts = Counter(results[policy1]['indices'])
        p2_counts = Counter(results[policy2]['indices'])
        all_indices = sorted(list(set(p1_counts.keys()) | set(p2_counts.keys())))

        if all_indices:
            max_idx = max(all_indices)
            num_bins = 20

            if max_idx == 0:
                bins = np.array([0, 1])
            else:
                log_bins = np.geomspace(1, max_idx + 1, num=num_bins -1)
                bins = np.concatenate(([0], log_bins))
            
            bins = np.unique(np.round(bins).astype(int))
            if bins[-1] <= max_idx:
                bins = np.append(bins, max_idx + 1)
            
            bin_labels = [f"{bins[i]}-{bins[i+1]-1}" if bins[i] < bins[i+1]-1 else f"{bins[i]}" for i in range(len(bins)-1)]

            p1_series = pd.Series(p1_counts)
            p2_series = pd.Series(p2_counts)

            p1_binned = p1_series.groupby(pd.cut(p1_series.index, bins, right=False, labels=bin_labels), observed=False).sum()
            p2_binned = p2_series.groupby(pd.cut(p2_series.index, bins, right=False, labels=bin_labels), observed=False).sum()
            
            binned_df = pd.concat([p1_binned, p2_binned], axis=1).fillna(0)
            binned_df.columns = ['p1', 'p2']
            
            differences = binned_df['p1'] - binned_df['p2']
            with np.errstate(divide='ignore', invalid='ignore'):
                relative_increases = (differences / binned_df['p2'])
                relative_increases.replace([np.inf, -np.inf], np.nan, inplace=True)
            
            x_pos = np.arange(len(binned_df.index))

            # Difference Plot
            fig, ax = plt.subplots(figsize=(15, 8))
            bar_plot = ax.bar(x_pos, differences.values, color='b', alpha=0.6, label=f'Absolute Difference ({policy1} - {policy2})')
            ax.axhline(0, color='grey', linewidth=0.8)
            ax.set_title(f'Eviction Count Difference (In-Request Index): {policy1} vs {policy2}\nTrace: {trace_name}, Cache Size: {cache_size}')
            ax.set_xlabel('Block Index in Request (Binned)')
            ax.set_ylabel('Difference in Eviction Counts', color='b')
            ax.tick_params(axis='y', labelcolor='b')
            ax.grid(True, which="major", axis='y', linestyle='--')
            
            ax.set_xticks(x_pos)
            ax.set_xticklabels(binned_df.index, rotation=45, ha='right')
            
            ax_twin = ax.twinx()
            line_plot, = ax_twin.plot(x_pos, relative_increases.values, color='r', marker='o', linestyle='--', label=f'Relative Increase (vs {policy2})')
            ax_twin.set_ylabel('Relative Increase', color='r')
            ax_twin.tick_params(axis='y', labelcolor='r')
            ax_twin.yaxis.set_major_formatter(mtick.PercentFormatter(1.0))
            align_yaxis_zero(ax, ax_twin)
            ax.legend(handles=[bar_plot, line_plot], loc='upper right')

            diff_path = os.path.join(plot_subdir, f'eviction_index_difference_{policy1}_vs_{policy2}.png')
            fig.savefig(diff_path, bbox_inches='tight')
            plt.close(fig)
            print(f"Saved Index Count Difference plot to {diff_path}")

            # Actual Counts Plot
            fig_actual, ax_actual = plt.subplots(figsize=(15, 8))
            width = 0.35
            rects1 = ax_actual.bar(x_pos - width/2, binned_df['p1'], width, label=policy1)
            rects2 = ax_actual.bar(x_pos + width/2, binned_df['p2'], width, label=policy2)
            ax_actual.set_ylabel('Eviction Counts')
            ax_actual.set_title(f'Eviction Counts (In-Request Index): {policy1} vs {policy2}\nTrace: {trace_name}, Cache Size: {cache_size}')
            ax_actual.set_xticks(x_pos)
            ax_actual.set_xticklabels(binned_df.index, rotation=45, ha='right')
            ax_actual.legend()
            ax_actual.grid(True, axis='y', linestyle='--')
            
            actual_path = os.path.join(plot_subdir, f'eviction_index_counts_{policy1}_vs_{policy2}.png')
            fig_actual.savefig(actual_path, bbox_inches='tight')
            plt.close(fig_actual)
            print(f"Saved Index Actual Counts plot to {actual_path}")
            
            # Weighted Cost Plot (Index)
            p1_idx_weights = defaultdict(float)
            for idx in results[policy1]['indices']:
                p1_idx_weights[idx] += (idx + 1)
            
            p2_idx_weights = defaultdict(float)
            for idx in results[policy2]['indices']:
                p2_idx_weights[idx] += (idx + 1)

            p1_series_w = pd.Series(p1_idx_weights)
            p2_series_w = pd.Series(p2_idx_weights)

            p1_binned_w = p1_series_w.groupby(pd.cut(p1_series_w.index, bins, right=False, labels=bin_labels), observed=False).sum()
            p2_binned_w = p2_series_w.groupby(pd.cut(p2_series_w.index, bins, right=False, labels=bin_labels), observed=False).sum()

            binned_df_w = pd.concat([p1_binned_w, p2_binned_w], axis=1).fillna(0)
            binned_df_w.columns = ['p1', 'p2']
            
            fig_w, ax_w = plt.subplots(figsize=(15, 8))
            ax_w.bar(x_pos - width/2, binned_df_w['p1'], width, label=policy1)
            ax_w.bar(x_pos + width/2, binned_df_w['p2'], width, label=policy2)
            ax_w.set_ylabel('Weighted Eviction Cost (Sum of Index+1)')
            ax_w.set_title(f'Weighted Eviction Cost by Index: {policy1} vs {policy2}\nTrace: {trace_name}, Cache Size: {cache_size}')
            ax_w.set_xticks(x_pos)
            ax_w.set_xticklabels(binned_df.index, rotation=45, ha='right')
            ax_w.legend()
            ax_w.grid(True, axis='y', linestyle='--')

            weighted_path = os.path.join(plot_subdir, f'eviction_weighted_cost_by_index_{policy1}_vs_{policy2}.png')
            fig_w.savefig(weighted_path, bbox_inches='tight')
            plt.close(fig_w)
            print(f"Saved Weighted Cost by Index plot to {weighted_path}")


        # --- Heatmap Plot ---
        p1_pop = results[policy1]['popularities']
        p1_idx = results[policy1]['indices']
        p2_pop = results[policy2]['popularities']
        p2_idx = results[policy2]['indices']

        all_pops = p1_pop + p2_pop
        all_idxs = p1_idx + p2_idx

        if not all_pops or not all_idxs:
            continue

        # Popularity bins
        max_pop = max(all_pops)
        num_pop_bins = 20
        pop_bins = np.geomspace(1, max_pop + 1, num=num_pop_bins)
        pop_bins = np.unique(np.round(pop_bins).astype(int))
        if pop_bins[-1] <= max_pop:
            pop_bins = np.append(pop_bins, max_pop + 1)
        pop_bin_labels = [f"{pop_bins[i]}-{pop_bins[i+1]-1}" if pop_bins[i] < pop_bins[i+1]-1 else f"{pop_bins[i]}" for i in range(len(pop_bins)-1)]

        # Index bins
        max_idx = max(all_idxs)
        num_idx_bins = 15
        if max_idx == 0:
            idx_bins = np.array([0, 1])
        else:
            log_bins = np.geomspace(1, max_idx + 1, num=num_idx_bins - 1)
            idx_bins = np.concatenate(([0], log_bins))
        idx_bins = np.unique(np.round(idx_bins).astype(int))
        if idx_bins[-1] <= max_idx:
            idx_bins = np.append(idx_bins, max_idx + 1)
        idx_bin_labels = [f"{idx_bins[i]}-{idx_bins[i+1]-1}" if idx_bins[i] < idx_bins[i+1]-1 else f"{idx_bins[i]}" for i in range(len(idx_bins)-1)]

        # Create 2D histograms
        counts1, _, _ = np.histogram2d(p1_pop, p1_idx, bins=[pop_bins, idx_bins])
        counts2, _, _ = np.histogram2d(p2_pop, p2_idx, bins=[pop_bins, idx_bins])

        counts1 = counts1.T
        counts2 = counts2.T

        # Calculate differences
        diff = counts1 - counts2
        with np.errstate(divide='ignore', invalid='ignore'):
            rel_diff = diff / counts2
            # Handle cases with zero in the denominator
            rel_diff[np.isinf(rel_diff)] = 1.0 # Significant increase
            rel_diff = np.nan_to_num(rel_diff) # Both zero -> zero difference

        # Plotting
        fig, ax = plt.subplots(figsize=(18, 14))
        im = ax.imshow(rel_diff, cmap='coolwarm', vmin=-1, vmax=1, aspect='auto', origin='lower')

        cbar = ax.figure.colorbar(im, ax=ax, format=mtick.PercentFormatter(1.0))
        cbar.ax.set_ylabel(f'Relative Difference in Eviction Counts vs {policy2}', rotation=-90, va="bottom")

        # Add text annotations for absolute difference
        # Iterate over the displayed image's rows (visual y-axis) and columns (visual x-axis)
        for i in range(len(idx_bin_labels)): # i is the visual row index, from bottom (0) to top (N-1)
            for j in range(len(pop_bin_labels)): # j is the visual column index
                # Map visual row index 'i' to data row index 'data_i'
                # Since origin='lower', the first row of data (index 0) corresponds to the bottom visual row (index 0).
                # So data_i is simply i.
                data_i = i
                if diff[data_i, j] != 0:
                    ax.text(j, i, f'{diff[data_i, j]:.0f}', ha="center", va="center", color="black", fontsize=8)


        ax.set_xticks(np.arange(len(pop_bin_labels)))
        ax.set_yticks(np.arange(len(idx_bin_labels)))
        ax.set_xticklabels(pop_bin_labels, rotation=45, ha="right")
        ax.set_yticklabels(idx_bin_labels)
        ax.set_xlabel("Block Popularity (Binned)")
        ax.set_ylabel("Block Index in Request (Binned)")
        ax.set_title(f'Eviction Count Difference Heatmap: {policy1} vs {policy2}\nTrace: {trace_name}, Cache Size: {cache_size}')

        heatmap_path = os.path.join(plot_subdir, f'eviction_heatmap_{policy1}_vs_{policy2}.png')
        fig.savefig(heatmap_path, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved Heatmap plot to {heatmap_path}")

        # --- Weighted Heatmap Plot ---
        # Weights are index + 1
        p1_weights = np.array(p1_idx) + 1
        p2_weights = np.array(p2_idx) + 1
        
        # Create 2D histograms with weights
        w_counts1, _, _ = np.histogram2d(p1_pop, p1_idx, bins=[pop_bins, idx_bins], weights=p1_weights)
        w_counts2, _, _ = np.histogram2d(p2_pop, p2_idx, bins=[pop_bins, idx_bins], weights=p2_weights)
        
        w_counts1 = w_counts1.T
        w_counts2 = w_counts2.T
        
        # Calculate differences
        w_diff = w_counts1 - w_counts2
        with np.errstate(divide='ignore', invalid='ignore'):
            w_rel_diff = w_diff / w_counts2
            # Handle cases with zero in the denominator
            w_rel_diff[np.isinf(w_rel_diff)] = 1.0 # Significant increase
            w_rel_diff = np.nan_to_num(w_rel_diff) # Both zero -> zero difference
            
        # Plotting Weighted Heatmap
        fig, ax = plt.subplots(figsize=(18, 14))
        im = ax.imshow(w_rel_diff, cmap='coolwarm', vmin=-1, vmax=1, aspect='auto', origin='lower')
        
        cbar = ax.figure.colorbar(im, ax=ax, format=mtick.PercentFormatter(1.0))
        cbar.ax.set_ylabel(f'Relative Difference in Weighted Eviction Cost vs {policy2}', rotation=-90, va="bottom")
        
        # Add text annotations for absolute weighted difference
        for i in range(len(idx_bin_labels)):
            for j in range(len(pop_bin_labels)):
                data_i = i
                if w_diff[data_i, j] != 0:
                    # Format as integer since weights are integers
                    ax.text(j, i, f'{w_diff[data_i, j]:.0f}', ha="center", va="center", color="black", fontsize=8)
                    
        ax.set_xticks(np.arange(len(pop_bin_labels)))
        ax.set_yticks(np.arange(len(idx_bin_labels)))
        ax.set_xticklabels(pop_bin_labels, rotation=45, ha="right")
        ax.set_yticklabels(idx_bin_labels)
        ax.set_xlabel("Block Popularity (Binned)")
        ax.set_ylabel("Block Index in Request (Binned)")
        ax.set_title(f'Weighted Eviction Cost Difference Heatmap: {policy1} vs {policy2}\nTrace: {trace_name}, Cache Size: {cache_size}')
        
        weighted_heatmap_path = os.path.join(plot_subdir, f'eviction_weighted_cost_heatmap_{policy1}_vs_{policy2}.png')
        fig.savefig(weighted_heatmap_path, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved Weighted Cost Heatmap plot to {weighted_heatmap_path}")

    # --- Plot for reuse distance difference ---
    gdsf_policy_name = 'GDSF_compute'
    belady_policy_name = 'BeladyCompute'
    lhd_policy_name = 'LHD_compute'
    lru_policy_name = 'LRU'

    if gdsf_policy_name in policies and belady_policy_name in policies:
        print(f"--- Generating Reuse Distance Difference plot for {gdsf_policy_name} vs {belady_policy_name} ---")
        gdsf_log_path = os.path.join(args.log_dir, f"{gdsf_policy_name}.txt")
        belady_log_path = os.path.join(args.log_dir, f"{belady_policy_name}.txt")

        plot_reuse_distance_diff(
            gdsf_log_path,
            belady_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )

        print(f"--- Generating Reuse Distance/Index Heatmap for {gdsf_policy_name} vs {belady_policy_name} ---")
        plot_miss_heatmap_by_reuse_dist_index(
            gdsf_log_path,
            belady_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )
    else:
        print(f"Skipping reuse distance difference plot: one or both policies ('{gdsf_policy_name}', '{belady_policy_name}') not found.")

    if lhd_policy_name in policies and belady_policy_name in policies:
        print(f"--- Generating Reuse Distance Difference plot for {lhd_policy_name} vs {belady_policy_name} ---")
        lhd_log_path = os.path.join(args.log_dir, f"{lhd_policy_name}.txt")
        belady_log_path = os.path.join(args.log_dir, f"{belady_policy_name}.txt")

        plot_reuse_distance_diff(
            lhd_log_path,
            belady_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )

        print(f"--- Generating Reuse Distance/Index Heatmap for {lhd_policy_name} vs {belady_policy_name} ---")
        plot_miss_heatmap_by_reuse_dist_index(
            lhd_log_path,
            belady_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )
        plot_miss_heatmap_by_reuse_dist_index(
            belady_log_path,
            lhd_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )
    else:
        print(f"Skipping reuse distance difference plot: one or both policies ('{lhd_policy_name}', '{belady_policy_name}') not found.")
    
    if gdsf_policy_name in policies and lhd_policy_name in policies:
        print(f"--- Generating Reuse Distance Difference plot for {gdsf_policy_name} vs {lhd_policy_name} ---")
        gdsf_log_path = os.path.join(args.log_dir, f"{gdsf_policy_name}.txt")
        lhd_log_path = os.path.join(args.log_dir, f"{lhd_policy_name}.txt")

        plot_reuse_distance_diff(
            gdsf_log_path,
            lhd_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )

        print(f"--- Generating Reuse Distance/Index Heatmap for {gdsf_policy_name} vs {lhd_policy_name} ---")
        plot_miss_heatmap_by_reuse_dist_index(
            gdsf_log_path,
            lhd_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )
    else:
        print(f"Skipping reuse distance difference plot: one or both policies ('{gdsf_policy_name}', '{lhd_policy_name}') not found.")
    
    if lru_policy_name in policies and belady_policy_name in policies:
        print(f"--- Generating Reuse Distance Difference plot for {lru_policy_name} vs {belady_policy_name} ---")
        lru_log_path = os.path.join(args.log_dir, f"{lru_policy_name}.txt")
        belady_log_path = os.path.join(args.log_dir, f"{belady_policy_name}.txt")

        plot_reuse_distance_diff(
            lru_log_path,
            belady_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )

        print(f"--- Generating Reuse Distance/Index Heatmap for {lru_policy_name} vs {belady_policy_name} ---")
        plot_miss_heatmap_by_reuse_dist_index(
            lru_log_path,
            belady_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )
        plot_miss_heatmap_by_reuse_dist_index(
            belady_log_path,
            lru_log_path,
            hash_indices_map,
            plot_subdir,
            trace_name,
            cache_size
        )
    else:
        print(f"Skipping reuse distance difference plot: one or both policies ('{lru_policy_name}', '{belady_policy_name}') not found.")


if __name__ == '__main__':
    main()