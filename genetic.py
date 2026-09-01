# -*- coding: utf-8 -*-
"""遗传算法(Genetic Algorithm)求解器

和 simanneal.py 同款接口, 输出结构和 optimize_route 完全一致.
原理(模拟"优胜劣汰"):
  1. 种群: 生成一堆候选路线(染色体 = 任务顺序)
  2. 适应度: 评分越低越好(直接复用 optimizer.evaluate_order)
  3. 选择: 锦标赛(随机挑几个, 留下适应度最好的当父母)
  4. 交叉: 顺序交叉 OX(从父亲取一段, 母亲按顺序补全) -> 生出一条子路线
  5. 变异: 小概率交换/反转一段, 保持种群多样性
  6. 精英保留: 每代最好的 2 个原样进下一代, 保证不会退化
重复若干代后, 取历史最优个体.

实验扩展(给 benchmark/learn_init 用, 默认不影响线上行为):
  init_solutions  传入若干初始解(每个是自由任务的顺序 list of task),
                  作为初始种群的种子个体, 其余个体仍随机. 用于对比
                  "随机初始化 vs 最近邻 vs 学习型初始化"的收敛速度.
  curve=True      返回每代历史最优 result["curve"] = [[代数, 最优total], ...].
"""
import random

from optimizer import DEFAULTS, evaluate_order, nearest_neighbor


def _build_base(tasks, fixed_positions):
    """和 simanneal 一样: 锁定任务放进固定槽位, 退火只折腾自由部分"""
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
    """把自由任务排列填回槽位数组, 得到完整顺序"""
    cand = list(base)
    for slot, task in zip(free_slots, free):
        cand[slot] = task
    return cand


def _tournament(scored, k, rng):
    """锦标赛选择: 随机抽 k 个, 返回适应度最好的那条染色体"""
    picks = rng.sample(scored, min(k, len(scored)))
    return min(picks, key=lambda x: x[0])[1]


def _crossover(a, b, rng):
    """顺序交叉(OX): 父亲 a 的一段保持原样, 其余基因按母亲 b 的顺序补全"""
    m = len(a)
    if m < 2:
        return list(a)
    i = rng.randrange(m - 1)
    j = rng.randrange(i + 1, m)
    child = [None] * m
    child[i:j + 1] = a[i:j + 1]
    pos = (j + 1) % m
    for gene in b:
        if gene not in child:
            child[pos] = gene
            pos = (pos + 1) % m
    return child


def _mutate(chromo, rng):
    """变异: 一半概率交换两个位置, 一半概率反转一段"""
    m = len(chromo)
    if m < 2:
        return chromo
    i = rng.randrange(m)
    j = rng.randrange(m)
    if i == j:
        return chromo
    if i > j:
        i, j = j, i
    if rng.random() < 0.5:
        chromo[i], chromo[j] = chromo[j], chromo[i]
    else:
        chromo[i:j + 1] = reversed(chromo[i:j + 1])
    return chromo


def _order_to_indices(order, free_tasks):
    """把 task 对象顺序转成 free_tasks 下标顺序"""
    idx = {id(t): i for i, t in enumerate(free_tasks)}
    return [idx[id(t)] for t in order]


def ga_route(tasks, start, options=None, fixed_positions=None, seed=None,
             pop_size=None, generations=None, mutation_rate=0.15,
             tournament=3, elite=2, init_solutions=None, curve=False):
    """遗传算法主函数, 返回结构和 optimize_route 一致.
    种子(seed)固定即可复现; pop_size/generations 不传用自适应默认值.
    init_solutions: 初始解列表(每个是自由任务的顺序), 注入初始种群.
    curve: 是否返回收敛曲线 result["curve"] = [[代数, 历史最优], ...]."""
    opts = dict(DEFAULTS)
    if options:
        opts.update(options)

    n = len(tasks)
    if n == 0:
        return {"order": [], "arrivals": [], "stats": None, "method": "genetic"}

    base, free_tasks, free_slots = _build_base(tasks, fixed_positions)
    m = len(free_tasks)

    # 没有自由任务(全锁定/只剩一个位置)时顺序已定, 直接算评分
    if m <= 1:
        best_order = _full_order(base, free_slots, list(free_tasks))
        ev = evaluate_order(best_order, start, opts)
        res = {
            "order": best_order,
            "arrivals": ev["arrivals"],
            "stats": {"total": ev["total"], "travel": ev["travel"],
                      "wait": ev["wait"], "penalty": ev["penalty"]},
            "method": "genetic",
        }
        if curve:
            res["curve"] = [[1, round(ev["total"], 2)]]
        return res

    if pop_size is None:
        pop_size = 60
    if generations is None:
        generations = max(60, m * 20)

    rng = random.Random(seed)

    # 初始种群: 默认 1 个最近邻解 + 其余随机; 也可注入若干初始解(学习型初始化)
    nn = nearest_neighbor(free_tasks, start, opts)
    seeds = [_order_to_indices(nn, free_tasks)]
    if init_solutions:
        for sol in init_solutions:
            if len(sol) != m or {id(t) for t in sol} != {id(t) for t in free_tasks}:
                raise ValueError("init_solutions 里每个解必须是 free 任务的完整排列")
            seeds.append(_order_to_indices(sol, free_tasks))
    pop = []
    for i in range(pop_size):
        if i < len(seeds):
            pop.append(list(seeds[i]))
        else:
            chromo = list(range(m))
            rng.shuffle(chromo)
            pop.append(chromo)

    def fitness(chromo):
        order = _full_order(base, free_slots, [free_tasks[i] for i in chromo])
        return evaluate_order(order, start, opts)["total"]

    best_total = None
    best_chromo = None
    curve_pts = []
    for gen in range(generations):
        scored = sorted((fitness(c), c) for c in pop)
        if best_total is None or scored[0][0] < best_total:
            best_total = scored[0][0]
            best_chromo = list(scored[0][1])
        if curve:
            curve_pts.append([gen + 1, round(best_total, 2)])
        # 精英保留 + 锦标赛选择 + 交叉 + 变异, 生成下一代
        next_pop = [list(c) for _, c in scored[:elite]]
        while len(next_pop) < pop_size:
            p1 = _tournament(scored, tournament, rng)
            p2 = _tournament(scored, tournament, rng)
            child = _crossover(p1, p2, rng)
            if rng.random() < mutation_rate:
                child = _mutate(child, rng)
            next_pop.append(child)
        pop = next_pop

    best_order = _full_order(base, free_slots, [free_tasks[i] for i in best_chromo])
    ev = evaluate_order(best_order, start, opts)
    res = {
        "order": best_order,
        "arrivals": ev["arrivals"],
        "stats": {"total": ev["total"], "travel": ev["travel"],
                  "wait": ev["wait"], "penalty": ev["penalty"]},
        "method": "genetic",
    }
    if curve:
        res["curve"] = curve_pts
    return res


if __name__ == "__main__":
    from parser import parse_tasks
    from simanneal import sa_route

    demo = """上午9点去银行办卡
下午3点去学校接孩子放学（重要）
顺便去超市买菜
晚上7点前从驿站取快递回家"""
    tasks = parse_tasks(demo)
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    for i, t in enumerate(tasks):
        t["lat"] = 31.23 + i * 0.01
        t["lng"] = 121.47 + i * 0.01
    from optimizer import optimize_route
    h = optimize_route(tasks, start)
    s = sa_route(tasks, start, seed=1)
    g = ga_route(tasks, start, seed=1)
    print("启发式  total:", h["stats"]["total"], " 方法:", h["method"])
    print("退火    total:", s["stats"]["total"], " 方法:", s["method"])
    print("遗传    total:", g["stats"]["total"], " 方法:", g["method"])
