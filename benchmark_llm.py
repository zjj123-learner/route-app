# -*- coding: utf-8 -*-
"""评测: 规则解析 vs LLM 解析 (50 条训练语料 + 15 条泛化集)

用法:
  python benchmark_llm.py              有 LLM key 就跑全量对比(训练集 50 条)
  python benchmark_llm.py --gen        跑泛化集 15 条(不在语料里的新表达, 测泛化)
  python benchmark_llm.py --rule       只跑规则基线(没配 key 时默认只跑规则)
  python benchmark_llm.py --limit 10   只跑前 10 条(调试用)

规则解析完全离线; LLM 解析需要 config.py 里配好:
  set DEEPSEEK_API_KEY=sk-xxx
  set LLM_BASE_URL=https://api.deepseek.com/v1
  set LLM_MODEL=deepseek-v4-flash

实验设计: 规则解析是固定对照组(不调优), 调优只针对 LLM 路径(prompt + 规范化).
训练集 50 条看"背题能力", 泛化集 15 条看"泛化能力"——后者更能说明 LLM 的价值.
"""
import sys

from corpus import ENTRIES, GEN_ENTRIES
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
    use_gen = "--gen" in args
    entries = GEN_ENTRIES if use_gen else ENTRIES
    limit = None
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    entries = entries[:limit] if limit else entries
    if use_gen:
        print("泛化集共 %d 条(不在训练语料里, 测没见过的新表达)\n" % len(entries))
    else:
        print("语料共 %d 条\n" % len(entries))

    rule = run_rule(entries)
    print_summary(rule, "规则解析")
    print_misses(rule, "规则解析")

    if "--rule" in args or not llm_available():
        if not llm_available():
            print("规则解析是固定对照组(不调优); 调优只针对 LLM 路径(prompt + 规范化).")
            print("\n[未配置 DEEPSEEK_API_KEY/LLM_API_KEY, 跳过 LLM 对比。配好环境变量后重跑即可。]")
        return

    print()
    llm = run_llm(entries)
    print_summary(llm, "LLM 解析")
    print_misses(llm, "LLM 解析")


if __name__ == "__main__":
    main()