# -*- coding: utf-8 -*-
"""评测: 暴力最优 vs 最近邻+2-opt vs 模拟退火

用法:
  python benchmark.py         完整跑一遍(5~12 个任务, 每个规模 5 个实例)
  python benchmark.py smoke   快速冒烟(3 个规模, 每规模 2 个实例, 验证能跑)

随机生成任务集, 距离用直线估算(不消耗高德配额), 固定随机种子可复现.
小规模(n<=8)能算出暴力最优, 用来量化两个算法的"离最优差距";
大规模(9~12)暴力不可行, 直接对比启发式 vs 模拟退火谁更好.
"""
import random
import sys

from optimizer import optimize_route
from simanneal import sa_route
from genetic import ga_route

HOME = {"name": "家", "lat": 31.23, "lng": 121.47}
SEED = 20260825


def make_task(i, rng):
    """随机生成一个任务, 字段结构和 parser.py 产出一致"""
    task = {
        "name": "任务" + str(i),
        "place": None,
        "lat": round(rng.uniform(31.0, 31.5), 6),
        "lng": round(rng.uniform(121.0, 121.8), 6),
        "priority": rng.choice([1, 2, 2, 2, 3]),
        "duration": rng.choice([20, 30, 30, 60, 90]),
        "earliest": None,
        "latest": None,
        "fixed": None,
        "deadline": None,
        "day": 0,
    }
    r = rng.random()
    if r < 0.2:
        # 固定预约: 和 parser.py 里 FIXED_VERBS 的行为一致
        t = rng.randint(480, 1380)
        task["fixed"] = t
        task["earliest"] = t
        task["latest"] = t + 180
    elif r < 0.9:
        t = rng.randint(480, 1200)
        task["earliest"] = t
        task["latest"] = min(1439, t + rng.choice([60, 120, 240]))
    if rng.random() < 0.3:
        task["deadline"] = min(1439, (task["latest"] or 1200) + rng.randint(0, 120))
    return task


def make_instance(n, rng):
    return [make_task(i, rng) for i in range(n)]


def run_one(n, rng, opts):
    """返回 (暴力最优 total 或 None, 启发式 total, 退火 total)"""
    tasks = make_instance(n, rng)
    brute = None
    if n <= 8:
        brute = optimize_route(tasks, HOME, dict(opts))["stats"]["total"]
    h = optimize_route(tasks, HOME, {**opts, "max_brute_force": 0})["stats"]["total"]
    s = sa_route(tasks, HOME, dict(opts), seed=n * 100 + 7)["stats"]["total"]
    g = ga_route(tasks, HOME, dict(opts), seed=n * 100 + 7)["stats"]["total"]
    return brute, h, s, g


def pct(better, base):
    if base:
        return (better - base) / base * 100
    return 0.0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    if mode == "smoke":
        sizes, inst = [5, 8, 10], 2
    else:
        sizes, inst = [5, 6, 7, 8, 9, 10, 11, 12], 5

    rng = random.Random(SEED)
    opts = {"mode": "walk"}
    print("规模 实例 暴力最优 启发式 模拟退火 遗传 启发式差距% 退火差距% 遗传差距%")
    h_gaps, s_gaps, g_gaps = [], [], []
    big_wins = {"heuristic": 0, "simanneal": 0, "genetic": 0, "tie": 0}
    for n in sizes:
        for k in range(inst):
            brute, h, s, g = run_one(n, rng, opts)
            if brute is not None:
                hg, sg, gg = pct(h, brute), pct(s, brute), pct(g, brute)
                h_gaps.append(hg)
                s_gaps.append(sg)
                g_gaps.append(gg)
                print("%d   %d   %d   %d   %d   %d   %.2f%%   %.2f%%   %.2f%%" % (n, k + 1, brute, h, s, g, hg, sg, gg))
            else:
                best_val = min(h, s, g)
                who = [k for k, v in (("heuristic", h), ("simanneal", s), ("genetic", g)) if v == best_val]
                if len(who) == 1:
                    big_wins[who[0]] += 1
                else:
                    big_wins["tie"] += 1
                print("%d   %d   -   %d   %d   %d   -   -   -" % (n, k + 1, h, s, g))

    print("\n=== 汇总 ===")
    if h_gaps:
        print("离暴力最优平均差距: 启发式 %.2f%%  模拟退火 %.2f%%  遗传 %.2f%%" % (
            sum(h_gaps) / len(h_gaps), sum(s_gaps) / len(s_gaps), sum(g_gaps) / len(g_gaps)))
    print("大规模(9+任务)谁最优: 启发式 %d 次 / 模拟退火 %d 次 / 遗传 %d 次 / 打平 %d 次" % (
        big_wins["heuristic"], big_wins["simanneal"], big_wins["genetic"], big_wins["tie"]))


if __name__ == "__main__":
    main()
