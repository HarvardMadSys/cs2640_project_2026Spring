import sys
import glob
import json
import matplotlib.pyplot as plt
import numpy as np
import os
from collections import defaultdict

ALGORITHM_ORDER = [
    "LRU",
    "GDCF",
    "LCD",
    "LCDBlock"
]

ALGORITHM_DISPLAY_NAMES = {
    "LRU": "LRU",
    "LCD": "LCD",
    "GDCF": "GDCF",
    "LCDBlock": "LCD Block"
}

def get_algorithm_display_name(algo_name):
    """Convert algorithm name to human-friendly text."""
    return ALGORITHM_DISPLAY_NAMES.get(algo_name, algo_name.replace('_', ' ').title())

def analyze_file(filename):
    print(f"\nAnalyzing {filename} ...")
    recompute_lengths = []
    recompute_indices = []
    recomputes_per_request = []
    first_recompute_lengths = []
    second_recompute_lengths = []
    last_recompute_lengths = []
    first_recompute_indices = []
    second_recompute_indices = []
    last_recompute_indices = []
    
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
                
            recompute_segments = data.get("recompute_segments", [])
            recomputes_per_request.append(len(recompute_segments))
            
            if recompute_segments:
                first_seg = recompute_segments[0]
                if len(first_seg) == 2:
                    first_recompute_indices.append(first_seg[0])
                    first_recompute_lengths.append(first_seg[1] - first_seg[0])
                
                if len(recompute_segments) > 1:
                    second_seg = recompute_segments[1]
                    if len(second_seg) == 2:
                        second_recompute_indices.append(second_seg[0])
                        second_recompute_lengths.append(second_seg[1] - second_seg[0])
                
                last_seg = recompute_segments[-1]
                if len(last_seg) == 2:
                    last_recompute_indices.append(last_seg[0])
                    last_recompute_lengths.append(last_seg[1] - last_seg[0])
            
            for seg in recompute_segments:
                if len(seg) == 2:
                    start, end = seg
                    recompute_indices.append(start)
                    recompute_lengths.append(end - start)
                
    if not recomputes_per_request:
        print("  No requests found.")
        return None
        
    avg_recomputes_per_req = np.mean(recomputes_per_request)
    max_recomputes_per_req = np.max(recomputes_per_request) if recomputes_per_request else 0
    avg_recompute_len = np.mean(recompute_lengths) if recompute_lengths else 0
    max_recompute_len = np.max(recompute_lengths) if recompute_lengths else 0
    total_recomputes = len(recompute_lengths)
    
    print(f"  Total Requests: {len(recomputes_per_request)}")
    print(f"  Total Recomputes (Misses): {total_recomputes}")
    print(f"  Avg recomputes per request: {avg_recomputes_per_req:.2f}")
    print(f"  Max recomputes per request: {max_recomputes_per_req}")
    if total_recomputes > 0:
        print(f"  Avg recompute length: {avg_recompute_len:.2f}")
        print(f"  Max recompute length: {max_recompute_len}")
        
    # Extract dataset, cache size, and algo name
    basename = os.path.basename(filename).replace('_segments.jsonl', '')
    if '_cache_' in basename:
        dataset, rest = basename.split('_cache_')
        if '_' in rest:
            cache_size, algo_name = rest.split('_', 1)
        else:
            cache_size, algo_name = rest, "unknown"
    else:
        dataset = 'unknown_dataset'
        cache_size = "unknown"
        algo_name = basename
        
    return {
        'filename': filename,
        'dataset': dataset,
        'cache_size': cache_size,
        'algo_name': algo_name,
        'recompute_lengths': recompute_lengths,
        'recompute_indices': recompute_indices,
        'recomputes_per_request': recomputes_per_request,
        'first_recompute_lengths': first_recompute_lengths,
        'second_recompute_lengths': second_recompute_lengths,
        'last_recompute_lengths': last_recompute_lengths,
        'first_recompute_indices': first_recompute_indices,
        'second_recompute_indices': second_recompute_indices,
        'last_recompute_indices': last_recompute_indices,
        'avg_recompute_len': avg_recompute_len,
        'avg_recomputes_per_req': avg_recomputes_per_req,
        'max_recomputes_per_req': max_recomputes_per_req
    }

def plot_cdf(ax, data, label):
    sorted_data = np.sort(data)
    yvals = np.arange(1, len(sorted_data) + 1) / len(sorted_data)
    ax.plot(sorted_data, yvals, label=label, linewidth=2)

INTENSITY_DISPLAY_NAMES = {
    'qwen_coder_30b': 'Qwen Coder 30B (865 + 2·idx)',
    'linear': 'Linear (cost = idx)',
    'constant': 'Constant (cost = 1)',
}

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments-dir", default="logs/segments", help="Directory containing segment jsonl files")
    parser.add_argument("--output-dir", default="plots/segments", help="Directory to save segment plots")
    args = parser.parse_args()

    leaf = os.path.basename(os.path.normpath(args.segments_dir))
    intensity_label = INTENSITY_DISPLAY_NAMES.get(leaf, leaf or None)

    files = sys.argv[1:] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else glob.glob(f'{args.segments_dir}/*_segments.jsonl')
    if not files:
        print(f"No _segments.jsonl files found in {args.segments_dir}/. Please run the evaluate program first.")
        return
        
    grouped_results = defaultdict(list)
    for f in files:
        res = analyze_file(f)
        if res and res['recompute_lengths']:
            group_key = f"{res['dataset']}_cache{res['cache_size']}"
            grouped_results[group_key].append(res)
            
    if not grouped_results:
        print("\nNo valid segment data to plot across the provided files.")
        return
        
    os.makedirs(args.output_dir, exist_ok=True)
    
    for group_key, results in grouped_results.items():
        print(f"\nGenerating visualizations for {group_key}...")
        fig, axes = plt.subplots(3, 3, figsize=(15, 15))
        axes = axes.flatten()
        
        # Filter and sort results based on ALGORITHM_ORDER
        results_sorted = []
        for algo in ALGORITHM_ORDER:
            for res in results:
                if res['algo_name'] == algo:
                    results_sorted.append(res)
        
        # If any results don't match our predefined order, append them at the end
        for res in results:
            if res not in results_sorted:
                results_sorted.append(res)
        
        results = results_sorted

        for res in results:
            display_name = get_algorithm_display_name(res['algo_name'])
            
            non_zero_recomputes = [h for h in res['recomputes_per_request'] if h > 0]
            if non_zero_recomputes:
                label = f"{display_name} (Avg={res['avg_recomputes_per_req']:.2f}, Max={res['max_recomputes_per_req']})"
                plot_cdf(axes[0], non_zero_recomputes, label)
                
            if res['recompute_lengths']:
                label = f"{display_name} (AvgLen={res['avg_recompute_len']:.1f})"
                plot_cdf(axes[1], res['recompute_lengths'], label)
                
            if res['recompute_indices']:
                plot_cdf(axes[2], res['recompute_indices'], display_name)
                
            if res.get('first_recompute_lengths'):
                plot_cdf(axes[3], res['first_recompute_lengths'], display_name)
                
            if res.get('second_recompute_lengths'):
                plot_cdf(axes[4], res['second_recompute_lengths'], display_name)
                
            if res.get('last_recompute_lengths'):
                plot_cdf(axes[5], res['last_recompute_lengths'], display_name)
                
            if res.get('first_recompute_indices'):
                plot_cdf(axes[6], res['first_recompute_indices'], display_name)
                
            if res.get('second_recompute_indices'):
                plot_cdf(axes[7], res['second_recompute_indices'], display_name)
                
            if res.get('last_recompute_indices'):
                plot_cdf(axes[8], res['last_recompute_indices'], display_name)
                
        # Subplot 1: Recomputes per request
        axes[0].set_title('CDF of Recompute Segments per Request (>0)')
        axes[0].set_xlabel('Number of Recompute Segments')
        axes[0].grid(True, linestyle='--', alpha=0.7)
        axes[0].legend()
        
        # Subplot 2: Recompute Lengths
        axes[1].set_title('CDF of Recompute Segment Lengths')
        axes[1].set_xlabel('Recompute Length (blocks)')
        axes[1].set_xscale('log') # Use log scale because lengths vary greatly
        axes[1].grid(True, linestyle='--', alpha=0.7)
        axes[1].legend()
        
        # Subplot 3: Recompute Start Indices
        axes[2].set_title('CDF of Recompute Start Indices')
        axes[2].set_xlabel('Recompute Start Index (within request)')
        axes[2].set_xscale('symlog') # symlog handles 0 well
        axes[2].grid(True, linestyle='--', alpha=0.7)
        axes[2].legend()
        
        # Subplot 4: First Recompute Lengths
        axes[3].set_title('CDF of First Recompute Length')
        axes[3].set_xlabel('Recompute Length (blocks)')
        axes[3].set_xscale('log')
        axes[3].grid(True, linestyle='--', alpha=0.7)
        axes[3].legend()
        
        # Subplot 5: Second Recompute Lengths
        axes[4].set_title('CDF of Second Recompute Length')
        axes[4].set_xlabel('Recompute Length (blocks)')
        axes[4].set_xscale('log')
        axes[4].grid(True, linestyle='--', alpha=0.7)
        axes[4].legend()
        
        # Subplot 6: Last Recompute Lengths
        axes[5].set_title('CDF of Last Recompute Length')
        axes[5].set_xlabel('Recompute Length (blocks)')
        axes[5].set_xscale('log')
        axes[5].grid(True, linestyle='--', alpha=0.7)
        axes[5].legend()
        
        # Subplot 7: First Recompute Start Indices
        axes[6].set_title('CDF of First Recompute Start Index')
        axes[6].set_xlabel('Recompute Start Index')
        axes[6].set_xscale('symlog')
        axes[6].grid(True, linestyle='--', alpha=0.7)
        axes[6].legend()
        
        # Subplot 8: Second Recompute Start Indices
        axes[7].set_title('CDF of Second Recompute Start Index')
        axes[7].set_xlabel('Recompute Start Index')
        axes[7].set_xscale('symlog')
        axes[7].grid(True, linestyle='--', alpha=0.7)
        axes[7].legend()
        
        # Subplot 9: Last Recompute Start Indices
        axes[8].set_title('CDF of Last Recompute Start Index')
        axes[8].set_xlabel('Recompute Start Index')
        axes[8].set_xscale('symlog')
        axes[8].grid(True, linestyle='--', alpha=0.7)
        axes[8].legend()
        
        title = f'Segment Analysis: {group_key}'
        if intensity_label:
            title += f'\nCompute intensity: {intensity_label}'
        fig.suptitle(title, fontsize=16)
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        output_img = f'{args.output_dir}/segments_analysis_{group_key}.png'
        plt.savefig(output_img)
        print(f"Saved visualization to {output_img}")
        plt.close(fig)

if __name__ == '__main__':
    main()
