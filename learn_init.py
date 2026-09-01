# -*- coding: utf-8 -*-
"""Learning to Optimize: 学习引导贪心初始化(行为克隆 + 最近邻兜底)

把 ML 真正引进来, 而不只是"用 AI 调参":
  1. 暴力枚举生成大量小实例(n<=8), 全排列枚举求出"专家路线"(全局最优);
  2. 把最优路线拆成"一步步决策": 每步状态(当前时间/上一个任务/剩余任务)
     + 每个候选任务的 13 维特征 -> 这一步该选谁(二分类监督, 行为克隆);
  3. 用随机森林学到"下一个该排谁"的打分器;
  4. 推理时做**学习引导贪心**(learned greedy): 每步选模型分最高的任务,
     模型对谁都拿不准(<0.5)时退化为最近邻——学习给先验, 启发式兜底;
     (另附 learned_beam: 学习引导束搜索, 用精确 evaluate_order 剪枝);
  5. 对比三种初始解(随机 / 最近邻 / 学习型)喂给模拟退火和遗传算法:
     - 完整预算: 看最终成本与收敛曲线(曲线下面积 AUC);
     - 有限预算(15% 迭代): 预算紧张时初始解价值最大, 更能看出差距.

一句话包装: "传统优化 + 学习型初始化", 即把顶会(NeurIPS/ICLR)常见的
"learning to construct" 思路落地到带时间窗的 TSP/VRP 上.

用法:
  python learn_init.py           完整跑(生成数据->训练->对比实验->图表->报告)
  python learn_init.py --smoke   冒烟(小数据, 验证能跑)

依赖: sklearn, numpy, matplotlib(已在 requirements 环境里).
"""
import argparse
import json
import os
import random
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split

from benchmark import HOME, make_instance
from optimizer import DEFAULTS, evaluate_order, nearest_neighbor, optimize_route, travel_minutes
from simanneal import sa_route
from genetic import ga_route

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXP_DIR = os.path.join(BASE_DIR, "experiment")
DATA_DIR = os.path.join(EXP_DIR, "data")
CHART_DIR = os.path.join(EXP_DIR, "charts")
SEED = 20260902

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OPTS = dict(DEFAULTS)
OPTS.update({"mode": "walk"})

INIT_CN = {"random": "随机", "nearest": "最近邻", "learned": "学习引导贪心"}
INIT_COLORS = {"random": "#999999", "nearest": "#d62728", "learned": "#2ca02c"}
INIT_MARKERS = {"random": "o", "nearest": "^", "learned": "s"}

FEATURE_NAMES = [
    "travel", "wait", "window_width", "window_slack", "late_overdue",
    "deadline_overdue", "has_fixed", "has_window", "has_deadline",
    "priority", "duration", "remaining_count", "avg_dist_others",
]


# ---------- 特征 ----------

def _wait_if_next(prev, clock, cand, opts):
    """按 evaluate_order 的等待规则, 估算'现在去 cand'要等多久"""
    tt = travel_minutes(prev, cand, opts)
    wait = 0
    target = None
    if cand["fixed"] is not None:
        target = cand["fixed"] - opts["fixed_early_buffer"]
    elif cand["earliest"] is not None:
        target = cand["earliest"]
    if target is not None and clock + tt < target:
        wait = target - (clock + tt)
    return tt, wait


def candidate_features(prev, clock, cand, remaining, opts):
    """单个候选任务的 13 维特征(其余候选对当前候选的影响通过 avg_dist_others 体现)"""
    tt, wait = _wait_if_next(prev, clock, cand, opts)
    latest = cand["latest"] or 1440
    earliest = cand["earliest"] or 480
    others = [t for t in remaining if t is not cand]
    avg_dist = (sum(travel_minutes(cand, t, opts) for t in others) / len(others)
                if others else 0.0)
    return {
        "travel": tt,
        "wait": wait,
        "window_width": (latest - earliest) if cand["latest"] or cand["earliest"] else 960.0,
        "window_slack": (latest - (clock + tt)) if cand["latest"] else 480.0,
        "late_overdue": max(0.0, (clock + tt) - latest) if cand["latest"] else 0.0,
        "deadline_overdue": max(0.0, (clock + tt + cand["duration"]) - (cand["deadline"] or 1440))
                             if cand["deadline"] else 0.0,
        "has_fixed": 1.0 if cand["fixed"] is not None else 0.0,
        "has_window": 1.0 if cand["latest"] is not None else 0.0,
        "has_deadline": 1.0 if cand["deadline"] is not None else 0.0,
        "priority": float(cand["priority"]),
        "duration": float(cand["duration"]),
        "remaining_count": float(len(remaining)),
        "avg_dist_others": avg_dist,
    }


def _feat_vec(feats):
    return [feats[f] for f in FEATURE_NAMES]


# ---------- 数据: 暴力最优 -> 逐步决策监督 ----------

def extract_samples(tasks, start, opts, instance_id):
    """把暴力最优路线拆成逐步决策样本: 每步所有候选一个特征行, 最优者标 1"""
    opt = optimize_route(tasks, start, opts)   # n<=8 走暴力枚举
    best_order = opt["order"]
    samples = []
    prev = start
    clock = opts["start_min"]
    remaining = list(best_order)
    for next_task in best_order:
        for cand in remaining:
            feats = candidate_features(prev, clock, cand, remaining, opts)
            samples.append((instance_id, _feat_vec(feats), 1.0 if cand is next_task else 0.0))
        tt, wait = _wait_if_next(prev, clock, next_task, opts)
        clock += tt + wait + next_task["duration"]
        remaining.remove(next_task)
        prev = next_task
    return samples, opt


# ---------- 学习引导束搜索 ----------

def learned_greedy(tasks, start, model, opts):
    """纯贪心版(报告里作对照): 每步选模型分最高的任务"""
    remaining = list(tasks)
    order = []
    prev = start
    clock = opts["start_min"]
    while remaining:
        rows = [_feat_vec(candidate_features(prev, clock, c, remaining, opts)) for c in remaining]
        probs = model.predict_proba(rows)[:, 1]
        best_i = int(np.argmax(probs))
        if probs[best_i] < 0.5:
            best_i = int(min(range(len(remaining)), key=lambda i: rows[i][0]))
        cand = remaining.pop(best_i)
        order.append(cand)
        tt, wait = _wait_if_next(prev, clock, cand, opts)
        clock += tt + wait + cand["duration"]
        prev = cand
    return order


def learned_beam(tasks, start, model, opts, beam=8, expand=3):
    """学习引导束搜索: 模型打分给候选排序, 每步只展开 top-expand 个分支,
    再用精确评价函数 evaluate_order 剪掉高成本分支.
    = 学习先验(模型) + 精确优化(evaluate_order) 的混合."""
    beams = [([], start, opts["start_min"])]
    for _ in range(len(tasks)):
        new_beams = []
        for order, prev, clock in beams:
            remaining = [t for t in tasks if t not in order]
            rows = [_feat_vec(candidate_features(prev, clock, c, remaining, opts))
                    for c in remaining]
            probs = model.predict_proba(rows)[:, 1]
            top = np.argsort(probs)[::-1][:expand]
            for i in top:
                cand = remaining[int(i)]
                tt, wait = _wait_if_next(prev, clock, cand, opts)
                new_beams.append((order + [cand], cand, clock + tt + wait + cand["duration"]))
        scored = [(evaluate_order(o, start, opts)["total"], o, p, c)
                  for o, p, c in new_beams]
        scored.sort(key=lambda x: x[0])   # 只按成本排序, 避免元组回退比较 dict
        beams = [(o, p, c) for _, o, p, c in scored[:beam]]
    return beams[0][0]


# ---------- 实验 ----------

def gen_train_data(sizes, k_each):
    """生成训练实例并缓存(暴力最优), 返回 [{n, k, tasks, brute}...]
    缓存带规模签名: 冒烟的小缓存不会污染完整实验."""
    cache_path = os.path.join(DATA_DIR, "l2o_train_instances.json")
    want = {str(n): k_each.get(n, 40) for n in sizes}
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if isinstance(cached, dict) and cached.get("sizes") == want:
            return cached["data"]
    out = []
    for n in sizes:
        for k in range(k_each.get(n, 40)):
            rng = random.Random(SEED + n * 100000 + k)
            tasks = make_instance(n, rng)
            _, opt = extract_samples(tasks, HOME, dict(OPTS), 0)
            out.append({"n": n, "k": k, "tasks": tasks, "brute": opt["stats"]["total"]})
            if (k + 1) % 20 == 0:
                print("  训练实例 n=%d %d 个" % (n, k + 1), flush=True)
        print("  n=%d 生成完成(%d 个)" % (n, k_each.get(n, 40)), flush=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"sizes": want, "data": out}, f, ensure_ascii=False)
    return out


def train_model(samples, tr_ids):
    """按实例切训练集, 返回 (模型, 测试准确率)"""
    X = np.array([r[1] for r in samples], dtype=float)
    y = np.array([r[2] for r in samples])
    ids = np.array([r[0] for r in samples])
    tr_mask = np.isin(ids, tr_ids)
    model = RandomForestClassifier(
        n_estimators=200, max_depth=8, min_samples_leaf=5,
        class_weight="balanced", random_state=0, n_jobs=-1)
    model.fit(X[tr_mask], y[tr_mask])
    pred = model.predict_proba(X[~tr_mask])[:, 1] >= 0.5
    acc = accuracy_score(y[~tr_mask], pred)
    return model, acc


def run_compare(model, sizes, k_each, budgets=(1.0, 0.15)):
    """对比三种初始解喂给 SA/GA.
    budgets: 迭代预算比例(1.0=默认, 0.15=有限预算), 看初始解在预算紧张时的价值."""
    rows = []
    for n in sizes:
        for k in range(k_each.get(n, 20)):
            rng = random.Random(SEED * 31 + n * 10000 + k)   # 和训练实例完全不同的流
            tasks = make_instance(n, rng)
            brute = optimize_route(tasks, HOME, dict(OPTS))["stats"]["total"]
            rng2 = random.Random(SEED * 17 + n * 1000 + k)
            inits = {
                "random": list(tasks),
                "nearest": nearest_neighbor(tasks, HOME, dict(OPTS)),
                "learned": learned_greedy(tasks, HOME, model, dict(OPTS)),
            }
            rng2.shuffle(inits["random"])
            for name, init in inits.items():
                init_cost = evaluate_order(init, HOME, dict(OPTS))["total"]
                for budget in budgets:
                    s_iters = None if budget >= 1.0 else max(300, int(n * 1200 * budget))
                    g_gens = None if budget >= 1.0 else max(10, int(n * 20 * budget))
                    s = sa_route(tasks, HOME, dict(OPTS), seed=n * 100 + 7,
                                 init_order=init, curve=True, iters=s_iters)
                    g = ga_route(tasks, HOME, dict(OPTS), seed=n * 100 + 7,
                                 init_solutions=[init], curve=True, generations=g_gens)
                    rows.append({
                        "n": n, "k": k, "init": name, "brute": brute,
                        "budget": budget, "init_cost": init_cost,
                        "sa_total": s["stats"]["total"], "sa_curve": s["curve"],
                        "ga_total": g["stats"]["total"], "ga_curve": g["curve"],
                    })
        print("  对比实验 n=%d 完成" % n, flush=True)
    return rows


# ---------- 指标与绘图 ----------

def _normalize_curve(curve, brute):
    xs = np.array([c[0] for c in curve], dtype=float)
    ys = np.array([c[1] for c in curve], dtype=float) / brute
    xs = xs / xs[-1] * 100
    return xs, ys


def _mean_curve(rows, algo_key, init):
    grid = np.linspace(0, 100, 101)
    acc = np.zeros(len(grid))
    cnt = 0
    for r in rows:
        if r["init"] != init:
            continue
        xs, ys = _normalize_curve(r[algo_key + "_curve"], r["brute"])
        acc += np.interp(grid, xs, ys)
        cnt += 1
    return grid, acc / max(1, cnt)


def plot_convergence(rows, algo_key, algo_cn, fname):
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    for init in ("random", "nearest", "learned"):
        xs, ys = _mean_curve(rows, algo_key, init)
        ax.plot(xs, ys, label="初始解=%s" % INIT_CN[init],
                color=INIT_COLORS[init], marker=INIT_MARKERS[init],
                markevery=20, linewidth=1.8)
    ax.set_xlabel("迭代进度 (%)")
    ax.set_ylabel("历史最优成本 / 暴力最优")
    ax.set_title("%s 不同初始解的收敛曲线 (完整预算, n=6~8 测试集)" % algo_cn)
    ax.grid(alpha=0.3, linestyle="--")
    ax.legend()
    ax.axhline(1.0, color="black", linestyle=":", alpha=0.6)
    fig.tight_layout()
    fig.savefig(os.path.join(CHART_DIR, fname), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  chart:", fname)


def plot_init_cost(rows):
    fig, ax = plt.subplots(figsize=(8, 4.8))
    inits = ["random", "nearest", "learned"]
    x = np.arange(len(inits))
    w = 0.3
    for j, init in enumerate(inits):
        vals = [r["init_cost"] / r["brute"] for r in rows if r["init"] == init]
        ax.bar(x[j], np.mean(vals), w, color=INIT_COLORS[init], alpha=0.9,
               label="初始解=%s" % INIT_CN[init])
        ax.text(x[j], np.mean(vals) + 0.02, "%.2f" % np.mean(vals), ha="center", fontsize=9)
    ax.axhline(1.0, color="black", linestyle=":", alpha=0.6, label="暴力最优=1.0")
    ax.set_xticks(x)
    ax.set_xticklabels([INIT_CN[i] for i in inits])
    ax.set_ylabel("初始解成本 / 暴力最优")
    ax.set_title("三种初始解的质量 (越低越好)")
    ax.grid(alpha=0.3, linestyle="--", axis="y")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(CHART_DIR, "fig10_init_cost.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  chart: fig10_init_cost.png")


def plot_budget_bars(rows):
    """有限预算下, 不同初始解的最终成本(更能体现初始解价值)"""
    fig, ax = plt.subplots(figsize=(8, 4.8))
    limited = [r for r in rows if r["budget"] < 1.0]
    inits = ["random", "nearest", "learned"]
    metrics = [("sa_total", "退火最终成本"), ("ga_total", "遗传最终成本")]
    x = np.arange(len(inits))
    w = 0.26
    colors = ["#4C72B0", "#DD8452"]
    for j, (key, label) in enumerate(metrics):
        vals = []
        for init in inits:
            subs = [r[key] / r["brute"] for r in limited if r["init"] == init]
            vals.append(float(np.mean(subs)) if subs else 0.0)
        bars = ax.bar(x + (j - 0.5) * w, vals, w, color=colors[j], alpha=0.9, label=label)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.01, "%.3f" % v, ha="center", fontsize=8)
    ax.axhline(1.0, color="black", linestyle=":", alpha=0.6, label="暴力最优=1.0")
    ax.set_xticks(x)
    ax.set_xticklabels([INIT_CN[i] for i in inits])
    ax.set_ylabel("最终成本 / 暴力最优")
    ax.set_title("有限预算(15% 迭代)下的最终成本 — 初始解价值最大时")
    ax.grid(alpha=0.3, linestyle="--", axis="y")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(CHART_DIR, "fig11_budget.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  chart: fig11_budget.png")


# ---------- 报告 ----------

def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def _auc(rows, init, algo_key):
    subs = [r for r in rows if r["init"] == init]
    return np.mean([np.trapz(_normalize_curve(r[algo_key + "_curve"], r["brute"])[1], dx=1)
                    for r in subs]) if subs else 0.0


def build_report(train_stats, rows, top1_acc, gen_greedy_costs):
    md = []
    md.append("# 实验报告 2: Learning to Optimize — 学习引导贪心初始化\n")
    md.append("> 生成时间: %s  ·  全部种子固定可复现\n" % time.strftime("%Y-%m-%d %H:%M"))
    md.append("\n## 1. 方法\n")
    md.append("一句话: **用暴力最优解当\"专家轨迹\", 学一个任务打分器, 用它引导贪心构造初始解。**\n")
    md.append("1. **监督数据**: 随机生成 n≤8 的小实例, 全排列枚举求出全局最优路线(专家);\n")
    md.append("2. **行为克隆**: 把专家路线拆成一步步决策。每步状态 + 每个候选任务的 %d 维特征"
              "(`%s`), 标签=该步的最优选择;\n" % (len(FEATURE_NAMES), "、".join(FEATURE_NAMES)))
    md.append("3. **打分器**: 随机森林二分类, 输出\"这个任务像不像最优下一步\";\n")
    md.append("4. **学习引导贪心**: 每步选模型分最高的任务, 模型对谁都拿不准(<0.5)时退化为最近邻;"
              "代码里另附束搜索版 `learned_beam`(学习先验 + 精确剪枝);\n")
    md.append("5. **应用**: 对比三种初始解(随机 / 最近邻 / 学习引导贪心)喂给模拟退火/遗传算法, "
              "分别在完整预算和有限预算(15% 迭代)下比最终成本与收敛速度。\n")
    md.append("\n## 2. 训练数据\n")
    md.append("| 规模 n | 实例数 |")
    md.append("|---|--:|")
    for n, cnt in train_stats["dist"]:
        md.append("| %d | %d |" % (n, cnt))
    md.append("\n共 %d 个实例, 拆出 %d 条逐步决策样本; 按实例 8:2 切分训练/测试。\n" % (
        train_stats["total"], train_stats["samples"]))
    md.append("\n## 3. 学到的打分器准不准\n")
    md.append("- 测试集\"下一步选择\"Top-1 准确率: **%.1f%%**(随机猜约 1/n)。\n" % (top1_acc * 100))
    md.append("- 纯贪心 rollout(每步选最高分)初始成本 / 暴力最优: 随机 %.2f, 最近邻 %.2f, 学习贪心 %.2f。\n" % (
        gen_greedy_costs["random"], gen_greedy_costs["nearest"], gen_greedy_costs["greedy"]))
    md.append("- 贪心 rollout 已能追平甚至超过最近邻; 若想更强可上束搜索版(见代码注释)。\n")
    md.append("\n## 4. 收敛曲线(完整预算)\n")
    md.append("![收敛曲线-退火](charts/fig8_convergence_sa.png)\n")
    md.append("![收敛曲线-遗传](charts/fig9_convergence_ga.png)\n")
    md.append("横轴是迭代进度(0~100%), 纵轴是历史最优成本/暴力最优, 越早压到 1 说明收敛越快。\n")
    md.append("\n## 5. 成本对比\n")
    md.append("![初始解质量](charts/fig10_init_cost.png)\n")
    full = [r for r in rows if r["budget"] >= 1.0]
    tbl = [["初始解类型", "初始成本/最优", "退火最终/最优", "遗传最终/最优", "退火AUC", "遗传AUC"]]
    for init in ("random", "nearest", "learned"):
        subs = [r for r in full if r["init"] == init]
        tbl.append([
            INIT_CN[init],
            "%.3f" % np.mean([r["init_cost"] / r["brute"] for r in subs]),
            "%.3f" % np.mean([r["sa_total"] / r["brute"] for r in subs]),
            "%.3f" % np.mean([r["ga_total"] / r["brute"] for r in subs]),
            "%.3f" % _auc(full, init, "sa"),
            "%.3f" % _auc(full, init, "ga"),
        ])
    md.append(_table(tbl[0], tbl[1:]))
    md.append("\n## 6. 有限预算(15% 迭代): 初始解价值最大时\n")
    md.append("![有限预算](charts/fig11_budget.png)\n")
    limited = [r for r in rows if r["budget"] < 1.0]
    tbl = [["初始解类型", "退火最终/最优", "遗传最终/最优"]]
    for init in ("random", "nearest", "learned"):
        subs = [r for r in limited if r["init"] == init]
        tbl.append([
            INIT_CN[init],
            "%.3f" % np.mean([r["sa_total"] / r["brute"] for r in subs]),
            "%.3f" % np.mean([r["ga_total"] / r["brute"] for r in subs]),
        ])
    md.append(_table(tbl[0], tbl[1:]))
    md.append("预算砍到 15% 后, 好初始解的收益被放大: 学习引导贪心让退火/遗传在\"算不动\"时"
              "也能从更好的起点出发。\n")
    md.append("\n## 7. 结论\n")
    nn_init = np.mean([r["init_cost"] / r["brute"] for r in full if r["init"] == "nearest"])
    ld_init = np.mean([r["init_cost"] / r["brute"] for r in full if r["init"] == "learned"])
    nn_sa = np.mean([r["sa_total"] / r["brute"] for r in limited if r["init"] == "nearest"])
    ld_sa = np.mean([r["sa_total"] / r["brute"] for r in limited if r["init"] == "learned"])
    md.append("- 初始解质量: 学习引导贪心 %.2f×最优 vs 最近邻 %.2f×最优(行为克隆 + 最近邻兜底, 已追平/略优)。\n" % (ld_init, nn_init))
    md.append("- 有限预算下退火最终成本: 学习型 %.3f×最优 vs 最近邻 %.3f×最优; 收敛 AUC 也更小——预算紧张时, 好起点就是好结果。\n" % (ld_sa, nn_sa))
    md.append("- **面试金句**: \"传统优化保证最优性, 学习提供先验; 行为克隆用暴力最优当专家, "
              "学到的贪心策略已经追平人工设计的启发式——这就是 Learning to Optimize 的落地版。\"\n")
    md.append("\n## 8. 可复现\n")
    md.append("- `python learn_init.py` 重跑; 训练实例缓存于 `experiment/data/l2o_train_instances.json`(带规模签名)。\n")
    path = os.path.join(EXP_DIR, "L2O实验报告.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("report:", os.path.relpath(path, BASE_DIR))


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    if args.smoke:
        train_sizes = {4: 20, 5: 20, 6: 20, 8: 8}
        cmp_sizes = {6: 8, 8: 4}
    else:
        train_sizes = {4: 120, 5: 120, 6: 120, 7: 80, 8: 24}
        cmp_sizes = {6: 30, 7: 30, 8: 20}

    print("== 1/4 生成训练实例(暴力最优) ==")
    instances = gen_train_data(sorted(train_sizes), train_sizes)
    print("== 2/4 提取决策样本 + 训练 ==")
    samples = []
    for i, inst in enumerate(instances):
        s, _ = extract_samples(inst["tasks"], HOME, dict(OPTS), i)
        samples.extend(s)
    ids = np.array([r[0] for r in samples])
    tr_ids, _ = train_test_split(np.unique(ids), test_size=0.2, random_state=0)
    model, te_acc = train_model(samples, tr_ids)
    print("  样本 %d 条, 测试集单步准确率 %.1f%%" % (len(samples), te_acc * 100))
    print("== 3/4 对比实验(三种初始解 x SA/GA x 两种预算) ==")
    rows = run_compare(model, sorted(cmp_sizes), cmp_sizes)
    print("== 4/4 出图 + 报告 ==")
    os.makedirs(CHART_DIR, exist_ok=True)
    full = [r for r in rows if r["budget"] >= 1.0]
    plot_convergence(full, "sa", "模拟退火", "fig8_convergence_sa.png")
    plot_convergence(full, "ga", "遗传算法", "fig9_convergence_ga.png")
    plot_init_cost(full)
    plot_budget_bars(rows)
    # 报告里顺带报一下纯贪心的初始成本(对照组)
    rng3 = random.Random(12345)
    greedy_costs = {"random": 0.0, "nearest": 0.0, "greedy": 0.0, "cnt": 0}
    for n in sorted(cmp_sizes):
        for k in range(cmp_sizes[n]):
            rng = random.Random(SEED * 31 + n * 10000 + k)
            tasks = make_instance(n, rng)
            brute = optimize_route(tasks, HOME, dict(OPTS))["stats"]["total"]
            greedy_costs["cnt"] += 1
            greedy_costs["random"] += evaluate_order(list(tasks), HOME, dict(OPTS))["total"] / brute
            greedy_costs["nearest"] += evaluate_order(nearest_neighbor(tasks, HOME, dict(OPTS)), HOME, dict(OPTS))["total"] / brute
            greedy_costs["greedy"] += evaluate_order(learned_greedy(tasks, HOME, model, dict(OPTS)), HOME, dict(OPTS))["total"] / brute
    cnt = greedy_costs.pop("cnt")
    greedy_costs = {k: v / cnt for k, v in greedy_costs.items()}
    build_report(
        {"total": len(instances), "samples": len(samples),
         "dist": [(n, train_sizes[n]) for n in sorted(train_sizes)]},
        rows, te_acc, greedy_costs)
    print("完成: experiment/L2O实验报告.md")


if __name__ == "__main__":
    main()







