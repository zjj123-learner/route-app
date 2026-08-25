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
    return brute, h, s


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
    print("规模 实例 暴力最优 启发式 模拟退火 启发式差距% 退火差距%")
    h_gaps, s_gaps = [], []
    big_h_wins = big_s_wins = big_tie = 0
    for n in sizes:
        for k in range(inst):
            brute, h, s = run_one(n, rng, opts)
            if brute is not None:
                hg, sg = pct(h, brute), pct(s, brute)
                h_gaps.append(hg)
                s_gaps.append(sg)
                print("%d   %d   %d   %d   %d   %.2f%%   %.2f%%" % (n, k + 1, brute, h, s, hg, sg))
            else:
                if s < h:
                    big_s_wins += 1
                elif h < s:
                    big_h_wins += 1
                else:
                    big_tie += 1
                print("%d   %d   -   %d   %d   -   -" % (n, k + 1, h, s))

    print("\n=== 汇总 ===")
    if h_gaps:
        print("离暴力最优平均差距: 启发式 %.2f%%  模拟退火 %.2f%%" % (sum(h_gaps) / len(h_gaps), sum(s_gaps) / len(s_gaps)))
    print("大规模(9+任务): 退火更优 %d 次 / 启发式更优 %d 次 / 打平 %d 次" % (big_s_wins, big_h_wins, big_tie))


if __name__ == "__main__":
    main()
