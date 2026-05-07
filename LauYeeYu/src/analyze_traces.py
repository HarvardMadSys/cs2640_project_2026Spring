#!/usr/bin/env python3
"""
Script to analyze QWen traces and generate CDF (Cumulative Distribution Function) plots.

This script analyzes various aspects of the QWen traces including:
- Hash ID sequence length distribution
- Input/Output length distributions
- Request type distributions
- Temporal patterns
- Inter-arrival times
"""

import json
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict, Counter
import argparse
import os
from pathlib import Path
from datetime import datetime

# Set style for better-looking plots
plt.style.use('default')
plt.rcParams['figure.dpi'] = 100
plt.rcParams['axes.grid'] = True
plt.rcParams['grid.alpha'] = 0.3

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
                continue
    
    print(f"Loaded {len(data)} requests")
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
            cumulative_hash = hash((cumulative_hash, hash_id))
            new_hash_ids.append(cumulative_hash)
        new_req['hash_ids'] = new_hash_ids
        translated_data.append(new_req)
    print("Translation complete.")
    return translated_data

def compute_cdf(data):
    """Compute CDF from data."""
    sorted_data = np.sort(data)
    n = len(sorted_data)
    cdf = np.arange(1, n + 1) / n
    return sorted_data, cdf

def plot_cdf(data, title, xlabel, output_path, log_scale=False):
    """Plot CDF for given data."""
    x_sorted, cdf = compute_cdf(data)
    
    plt.figure(figsize=(10, 6))
    plt.plot(x_sorted, cdf, linewidth=2, alpha=0.8)
    plt.xlabel(xlabel, fontsize=12)
    plt.ylabel('Cumulative Probability', fontsize=12)
    plt.title(title, fontsize=14, fontweight='bold')
    plt.grid(True, alpha=0.3)
    
    if log_scale:
        plt.xscale('log')
    
    # Add statistics text
    stats_text = f'Mean: {np.mean(data):.2f}\n'
    stats_text += f'Median: {np.median(data):.2f}\n'
    stats_text += f'95th %ile: {np.percentile(data, 95):.2f}\n'
    stats_text += f'99th %ile: {np.percentile(data, 99):.2f}'
    
    plt.text(0.02, 0.98, stats_text, transform=plt.gca().transAxes, 
             verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8),
             fontsize=10)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved CDF plot: {output_path}")

def plot_cdf_with_markers(data, title, xlabel, output_path, log_scale=False):
    """Plot CDF for given data with mean, median, p10, p25, p75, p90 markers."""
    x_sorted, cdf = compute_cdf(data)

    plt.figure(figsize=(10, 6))
    plt.plot(x_sorted, cdf, linewidth=2, alpha=0.8)
    plt.xlabel(xlabel, fontsize=12)
    plt.ylabel('Cumulative Probability', fontsize=12)
    plt.title(title, fontsize=14, fontweight='bold')
    plt.grid(True, alpha=0.3)

    if log_scale:
        plt.xscale('log')

    mean_val = float(np.mean(data))
    markers = [
        ('P10', float(np.percentile(data, 10)), 0.10, 'tab:purple'),
        ('P25', float(np.percentile(data, 25)), 0.25, 'tab:blue'),
        ('Median', float(np.median(data)), 0.50, 'tab:orange'),
        ('P75', float(np.percentile(data, 75)), 0.75, 'tab:green'),
        ('P90', float(np.percentile(data, 90)), 0.90, 'tab:red'),
    ]

    for label, val, y, color in markers:
        plt.axvline(val, color=color, linestyle='--', linewidth=1.2, alpha=0.8)
        plt.scatter([val], [y], color=color, s=40, zorder=5, label=f'{label}: {val:.2f}')

    # Mean marker (position on CDF curve)
    mean_cdf_y = float(np.searchsorted(x_sorted, mean_val, side='right')) / len(x_sorted)
    plt.axvline(mean_val, color='black', linestyle=':', linewidth=1.2, alpha=0.8)
    plt.scatter([mean_val], [mean_cdf_y], color='black', s=40, zorder=5,
                label=f'Mean: {mean_val:.2f}')

    plt.legend(loc='lower right', fontsize=10)
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved CDF plot: {output_path}")

def plot_block_frequency_boxplot(hash_id_counts, hash_id_positions, output_dir, mode='linear', param=None):
    print(f"Generating block frequency boxplot ({mode} bins)...")
    if not hash_id_positions:
        return
        
    max_pos = max(hash_id_positions.values())
    
    binned_frequencies = defaultdict(list)
    labels = []
    
    if mode == 'linear':
        bin_size = param if param is not None else 100
        
        # Determine appropriate bin size based on max_pos to avoid too many bins
        estimated_bins = (max_pos // bin_size) + 1
        if estimated_bins > 50:
            bin_size = max(10, int(round((max_pos / 50) / 10.0)) * 10)
            if bin_size == 0:
                bin_size = 100
                
        for hash_id, count in hash_id_counts.items():
            pos_idx = hash_id_positions[hash_id]
            bin_idx = (pos_idx // bin_size) * bin_size
            binned_frequencies[bin_idx].extend([count] * count)
            
        if not binned_frequencies:
            return
            
        sorted_bins_idx = sorted(binned_frequencies.keys())
        data_to_plot = [binned_frequencies[b] for b in sorted_bins_idx]
        labels = [f"{b}-{b+bin_size-1}" for b in sorted_bins_idx]
        
    elif mode == 'log':
        n_bins = param if param is not None else 50
        
        if max_pos == 0:
            bins = np.array([0, 1])
        else:
            bins = np.logspace(0, np.log10(max_pos + 1), num=n_bins, dtype=int)
            bins = np.unique(bins)
            if bins[0] != 0:
                bins = np.insert(bins, 0, 0)
                
        for hash_id, count in hash_id_counts.items():
            pos_idx = hash_id_positions[hash_id]
            bin_idx = np.digitize([pos_idx], bins)[0] - 1
            binned_frequencies[bin_idx].extend([count] * count)
            
        if not binned_frequencies:
            return
            
        sorted_bins_idx = sorted(binned_frequencies.keys())
        data_to_plot = [binned_frequencies[b] for b in sorted_bins_idx]
        
        for b in sorted_bins_idx:
            start_val = bins[b]
            end_val = bins[b+1] - 1 if (b+1) < len(bins) else max_pos
            if start_val == end_val:
                labels.append(f"{start_val}")
            else:
                labels.append(f"{start_val}-{end_val}")

    fig_width = min(24, max(12, len(labels) * 0.4))
    plt.figure(figsize=(fig_width, 6))
    
    # Enable means and format lines for legend
    bp = plt.boxplot(data_to_plot, tick_labels=labels, showmeans=True, 
                     meanline=True,
                     meanprops={'color': 'green', 'linewidth': 1.5, 'linestyle': '--'},
                     medianprops={'color': 'orange', 'linewidth': 1.5})
                     
    # Add P10 and P90 markers
    x_positions = np.arange(1, len(data_to_plot) + 1)
    p10_values = [np.percentile(d, 10) if len(d) > 0 else np.nan for d in data_to_plot]
    p90_values = [np.percentile(d, 90) if len(d) > 0 else np.nan for d in data_to_plot]
    
    plt.scatter(x_positions, p10_values, marker='_', color='purple', s=100, linewidth=2, zorder=3)
    plt.scatter(x_positions, p90_values, marker='_', color='purple', s=100, linewidth=2, zorder=3)
    
    plt.yscale('log')
    plt.xlabel(f'Block Index ({mode.capitalize()}-binned)', fontsize=12)
    plt.ylabel('Block Frequency', fontsize=12)
    plt.title(f'Distribution of Block Frequencies by Block Index ({mode.capitalize()} Bins, Frequency-Weighted)', fontsize=14, fontweight='bold')
    plt.xticks(rotation=45, ha='right', fontsize=10)
    plt.grid(True, alpha=0.3, axis='y')
    
    # Custom legend for mean, median, P10/P90
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color='orange', lw=1.5, label='Median'),
        Line2D([0], [0], color='green', lw=1.5, linestyle='--', label='Mean'),
        Line2D([0], [0], color='purple', marker='_', linestyle='None', markersize=10, markeredgewidth=2, label='P10 / P90')
    ]
    plt.legend(handles=legend_elements, loc='upper right')
    
    plt.tight_layout()
    output_path = f"{output_dir}/block_frequency_boxplot_{mode}.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved block frequency boxplot: {output_path}")

def plot_block_frequency_cdf_at_indices(hash_id_counts, hash_id_positions, output_dir, target_indices=[0, 4, 50, 100]):
    print(f"Generating frequency-weighted CDFs for block indices {target_indices}...")
    
    # Collect frequencies for the exact indices
    frequencies_by_index = {idx: [] for idx in target_indices}
    for hash_id, count in hash_id_counts.items():
        pos_idx = hash_id_positions[hash_id]
        if pos_idx in frequencies_by_index:
            # Frequency-weighted: Add 'count' to the list 'count' times
            frequencies_by_index[pos_idx].extend([count] * count)
            
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('CDF of Frequency-Weighted Block Frequencies by Block Index', fontsize=16, fontweight='bold')
    
    axes = axes.flatten()
    
    for i, target_idx in enumerate(target_indices):
        ax = axes[i]
        data = frequencies_by_index[target_idx]
        
        if not data:
            ax.text(0.5, 0.5, 'No Data', ha='center', va='center', transform=ax.transAxes)
            ax.set_title(f'Index: {target_idx}')
            ax.grid(True, alpha=0.3)
            continue
            
        x_sorted, cdf = compute_cdf(data)
        ax.plot(x_sorted, cdf, linewidth=2, color='C' + str(i))
        ax.set_title(f'Index: {target_idx} (N={len(data):,})')
        ax.set_xlabel('Block Frequency (Frequency-Weighted)')
        ax.set_ylabel('CDF')
        ax.set_xscale('log')
        ax.grid(True, alpha=0.3)
        
        # Add basic stats directly to the subplot
        stats_text = (f"Mean: {np.mean(data):.1f}\n"
                      f"Median: {np.median(data):.1f}\n"
                      f"P90: {np.percentile(data, 90):.1f}")
        ax.text(0.05, 0.95, stats_text, transform=ax.transAxes,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='white', alpha=0.8),
                fontsize=10)
                
    plt.tight_layout()
    output_path = f"{output_dir}/block_frequency_cdfs_at_indices.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved block frequency CDFs: {output_path}")

def analyze_hash_id_patterns(data, output_dir):
    """
    Analyze hash ID patterns and sequences.
    
    This function generates two key CDFs for understanding cache behavior:
    1. Sequence Length: How many hash IDs per request (context size)
    2. Reuse Distance: Requests between repeated hash IDs (cache lifetime)
    """
    print("Analyzing hash ID patterns...")
    
    # 1. Hash ID sequence lengths - measures context complexity
    # Higher values = more complex requests needing larger cache capacity
    seq_lengths = [len(req['hash_ids']) for req in data]
    # print maximum sequence length for quick diagnostics
    if seq_lengths:
        max_seq_length = max(seq_lengths)
        print(f"Max hash id sequence length: {max_seq_length}")
    else:
        max_seq_length = 0
    plot_cdf(seq_lengths, 'CDF of Hash ID Sequence Lengths', 'Sequence Length', 
             f'{output_dir}/hash_id_sequence_length_cdf.png', log_scale=True)
    
    # 2. Hash ID reuse distances - MOST CRITICAL for cache design
    # Short distances = good cache hits, long distances = likely cache misses
    # This directly predicts cache performance at different sizes
    hash_distances = []
    hash_block_distances = []
    hash_last_seen = {}
    hash_last_seen_block = {}

    # Check if the same hash_id always appears at the same position index
    hash_id_positions = {}
    positional_violations = 0
    total_hash_id_occurrences = 0

    # additionally collect reuse distances by hash-id position index (0 = first id in request)
    reuse_by_index = defaultdict(list)
    hash_id_counts = Counter()

    requests_at_index = Counter()
    block_idx = 0
    for req_idx, req in enumerate(data):
        for pos_idx, hash_id in enumerate(req['hash_ids']):
            total_hash_id_occurrences += 1
            hash_id_counts[hash_id] += 1
            if hash_id in hash_id_positions:
                if hash_id_positions[hash_id] != pos_idx:
                    positional_violations += 1
            else:
                hash_id_positions[hash_id] = pos_idx

            if hash_id in hash_last_seen:
                distance = req_idx - hash_last_seen[hash_id]
                hash_distances.append(distance)
            if hash_id in hash_last_seen_block:
                block_distance = block_idx - hash_last_seen_block[hash_id]
                hash_block_distances.append(block_distance)
                # record by the position index where the hash_id appears in the current request
                reuse_by_index[pos_idx].append(block_distance)
            hash_last_seen[hash_id] = req_idx
            hash_last_seen_block[hash_id] = block_idx
            requests_at_index[pos_idx] += 1
            block_idx += 1

    if total_hash_id_occurrences > 0:
        violation_rate = (positional_violations / total_hash_id_occurrences) * 100
        print(f"Positional consistency check: {positional_violations:,} violations found ({violation_rate:.4f}% of occurrences).")
        if violation_rate > 0.0001:
            print("WARNING: The same hash_id appears at different position indices. This may affect position-based caching logic.")


    if hash_distances:
        plot_cdf(hash_distances, 'CDF of Hash ID Reuse Distance', 'Requests Since Last Use',
                 f'{output_dir}/hash_id_reuse_distance_cdf.png', log_scale=True)

    if hash_block_distances:
        plot_cdf_with_markers(hash_block_distances,
                              'CDF of Hash ID Reuse Interval (blocks)',
                              'Blocks Since Last Use',
                              f'{output_dir}/hash_id_reuse_interval_blocks_cdf.png',
                              log_scale=True)

        # Create heatmap: rows = hash-id position index, cols = reuse-distance bins (log-spaced)
        max_pos = max(reuse_by_index.keys()) + 1 if reuse_by_index else 0
        # group hash-id positions into blocks of 10 positions per row
        pos_block_size = 10  # each heatmap row aggregates this many hash-id positions
        pos_bins = (max_pos + pos_block_size - 1) // pos_block_size
        # cap number of rows to 500 (so max covered positions = 500 * 10 = 5000)
        max_rows_allowed = 500
        if pos_bins > max_rows_allowed:
            print(f"Info: capping heatmap rows to {max_rows_allowed} (covering up to {max_rows_allowed * pos_block_size} hash-id positions)")
        display_rows = min(pos_bins, max_rows_allowed)

        # Use logarithmic bins on reuse-distance (log-binned X axis)
        all_distances = np.array(hash_block_distances)
        max_dist = int(np.max(all_distances)) if all_distances.size > 0 else 1
        # create logarithmic bins (n_bins bins across [1, max_dist])
        n_bins = 50
        # start at 1 (10^0) to avoid zero
        try:
            bins = np.unique(np.logspace(0, np.log10(max(max_dist, 1)), num=n_bins).astype(int))
        except Exception:
            bins = np.array([1, 2, 5, 10, 50, 100])
        if bins.size < 2:
            bins = np.array([1, 2, 5, 10, 50, 100])

        matrix = np.zeros((display_rows, bins.size - 1), dtype=float)
        matrix_raw_reuse = np.zeros((display_rows, bins.size - 1), dtype=float)

        # aggregate distances for position blocks using the log bins
        for row in range(display_rows):
            start_pos = row * pos_block_size
            end_pos = start_pos + pos_block_size  # exclusive
            distances = []
            total_requests_in_block = 0
            for pos in range(start_pos, min(end_pos, max_pos)):
                distances.extend(reuse_by_index.get(pos, []))
                total_requests_in_block += requests_at_index.get(pos, 0)

            if len(distances) == 0:
                continue
            hist, _ = np.histogram(distances, bins=bins)
            matrix_raw_reuse[row, :] = hist
            if total_requests_in_block > 0:
                matrix[row, :] = hist / total_requests_in_block
            else:
                matrix[row, :] = 0.0

        # plot heatmap (log color scale for visibility)
        # shorten figure height: use a smaller per-row height and cap overall height
        per_row_height = 0.12  # inches per aggregated position row
        max_height = 12  # maximum figure height in inches
        fig_height = min(max_height, max(4, display_rows * per_row_height))
        plt.figure(figsize=(12, fig_height))
        im = plt.imshow(matrix, aspect='auto', cmap='viridis', origin='lower', vmin=0, vmax=np.percentile(matrix[matrix > 0], 99) if np.any(matrix > 0) else 1)
        cbar = plt.colorbar(im)
        cbar.set_label('Reuse Ratio (Reuses / Requests at Index)', fontsize=16)
        cbar.ax.tick_params(labelsize=14)

        # x axis: use log-binned labels (show upper edge of selected bins)
        bin_tick_locs = np.linspace(0, matrix.shape[1]-1, num=8, dtype=int)
        bin_tick_labels = [f"{bins[i+1]:,}" for i in bin_tick_locs]
        plt.xticks(bin_tick_locs, bin_tick_labels, rotation=45, fontsize=14)
        # y-axis: label each aggregated block as a position range (e.g., "0-9", "10-19")
        # show at most ~20 tick labels to avoid overcrowding
        if display_rows > 20:
            ytick_step = max(1, display_rows // 20)
        else:
            ytick_step = 1
        ytick_locs = list(range(0, display_rows, ytick_step))
        ytick_labels = []
        for loc in ytick_locs:
            start = loc * pos_block_size
            end = min(start + pos_block_size - 1, max_pos - 1)
            ytick_labels.append(f"{start}-{end}")
        plt.yticks(ytick_locs, ytick_labels, fontsize=14)
        plt.xlabel('Reuse Distance (hash blocks, log-binned)', fontsize=18)
        plt.ylabel('Hash ID position index in request (0 = first)', fontsize=18)
        plt.title('Reuse Distance Heatmap by Hash ID Position', fontsize=18, fontweight='bold')
        plt.tight_layout()
        plt.savefig(f'{output_dir}/reuse_distance_by_hash_index_heatmap.png', dpi=300, bbox_inches='tight')
        plt.close()
        print(f"Saved heatmap: {output_dir}/reuse_distance_by_hash_index_heatmap.png")

        # plot raw reuse count heatmap
        plt.figure(figsize=(12, fig_height))
        # Use a logarithmic color scale for better visibility of a wide range of counts
        im_raw = plt.imshow(matrix_raw_reuse, aspect='auto', cmap='inferno', origin='lower',
                            norm=plt.cm.colors.LogNorm(vmin=1, vmax=np.percentile(matrix_raw_reuse[matrix_raw_reuse > 0], 99.5) if np.any(matrix_raw_reuse > 0) else 1),
                            interpolation='nearest')
        cbar_raw = plt.colorbar(im_raw)
        cbar_raw.set_label('Raw Reuse Count (log scale)', fontsize=16)
        cbar_raw.ax.tick_params(labelsize=14)

        # x axis: use log-binned labels (show upper edge of selected bins)
        bin_tick_locs_raw = np.linspace(0, matrix_raw_reuse.shape[1]-1, num=8, dtype=int)
        bin_tick_labels_raw = [f"{bins[i+1]:,}" for i in bin_tick_locs_raw]
        plt.xticks(bin_tick_locs_raw, bin_tick_labels_raw, rotation=45, fontsize=14)

        # y-axis: label each aggregated block as a position range (e.g., "0-9", "10-19")
        if display_rows > 20:
            ytick_step_raw = max(1, display_rows // 20)
        else:
            ytick_step_raw = 1
        ytick_locs_raw = list(range(0, display_rows, ytick_step_raw))
        ytick_labels_raw = []
        for loc in ytick_locs_raw:
            start = loc * pos_block_size
            end = min(start + pos_block_size - 1, max_pos - 1)
            ytick_labels_raw.append(f"{start}-{end}")
        plt.yticks(ytick_locs_raw, ytick_labels_raw, fontsize=14)
        plt.xlabel('Reuse Distance (hash blocks, log-binned)', fontsize=18)
        plt.ylabel('Hash ID position index in request (0 = first)', fontsize=18)
        plt.title('Raw Reuse Count Heatmap by Hash ID Position', fontsize=18, fontweight='bold')
        plt.tight_layout()
        plt.savefig(f'{output_dir}/reuse_count_by_hash_index_heatmap.png', dpi=300, bbox_inches='tight')
        plt.close()
        print(f"Saved raw count heatmap: {output_dir}/reuse_count_by_hash_index_heatmap.png")

    # Generate block frequency boxplots
    plot_block_frequency_boxplot(hash_id_counts, hash_id_positions, output_dir, mode='linear')
    plot_block_frequency_boxplot(hash_id_counts, hash_id_positions, output_dir, mode='log')

    # Generate CDF subplots for specific indices
    plot_block_frequency_cdf_at_indices(hash_id_counts, hash_id_positions, output_dir, target_indices=[0, 4, 50, 100])

    return {
        'seq_lengths': seq_lengths,
        'max_seq_length': max_seq_length,
        'reuse_distances': hash_distances,
        'reuse_by_index': reuse_by_index
    }

def analyze_request_characteristics(data, output_dir):
    """
    Analyze request input/output characteristics.
    
    These CDFs help understand processing costs and cache value:
    1. Input Length: Complexity of requests (affects processing time)
    2. Output Length: Generation cost (higher value items to cache)
    3. Total Length: Complete resource usage per request
    4. I/O Ratio: Processing pattern (expansion vs compression)
    """
    print("Analyzing request characteristics...")
    
    # 1. Input lengths - measures request complexity and processing cost
    # Larger inputs = more expensive to process = higher cache value
    input_lengths = [req['input_length'] for req in data]
    plot_cdf(input_lengths, 'CDF of Input Lengths', 'Input Length (tokens)', 
             f'{output_dir}/input_length_cdf.png', log_scale=True)
    
    # # 2. Output lengths - measures generation cost and cache benefit  
    # # Longer outputs = more expensive to regenerate = prioritize in cache
    # output_lengths = [req['output_length'] for req in data]
    # plot_cdf(output_lengths, 'CDF of Output Lengths', 'Output Length (tokens)', 
    #          f'{output_dir}/output_length_cdf.png', log_scale=True)
    
    # # 3. Total lengths - measures complete resource usage per cache entry
    # # Guides cache capacity planning and memory allocation
    # total_lengths = [req['input_length'] + req['output_length'] for req in data]
    # plot_cdf(total_lengths, 'CDF of Total Request Lengths', 'Total Length (tokens)', 
    #          f'{output_dir}/total_length_cdf.png', log_scale=True)
    
    # # 4. Input/Output ratio - reveals processing patterns
    # # Low ratios = compression/summarization, High ratios = generation/expansion
    # io_ratios = [req['output_length'] / max(req['input_length'], 1) for req in data]
    # plot_cdf(io_ratios, 'CDF of Output/Input Length Ratios', 'Output/Input Ratio', 
    #          f'{output_dir}/io_ratio_cdf.png', log_scale=True)
    
    return {
        'input_lengths': input_lengths,
        # 'output_lengths': output_lengths,
        # 'total_lengths': total_lengths,
        # 'io_ratios': io_ratios
    }

def analyze_temporal_patterns(data, output_dir):
    """
    Analyze temporal patterns in the trace.
    
    These CDFs reveal timing characteristics affecting cache behavior:
    1. Inter-Arrival Times: Request frequency and burstiness patterns
    """
    print("Analyzing temporal patterns...")
    
    # 1. Inter-arrival times - measures request frequency and burst patterns
    # Short intervals = high load, potential cache thrashing
    # Long intervals = cache has time to stabilize, better hit rates
    timestamps = []
    if 'timestamp' in data[0]:
        timestamps = [req['timestamp'] for req in data]
    elif 'start_time' in data[0]:
        timestamps = [req['start_time'] for req in data]
    else:
        print("No timestamp information found in data.")
        return {'inter_arrival_times': []}
    if type(timestamps[0]) is str:
        timestamps = [datetime.fromisoformat(ts).timestamp() for ts in timestamps]
    inter_arrival_times = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    inter_arrival_times = [t for t in inter_arrival_times if t > 0]  # Remove negative/zero intervals
    
    if inter_arrival_times:
        plot_cdf(inter_arrival_times, 'CDF of Inter-Arrival Times', 'Inter-Arrival Time (seconds)', 
                 f'{output_dir}/inter_arrival_time_cdf.png', log_scale=True)
    
    return {
        'inter_arrival_times': inter_arrival_times
    }

def analyze_request_types(data, output_dir):
    """Analyze request type distributions."""
    print("Analyzing request types...")

    if not data or 'type' not in data[0]:
        print("No request type information found in data.")
        return {}
    
    # Request type distribution
    type_counts = Counter(req['type'] for req in data)
    
    plt.figure(figsize=(10, 6))
    types = list(type_counts.keys())
    counts = list(type_counts.values())
    percentages = [c/sum(counts)*100 for c in counts]
    
    bars = plt.bar(types, percentages, alpha=0.8)
    plt.xlabel('Request Type', fontsize=12)
    plt.ylabel('Percentage (%)', fontsize=12)
    plt.title('Distribution of Request Types', fontsize=14, fontweight='bold')
    plt.grid(True, alpha=0.3, axis='y')
    
    # Add percentage labels on bars
    for bar, pct in zip(bars, percentages):
        plt.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                f'{pct:.1f}%', ha='center', va='bottom', fontsize=11, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(f'{output_dir}/request_type_distribution.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved request type distribution: {output_dir}/request_type_distribution.png")
    
    return type_counts

def create_summary_plots(hash_analysis, req_analysis, temporal_analysis, output_dir):
    """Create summary comparison plots."""
    print("Creating summary plots...")
    
    # Multi-plot figure comparing key metrics
    fig, axes = plt.subplots(2, 3, figsize=(18, 12))
    fig.suptitle('QWen Trace Analysis Summary', fontsize=16, fontweight='bold')
    
    # Plot 1: Hash ID sequence lengths
    x, cdf = compute_cdf(hash_analysis['seq_lengths'])
    axes[0,0].plot(x, cdf, linewidth=2)
    axes[0,0].set_xlabel('Hash ID Sequence Length')
    axes[0,0].set_ylabel('CDF')
    axes[0,0].set_title('Hash ID Sequence Lengths')
    axes[0,0].set_xscale('log')
    axes[0,0].grid(True, alpha=0.3)
    
    # Plot 2: Input lengths
    x, cdf = compute_cdf(req_analysis['input_lengths'])
    axes[0,1].plot(x, cdf, linewidth=2, color='orange')
    axes[0,1].set_xlabel('Input Length (tokens)')
    axes[0,1].set_ylabel('CDF')
    axes[0,1].set_title('Input Lengths')
    axes[0,1].set_xscale('log')
    axes[0,1].grid(True, alpha=0.3)
    
    # # Plot 3: Output lengths
    # x, cdf = compute_cdf(req_analysis['output_lengths'])
    # axes[0,2].plot(x, cdf, linewidth=2, color='green')
    # axes[0,2].set_xlabel('Output Length (tokens)')
    # axes[0,2].set_ylabel('CDF')
    # axes[0,2].set_title('Output Lengths')
    # axes[0,2].set_xscale('log')
    # axes[0,2].grid(True, alpha=0.3)
    
    # Plot 4: Hash ID reuse distances
    if hash_analysis['reuse_distances']:
        x, cdf = compute_cdf(hash_analysis['reuse_distances'])
        axes[1,0].plot(x, cdf, linewidth=2, color='red')
        axes[1,0].set_xlabel('Reuse Distance (requests)')
        axes[1,0].set_ylabel('CDF')
        axes[1,0].set_title('Hash ID Reuse Distances')
        axes[1,0].set_xscale('log')
        axes[1,0].grid(True, alpha=0.3)
    
    # Plot 5: Inter-arrival times
    if temporal_analysis['inter_arrival_times']:
        x, cdf = compute_cdf(temporal_analysis['inter_arrival_times'])
        axes[1,1].plot(x, cdf, linewidth=2, color='purple')
        axes[1,1].set_xlabel('Inter-Arrival Time (seconds)')
        axes[1,1].set_ylabel('CDF')
        axes[1,1].set_title('Inter-Arrival Times')
        axes[1,1].set_xscale('log')
        axes[1,1].grid(True, alpha=0.3)
    
    # Plot 6: I/O Ratios (replacing session lengths)
    # x, cdf = compute_cdf(req_analysis['io_ratios'])
    # axes[1,2].plot(x, cdf, linewidth=2, color='brown')
    # axes[1,2].set_xlabel('Output/Input Ratio')
    # axes[1,2].set_ylabel('CDF')
    # axes[1,2].set_title('I/O Ratios')
    axes[1,2].set_xscale('log')
    axes[1,2].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(f'{output_dir}/summary_analysis.png', dpi=300, bbox_inches='tight')
    plt.close()
    print(f"Saved summary analysis: {output_dir}/summary_analysis.png")

def interpret_cdf_results(hash_analysis, req_analysis, temporal_analysis, trace_name):
    """
    Interpret CDF results and provide cache design recommendations.
    """
    print(f"\n" + "="*80)
    print(f"🔍 CDF INTERPRETATION FOR {trace_name.upper()}")
    print("="*80)
    
    # Hash ID reuse distance analysis
    if hash_analysis['reuse_distances']:
        reuse_dist = hash_analysis['reuse_distances']
        median_reuse = np.median(reuse_dist)
        p95_reuse = np.percentile(reuse_dist, 95)
        
        print(f"\n🎯 CACHE SIZE RECOMMENDATIONS:")
        print(f"├─ For 50% hit rate: Cache ~{int(median_reuse)} items")
        print(f"├─ For 95% hit rate: Cache ~{int(p95_reuse)} items") 
        print(f"└─ Optimal algorithm: {'LRU/ARC' if median_reuse < 1000 else 'LFU/S3FIFO'}")
    
    # Sequence length analysis
    seq_lengths = hash_analysis['seq_lengths']
    median_seq = np.median(seq_lengths)
    p95_seq = np.percentile(seq_lengths, 95)
    
    print(f"\n📏 CONTEXT COMPLEXITY:")
    print(f"├─ Typical request: {int(median_seq)} hash IDs")
    print(f"├─ Complex requests: {int(p95_seq)} hash IDs (95th percentile)")
    print(f"└─ Cache entry size: {'Small-Medium' if median_seq < 100 else 'Medium-Large'}")
    
    # Request characteristics analysis
    input_lens = req_analysis['input_lengths']
    # output_lens = req_analysis['output_lengths']
    # io_ratios = req_analysis['io_ratios']
    
    median_input = np.median(input_lens)
    # median_output = np.median(output_lens)
    # median_ratio = np.median(io_ratios)
    
    print(f"\n💰 CACHE VALUE ANALYSIS:")
    print(f"├─ Input processing cost: {'High' if median_input > 1000 else 'Low-Medium'}")
    # print(f"├─ Output generation cost: {'High' if median_output > 200 else 'Low-Medium'}")
    # print(f"├─ Cache ROI: {'Output-focused' if median_ratio > 0.5 else 'Input-focused'}")
    # print(f"└─ Processing pattern: {'Generative' if median_ratio > 0.3 else 'Analytical'}")
    
    # Temporal pattern analysis
    if temporal_analysis['inter_arrival_times']:
        inter_times = temporal_analysis['inter_arrival_times']
        median_inter = np.median(inter_times)
        
        print(f"\n⏱️  TEMPORAL BEHAVIOR:")
        print(f"├─ Request frequency: {'High' if median_inter < 0.1 else 'Medium' if median_inter < 1.0 else 'Low'}")
        print(f"├─ Cache pressure: {'High (burst risk)' if median_inter < 0.1 else 'Moderate'}")
        print(f"└─ Replacement policy: {'Aggressive' if median_inter < 0.1 else 'Conservative'}")
    
    print(f"\n🚀 RECOMMENDED CACHE STRATEGY:")
    if median_reuse < 100:
        print("├─ Small, fast cache with LRU policy")
        print("└─ Focus on recent items, quick replacement")
    elif median_reuse < 1000:
        print("├─ Medium cache with ARC or S3FIFO policy") 
        print("└─ Balance recency and frequency")
    else:
        print("├─ Large cache with LFU or advanced policy")
        print("└─ Long-term retention, frequency-based replacement")

def print_statistics_summary(data, hash_analysis, req_analysis, temporal_analysis, type_counts):
    """Print comprehensive statistics summary."""
    print("\n" + "="*80)
    print("QWEN TRACE ANALYSIS SUMMARY")
    print("="*80)
    
    print(f"\n📊 Dataset Overview:")
    print(f"  Total requests: {len(data):,}")
    # print(f"  Unique chat sessions: {len(set(req['chat_id'] for req in data)):,}")
    # print(f"  Time span: {data[0]['timestamp']:.3f} - {data[-1]['timestamp']:.3f} seconds")
    # print(f"  Duration: {data[-1]['timestamp'] - data[0]['timestamp']:.3f} seconds")
    
    print(f"\n🔗 Hash ID Analysis:")
    seq_lengths = hash_analysis['seq_lengths']
    print(f"  Sequence length - Mean: {np.mean(seq_lengths):.1f}, Median: {np.median(seq_lengths):.1f}")
    print(f"  Sequence length - 95th percentile: {np.percentile(seq_lengths, 95):.1f}")
    print(f"  Sequence length - Max: {np.max(seq_lengths):,}")
    
    if hash_analysis['reuse_distances']:
        reuse_dist = hash_analysis['reuse_distances']
        print(f"  Reuse distance - Mean: {np.mean(reuse_dist):.1f}, Median: {np.median(reuse_dist):.1f}")
    
    print(f"\n📝 Request Characteristics:")
    input_lens = req_analysis['input_lengths']
    # output_lens = req_analysis['output_lengths']
    print(f"  Input length - Mean: {np.mean(input_lens):.1f}, Median: {np.median(input_lens):.1f}")
    # print(f"  Output length - Mean: {np.mean(output_lens):.1f}, Median: {np.median(output_lens):.1f}")
    # print(f"  I/O ratio - Mean: {np.mean(req_analysis['io_ratios']):.2f}, Median: {np.median(req_analysis['io_ratios']):.2f}")
    
    print(f"\n⏰ Temporal Patterns:")
    if temporal_analysis['inter_arrival_times']:
        inter_times = temporal_analysis['inter_arrival_times']
        print(f"  Inter-arrival time - Mean: {np.mean(inter_times):.3f}s, Median: {np.median(inter_times):.3f}s")
    
    print(f"\n📋 Request Types:")
    total_reqs = sum(type_counts.values())
    for req_type, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
        percentage = count / total_reqs * 100
        print(f"  {req_type}: {count:,} ({percentage:.1f}%)")

def main():
    parser = argparse.ArgumentParser(description='Analyze QWen traces and generate CDF plots')
    parser.add_argument('trace_files', nargs='+', help='Path(s) to QWen trace JSONL files')
    parser.add_argument('--output-dir', default='trace_analysis', 
                       help='Output directory for plots (default: trace_analysis)')
    parser.add_argument('--max-lines', type=int, 
                       help='Maximum number of lines to process per file (for testing)')
    
    args = parser.parse_args()
    
    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    for trace_file in args.trace_files:
        trace_name = Path(trace_file).stem
        trace_output_dir = output_dir / trace_name
        trace_output_dir.mkdir(exist_ok=True)
        
        print(f"\n🔍 Analyzing trace: {trace_file}")
        print(f"📁 Output directory: {trace_output_dir}")
        
        # Load data
        data = load_trace_data(trace_file, args.max_lines)
        if not data:
            print(f"❌ No data loaded from {trace_file}")
            continue

        # Translate hash IDs to be position-aware by incorporating prefix hashes
        # This is crucial for LLM traces where block identity depends on context
        data = translate_hash_ids_with_prefix(data)
        
        # Perform analyses
        hash_analysis = analyze_hash_id_patterns(data, trace_output_dir)
        req_analysis = analyze_request_characteristics(data, trace_output_dir)
        temporal_analysis = analyze_temporal_patterns(data, trace_output_dir)
        type_counts = analyze_request_types(data, trace_output_dir)
        
        # Create summary plots
        create_summary_plots(hash_analysis, req_analysis, temporal_analysis, trace_output_dir)
        
        # Print statistics
        print_statistics_summary(data, hash_analysis, req_analysis, temporal_analysis, type_counts)
        
        # Interpret CDFs for cache design
        interpret_cdf_results(hash_analysis, req_analysis, temporal_analysis, trace_name)
        
        print(f"\n✅ Analysis complete for {trace_name}")
        print(f"📊 Generated plots in: {trace_output_dir}")

if __name__ == '__main__':
    main()