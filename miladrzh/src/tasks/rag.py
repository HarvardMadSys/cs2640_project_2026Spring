"""
RAG agent task definitions for the GAIA benchmark (all levels: 1, 2, 3).

Each task is a GAIA question requiring multi-hop web search + page reading.
The 5 hardcoded fallback questions are always prepended at the beginning,
followed by GAIA questions loaded from HuggingFace, capped at 50 total.

Data source: gaia-benchmark/GAIA on HuggingFace (gated; requires HF token
and accepted terms at https://huggingface.co/datasets/gaia-benchmark/GAIA).
Falls back to just the 5 hardcoded examples if HuggingFace is not accessible.
"""

import os


def _t(task_id, prompt):
    return {
        "id": task_id,
        "agent_type": "rag",
        "benchmark": "gaia",
        "prompt": prompt,
    }


_FALLBACK_TASKS = [
    _t("gaia_fallback_01",
       "How many days passed between the publication of the paper that introduced "
       "the Transformer architecture ('Attention Is All You Need') and the public "
       "release of GPT-2 by OpenAI?"),

    _t("gaia_fallback_02",
       "Which country won the most gold medals at the 2020 Summer Olympics "
       "(held in 2021), and how many total medals did that country win across "
       "all three categories?"),

    _t("gaia_fallback_03",
       "What is the name of the river that flows through the city where the "
       "2024 Summer Olympics opening ceremony took place, and what is its "
       "approximate length in kilometers?"),

    _t("gaia_fallback_04",
       "Find the author of the 2023 Booker Prize-winning novel. What other "
       "literary awards has that author won, and in what year was their first "
       "major award?"),

    _t("gaia_fallback_05",
       "What is the population of the capital city of the country that hosted "
       "the most recent G20 summit before 2024? Use the most recent census data "
       "available."),
]


def load_tasks(max_tasks=50):
    """
    Load all GAIA tasks (Levels 1, 2, 3) from HuggingFace, prepended by the
    5 hardcoded fallback questions. Total capped at `max_tasks`.
    Falls back to just the hardcoded examples if HF is unavailable.
    """
    tasks = list(_FALLBACK_TASKS)

    try:
        from datasets import load_dataset
        ds = load_dataset("gaia-benchmark/GAIA", "2023_all", trust_remote_code=True)

        for split_name in ("validation", "test"):
            if split_name not in ds:
                continue
            for i, row in enumerate(ds[split_name]):
                try:
                    level = int(row.get("Level", 0))
                except (TypeError, ValueError):
                    continue
                if level not in (1, 2, 3):
                    continue
                raw_id = row.get("task_id", i)
                task_id = f"gaia_L{level}_{split_name}_{raw_id}"
                tasks.append(_t(task_id, row["Question"]))
                if len(tasks) >= max_tasks:
                    return tasks
    except Exception as e:
        print(f"[rag] HuggingFace load failed ({e}), using fallback tasks only.")

    return tasks


TASKS = load_tasks()
TASKS_BY_ID = {t["id"]: t for t in TASKS}
