"""
Smoke test: run one task per agent type and print turn-by-turn output.
Use this to sanity-check agent behavior with a live vLLM instance.

Usage:
    python benchmarks/smoke_test.py --vllm-url http://localhost:8000/v1 \
        --model meta-llama/Llama-3.1-8B-Instruct

Agents tested:
    data_analysis  ->  taxi_04 (tip analysis, expects python_exec with pandas)
    sql            ->  first available task
    rag            ->  first available task (Brave key optional, uses fallback)
    swe_bench      ->  toy bug: off-by-one in a small Python function
"""

import argparse
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _hr(label):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print('='*60)


def _print_turn(turn_id, tool_calls):
    for tc in tool_calls:
        name = tc['tool_name']
        args = tc['args']
        dur  = tc['duration_ms']
        result = tc.get('_result_preview', '')
        print(f"  [turn {turn_id}] {name}({_args_summary(name, args)})  {dur}ms")
        if result:
            for line in result.splitlines()[:8]:
                print(f"           {line}")
            if result.count('\n') > 8:
                print("           ...")


def _args_summary(name, args):
    if name == 'python_exec':
        code = args.get('code', '')
        return repr(code[:80] + '...' if len(code) > 80 else code)
    if name == 'sql_exec':
        q = args.get('query', '')
        return repr(q[:80] + '...' if len(q) > 80 else q)
    if name == 'web_search':
        return repr(args.get('query', ''))
    if name == 'fetch_url':
        return repr(args.get('url', ''))
    if name == 'bash_exec':
        return repr(args.get('command', ''))
    if name == 'view_file':
        return repr(args.get('path', ''))
    if name == 'edit_file':
        return f"path={args.get('path')!r}"
    if name == 'search_dir':
        return repr(args.get('pattern', ''))
    return str(args)[:60]


def patched_run_task(task, model, vllm_url, max_turns):
    """Thin wrapper around run_task that prints each turn as it happens."""
    import json as _json
    from agent.tools import dispatch_tool, clear_namespace
    from agent.tracer import Tracer
    from agent.loop import SYSTEM_PROMPTS, TOOL_SCHEMAS
    from openai import OpenAI

    agent_type = task['agent_type']
    client = OpenAI(base_url=vllm_url, api_key='local-token')
    tracer = Tracer(task, model)

    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPTS[agent_type]},
        {'role': 'user',   'content': task['prompt']},
    ]
    tools = TOOL_SCHEMAS[agent_type]

    print(f"\n  Prompt (first 200 chars):\n  {task['prompt'][:200]}...")

    for turn_id in range(max_turns):
        t_request = time.time()
        response = client.chat.completions.create(
            model=model, messages=messages, tools=tools,
            tool_choice='auto', temperature=0,
        )
        t_received = time.time()
        choice   = response.choices[0]
        msg      = choice.message
        msg_dict = msg.model_dump(exclude_unset=True)
        msg_dict.setdefault('content', '')
        messages.append(msg_dict)

        if choice.finish_reason == 'length':
            print(f"  [turn {turn_id}] context_length_exceeded")
            tracer.finish('timeout', 'context_length_exceeded')
            break

        if not msg.tool_calls:
            answer = (msg.content or '').strip()
            print(f"  [turn {turn_id}] FINAL: {answer[:300]}")
            tracer.finish('success', answer)
            break

        turn_tcs = []
        for tc in msg.tool_calls:
            args   = _json.loads(tc.function.arguments)
            t0     = time.time()
            result = dispatch_tool(tc.function.name, args, task)
            t1     = time.time()
            turn_tcs.append({
                'tool_call_id': tc.id,
                'tool_name':    tc.function.name,
                'args':         args,
                'result':       result,
                '_result_preview': result,
                'start_ts':     t0,
                'end_ts':       t1,
                'duration_ms':  int((t1 - t0) * 1000),
            })
            messages.append({'role': 'tool', 'tool_call_id': tc.id, 'content': result})

        _print_turn(turn_id, turn_tcs)
        tracer.record_turn(turn_id, t_request, t_received, msg.content or '', turn_tcs, response.usage)
    else:
        tracer.finish('timeout', '')

    clear_namespace(task['id'])
    return tracer.to_dict()


def make_swe_task(workspace):
    """Toy swe_bench task: fix an off-by-one in a small function."""
    os.makedirs(workspace, exist_ok=True)
    # write the buggy file
    with open(os.path.join(workspace, 'utils.py'), 'w') as f:
        f.write(
            "def first_n(lst, n):\n"
            "    \"\"\"Return the first n elements of lst.\"\"\"\n"
            "    return lst[:n - 1]  # bug: should be lst[:n]\n"
        )
    with open(os.path.join(workspace, 'test_utils.py'), 'w') as f:
        f.write(
            "from utils import first_n\n\n"
            "def test_first_n():\n"
            "    assert first_n([1, 2, 3, 4], 3) == [1, 2, 3]\n"
        )
    return {
        'id':                'smoke__off-by-one',
        'agent_type':        'swe_bench',
        'benchmark':         'swe_bench_lite',
        'repo':              'smoke/test',
        'base_commit':       'HEAD',
        'problem_statement': 'first_n returns one fewer element than requested',
        'fail_to_pass':      ['test_utils.py::test_first_n'],
        'pass_to_pass':      [],
        'workspace_dir':     workspace,
        'prompt': (
            "You are fixing a bug in a small Python repository.\n\n"
            "Issue: `first_n(lst, n)` returns n-1 elements instead of n.\n\n"
            "The failing test is: test_utils.py::test_first_n\n\n"
            "Fix the bug and verify the test passes."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vllm-url', default='http://localhost:8000/v1')
    parser.add_argument('--model',    default='meta-llama/Llama-3.1-8B-Instruct')
    parser.add_argument('--agents',   default='data_analysis,sql,rag,swe_bench',
                        help='Comma-separated list of agents to test')
    parser.add_argument('--max-turns', type=int, default=8)
    args = parser.parse_args()

    agents = [a.strip() for a in args.agents.split(',')]

    # --- data_analysis ---
    if 'data_analysis' in agents:
        _hr('AGENT: data_analysis  (task: taxi_04)')
        from tasks.data_analysis import TASKS_BY_ID
        trace = patched_run_task(
            TASKS_BY_ID['taxi_04'], args.model, args.vllm_url, args.max_turns
        )
        print(f"\n  outcome={trace['outcome']}  turns={len(trace['turns'])}")

    # --- sql ---
    if 'sql' in agents:
        _hr('AGENT: sql  (task: first available)')
        from tasks.sql import TASKS
        trace = patched_run_task(
            TASKS[0], args.model, args.vllm_url, args.max_turns
        )
        print(f"\n  outcome={trace['outcome']}  turns={len(trace['turns'])}")

    # --- rag ---
    if 'rag' in agents:
        _hr('AGENT: rag  (task: first available)')
        from tasks.rag import TASKS
        trace = patched_run_task(
            TASKS[0], args.model, args.vllm_url, args.max_turns
        )
        print(f"\n  outcome={trace['outcome']}  turns={len(trace['turns'])}")

    # --- swe_bench ---
    if 'swe_bench' in agents:
        _hr('AGENT: swe_bench  (task: toy off-by-one bug)')
        ws = tempfile.mkdtemp(prefix='smoke_swe_')
        task = make_swe_task(ws)
        try:
            trace = patched_run_task(
                task, args.model, args.vllm_url, args.max_turns
            )
            print(f"\n  outcome={trace['outcome']}  turns={len(trace['turns'])}")
        finally:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)

    print('\nSmoke test done.')


if __name__ == '__main__':
    main()
