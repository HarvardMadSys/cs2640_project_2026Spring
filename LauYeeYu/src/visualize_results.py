#!/usr/bin/env python3
"""
Script to visualize cache evaluation results from log files using matplotlib.
"""

import os
import re
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
import argparse

# Define the desired order of algorithms for consistent comparison
# ALGORITHM_ORDER = [
#     "LRU",
#     "S3FIFO",
#     "ARC",
#     "GDSF",
#     "GDSF_compute",
#     "LHD",
#     "LHD_compute",
#     "Belady",
#     "BeladyCompute",
#     # "Optimal",
# ]
ALGORITHM_ORDER = [
    "LRU",
    "S3FIFO",
    "ARC",
    "GDSF_compute",
    "LHD_compute",
    "RandomCompute",
    "RandomComputeAdmission",
    "Belady",
    "BeladyCompute",
    "Optimal",
]

# Mapping from algorithm names to human-friendly text
ALGORITHM_DISPLAY_NAMES = {
    "LRU": "LRU",
    "S3FIFO": "S3FIFO", 
    "ARC": "ARC",
    # "GDSF": "GDSF",
    "GDSF_compute": "GDSF",
    "LHD": "LHD",
    "LHD_compute": "LCD",
    "Belady": "Belady",
    "BeladyCompute": "Belady Compute",
    "RandomCompute": "Random Compute",
    "RandomComputeAdmission": "Random Compute Admission",
    "Optimal": "Optimal"
}

def get_algorithm_display_name(algo_name):
    """Convert algorithm name to human-friendly text."""
    return ALGORITHM_DISPLAY_NAMES.get(algo_name, algo_name.replace('_', ' ').title())

def parse_log_file(filepath):
    """Parse a single log file and extract results."""
    results = {}
    cache_size = None
    trace_file = None

    with open(filepath, 'r') as f:
        content = f.read()

    # Extract cache size
    cache_match = re.search(r'Cache size: (\d+) bytes', content)
    if cache_match:
        cache_size = int(cache_match.group(1))

    # Extract trace file
    trace_match = re.search(r'Trace file: (.+)', content)
    if trace_match:
        trace_file = trace_match.group(1).split('/')[-1]  # Get just the filename

    # Extract algorithm results
    algo_pattern = r'Algorithm (\w+): Saved compute: (\d+), Actual compute: (\d+), Ratio: ([\d.]+)'
    matches = re.findall(algo_pattern, content)

    for algo, saved, actual, ratio in matches:
        saved_compute = int(saved)
        actual_compute = int(actual)
        total_compute = saved_compute + actual_compute
        new_ratio = saved_compute / total_compute if total_compute > 0 else 0.0
        
        results[algo] = {
            'saved_compute': saved_compute,
            'actual_compute': actual_compute,
            'ratio': new_ratio,
            'total_compute': total_compute
        }

    return {
        'cache_size': cache_size,
        'trace_file': trace_file,
        'results': results
    }

def parse_optimal_log_file(filepath, total_compute):
    """Parse an optimal log file and extract the optimal saved compute."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        match = re.search(r'Optimal objective value \(total compute saving\): ([\d.]+)', content)
        if match:
            saved_compute = float(match.group(1))
            return {
                'saved_compute': saved_compute,
                'actual_compute': total_compute - saved_compute,
                'ratio': saved_compute / total_compute if total_compute > 0 else 0,
                'total_compute': total_compute
            }
    except FileNotFoundError:
        return None
    return None

def find_convergence_point(cache_sizes, trace_data, convergence_threshold=0.01):
    """
    Find the first cache size where performance starts to converge.
    Convergence is detected when the improvement rate drops below the threshold.
    
    Args:
        cache_sizes: Sorted list of cache sizes
        trace_data: Dictionary containing algorithm performance data for each cache size
        convergence_threshold: Minimum improvement rate to continue (default 1%)
    
    Returns:
        index: The index of the cache size at the convergence point
    """
    if len(cache_sizes) < 3:
        return len(cache_sizes) - 1  # If less than 3 points, use the largest
    
    # Calculate average improvement rate across all algorithms
    improvement_rates = []
    
    for i in range(1, len(cache_sizes)):
        current_size = cache_sizes[i]
        prev_size = cache_sizes[i-1]
        
        # Calculate average ratio improvement for this cache size step
        total_improvement = 0
        valid_comparisons = 0
        
        for algo in trace_data[current_size]:
            if algo in trace_data[prev_size]:
                current_ratio = trace_data[current_size][algo]['ratio']
                prev_ratio = trace_data[prev_size][algo]['ratio']
                
                if prev_ratio > 0:  # Avoid division by zero
                    improvement = (current_ratio - prev_ratio) / prev_ratio
                    total_improvement += improvement
                    valid_comparisons += 1
        
        if valid_comparisons > 0:
            avg_improvement = total_improvement / valid_comparisons
            improvement_rates.append(avg_improvement)
        else:
            improvement_rates.append(0)
    
    # Find first point where improvement rate falls below threshold.
    # Require non-negative rate so that a noisy dip (ratio decreasing) is not
    # mistaken for convergence.
    for i, rate in enumerate(improvement_rates):
        if 0 <= rate < convergence_threshold:
            return i  # Return the index of the cache size at convergence point

    return len(cache_sizes) - 1  # If no convergence found, use the largest size

def collect_all_data(logs_dir):
    """Collect data from all log files."""
    all_data = defaultdict(lambda: defaultdict(dict))
    
    for filename in os.listdir(logs_dir):
        if filename.endswith('.log'):
            filepath = os.path.join(logs_dir, filename)
            data = parse_log_file(filepath)
            
            if data['cache_size'] is not None and data['trace_file'] is not None:
                if not data['results']:
                    # Log produced no parseable algorithm results (e.g. all
                    # ratios were -nan because the trace had zero compute).
                    # Skip so downstream code doesn't see an empty cache-size
                    # entry, which breaks the summary table's max() call.
                    continue

                trace_name = data['trace_file'].replace('.jsonl', '')
                cache_size = data['cache_size']

                all_data[trace_name][cache_size] = data['results']

                # Check for optimal log
                if data['results']:
                    # Get total_compute from the first algorithm entry
                    any_algo = list(data['results'].keys())[0]
                    total_compute = data['results'][any_algo]['total_compute']

                    # Construct optimal log path
                    optimal_log_path = os.path.join(logs_dir, 'optimal', filename)
                    
                    if os.path.exists(optimal_log_path):
                        optimal_result = parse_optimal_log_file(optimal_log_path, total_compute)
                        if optimal_result:
                            all_data[trace_name][cache_size]['Optimal'] = optimal_result
    
    return all_data

def create_visualizations(all_data, output_dir):
    """Create various visualizations for the cache evaluation results."""
    os.makedirs(output_dir, exist_ok=True)
    
    plt.style.use('default')

    trace_name_map = {
        'qwen_traceA_blksz_16': 'Qwen To-C trace',
        'qwen_traceB_blksz_16': 'QWen To-B trace',
        'qwen_thinking_blksz_16': 'Qwen Thinking trace',
        'qwen_coder_blksz_16': 'Qwen Coder trace',
    }

    # Set larger default font sizes
    plt.rcParams.update({
        'font.size': 16,
        'axes.titlesize': 20,
        'axes.labelsize': 18,
        'xtick.labelsize': 16,
        'ytick.labelsize': 16,
        'legend.fontsize': 16
    })
    # Use a color palette with better contrast and visibility
    colors = plt.cm.tab10(np.linspace(0, 1, 10))

    # --- Find global max ratio for consistent y-axis scaling ---
    global_max_ratio = 0
    for trace_name, trace_data in all_data.items():
        for cache_size, cache_size_data in trace_data.items():
            for algo, algo_data in cache_size_data.items():
                if algo_data['ratio'] > global_max_ratio:
                    global_max_ratio = algo_data['ratio']

    # Set a common y-axis limit, rounding up to the next 0.1, with a max of 1.0
    y_axis_max = min(1.0, np.ceil(global_max_ratio * 10) / 10 if global_max_ratio > 0 else 0.1)
    # ---

    for trace_name, trace_data in all_data.items():
        display_name = trace_name_map.get(trace_name, trace_name)
        print(f"Creating plots for {display_name}...")
        
        cache_sizes = sorted(trace_data.keys())
        
        # Skip cache sizes larger than the first converging point
        convergence_idx = find_convergence_point(cache_sizes, trace_data)
        truncated_cache_sizes = cache_sizes[:convergence_idx + 1]
        print(f"Convergence detected at cache size {cache_sizes[convergence_idx]:,}")
        print(f"Using cache sizes up to convergence point: {[f'{size:,}' for size in truncated_cache_sizes]}")
        
        # 1. Cache Hit Ratio vs Cache Size for each algorithm
        plt.figure(figsize=(12, 8))
        
        algorithms = set()
        for cache_size_data in trace_data.values():
            algorithms.update(cache_size_data.keys())
        
        # Filter algorithms to only include those in ALGORITHM_ORDER, then sort according to the specified order
        sorted_algorithms = []
        for algo in ALGORITHM_ORDER:
            if algo in algorithms:
                sorted_algorithms.append(algo)
        
        for i, algo in enumerate(sorted_algorithms):
            ratios = []
            sizes = []
            for cache_size in truncated_cache_sizes:
                if algo in trace_data[cache_size]:
                    ratios.append(trace_data[cache_size][algo]['ratio'])
                    sizes.append(cache_size)
            
            if ratios:
                plt.plot(sizes, ratios, 'o-', label=get_algorithm_display_name(algo), color=colors[i % len(colors)],
                        linewidth=2, markersize=6)
        
        plt.xlabel('Cache Size (blocks)')
        plt.ylabel('Compute Saved / Total Compute Ratio')
        plt.title(f'Cache Performance Comparison\n{display_name}', fontweight='bold')
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        # plt.ylim(0, y_axis_max)  # Apply consistent y-axis scale
        plt.grid(True, alpha=0.3)
        plt.xscale('log')

        # Set explicit x-axis ticks and labels to show all cache sizes clearly
        plt.xticks(truncated_cache_sizes, [f'{size:,}' for size in truncated_cache_sizes], rotation=45, ha='right')
        plt.gca().xaxis.set_minor_locator(plt.NullLocator())  # Remove minor tick marks
        plt.tight_layout()
        plt.savefig(f'{output_dir}/{trace_name}_ratio_vs_cache_size.png', dpi=300, bbox_inches='tight')
        plt.close()
        
        # 2. Bar chart for each cache size (ratio only)
        # This section generates individual comparison plots for each cache size up to the convergence point,
        # allowing detailed analysis of algorithm performance at specific cache capacities.
        # Each plot shows the compute saved ratio for all algorithms at that particular cache size,
        # with algorithms displayed in the specified order for easy comparison.
        for cache_size in truncated_cache_sizes:
            if cache_size in trace_data:
                plt.figure(figsize=(8, 8))
                
                # Sort algorithms according to the predefined order, but only include those present in the current cache size data
                algos = []
                for algo in ALGORITHM_ORDER:
                    if algo in trace_data[cache_size]:
                        algos.append(algo)
                
                # Extract ratios for the selected algorithms in the specified order
                ratios = [trace_data[cache_size][algo]['ratio'] for algo in algos]
                
                # Create bar chart with custom algorithm ordering
                bars = plt.bar([get_algorithm_display_name(algo) for algo in algos], ratios, color=colors[:len(algos)])
                plt.xlabel('Algorithm')
                plt.ylabel('Compute Saved / Total Compute Ratio', y=0.5)
                plt.title(f'Compute Saved Ratios\n{display_name}\nCache Size: {cache_size:,} blocks', fontweight='bold')
                plt.xticks(rotation=45, ha='right')
                plt.grid(True, alpha=0.3, axis='y')
                
                # Add numerical value labels on top of each bar for precise reading
                for bar, ratio in zip(bars, ratios):
                    plt.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.001,
                            f'{ratio:.3f}', ha='center', va='bottom', fontsize=14, fontweight='bold')
                
                plt.tight_layout()
                plt.savefig(f'{output_dir}/{trace_name}_cache_{cache_size}_comparison.png',
                           dpi=300, bbox_inches='tight')
                plt.close()
        
        # 3. Heatmap showing ratios across cache sizes and algorithms up to convergence point
        if len(truncated_cache_sizes) > 1:
            plt.figure(figsize=(12, 8))
            
            # Create matrix for heatmap using the specified algorithm order
            matrix = []
            # Create ordered list of algorithms based on predefined order, but only include those present in the data
            algo_labels = []
            for algo in ALGORITHM_ORDER:
                if algo in algorithms:
                    algo_labels.append(get_algorithm_display_name(algo))
            
            for algo in algo_labels:
                row = []
                for cache_size in truncated_cache_sizes:
                    if algo in trace_data[cache_size]:
                        row.append(trace_data[cache_size][algo]['ratio'])
                    else:
                        row.append(0)
                matrix.append(row)
            
            matrix = np.array(matrix)
            
            im = plt.imshow(matrix, cmap='RdYlGn', aspect='auto')
            cbar = plt.colorbar(im, label='Compute Saved / Total Compute Ratio')
            cbar.ax.tick_params()
            cbar.set_label('Compute Saved / Total Compute Ratio')
            
            plt.xticks(range(len(truncated_cache_sizes)), [f'{size:,}' for size in truncated_cache_sizes], rotation=45, ha='right')
            plt.yticks(range(len(algo_labels)), algo_labels)
            plt.xlabel('Cache Size (blocks)')
            plt.ylabel('Algorithm')
            plt.title(f'Performance Heatmap - {display_name}', fontweight='bold')
            
            # Add text annotations
            for i in range(len(algo_labels)):
                for j in range(len(truncated_cache_sizes)):
                    text = plt.text(j, i, f'{matrix[i, j]:.3f}',
                                   ha="center", va="center", color="black", fontsize=14)
            
            plt.tight_layout()
            plt.savefig(f'{output_dir}/{trace_name}_heatmap.png', dpi=300, bbox_inches='tight')
            plt.close()

def print_summary_table(all_data):
    """Print a summary table of results."""
    print("\n" + "="*80)
    print("CACHE EVALUATION RESULTS SUMMARY")
    print("="*80)

    trace_name_map = {
        'qwen_traceA_blksz_16': 'Qwen To-C trace',
        'qwen_traceB_blksz_16': 'QWen To-B trace',
        'qwen_thinking_blksz_16': 'Qwen Thinking trace',
        'qwen_coder_blksz_16': 'Qwen Coder trace',
    }
    
    for trace_name, trace_data in all_data.items():
        display_name = trace_name_map.get(trace_name, trace_name)
        print(f"\n📊 {display_name.upper()}")
        print("-" * 60)
        
        cache_sizes = sorted(trace_data.keys())
        
        # Find all algorithms
        all_algorithms = set()
        for cache_data in trace_data.values():
            all_algorithms.update(cache_data.keys())
        
        # Filter algorithms to only include those in ALGORITHM_ORDER
        filtered_algorithms = [algo for algo in ALGORITHM_ORDER if algo in all_algorithms]
        all_algorithms = sorted(filtered_algorithms)
        
        # Use truncated cache sizes (up to convergence point) for summary table
        convergence_idx = find_convergence_point(cache_sizes, trace_data)
        truncated_cache_sizes_summary = cache_sizes[:convergence_idx + 1]
        
        # Print header
        print(f"{'Algorithm':<12}", end='')
        for cache_size in truncated_cache_sizes_summary:
            print(f"{cache_size:>12,}", end='')
        print()
        
        print("-" * (12 + 12 * len(truncated_cache_sizes_summary)))
        
        # Print data for each algorithm using the specified order
        # Only include algorithms that are in ALGORITHM_ORDER
        ordered_algos = []
        for algo in ALGORITHM_ORDER:
            if algo in all_algorithms:
                ordered_algos.append(algo)
        
        for algo in ordered_algos:
            print(f"{get_algorithm_display_name(algo):<12}", end='')
            for cache_size in truncated_cache_sizes_summary:
                if algo in trace_data[cache_size]:
                    ratio = trace_data[cache_size][algo]['ratio']
                    print(f"{ratio:>12.3f}", end='')
                else:
                    print(f"{'N/A':>12}", end='')
            print()
        
        # Find best algorithm for each cache size (up to convergence point)
        print("\n🏆 Best performing algorithms (up to convergence point):")
        for cache_size in truncated_cache_sizes_summary:
            best_algo = max(trace_data[cache_size].items(),
                          key=lambda x: x[1]['ratio'])
            print(f"  Cache {cache_size:,} blocks: {get_algorithm_display_name(best_algo[0])} (ratio: {best_algo[1]['ratio']:.3f})")

def plot_gdsf_scores(data_path, output_dir):
    """Parses saved compute data and creates a bar chart."""
    output_path = os.path.join(output_dir, 'saved_compute.png')
    
    try:
        import pandas as pd
    except ImportError:
        print("Error: pandas is required for this plot. Please install it.")
        return

    try:
        df = pd.read_csv(data_path, sep='\t')
        df = df.set_index('Type')
    except FileNotFoundError:
        print(f"Error: Data file not found at {data_path}")
        return

    df = df.loc[:, (df != 0).any(axis=0)]
    types = df.index
    ranges = df.columns
    n_types = len(types)
    n_ranges = len(ranges)

    plt.figure(figsize=(24, 12))
    # Adjust bar_width based on the number of types
    bar_width = 0.8 / n_types 
    r = np.arange(n_ranges)

    for i, type_name in enumerate(types):
        plt.bar(r + i * bar_width, df.loc[type_name], width=bar_width, edgecolor='grey', label=type_name)

    plt.xlabel('Ranges', fontweight='bold', fontsize=20)
    plt.ylabel('Saved Compute', fontweight='bold', fontsize=20)
    plt.xticks([r + bar_width * (n_types - 1) / 2 for r in range(n_ranges)], ranges, rotation=45, ha="right", fontsize=16)
    plt.yticks(fontsize=20)
    plt.legend(fontsize=20)
    plt.title('Saved Compute by Type and Range', fontweight='bold', fontsize=24)
    plt.grid(True, axis='y', linestyle='--', alpha=0.7)
    plt.tight_layout()
    plt.savefig(output_path)
    print(f"Plot saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Visualize cache evaluation results')
    parser.add_argument('--logs-dir', default='logs', 
                       help='Directory containing log files (default: logs)')
    parser.add_argument('--output-dir', default='plots',
                       help='Directory to save plots (default: plots)')
    parser.add_argument('--no-plots', action='store_true',
                       help='Skip generating plots, only show summary')
    parser.add_argument('--custom-plot', action='store_true',
                       help='Generate a custom plot from a data file.')
    parser.add_argument('--data-file', default='other_data/GDSF.csv',
                       help='Path to the custom data file.')
    
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    if args.custom_plot:
        print(f"📊 Generating custom plot from: {args.data_file}")
        plot_gdsf_scores(args.data_file, args.output_dir)
        return
    
    if not os.path.exists(args.logs_dir):
        print(f"Error: Logs directory '{args.logs_dir}' not found!")
        return
    
    print(f"📁 Reading log files from: {args.logs_dir}")
    all_data = collect_all_data(args.logs_dir)
    
    if not all_data:
        print("❌ No valid log files found!")
        return
    
    print(f"✅ Found data for {len(all_data)} trace(s)")
    
    # Print summary table
    print_summary_table(all_data)
    
    if not args.no_plots:
        print(f"\n📈 Generating plots in: {args.output_dir}")
        create_visualizations(all_data, args.output_dir)
        print("✅ Plots generated successfully!")

        # List generated files
        if os.path.exists(args.output_dir):
            plot_files = [f for f in os.listdir(args.output_dir) if f.endswith('.png')]
            print(f"\n📋 Generated {len(plot_files)} plot files:")
            for f in sorted(plot_files):
                print(f"  - {f}")

if __name__ == '__main__':
    main()