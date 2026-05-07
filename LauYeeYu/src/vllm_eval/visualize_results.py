#!/usr/bin/env python3
"""
Script to visualize cache evaluation results from vLLM log files using matplotlib.
"""

import os
import re
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
import argparse

ALGO_ORDER = ['LRU', 'Random', 'RandomSmallQueue', 'RandomQuickDemotion', 'RandomQuickDemotionGhost', 'BeladyCompute', 'BeladyBlockCompute']

# Mapping from algorithm names to human-friendly text
ALGORITHM_DISPLAY_NAMES = {
    "LRU": "LRU",
    "S3FIFO": "S3FIFO",
    "ARC": "ARC",
    "GDSF": "GDSF",
    "GDCF": "GDCF",
    "LHD": "LHD",
    "LCD": "LCD",
    "LCDBlock": "LCD Block",
    "Belady": "Belady",
    "BeladyCompute": "Belady Partial-Node Compute",
    "BeladyNodeCompute": "Belady Node Compute",
    "BeladyBlockCompute": "Belady Block Compute",
    "Random": "Random",
    "RandomSmallQueue": "Random Small Queue",
    "RandomQuickDemotion": "Random Quick Demotion",
    "RandomQuickDemotionGhost": "Random Quick Demotion with Ghost",
    "Optimal": "Optimal"
}

def get_algorithm_display_name(algo_name):
    """Convert algorithm name to human-friendly text."""
    return ALGORITHM_DISPLAY_NAMES.get(algo_name, algo_name.replace('_', ' ').title())

def sort_algorithms(algos):
    """Sort algorithms based on the predefined ALGO_ORDER."""
    filtered_algos = [algo for algo in algos if algo in ALGO_ORDER]
    return sorted(filtered_algos, key=lambda x: ALGO_ORDER.index(x))


def parse_vllm_log_file(filepath):
    """Parse a single log file and extract results based on filename logic in vllm/Makefile."""
    basename = os.path.basename(filepath)
    # Match pattern: (trace_name)_cache_(size)_(algo).log
    match = re.match(r'(.*?)_cache_(.*?)_(.*?)\.log', basename)
    if not match:
        return None

    trace_name = match.group(1)
    cache_size = match.group(2)
    algo = match.group(3)

    if algo not in ALGO_ORDER:
        return None

    with open(filepath, 'r') as f:
        content = f.read()

    # Extract compute savings results
    algo_pattern = r'Algorithm (\w+): Saved compute: ([\d.]+), Actual compute: ([\d.]+), Ratio: ([\d.]+)'
    matches = re.findall(algo_pattern, content)

    if not matches:
        return None

    algo_in_log, saved, actual, ratio = matches[0]
    saved_compute = float(saved)
    actual_compute = float(actual)
    total_compute = saved_compute + actual_compute
    ratio_val = float(ratio)

    result = {
        'trace_file': trace_name,
        'cache_size': cache_size,
        'algo': algo,
        'results': {
            'saved_compute': saved_compute,
            'actual_compute': actual_compute,
            'ratio': ratio_val,
            'total_compute': total_compute
        }
    }

    # Extract TTFT results if present
    ttft_pattern = r'Algorithm (\w+): Saved TTFT: ([\d.]+), Actual TTFT: ([\d.]+), TTFT Ratio: ([\d.]+)'
    ttft_matches = re.findall(ttft_pattern, content)
    if ttft_matches:
        _, ttft_saved, ttft_actual, ttft_ratio = ttft_matches[0]
        result['results']['ttft_ratio'] = float(ttft_ratio)

    return result

def collect_all_data(logs_dir):
    """Collect data from all log files."""
    all_data = defaultdict(lambda: defaultdict(dict))
    
    if not os.path.exists(logs_dir):
        return all_data

    for filename in os.listdir(logs_dir):
        if filename.endswith('.log'):
            filepath = os.path.join(logs_dir, filename)
            data = parse_vllm_log_file(filepath)
            
            if data is not None:
                trace_name = data['trace_file']
                cache_size = data['cache_size']
                algo = data['algo']
                all_data[trace_name][cache_size][algo] = data['results']
    
    return all_data

# Used to sort cache sizes logically instead of pseudo-alphabetically
def parse_cache_size(size_str: str) -> int:
    size_str = size_str.lower()
    if size_str.endswith('g'):
        return int(float(size_str[:-1]) * 1024 * 1024 * 1024)
    if size_str.endswith('m'):
        return int(float(size_str[:-1]) * 1024 * 1024)
    if size_str.endswith('k'):
        return int(float(size_str[:-1]) * 1024)
    return int(size_str)

INTENSITY_DISPLAY_NAMES = {
    'qwen_coder_30b': 'Qwen Coder 30B (865 + 2·idx)',
    'linear': 'Linear (cost = idx)',
    'constant': 'Constant (cost = 1)',
}

def infer_intensity_label(logs_dir):
    """Pull the compute-intensity label from the logs-dir basename, if recognized."""
    leaf = os.path.basename(os.path.normpath(logs_dir))
    return INTENSITY_DISPLAY_NAMES.get(leaf, leaf or None)

def create_visualizations(all_data, output_dir, intensity_label=None):
    """Create various visualizations for the cache evaluation results."""
    os.makedirs(output_dir, exist_ok=True)
    intensity_suffix = f"\nCompute intensity: {intensity_label}" if intensity_label else ""
    
    plt.style.use('default')

    trace_name_map = {
        'qwen_traceA_blksz_16_pos': 'Qwen To-C trace',
        'qwen_traceB_blksz_16_pos': 'QWen To-B trace',
        'qwen_thinking_blksz_16_pos': 'Qwen Thinking trace',
        'qwen_coder_blksz_16_pos': 'Qwen Coder trace',
        'gaia_text_generations_blksz_16_pos_concurrency_1': 'GAIA Trace (Concurrency 1)',
        'gaia_text_generations_blksz_16_pos_concurrency_8': 'GAIA Trace (Concurrency 8)',
    }

    plt.rcParams.update({
        'font.size': 16,
        'axes.titlesize': 20,
        'axes.labelsize': 18,
        'xtick.labelsize': 16,
        'ytick.labelsize': 16,
        'legend.fontsize': 16
    })
    colors = plt.cm.tab10(np.linspace(0, 1, 10))

    for trace_name, trace_data in all_data.items():
        display_name = trace_name_map.get(trace_name, trace_name)
        print(f"Creating plots for {display_name}...")

        cache_sizes = sorted(trace_data.keys(), key=parse_cache_size)

        algorithms = set()
        for cache_size_data in trace_data.values():
            algorithms.update(cache_size_data.keys())
        algorithms = sort_algorithms(algorithms)

        sizes_numeric = [parse_cache_size(cs) for cs in cache_sizes]

        # Check if TTFT data is available
        has_ttft = any(
            'ttft_ratio' in trace_data[cs].get(algo, {})
            for cs in cache_sizes
            for algo in algorithms
        )

        # 1. Compute Savings Ratio vs Cache Size
        plt.figure(figsize=(12, 8))
        for i, algo in enumerate(algorithms):
            ratios = []
            sizes = []
            for cache_size in cache_sizes:
                if algo in trace_data[cache_size]:
                    ratios.append(float(trace_data[cache_size][algo]['ratio']))
                    sizes.append(parse_cache_size(cache_size))
            if ratios:
                plt.plot(sizes, ratios, 'o-', label=get_algorithm_display_name(algo),
                        color=colors[i % len(colors)], linewidth=2, markersize=6)

        plt.xlabel('Cache Size (blocks)')
        plt.ylabel('Compute Savings Ratio')
        plt.title(f'Compute Savings Ratio vs Cache Size - {display_name}{intensity_suffix}', fontweight='bold')
        plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        plt.grid(True, alpha=0.3)
        plt.xscale('log')
        plt.tight_layout()
        plt.savefig(f'{output_dir}/{trace_name}_compute_ratio_vs_cache_size.png', dpi=300, bbox_inches='tight')
        plt.close()

        # 2. Normalized TTFT vs Cache Size (only if TTFT data is available)
        if has_ttft:
            plt.figure(figsize=(12, 8))
            for i, algo in enumerate(algorithms):
                ttfts = []
                sizes = []
                for cache_size in cache_sizes:
                    if algo in trace_data[cache_size] and 'ttft_ratio' in trace_data[cache_size][algo]:
                        ttfts.append(1.0 - float(trace_data[cache_size][algo]['ttft_ratio']))
                        sizes.append(parse_cache_size(cache_size))
                if ttfts:
                    plt.plot(sizes, ttfts, 'o-', label=get_algorithm_display_name(algo),
                            color=colors[i % len(colors)], linewidth=2, markersize=6)

            plt.xlabel('Cache Size (blocks)')
            plt.ylabel('Normalized TTFT')
            plt.title(f'Normalized TTFT vs Cache Size - {display_name}{intensity_suffix}', fontweight='bold')
            plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
            plt.grid(True, alpha=0.3)
            plt.xscale('log')
            plt.tight_layout()
            plt.savefig(f'{output_dir}/{trace_name}_ttft_vs_cache_size.png', dpi=300, bbox_inches='tight')
            plt.close()

        # 3. Bar chart for each cache size
        for cache_size in cache_sizes:
            if cache_size in trace_data:
                plt.figure(figsize=(6, 6))

                algos = sort_algorithms(trace_data[cache_size].keys())
                ratios = [float(trace_data[cache_size][algo]['ratio']) for algo in algos]
                algo_labels = [get_algorithm_display_name(algo) for algo in algos]

                bars = plt.bar(algo_labels, ratios, color=colors[:len(algos)])
                plt.xlabel('Algorithm')
                plt.ylabel('Compute Savings Ratio')
                plt.title(f'Compute Savings Ratio - {display_name}\nCache Size: {cache_size} blocks{intensity_suffix}', fontweight='bold')
                plt.xticks(rotation=45, ha='right')
                plt.grid(True, alpha=0.3, axis='y')

                for bar, r in zip(bars, ratios):
                    plt.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.001,
                            f'{r:.3f}', ha='center', va='bottom', fontsize=14, fontweight='bold')

                plt.tight_layout()
                plt.savefig(f'{output_dir}/{trace_name}_cache_{cache_size}_comparison.png',
                           dpi=300, bbox_inches='tight')
                plt.close()

        # 4. Heatmap
        if len(cache_sizes) > 1:
            plt.figure(figsize=(12, 8))

            matrix = []
            algo_labels = sort_algorithms(algorithms)
            algo_display_labels = [get_algorithm_display_name(algo) for algo in algo_labels]

            for algo in algo_labels:
                row = []
                for cache_size in cache_sizes:
                    if algo in trace_data[cache_size]:
                        row.append(float(trace_data[cache_size][algo]['ratio']))
                    else:
                        row.append(0.0)
                matrix.append(row)

            matrix = np.array(matrix)

            im = plt.imshow(matrix, cmap='RdYlGn', aspect='auto')
            cbar = plt.colorbar(im, label='Compute Savings Ratio')

            plt.xticks(range(len(cache_sizes)), cache_sizes)
            plt.yticks(range(len(algo_display_labels)), algo_display_labels)
            plt.xlabel('Cache Size (blocks)')
            plt.ylabel('Algorithm')
            plt.title(f'Compute Savings Ratio Heatmap - {display_name}{intensity_suffix}', fontweight='bold')

            for i in range(len(algo_labels)):
                for j in range(len(cache_sizes)):
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
        'qwen_traceA_blksz_16_pos': 'Qwen To-C trace',
        'qwen_traceB_blksz_16_pos': 'QWen To-B trace',
        'qwen_thinking_blksz_16_pos': 'Qwen Thinking trace',
        'qwen_coder_blksz_16_pos': 'Qwen Coder trace',
        'gaia_text_generations_blksz_16_pos_concurrency_1': 'GAIA Trace (Concurrency 1)',
        'gaia_text_generations_blksz_16_pos_concurrency_8': 'GAIA Trace (Concurrency 8)',
    }

    for trace_name, trace_data in all_data.items():
        display_name = trace_name_map.get(trace_name, trace_name)
        cache_sizes = sorted(trace_data.keys(), key=parse_cache_size)

        all_algorithms = set()
        for cache_data in trace_data.values():
            all_algorithms.update(cache_data.keys())
        all_algorithms = sort_algorithms(all_algorithms)

        # Compute Savings Ratio table
        print(f"\n📊 {display_name.upper()} - Compute Savings Ratio")
        print("-" * (12 + 12 * len(cache_sizes)))
        print(f"{'Algorithm':<12}", end='')
        for cache_size in cache_sizes:
            print(f"{cache_size:>12}", end='')
        print()
        print("-" * (12 + 12 * len(cache_sizes)))

        for algo in all_algorithms:
            print(f"{algo:<12}", end='')
            for cache_size in cache_sizes:
                if algo in trace_data[cache_size]:
                    ratio = float(trace_data[cache_size][algo]['ratio'])
                    print(f"{ratio:>12.3f}", end='')
                else:
                    print(f"{'N/A':>12}", end='')
            print()

        print("\n🏆 Best performing algorithms (highest compute savings):")
        for cache_size in cache_sizes:
            best_algo = max(trace_data[cache_size].items(),
                          key=lambda x: float(x[1]['ratio']))
            print(f"  Cache {cache_size} blocks: {best_algo[0]} (Ratio: {float(best_algo[1]['ratio']):.3f})")

        # Normalized TTFT table (only if data available)
        has_ttft = any(
            'ttft_ratio' in trace_data[cs].get(algo, {})
            for cs in cache_sizes
            for algo in all_algorithms
        )
        if has_ttft:
            print(f"\n📊 {display_name.upper()} - Normalized TTFT")
            print("-" * (12 + 12 * len(cache_sizes)))
            print(f"{'Algorithm':<12}", end='')
            for cache_size in cache_sizes:
                print(f"{cache_size:>12}", end='')
            print()
            print("-" * (12 + 12 * len(cache_sizes)))

            for algo in all_algorithms:
                print(f"{algo:<12}", end='')
                for cache_size in cache_sizes:
                    if algo in trace_data[cache_size] and 'ttft_ratio' in trace_data[cache_size][algo]:
                        ttft = 1.0 - float(trace_data[cache_size][algo]['ttft_ratio'])
                        print(f"{ttft:>12.3f}", end='')
                    else:
                        print(f"{'N/A':>12}", end='')
                print()

            print("\n🏆 Best performing algorithms (lowest TTFT):")
            for cache_size in cache_sizes:
                candidates = {a: d for a, d in trace_data[cache_size].items() if 'ttft_ratio' in d}
                if candidates:
                    best_algo = min(candidates.items(),
                                  key=lambda x: 1.0 - float(x[1]['ttft_ratio']))
                    best_ttft = 1.0 - float(best_algo[1]['ttft_ratio'])
                    print(f"  Cache {cache_size} blocks: {best_algo[0]} (TTFT: {best_ttft:.3f})")

def main():
    parser = argparse.ArgumentParser(description='Visualize cache evaluation results')
    parser.add_argument('--logs-dir', default='logs', 
                       help='Directory containing log files (default: logs)')
    parser.add_argument('--output-dir', default='plots',
                       help='Directory to save plots (default: plots)')
    parser.add_argument('--no-plots', action='store_true',
                       help='Skip generating plots, only show summary')
    
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    
    if not os.path.exists(args.logs_dir):
        print(f"Error: Logs directory '{args.logs_dir}' not found!")
        return
    
    print(f"📁 Reading log files from: {args.logs_dir}")
    all_data = collect_all_data(args.logs_dir)
    
    if not all_data:
        print("❌ No valid log files found!")
        return
    
    print(f"✅ Found data for {len(all_data)} trace(s)")
    
    print_summary_table(all_data)
    
    if not args.no_plots:
        print(f"\n📈 Generating plots in: {args.output_dir}")
        create_visualizations(all_data, args.output_dir,
                              intensity_label=infer_intensity_label(args.logs_dir))
        print("✅ Plots generated successfully!")
        
        if os.path.exists(args.output_dir):
            plot_files = [f for f in os.listdir(args.output_dir) if f.endswith('.png')]
            print(f"\n📋 Generated {len(plot_files)} plot files:")
            for f in sorted(plot_files):
                print(f"  - {f}")

if __name__ == '__main__':
    main()
