import sys
import glob
import re
import matplotlib.pyplot as plt
import numpy as np
import os
from collections import defaultdict

ALGORITHM_ORDER = [
    "LRU",
    "S3FIFO",
    # "ARC",
    # "GDSF",
    "GDSF_compute",
    # "LHD",
    "LHD_compute",
    # "Belady",
    "BeladyCompute",
    # "Optimal",
]

ALGORITHM_DISPLAY_NAMES = {
    "LRU": "LRU",
    "S3FIFO": "S3FIFO", 
    "ARC": "ARC",
    "GDSF": "GDSF",
    "GDSF_compute": "GDCF",
    "LHD": "LHD",
    "LHD_compute": "LCD",
    "Belady": "Belady",
    "BeladyCompute": "Belady Compute",
    "Optimal": "Optimal"
}

def get_algorithm_display_name(algo_name):
    """Convert algorithm name to human-friendly text."""
    return ALGORITHM_DISPLAY_NAMES.get(algo_name, algo_name.replace('_', ' ').title())

def analyze_file(filename):
    print(f"\nAnalyzing {filename} ...")
    hole_lengths = []
    hole_indices = []
    holes_per_request = []
    
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            # Format: Request 1: (idx: 5, len: 2) (idx: 12, len: 1)
            matches = re.findall(r'\(idx:\s*(\d+),\s*len:\s*(\d+)\)', line)
            
            holes_per_request.append(len(matches))
            for idx, length in matches:
                hole_indices.append(int(idx))
                hole_lengths.append(int(length))
                
    if not holes_per_request:
        print("  No requests found.")
        return None
        
    avg_holes_per_req = np.mean(holes_per_request)
    max_holes_per_req = np.max(holes_per_request) if holes_per_request else 0
    avg_hole_len = np.mean(hole_lengths) if hole_lengths else 0
    max_hole_len = np.max(hole_lengths) if hole_lengths else 0
    total_holes = len(hole_lengths)
    
    print(f"  Total Requests: {len(holes_per_request)}")
    print(f"  Total Holes: {total_holes}")
    print(f"  Avg holes per request: {avg_holes_per_req:.2f}")
    print(f"  Max holes per request: {max_holes_per_req}")
    if total_holes > 0:
        print(f"  Avg hole length: {avg_hole_len:.2f}")
        print(f"  Max hole length: {max_hole_len}")
        
    # Extract dataset, cache size, and algo name
    basename = os.path.basename(filename).replace('holes_', '').replace('.txt', '')
    if '_cache' in basename:
        dataset, rest = basename.split('_cache')
        if '_' in rest:
            cache_size, algo_name = rest.split('_', 1)
        else:
            cache_size, algo_name = rest, "unknown"
    else:
        # Fallback for old formatting
        parts = basename.rsplit('_', 1)
        dataset = parts[0] if len(parts) > 1 else 'unknown_dataset'
        cache_size = "unknown"
        algo_name = parts[-1] if len(parts) > 0 else 'unknown_algo'
        
    return {
        'filename': filename,
        'dataset': dataset,
        'cache_size': cache_size,
        'algo_name': algo_name,
        'hole_lengths': hole_lengths,
        'hole_indices': hole_indices,
        'holes_per_request': holes_per_request,
        'avg_hole_len': avg_hole_len,
        'avg_holes_per_req': avg_holes_per_req,
        'max_holes_per_req': max_holes_per_req
    }

def plot_cdf(ax, data, label):
    sorted_data = np.sort(data)
    yvals = np.arange(1, len(sorted_data) + 1) / len(sorted_data)
    ax.plot(sorted_data, yvals, label=label, linewidth=2)

def main():
    files = sys.argv[1:] if len(sys.argv) > 1 else glob.glob('holes_logs/holes_*.txt')
    if not files:
        print("No holes_*.txt files found in holes_logs/. Please run the evaluate program first.")
        return
        
    grouped_results = defaultdict(list)
    for f in files:
        res = analyze_file(f)
        if res and res['hole_lengths']:
            group_key = f"{res['dataset']}_cache{res['cache_size']}"
            grouped_results[group_key].append(res)
            
    if not grouped_results:
        print("\nNo valid hole data to plot across the provided files.")
        return
        
    os.makedirs('plots/holes', exist_ok=True)
    
    for group_key, results in grouped_results.items():
        print(f"\nGenerating visualizations for {group_key}...")
        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        
        # Filter and sort results based on ALGORITHM_ORDER
        results = [res for res in results if res['algo_name'] in ALGORITHM_ORDER]
        results.sort(key=lambda x: ALGORITHM_ORDER.index(x['algo_name']))
        
        for res in results:
            display_name = get_algorithm_display_name(res['algo_name'])
            
            non_zero_holes = [h for h in res['holes_per_request'] if h > 0]
            if non_zero_holes:
                label = f"{display_name} (Avg={res['avg_holes_per_req']:.2f}, Max={res['max_holes_per_req']})"
                plot_cdf(axes[0], non_zero_holes, label)
                
            if res['hole_lengths']:
                label = f"{display_name} (AvgLen={res['avg_hole_len']:.1f})"
                plot_cdf(axes[1], res['hole_lengths'], label)
                
            if res['hole_indices']:
                plot_cdf(axes[2], res['hole_indices'], display_name)
                
        # Subplot 1: Holes per request
        axes[0].set_title('CDF of Holes per Request (>0)')
        axes[0].set_xlabel('Number of Holes')
        axes[0].grid(True, linestyle='--', alpha=0.7)
        axes[0].legend()
        
        # Subplot 2: Hole Lengths
        axes[1].set_title('CDF of Hole Lengths')
        axes[1].set_xlabel('Hole Length (blocks)')
        axes[1].set_xscale('log') # Use log scale because lengths vary greatly
        axes[1].grid(True, linestyle='--', alpha=0.7)
        axes[1].legend()
        
        # Subplot 3: Hole Start Indices
        axes[2].set_title('CDF of Hole Start Indices')
        axes[2].set_xlabel('Hole Start Index (within request)')
        axes[2].set_xscale('symlog') # symlog handles 0 well
        axes[2].grid(True, linestyle='--', alpha=0.7)
        axes[2].legend()
        
        fig.suptitle(f'Hole Analysis: {group_key}', fontsize=16)
        plt.tight_layout(rect=[0, 0, 1, 0.95])
        output_img = f'plots/holes/holes_analysis_{group_key}.png'
        plt.savefig(output_img)
        print(f"Saved visualization to {output_img}")
        plt.close(fig)

if __name__ == '__main__':
    main()
