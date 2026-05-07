"""
RAG agent task definitions for BrowseComp (OpenAI, 2025).

BrowseComp is a benchmark of 1,266 hard browsing questions designed to require
extensive web search and page reading. Questions ship encrypted on OpenAI's
public blob storage; each row carries its own decryption password ("canary")
to prevent accidental training contamination.

Citation:
  Wei et al., "BrowseComp: A Simple Yet Challenging Benchmark for Browsing
  Agents", OpenAI, 2025. arXiv:2504.12516.
  Eval harness + decrypt: https://github.com/openai/simple-evals
"""

import base64
import csv
import hashlib
import io
import os
import urllib.request


CSV_URL = "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv"
CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "benchmarks", "browsecomp", "browse_comp_test_set.csv")


def _t(task_id, prompt, gold_answer=None):
    return {
        "id": task_id,
        "agent_type": "rag",
        "benchmark": "browsecomp",
        "prompt": prompt,
        "gold_answer": gold_answer,
    }


def _derive_key(password: str, length: int) -> bytes:
    h = hashlib.sha256()
    h.update(password.encode())
    key = h.digest()
    return key * (length // len(key)) + key[: length % len(key)]


def _decrypt(ciphertext_b64: str, password: str) -> str:
    enc = base64.b64decode(ciphertext_b64)
    key = _derive_key(password, len(enc))
    dec = bytes(a ^ b for a, b in zip(enc, key))
    return dec.decode("utf-8")


def _fetch_csv() -> str:
    """Return path to a local CSV cache; download if missing."""
    abs_cache = os.path.abspath(CACHE_PATH)
    if os.path.exists(abs_cache):
        return abs_cache
    os.makedirs(os.path.dirname(abs_cache), exist_ok=True)
    print(f"[browsecomp] downloading dataset CSV to {abs_cache}")
    urllib.request.urlretrieve(CSV_URL, abs_cache)
    return abs_cache


def load_tasks(max_tasks=None):
    """
    Load BrowseComp tasks. Decrypts question and answer per row using the
    row's canary password. Returns up to `max_tasks` tasks.
    """
    tasks = []
    try:
        path = _fetch_csv()
        with open(path, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for i, row in enumerate(reader):
                canary = row.get("canary") or row.get("Canary")
                problem_enc = row.get("problem") or row.get("Problem")
                answer_enc = row.get("answer") or row.get("Answer")
                if not (canary and problem_enc):
                    continue
                try:
                    question = _decrypt(problem_enc, canary)
                    answer = _decrypt(answer_enc, canary) if answer_enc else None
                except Exception as e:
                    print(f"[browsecomp] decrypt failed on row {i}: {e}")
                    continue
                tid = f"browsecomp_{i:04d}"
                tasks.append(_t(tid, question, answer))
                if max_tasks is not None and len(tasks) >= max_tasks:
                    break
    except Exception as e:
        print(f"[browsecomp] load failed ({e}); returning empty task list.")
    return tasks


TASKS = load_tasks()
TASKS_BY_ID = {t["id"]: t for t in TASKS}
