"""
RAG agent task definitions for HotpotQA (fullwiki, validation split).

HotpotQA is a multi-hop QA benchmark over Wikipedia. The fullwiki config is
the open-domain setting (no gold paragraphs supplied), suited to a web-search
agent that retrieves passages on its own.

Citation:
  Yang et al., "HotpotQA: A Dataset for Diverse, Explainable Multi-hop
  Question Answering", EMNLP 2018, arXiv:1809.09600.
"""


def _t(task_id, prompt, gold_answer=None):
    return {
        "id": task_id,
        "agent_type": "rag",
        "benchmark": "hotpotqa",
        "prompt": prompt,
        "gold_answer": gold_answer,
    }


def load_tasks(max_tasks=None):
    """
    Load HotpotQA fullwiki validation questions from HuggingFace.
    Returns up to `max_tasks` tasks (None = all 7,405).
    """
    tasks = []
    try:
        from datasets import load_dataset
        ds = load_dataset("hotpot_qa", "fullwiki", trust_remote_code=True)
        split = ds["validation"]
        for i, row in enumerate(split):
            tid = f"hotpotqa_val_{row.get('id', i)}"
            tasks.append(_t(tid, row["question"], row.get("answer")))
            if max_tasks is not None and len(tasks) >= max_tasks:
                break
    except Exception as e:
        print(f"[hotpotqa] HuggingFace load failed ({e}); returning empty task list.")
    return tasks


TASKS = load_tasks()
TASKS_BY_ID = {t["id"]: t for t in TASKS}
