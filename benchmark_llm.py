# -*- coding: utf-8 -*-
"""评测: 规则解析 vs LLM 解析 (50 条人工标注语料)

用法:
  python benchmark_llm.py              有 LLM key 就跑全量对比
  python benchmark_llm.py --rule       只跑规则基线(没配 key 时默认只跑规则)
  python benchmark_llm.py --limit 10   只跑前 10 条(调试用)

规则解析完全离线; LLM 解析需要 config.py 里配好:
  set LLM_API_KEY=sk-xxx
  set LLM_BASE_URL=https://api.deepseek.com/v1
  set LLM_MODEL=deepseek-chat
"""
import sys

from corpus import ENTRIES
from parser import parse_line
from llm_parser import llm_parse_line, llm_available

FIELDS = ["day", "earliest", "latest", "fixed", "deadline", "priority", "duration", "place"]


def absolute(entry):
    """把语料里'当天分钟'抬成绝对分钟(day*1440 + 分钟), 和解析器输出一致"""
    out = dict(entry)
    base = entry["day"] * 1440
    for f in ("earliest", "latest", "fixed", "deadline"):
        if entry.get(f) is not None:
            out[f] = entry[f] + base
    return out


def task_fields(task):
    return {f: task.get(f) for f in FIELDS}


def score_one(expected, got):
    """返回 (是否全对, {字段: 对不对})"""
    right = {}
    for f in FIELDS:
        right[f] = expected[f] == got.get(f)
    return all(right.values()), right


def run_rule(entries):
    results = []
    for e in entries:
        exp = absolute(e)
        got = parse_line(e["text"])
        ok, right = score_one(exp, task_fields(got))
        results.append((e["text"], ok, right, exp, got))
    return results


def run_llm(entries):
    results = []
    for e in entries:
        exp = absolute(e)
        got = llm_parse_line(e["text"])
        if got is None:
            results.append((e["text"], False,
                            {f: False for f in FIELDS}, exp, None))
            continue
        ok, right = score_one(exp, task_fields(got))
        results.append((e["text"], ok, right, exp, got))
    return results


def print_summary(results, label):
    n = len(results)
    ok = sum(1 for r in results if r[1])
    print(label + ": 全对 %d/%d (%.1f%%)" % (ok, n, ok / n * 100 if n else 0))
    field_ok = {f: sum(1 for r in results if r[2][f]) for f in FIELDS}
    for f in FIELDS:
        print("    %-10s %2d/%d" % (f, field_ok[f], n))


def print_misses(results, label, n=8):
    misses = [r for r in results if not r[1]]
    print(label + " 没全对 %d 条:" % len(misses))
    for text, ok, right, exp, got in misses[:n]:
        bad = [f for f in FIELDS if not right[f]]
        print("    - %s  [错在: %s]" % (text, ",".join(bad)))


def main():
    args = sys.argv[1:]
    limit = None
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    entries = ENTRIES[:limit] if limit else ENTRIES
    print("语料共 %d 条\n" % len(entries))

    rule = run_rule(entries)
    print_summary(rule, "规则解析")
    print_misses(rule, "规则解析")

    if "--rule" in args or not llm_available():
        if not llm_available():
            print("\n[未配置 LLM_API_KEY, 跳过 LLM 对比。配好环境变量后重跑即可。]")
        return

    print()
    llm = run_llm(entries)
    print_summary(llm, "LLM 解析")
    print_misses(llm, "LLM 解析")


if __name__ == "__main__":
    main()
