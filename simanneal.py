# -*- coding: utf-8 -*-
"""模拟退火(Simulated Annealing)求解器

和 optimizer.py 的手写启发式(最近邻+2-opt)做对比, 接口保持一致:
  输入 tasks + start + options, 输出和 optimize_route 完全相同的结构.

原理:
  从一个初始解出发, 每次随机"扰动"出一个邻居解:
   - 邻居比当前好  -> 直接接受
   - 邻居比当前差  -> 也以一定概率接受(温度越高概率越大)
  温度随迭代逐渐降低, "偶尔接受差解"的概率越来越小, 最终收敛.
  正是这一步让算法能跳出 2-opt 容易卡住的局部最优.

关键参数(都可以调):
  t0    初始温度. 默认取初始解总代价的 10%, 让开头能接受一部分差解.
        太小 -> 退火退化成爬山, 跳不出局部最优; 太大 -> 前期纯随机乱走.
  iters 迭代次数. 越多效果越好, 但更慢.
  alpha 降温系数. 默认按 iters 自动算, 保证最后温度约等于 0.01.

实验扩展(给 benchmark/learn_init 用, 默认不影响线上行为):
  init_order  传入初始解(自由任务的顺序, list of task), 替代默认的最近邻.
              用于"学习型初始解"对比: 换一个起点, 看退火收敛到哪.
  curve=True  返回收敛曲线 result["curve"] = [[迭代步, 历史最优total], ...].
"""
import math
import random

from optimizer import DEFAULTS, evaluate_order, nearest_neighbor, optimize_route


def _build_base(tasks, fixed_positions):
    """把锁定任务放进固定槽位, 返回 (base, free_tasks, free_slots).
    base: 长度=n 的槽位数组, 锁定任务占位, 空槽是 None.
    free_tasks/free_slots: 没锁定的任务和它们能放的槽位.
    这样退火只折腾 free 部分, 锁定任务永不移动."""
    n = len(tasks)
    if fixed_positions:
        if len(set(fixed_positions.values())) != len(fixed_positions):
            raise ValueError("锁定位置冲突")
        base = [None] * n
        for ti, slot in fixed_positions.items():
            base[slot] = tasks[ti]
        free_tasks = [t for i, t in enumerate(tasks) if i not in fixed_positions]
        free_slots = [i for i, x in enumerate(base) if x is None]
    else:
        base = [None] * n
        free_tasks = list(tasks)
        free_slots = list(range(n))
    return base, free_tasks, free_slots


def _full_order(base, free_slots, free):
    """把自由任务的当前排列填回槽位数组, 得到完整顺序"""
    cand = list(base)
    for slot, task in zip(free_slots, free):
        cand[slot] = task
    return cand


def _neighbor(free, rng):
    """随机扰动: 一半概率交换两个位置, 一半概率反转一段(2-opt 式)"""
    n = len(free)
    if n < 2:
        return list(free)
    i = rng.randrange(n)
    j = rng.randrange(n)
    if i == j:
        j = (j + 1) % n
    if i > j:
        i, j = j, i
    if rng.random() < 0.5:
        cand = list(free)
        cand[i], cand[j] = cand[j], cand[i]
    else:
        cand = free[:i] + free[i:j + 1][::-1] + free[j + 1:]
    return cand


def sa_route(tasks, start, options=None, fixed_positions=None, seed=None,
             t0=None, alpha=None, iters=None, init_order=None, curve=False):
    """模拟退火主函数, 返回结构和 optimize_route 一致.
    seed: 随机种子, 传了就能复现.
    t0/alpha/iters 见文件顶部说明, 不传用自适应默认值.
    init_order: 自定义初始解(自由任务的顺序), 不传用最近邻.
    curve: 是否返回收敛曲线 result["curve"] = [[步数, 历史最优], ...]."""
    opts = dict(DEFAULTS)
    if options:
        opts.update(options)

    n = len(tasks)
    if n == 0:
        return {"order": [], "arrivals": [], "stats": None, "method": "simanneal"}

    base, free_tasks, free_slots = _build_base(tasks, fixed_positions)

    # 没有自由任务(全锁定/只剩一个位置)时顺序已定, 直接算评分
    if len(free_tasks) <= 1:
        best_order = _full_order(base, free_slots, list(free_tasks))
        ev = evaluate_order(best_order, start, opts)
        res = {
            "order": best_order,
            "arrivals": ev["arrivals"],
            "stats": {"total": ev["total"], "travel": ev["travel"],
                      "wait": ev["wait"], "penalty": ev["penalty"]},
            "method": "simanneal",
        }
        if curve:
            res["curve"] = [[1, round(ev["total"], 2)]]
        return res

    # 初始解: 默认最近邻构造(保证起点不差), 也可以外部注入(学习型初始解)
    if init_order is not None:
        if len(init_order) != len(free_tasks) or {id(t) for t in init_order} != {id(t) for t in free_tasks}:
            raise ValueError("init_order 必须是 free 任务的完整排列")
        cur_free = list(init_order)
    else:
        cur_free = nearest_neighbor(free_tasks, start, opts)
    best_free = list(cur_free)
    best_total = evaluate_order(_full_order(base, free_slots, best_free), start, opts)["total"]
    cur_total = best_total

    if iters is None:
        iters = max(4000, n * 1200)
    if t0 is None:
        # 自适应: 初始温度取初始总代价的 10%, 不同规模/惩罚强度都适用
        t0 = max(10.0, best_total * 0.1)
    if alpha is None:
        alpha = (0.01 / t0) ** (1.0 / iters)

    rng = random.Random(seed)
    curve_pts = []
    sample_step = max(1, iters // 200)
    for k in range(iters):
        T = t0 * (alpha ** k)
        cand = _neighbor(cur_free, rng)
        total = evaluate_order(_full_order(base, free_slots, cand), start, opts)["total"]
        delta = total - cur_total
        if delta < 0 or rng.random() < math.exp(-delta / T):
            cur_free, cur_total = cand, total
            if total < best_total:
                best_free, best_total = list(cand), total
        if curve and k % sample_step == 0:
            curve_pts.append([k + 1, round(best_total, 2)])
    if curve:
        curve_pts.append([iters, round(best_total, 2)])

    best_order = _full_order(base, free_slots, best_free)
    ev = evaluate_order(best_order, start, opts)
    res = {
        "order": best_order,
        "arrivals": ev["arrivals"],
        "stats": {"total": ev["total"], "travel": ev["travel"],
                  "wait": ev["wait"], "penalty": ev["penalty"]},
        "method": "simanneal",
    }
    if curve:
        res["curve"] = curve_pts
    return res


def sa_compare(tasks, start, options=None, fixed_positions=None, seed=None):
    """对比一次: 返回 (启发式结果, 模拟退火结果)"""
    h = optimize_route(tasks, start, options, fixed_positions)
    s = sa_route(tasks, start, options, fixed_positions, seed=seed)
    return h, s


if __name__ == "__main__":
    from parser import parse_tasks

    demo = """上午9点去银行办卡
下午3点去学校接孩子放学（重要）
顺便去超市买菜
晚上7点前从驿站取快递回家"""
    tasks = parse_tasks(demo)
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    for i, t in enumerate(tasks):
        t["lat"] = 31.23 + i * 0.01
        t["lng"] = 121.47 + i * 0.01
    h, s = sa_compare(tasks, start, seed=1)
    print("启发式  total:", h["stats"]["total"], " 方法:", h["method"])
    print("退火    total:", s["stats"]["total"], " 方法:", s["method"])
    for i, st in enumerate(s["arrivals"]):
        hh, mm = divmod(st["arrival"], 60)
        print(f"{i+1}. {st['task']['name']}  到达 {hh:02d}:{mm:02d}")
