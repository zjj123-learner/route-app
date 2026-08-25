import math
from itertools import permutations

DEFAULTS = {
    "mode": "walk",
    "walk_speed": 4.5,
    "drive_speed": 28,
    "start_min": 480,
    "wait_factor": 0.5,
    "fixed_late_penalty": 100,
    "window_late_penalty": 30,
    "deadline_penalty": 50,
    "priority_weight": 3,
    "fixed_early_buffer": 30,
    "max_brute_force": 8,
    "buffer": 0,   # 每段路程额外预留的分钟数(防踩点)
}


def haversine_km(a, b):
    R = 6371.0
    dlat = math.radians(b["lat"] - a["lat"])
    dlng = math.radians(b["lng"] - a["lng"])
    la1 = math.radians(a["lat"])
    la2 = math.radians(b["lat"])
    h = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def travel_minutes(a, b, opts):
    # 有真实路网矩阵就用矩阵里的分钟数, 没有就回退直线估算
    tm = opts.get("time_matrix")
    if tm is not None:
        key = (round(a["lat"], 5), round(a["lng"], 5), round(b["lat"], 5), round(b["lng"], 5))
        got = tm.get(key)
        if got is not None:
            return got[1] + opts.get("buffer", 0)
    km = haversine_km(a, b)
    speed = opts["drive_speed"] if opts["mode"] == "drive" else opts["walk_speed"]
    extra = 2 if opts["mode"] == "drive" else 3
    return round(km / speed * 60 + extra) + opts.get("buffer", 0)

def evaluate_order(order, start, opts):
    t = opts["start_min"]
    travel = 0
    wait = 0
    penalty = 0
    arrivals = []
    prev = start

    for i, task in enumerate(order):
        tt = travel_minutes(prev, task, opts)
        travel += tt
        t += tt

        wait_target = None
        if task["fixed"] is not None:
            wait_target = task["fixed"] - opts["fixed_early_buffer"]
        elif task["earliest"] is not None:
            wait_target = task["earliest"]
        if wait_target is not None and t < wait_target:
            wait += wait_target - t
            t = wait_target

        if task["fixed"] is not None and t > task["fixed"]:
            penalty += (t - task["fixed"]) * opts["fixed_late_penalty"]
        if task["latest"] is not None and t > task["latest"]:
            penalty += (t - task["latest"]) * opts["window_late_penalty"]
        done = t + task["duration"]
        if task["deadline"] is not None and done > task["deadline"]:
            penalty += (done - task["deadline"]) * opts["deadline_penalty"]

        arrivals.append({
            "task": task,
            "arrival": t,
            "depart": done,
            "travel": tt,
        })
        penalty += (task["priority"] - 1) * opts["priority_weight"] * i
        t = done
        prev = task

    total = travel + wait * opts["wait_factor"] + penalty
    return {"total": total, "travel": travel, "wait": wait, "penalty": penalty, "arrivals": arrivals}


def nearest_neighbor(tasks, start, opts):
    """最近邻贪心: 从起点出发, 每次挑'耗时+等待+优先级'综合代价最小的下一个.
    只用来构造一个还不错的初始解, 后面交给 2-opt 精修"""
    remaining = list(tasks)
    order = []
    prev = start
    clock = opts["start_min"]
    while remaining:
        best_i = 0
        best_cost = None
        best_tt = 0
        best_wait = 0
        for i, t in enumerate(remaining):
            tt = travel_minutes(prev, t, opts)
            wait = 0
            target = None
            if t["fixed"] is not None:
                target = t["fixed"] - opts["fixed_early_buffer"]
            elif t["earliest"] is not None:
                target = t["earliest"]
            if target is not None and clock + tt < target:
                wait = target - (clock + tt)
            cost = tt + wait + (t["priority"] - 1) * opts["priority_weight"]
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_tt = tt
                best_wait = wait
                best_i = i
        t = remaining.pop(best_i)
        order.append(t)
        prev = t
        clock += best_tt + best_wait + t["duration"]
    return order


def two_opt(order, start, opts, max_passes=20, fixed_slots=()):
    """局部搜索: 反复尝试'反转一段'和'交换两个位置', 评分变好就接受.
    fixed_slots: 锁定的槽位集合, 这些位置上的任务不会被移动.
    评分永远用 evaluate_order, 所以结果只会越来越接近暴力最优"""
    fixed_slots = set(fixed_slots)
    best_order = list(order)
    best_total = evaluate_order(best_order, start, opts)["total"]
    for _ in range(max_passes):
        n = len(best_order)
        improved = False

        # 邻域1: 2-opt, 反转 order[i..j] (不能碰到锁定槽)
        for i in range(n):
            for j in range(i + 1, n):
                if any(s in fixed_slots for s in range(i, j + 1)):
                    continue
                candidate = best_order[:i] + best_order[i:j + 1][::-1] + best_order[j + 1:]
                total = evaluate_order(candidate, start, opts)["total"]
                if total < best_total:
                    best_order, best_total = candidate, total
                    improved = True
                    break
            if improved:
                break
        if improved:
            continue

        # 邻域2: 交换位置 i, j (锁定槽不能动)
        for i in range(n):
            for j in range(i + 1, n):
                if i in fixed_slots or j in fixed_slots:
                    continue
                candidate = list(best_order)
                candidate[i], candidate[j] = candidate[j], candidate[i]
                total = evaluate_order(candidate, start, opts)["total"]
                if total < best_total:
                    best_order, best_total = candidate, total
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break
    return best_order


def heuristic_route(tasks, start, opts):
    """启发式: 最近邻构造初始解, 再用 2-opt/交换 精修"""
    order = nearest_neighbor(tasks, start, opts)
    order = two_opt(order, start, opts)
    ev = evaluate_order(order, start, opts)
    return order, ev


def _solve_with_locks(tasks, start, opts, fixed_positions):
    """有锁定任务的求解: 锁定任务放到固定槽位, 其余任务填剩余槽.
    fixed_positions: {任务在tasks里的下标: 最终位置槽位}"""
    n = len(tasks)
    if len(set(fixed_positions.values())) != len(fixed_positions):
        raise ValueError("锁定位置冲突")
    base = [None] * n
    for ti, slot in fixed_positions.items():
        base[slot] = tasks[ti]
    free_tasks = [t for i, t in enumerate(tasks) if i not in fixed_positions]
    free_slots = [i for i, x in enumerate(base) if x is None]

    if len(free_tasks) <= opts["max_brute_force"]:
        best = None
        for perm in permutations(free_tasks):
            cand = list(base)
            for slot, t in zip(free_slots, perm):
                cand[slot] = t
            ev = evaluate_order(cand, start, opts)
            if best is None or ev["total"] < best[1]["total"]:
                best = (cand, ev)
        return best[0], best[1], "brute-force"

    nn = nearest_neighbor(free_tasks, start, opts)
    cand = list(base)
    for slot, t in zip(free_slots, nn):
        cand[slot] = t
    cand = two_opt(cand, start, opts, fixed_slots=set(fixed_positions.values()))
    ev = evaluate_order(cand, start, opts)
    return cand, ev, "heuristic"


def optimize_route(tasks, start, options=None, fixed_positions=None):
    opts = dict(DEFAULTS)
    if options:
        opts.update(options)

    n = len(tasks)
    if n == 0:
        return {"order": [], "arrivals": [], "stats": None, "method": "none"}

    if fixed_positions:
        order, ev, method = _solve_with_locks(tasks, start, opts, fixed_positions)
    elif n <= opts["max_brute_force"]:
        best = None
        for perm in permutations(range(n)):
            order = [tasks[i] for i in perm]
            ev = evaluate_order(order, start, opts)
            if best is None or ev["total"] < best[1]["total"]:
                best = (order, ev)
        order, ev = best
        method = "brute-force"
    else:
        order, ev = heuristic_route(tasks, start, opts)
        method = "heuristic"

    result = {
        "order": order,
        "arrivals": ev["arrivals"],
        "stats": {"total": ev["total"], "travel": ev["travel"], "wait": ev["wait"], "penalty": ev["penalty"]},
        "method": method,
    }
    return result


def schedule_in_order(tasks, start, options=None):
    opts = dict(DEFAULTS)
    if options:
        opts.update(options)
    return evaluate_order(tasks, start, opts)["arrivals"]


if __name__ == "__main__":
    from parser import parse_tasks

    demo = (
        "明天上午9点去银行办卡\n"
        "下午3点去学校接孩子放学（重要）\n"
        "顺便去超市买菜\n"
        "晚上7点前从驿站取快递回家"
    )
    tasks = parse_tasks(demo)
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    result = optimize_route(tasks, start)
    print("方法:", result["method"])
    for i, s in enumerate(result["arrivals"]):
        h, m = divmod(s["arrival"], 60)
        task = s["task"]
        print(f"{i+1}. {task['name']}  到达 {h:02d}:{m:02d}  停留{task['duration']}分钟")
    print("路程总耗时:", result["stats"]["travel"], "分钟")


PERIODS = [
    ("morning", 360, 660),     # 上午 6:00-11:00
    ("noon", 660, 840),        # 中午 11:00-14:00
    ("afternoon", 840, 1080),  # 下午 14:00-18:00
    ("evening", 1080, 1440),   # 晚上 18:00-24:00
]


def period_of(minute):
    """分钟数落在哪个时段: 'morning'/'noon'/'afternoon'/'evening', 范围外返回 None"""
    for name, lo, hi in PERIODS:
        if lo <= minute < hi:
            return name
    return None


def insert_base_stops(arrivals, base, options, threshold=60, min_rest=30, prefs=None):
    """在长空闲间隙里插入'回基地休息'停靠, 返回新的停靠列表.
    prefs: 每个时段的回家偏好, 如 {'morning': 'stay', 'evening': 'home'}
      auto  自动判断(跟以前一样, 长空隙才回)
      stay  不回家, 再长的空隙也在外面等
      home  强制回家, 只要来回还够休息就回
    不传或某时段缺省按 auto"""
    opts = dict(DEFAULTS)
    if options:
        opts.update(options)
    if prefs is None:
        prefs = {}
    stops = []
    for i, s in enumerate(arrivals):
        task = s["task"]
        if i > 0:
            prev = arrivals[i - 1]
            gap = s["arrival"] - prev["depart"]
            # 过夜空隙(>=8小时)不插'回家休息', 属于第二天重新出发; 任务本身仍然保留
            if gap < 480:
                go = travel_minutes(prev["task"], base, opts)
                back = travel_minutes(base, task, opts)
                round_trip = go + back
                mid = (prev["depart"] + s["arrival"]) // 2
                pref = prefs.get(period_of(mid % 1440), "auto")
                if pref == "stay":
                    can_rest = False
                elif pref == "home":
                    can_rest = round_trip <= gap - min_rest   # 不要求空隙多长, 来回够就回
                else:
                    can_rest = gap >= threshold and round_trip <= gap - min_rest
                if can_rest:
                    arrive_base = prev["depart"] + go
                    depart_base = s["arrival"] - back
                    rest_min = depart_base - arrive_base
                    if rest_min >= min_rest:
                        stops.append({
                            "type": "base",
                            "task": {
                                "name": "回" + base["name"] + "休息",
                                "place": base["name"],
                                "lat": base["lat"],
                                "lng": base["lng"],
                                "priority": 0,
                                "duration": rest_min,
                            },
                            "arrival": arrive_base,
                            "depart": depart_base,
                        })
        stops.append({
            "type": "task",
            "task": task,
            "arrival": s["arrival"],
            "depart": s["depart"],
        })
    return stops
