"""
Download SEC EDGAR quarterly company index files (2019-2023).
~20 gzipped files, ~20MB each compressed, ~400MB compressed total.
Columns: CIK, company name, form type, date filed, filename.

Run: python benchmarks/data_analysis/download_sec_edgar.py
Override data dir: DATA_DIR=/path/to/data python ...
"""

import os
import sys
import requests

BASE_URL = "https://www.sec.gov/Archives/edgar/full-index"
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_ROOT, "data", "sec_edgar"))
YEARS = range(2019, 2024)
QUARTERS = range(1, 5)

HEADERS = {"User-Agent": "research-agent miladrzh@gmail.com"}


def download_file(url, dest):
    if os.path.exists(dest):
        print(f"  skip {os.path.basename(dest)} (exists)")
        return
    print(f"  downloading {os.path.basename(dest)} ...", end="", flush=True)
    r = requests.get(url, stream=True, timeout=120, headers=HEADERS)
    r.raise_for_status()
    total = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
            total += len(chunk)
            print(f"\r  downloading {os.path.basename(dest)} ... {total >> 20} MB", end="", flush=True)
    print(f"\r  {os.path.basename(dest)}: {total >> 20} MB")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"Data dir: {DATA_DIR}\n")
    for year in YEARS:
        for q in QUARTERS:
            filename = f"{year}_QTR{q}_company.gz"
            url = f"{BASE_URL}/{year}/QTR{q}/company.gz"
            dest = os.path.join(DATA_DIR, filename)
            try:
                download_file(url, dest)
            except Exception as e:
                print(f"  FAILED {filename}: {e}", file=sys.stderr)
    print("\nDone.")


if __name__ == "__main__":
    main()
