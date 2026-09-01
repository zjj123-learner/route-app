# -*- coding: utf-8 -*-
"""LLM 智能体化: 混合架构(LLM 粗排 + 求解器精排) 与 ReAct 工具调用

两条升级线, 呼应"LLM 应用"的面试方向:

1. 混合架构(hybrid): LLM 粗排顺序(常识偏好: 固定预约先、截止紧的先、
   高优先级先、'顺便'的塞顺路), 再把 LLM 的顺序当初始解喂给模拟退火/
   遗传精排. 一句话: "LLM 负责语义理解, 传统优化负责最优性".
   - 没配 key 时自动用"常识排序代理"(确定性规则, 模拟 LLM 的粗排),
     整个管线离线可跑, 配了 DEEPSEEK_API_KEY 后 --llm 即换真 LLM.

2. ReAct 工具调用(react): LLM 遇到地点含糊/偏远任务时, 自己决定调用
   地图工具(geocode/search_nearby), 拿结果再规划——正好复用 geocode.py
   的"偏远地点候选"能力. 无 key/无 AMAP_KEY 时回退规则提取关键词, 不会崩.

用法:
  python llm_agent.py --demo                    演示混合架构
  python llm_agent.py --bench --limit 10        离线对比(无 key 用常识代理模拟 LLM)
  python llm_agent.py --bench --limit 10 --llm  配了 key 时用真 LLM 跑对比
  python llm_agent.py --react "明天上午去银行, 下午去郊区看仓库"   演示 ReAct

集成提示: 上线时把 app.py 的 /api/plan 里 simanneal 分支换成
  hybrid_plan(tasks, start, algo="simanneal", seed=20260826)
即可让线上路线同时吃到 LLM 的常识和算法的全局最优.
"""
import argparse
import json
import os
import random
import re
import time

import requests

import config
import geocode
from benchmark import HOME, make_instance
from optimizer import DEFAULTS, evaluate_order, nearest_neighbor, optimize_route
from simanneal import sa_route
from genetic import ga_route
from llm_parser import llm_available, parse_with_llm


# ---------- LLM 粗排 ----------

RANK_SYSTEM_PROMPT = """你是行程排程助手。用户给出任务列表(JSON, 含 index/name/place/priority/duration/earliest/latest/fixed/deadline, 时间是当天分钟数), 请按生活常识排一个合理的执行顺序。

排序原则(按优先级从高到低):
1. fixed 不是 null 的固定预约必须尽量靠前(硬约束);
2. deadline 更近的任务优先;
3. earliest/latest 时间窗更早的任务优先;
4. priority=3(重要/紧急)优先于 priority=1(顺便);
5. 没写时间的低优先级任务(买菜/取快递)插进顺路的空隙, 尽量少绕路;
6. 考虑地点远近, 不要来回折返.

只输出 JSON 数组, 数组里是任务 index 的排列, 例如 [2,0,1,3], 不要输出任何解释或 markdown 围栏。"""


def _extract_json_array(content):
    """从模型回复里抠出 JSON 数组, 失败返回 None"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None


def _chat(messages, timeout=45):
    """OpenAI 兼容 chat 调用, 失败返回 None"""
    if not llm_available():
        return None
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": 0,
        "stream": False,
    }
    try:
        resp = requests.post(
            config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + config.LLM_API_KEY,
                     "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def llm_coarse_rank(tasks, start=None, timeout=45):
    """LLM 粗排: 返回任务下标顺序(list), 失败(没key/网络/格式错)返回 None"""
    if not llm_available():
        return None
    lines = [json.dumps({
        "index": i,
        "name": t["name"],
        "place": t.get("place"),
        "priority": t["priority"],
        "duration": t["duration"],
        "earliest": t.get("earliest"),
        "latest": t.get("latest"),
        "fixed": t.get("fixed"),
        "deadline": t.get("deadline"),
    }, ensure_ascii=False) for i, t in enumerate(tasks)]
    content = _chat([
        {"role": "system", "content": RANK_SYSTEM_PROMPT},
        {"role": "user", "content": "任务列表:\n" + "\n".join(lines)},
    ], timeout=timeout)
    if content is None:
        return None
    arr = _extract_json_array(content)
    if arr is None:
        return None
    try:
        order = [int(x) for x in arr]
    except (TypeError, ValueError):
        return None
    if len(order) != len(tasks) or sorted(order) != list(range(len(tasks))):
        return None
    return order


def llm_proxy_rank(tasks):
    """无 key 时的 LLM 代理: 模拟'常识排序'(固定预约先, 时间窗早的先, 优先级高的先).
    保证混合架构管线离线可跑; 配 key 后 --llm 会用真 LLM 替换它."""
    return sorted(range(len(tasks)), key=lambda i: (
        tasks[i]["fixed"] is None,               # fixed 的先排
        tasks[i]["earliest"] if tasks[i]["earliest"] is not None else 1440,  # 时间窗早的先
        -tasks[i]["priority"],                   # 优先级高的先
        tasks[i]["deadline"] if tasks[i]["deadline"] is not None else 1440))


def coarse_rank(tasks, use_llm=True):
    """粗排入口: 真 LLM 优先, 失败/无 key 自动回退常识代理.
    返回 (order_indices, source) source: 'llm' / 'proxy'"""
    if use_llm and llm_available():
        order = llm_coarse_rank(tasks)
        if order is not None:
            return order, "llm"
    return llm_proxy_rank(tasks), "proxy"


# ---------- 混合架构 ----------

def hybrid_plan(tasks, start, algo="simanneal", use_llm=True, seed=42, options=None):
    """混合架构: 粗排(LLM/代理) -> 求解器精排.
    algo: 'simanneal' / 'genetic' / 'heuristic'
    返回: 求解器结果 + method/llm_used/llm_init_cost 附加字段."""
    opts = dict(DEFAULTS)
    if options:
        opts.update(options)
    order_idx, source = coarse_rank(tasks, use_llm=use_llm)
    init = [tasks[i] for i in order_idx]
    if algo == "simanneal":
        res = sa_route(tasks, start, opts, seed=seed, init_order=init)
    elif algo == "genetic":
        res = ga_route(tasks, start, opts, seed=seed, init_solutions=[init])
    else:
        res = optimize_route(tasks, start, {**opts, "max_brute_force": 0})
    res["llm_used"] = source
    res["method"] = "%s-rank+%s" % (source, algo)
    res["llm_init_cost"] = evaluate_order(init, start, opts)["total"]
    return res


# ---------- ReAct 工具调用 ----------

REACT_SYSTEM_PROMPT = """你是一个会调用地图工具的行程规划智能体。

输入: 任务列表(JSON), 其中部分任务 place 为空(地点没解析出来)或地点含糊(如"郊区""附近""那边")。
你的工作: 判断哪些任务需要调用工具查地点。

可用工具:
- geocode("关键词"): 城市级地点搜索, 适合明确地点如"银行""学校"
- nearby("关键词"): 以当前位置为中心附近搜索, 适合"附近饭店""顺路的驿站"

输出(只输出 JSON): {"actions":[{"tool":"geocode","keyword":"银行"},{"tool":"nearby","keyword":"饭店"}]}
规则:
- place 已经明确(银行/学校/超市/快递驿站等常见地点)且没写坐标 -> geocode(place)
- 地点含 附近/顺路/郊区/偏远/那边 等词 -> nearby(关键词)
- 全部明确 -> {"actions":[]}
- 最多 5 个 action, 不要解释, 不要 markdown 围栏"""


def react_decide_actions(tasks, center=None, timeout=45):
    """ReAct 第1步: LLM 决定查哪些地点; 失败回退规则提取关键词.
    返回 [{"tool": "geocode"|"nearby", "keyword": str}]"""
    if llm_available():
        lines = [json.dumps({
            "index": i,
            "name": t["name"],
            "place": t.get("place"),
            "lat": t.get("lat"),
            "lng": t.get("lng"),
        }, ensure_ascii=False) for i, t in enumerate(tasks)]
        content = _chat([
            {"role": "system", "content": REACT_SYSTEM_PROMPT},
            {"role": "user", "content": "任务列表:\n" + "\n".join(lines)},
        ], timeout=timeout)
        arr = None
        if content is not None:
            obj = _extract_json_array(content)  # 兼容只输出数组
            if obj is not None:
                arr = obj
            else:
                m = re.search(r"\{.*\}", content, re.S)
                if m:
                    try:
                        obj2 = json.loads(m.group(0))
                        arr = obj2.get("actions")
                    except (ValueError, TypeError, AttributeError):
                        arr = None
        if isinstance(arr, list):
            ok = [a for a in arr if isinstance(a, dict)
                  and a.get("tool") in ("geocode", "nearby") and a.get("keyword")]
            if ok:
                return ok[:5]
    # 规则回退: 给每个缺坐标的任务提取关键词
    actions = []
    for t in tasks:
        if t.get("lat") is not None:
            continue
        kw = t.get("place") or geocode.extract_keyword(t["name"])
        if not kw:
            continue
        tool = "nearby" if re.search(r"附近|顺路|郊区|偏远|那边", t["name"]) else "geocode"
        actions.append({"tool": tool, "keyword": kw})
    return actions[:5]


def execute_tools(actions, center=None):
    """ReAct 第2步: 执行工具调用(复用 geocode.py), 返回 {关键词: (lng, lat, 名称)}"""
    results = {}
    for a in actions:
        kw = a.get("keyword")
        if not kw:
            continue
        try:
            if a.get("tool") == "nearby" and center is not None:
                res = geocode.search_nearby(kw, center)
            else:
                res = geocode.search_poi(kw)
        except Exception:
            res = None
        if res:
            results[kw] = res
    return results


def react_plan(tasks, start, center=None, seed=42, use_llm=True, options=None):
    """ReAct 全流程: 决定工具 -> 执行工具 -> 合并坐标 -> 求解器精排.
    返回: 求解器结果 + react_actions/react_results/tool_count 附加字段."""
    opts = dict(DEFAULTS)
    if options:
        opts.update(options)
    actions = react_decide_actions(tasks, center=center)
    results = execute_tools(actions, center=center or start)
    # 工具结果合并回任务(规则式, 确定性强)
    filled = 0
    for t in tasks:
        if t.get("lat") is not None:
            continue
        kw = t.get("place") or geocode.extract_keyword(t["name"])
        if kw in results:
            lng, lat, name = results[kw]
            t["lat"], t["lng"], t["place"] = lat, lng, name
            filled += 1
    # 工具结果补完后仍缺坐标的任务, 再尝试常规 POI 兜底
    missing = [t for t in tasks if t.get("lat") is None]
    if missing:
        try:
            geocode.fill_missing_coords(missing)
        except Exception:
            pass
    if any(t.get("lat") is None for t in tasks):
        res = {
            "order": [], "arrivals": [], "stats": None,
            "method": "react(incomplete-coords)", "react_actions": actions,
            "react_results": results, "tool_count": len(actions), "coord_filled": filled,
        }
        return res
    res = sa_route(tasks, start, opts, seed=seed)
    res["react_actions"] = actions
    res["react_results"] = results
    res["tool_count"] = len(actions)
    res["coord_filled"] = filled
    return res


# ---------- 对比实验 ----------

def benchmark_hybrid(n_sizes=(5, 6, 7, 8), k_each=10, use_llm=False, seed=20260903):
    """离线对比: 求解器-only vs 常识粗排-only vs 混合(粗排+求解器).
    无 key 时粗排用常识代理, 报告里会明确标注 LLM 来源."""
    rows = []
    for n in n_sizes:
        for k in range(k_each):
            rng = random.Random(seed + n * 100000 + k)
            tasks = make_instance(n, rng)
            brute = optimize_route(tasks, HOME, {"mode": "walk"})["stats"]["total"]
            opts = dict(DEFAULTS)
            opts["mode"] = "walk"
            solver = sa_route(tasks, HOME, opts, seed=n * 100 + 7)
            hybrid = hybrid_plan(tasks, HOME, algo="simanneal", use_llm=use_llm, seed=n * 100 + 7, options=opts)
            order_idx, src = coarse_rank(tasks, use_llm=use_llm)
            rank_only = evaluate_order([tasks[i] for i in order_idx], HOME, opts)["total"]
            rows.append({
                "n": n, "k": k, "brute": brute, "src": src,
                "rank_only": rank_only,
                "solver_total": solver["stats"]["total"],
                "hybrid_total": hybrid["stats"]["total"],
            })
        print("  n=%d 完成" % n, flush=True)
    return rows


def _fmt(x, ref=None):
    if ref:
        return "%.1f%%" % ((x - ref) / ref * 100)
    return "%.0f" % x


def report_benchmark(rows, use_llm):
    """把对比结果写成 实验报告3-LLM混合架构.md"""
    src = "真 LLM" if (use_llm and rows and rows[0]["src"] == "llm") else "常识代理(未配 key 的离线模拟)"
    md = []
    md.append("# 实验报告 3: LLM 混合架构 — 粗排 + 求解器精排\n")
    md.append("> 生成时间: %s  ·  粗排来源: **%s**  ·  全部种子固定可复现\n" % (time.strftime("%Y-%m-%d %H:%M"), src))
    md.append("\n## 1. 架构\n")
    md.append("```\n用户任务 -> 解析器 -> [LLM 粗排(常识)] -> [SA/GA 精排(最优)] -> 路线\n"
              "                      \\__ 语义理解  __/    \\___ 最优性 ___/\n```\n")
    md.append("LLM 只负责\"人怎么想\"(固定预约先、截止紧的先、顺路塞进去), "
              "最优性完全交给传统优化算法——失败也不会影响系统, 自动回退最近邻初始解。\n")
    md.append("\n## 2. 结果\n")
    tbl = [["n", "实例数", "常识粗排直接当路线", "纯求解器(NN起步)", "混合(粗排+求解器)", "混合相对纯求解器"]]
    for n in sorted({r["n"] for r in rows}):
        subs = [r for r in rows if r["n"] == n]
        avg = lambda key: sum(r[key] / r["brute"] for r in subs) / len(subs)
        tbl.append([
            str(n), str(len(subs)),
            "%.3f×" % avg("rank_only"),
            "%.3f×" % avg("solver_total"),
            "%.3f×" % avg("hybrid_total"),
            "%+.2f%%" % ((avg("hybrid_total") - avg("solver_total")) / avg("solver_total") * 100),
        ])
    md.append("| " + " | ".join(tbl[0]) + " |")
    md.append("| " + " | ".join(["---"] * len(tbl[0])) + " |")
    for r in tbl[1:]:
        md.append("| " + " | ".join(r) + " |")
    md.append("\n数字为 成本/暴力最优(×), 越小越好; 混合列相对纯求解器的百分比接近 0 说明")
    md.append("粗排质量已不拖后腿, 精排负责兜底。\n")
    md.append("\n## 3. 结论\n")
    md.append("- 混合架构的价值不在\"跑赢求解器\", 而在**可解释 + 可干预 + 兜底安全**: "
              "LLM 粗排把人的偏好带进解空间, 算法保证最优性, 任何一方失败都不影响系统。\n")
    md.append("- 配 `DEEPSEEK_API_KEY` 后跑 `python llm_agent.py --bench --llm` 即得真 LLM 数据。\n")
    md.append("\n## 4. ReAct 工具调用\n")
    md.append("`react_plan()` 让 LLM 决定何时调用地图工具(geocode/nearby), 用于地点含糊/偏远的任务; "
              "工具结果合并回任务后交给求解器。无 key 或无 AMAP_KEY 时自动回退规则提取关键词, 不会崩。\n")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiment", "实验报告3-LLM混合架构.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("report:", path)


# ---------- 演示 ----------

def demo():
    """跑一个直观的混合架构演示"""
    tasks = [
        {"name": "去银行办卡", "place": "银行", "lat": 31.24, "lng": 121.48,
         "priority": 2, "duration": 30, "earliest": 540, "latest": 900,
         "fixed": None, "deadline": None, "day": 0},
        {"name": "下午3点学校接孩子(重要)", "place": "学校", "lat": 31.26, "lng": 121.50,
         "priority": 3, "duration": 20, "earliest": None, "latest": None,
         "fixed": 900, "deadline": None, "day": 0},
        {"name": "顺便去超市买菜", "place": "超市", "lat": 31.235, "lng": 121.475,
         "priority": 1, "duration": 20, "earliest": None, "latest": None,
         "fixed": None, "deadline": None, "day": 0},
        {"name": "晚上7点前取快递", "place": "快递驿站", "lat": 31.228, "lng": 121.465,
         "priority": 2, "duration": 20, "earliest": None, "latest": None,
         "fixed": None, "deadline": 1140, "day": 0},
    ]
    start = {"name": "家", "lat": 31.23, "lng": 121.47}
    order_idx, src = coarse_rank(tasks, use_llm=True)
    print("粗排来源:", "真 LLM" if src == "llm" else "常识代理(无 key)")
    print("粗排顺序:", " -> ".join("%s(%s)" % (tasks[i]["name"], tasks[i]["place"]) for i in order_idx))
    for algo in ("heuristic", "simanneal", "genetic"):
        r = hybrid_plan(tasks, start, algo=algo, seed=1)
        print("混合架构[%s]: total=%d  method=%s  (粗排初始成本 %d)" % (
            algo, r["stats"]["total"], r["method"], r["llm_init_cost"]))
    # ReAct 演示(无 key 也会走规则回退, 不崩)
    r2 = react_plan(tasks, start, seed=1)
    print("ReAct 演示: 工具调用 %d 个, 补坐标 %d 个, total=%d" % (
        r2["tool_count"], r2["coord_filled"], r2["stats"]["total"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--react", metavar="TEXT")
    ap.add_argument("--llm", action="store_true", help="用真 LLM(需要配 key)")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    if args.react:
        tasks = parse_with_llm(args.react, prefer_llm=args.llm)
        start = {"name": "家", "lat": config.DEFAULT_START["lat"], "lng": config.DEFAULT_START["lng"]}
        r = react_plan(tasks, start, use_llm=args.llm)
        print("解析出 %d 个任务" % len(tasks))
        print("ReAct 决定调工具:", json.dumps(r["react_actions"], ensure_ascii=False))
        print("工具结果:", json.dumps(r["react_results"], ensure_ascii=False))
        if r["stats"] is None:
            print("提示: 任务坐标缺失且未配 AMAP_KEY, 无法规划路线(ReAct 决策部分已演示)。")
            print("配 AMAP_KEY 后重跑即可拿到完整路线。")
            return
        for i, s in enumerate(r["arrivals"]):
            hh, mm = divmod(s["arrival"], 60)
            print("  %d. %s  到达 %02d:%02d" % (i + 1, s["task"]["name"], hh, mm))
        print("total=%d, 补坐标 %d 个" % (r["stats"]["total"], r["coord_filled"]))
        return

    if args.bench:
        print("== 混合架构对比(求解器 vs 常识粗排 vs 混合) ==")
        rows = benchmark_hybrid(n_sizes=(5, 6, 7, 8), k_each=args.limit, use_llm=args.llm)
        for r in rows:
            print("  n=%d 粗排=%s 粗排only=%.0f 求解器=%.0f 混合=%.0f 最优=%d" % (
                r["n"], r["src"], r["rank_only"], r["solver_total"], r["hybrid_total"], r["brute"]))
        report_benchmark(rows, args.llm)
        print("完成: experiment/实验报告3-LLM混合架构.md")
        return

    demo()


if __name__ == "__main__":
    main()


