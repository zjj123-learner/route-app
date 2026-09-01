import json
import time
from flask import Flask, render_template, request, jsonify
from llm_parser import parse_with_llm
import config
from optimizer import optimize_route, insert_base_stops, DEFAULTS, haversine_km
from geocode import resolve_address, fill_missing_coords, search_nearby, search_candidates, extract_keyword
from config import (DEFAULT_START, HOST, PORT, DEBUG,
                    AMAP_JS_KEY, AMAP_JS_SECURITY_CODE)
import route
import db
from simanneal import sa_route
from genetic import ga_route

app = Flask(__name__)
db.init_db()   # 启动时确保表存在


def parse_hhmm(s):
    """把 '08:30' 变成一天中的第几分钟, 解析失败时返回默认 480(08:00)"""
    try:
        h, m = s.strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 480


@app.route("/")
def index():
    # 高德前端 Key/安全密钥从环境变量注入模板, 不用改 index.html
    return render_template("index.html",
                           amap_js_key=AMAP_JS_KEY,
                           amap_js_security_code=AMAP_JS_SECURITY_CODE)


@app.route("/api/plan", methods=["POST"])
def plan():
    data = request.get_json(force=True)
    text = data.get("text", "")

    mode = data.get("mode", "walk")
    if mode not in ("walk", "drive"):
        mode = "walk"

    start = {
        "name": data.get("start_name") or DEFAULT_START["name"],
        "lat": float(data.get("start_lat") or DEFAULT_START["lat"]),
        "lng": float(data.get("start_lng") or DEFAULT_START["lng"]),
    }
    try:
        buffer = int(data.get("buffer") or 0)
        buffer = buffer if 0 <= buffer <= 120 else 0
    except (TypeError, ValueError):
        buffer = 0
    options = {
        "mode": mode,
        "start_min": parse_hhmm(data.get("start_time") or "08:00"),
        "buffer": buffer,
    }

    valid_prefs = {"home", "stay"}
    prefs = {}
    raw_prefs = data.get("home_prefs") or {}
    if not raw_prefs:
        raw_prefs = db.load_prefs()   # 没传就用上次记住的偏好
    for k in ("morning", "noon", "afternoon", "evening"):
        v = raw_prefs.get(k)
        prefs[k] = v if v in valid_prefs else "home"

    tasks = parse_with_llm(text, prefer_llm=config.LLM_PARSER)
    if not tasks:
        return jsonify({"error": "没有解析出任务"}), 400

    # 每个任务先记录原始行索引 + 泛地点关键词('银行'), 关键词选过具体地点后也不变, 供候选/记忆用
    for i, t in enumerate(tasks):
        t["src"] = i
        t["_kw"] = t["place"] or extract_keyword(t["name"])

    # 用户在前一步选好的地点: {任务名: {place, lat, lng}}
    chosen = data.get("places") or {}
    for t in tasks:
        pick = chosen.get(t["name"])
        if pick and pick.get("lat") is not None:
            t["lat"] = float(pick["lat"])
            t["lng"] = float(pick["lng"])
            t["place"] = pick.get("place") or t["place"]

    # '回家'这类任务直接用起点(用户设置的家)坐标, 不靠POI猜
    for t in tasks:
        if t["place"] == "家":
            t["lat"] = start["lat"]
            t["lng"] = start["lng"]

    # '去附近XX'这类任务: 以家为中心搜周边, 而不是全城瞎猜
    for t in tasks:
        if "附近" in t["name"] and t["lat"] is None:
            result = search_nearby(extract_keyword(t["name"]), start)
            if result:
                lng, lat, name = result
                t["lat"] = lat
                t["lng"] = lng
                t["place"] = name

    filled = 0
    # B2: 地点记忆 - 记住的地点全部进候选列表, 用户自己选, 不自动代选
    filled += fill_missing_coords(tasks)

    # 文本搜索找不到的泛词(比如"超市"), 以家为中心兜底搜一次"附近的XX"
    for t in tasks:
        if t["lat"] is None:
            kw = t.get("_kw") or extract_keyword(t["name"])
            if not kw:
                continue
            result = search_nearby(kw, start)
            if result:
                lng, lat, name = result
                t["lat"] = lat
                t["lng"] = lng
                t["place"] = name
                filled += 1

    # 地点模糊: 返回候选让用户挑, 挑完带上 places 再来一次
    if not chosen:
        need_pick = []
        speed = DEFAULTS["drive_speed"] if mode == "drive" else DEFAULTS["walk_speed"]
        extra = 2 if mode == "drive" else 3
        for i, t in enumerate(tasks):
            kw = t.get("_kw")
            if not kw or t["place"] == "家":
                continue
            items = []
            seen = set()
            # 去过的地点排最前, 标'常用'
            for p in db.search_places(kw, 5):
                km = round(haversine_km(start, p), 1)
                minutes = max(1, round(km / speed * 60 + extra))
                items.append({
                    "name": p["name"], "address": p.get("address") or "",
                    "lat": p["lat"], "lng": p["lng"],
                    "km": km, "minutes": minutes, "tag": "常用",
                })
                seen.add(p["name"])
            # 高德搜索候选, 和记忆去重
            for c in search_candidates(kw, start):
                if c["name"] in seen:
                    continue
                km = round(c["distance_m"] / 1000.0, 1)
                minutes = max(1, round(c["distance_m"] / 1000.0 / speed * 60 + extra))
                items.append({
                    "name": c["name"], "address": c["address"],
                    "lat": c["lat"], "lng": c["lng"],
                    "km": km, "minutes": minutes, "tag": "搜索",
                })
            if items:
                need_pick.append({"idx": i, "name": t["name"], "keyword": kw, "candidates": items})
        if need_pick:
            return jsonify({"need_pick": need_pick}), 200

    missing = [t["name"] for t in tasks if t["lat"] is None or t["lng"] is None]
    if missing:
        return jsonify({
            "error": "这些任务的地点无法识别: " + "、".join(missing)
                     + "。请把地点说清楚, 比如'去银行''去学校'。"
        }), 400

    if len(tasks) > 30:
        return jsonify({"error": "任务太多, 一次最多30个"}), 400

    # 锁定: 前端传 [{"name": "任务名", "pos": 位置}], 锁定的任务重新排序时钉在原位
    fixed_positions = {}
    for item in data.get("locked") or []:
        name = item.get("name")
        pos = item.get("pos")
        if pos is None:
            continue
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            continue
        if not 0 <= pos < len(tasks):
            continue
        for i, t in enumerate(tasks):
            if t["name"] == name:
                fixed_positions[i] = pos
                break
    if len(set(fixed_positions.values())) != len(fixed_positions):
        return jsonify({"error": "锁定位置冲突, 两个任务不能钉在同一个位置"}), 400

    # 真实路网时间: 把所有"会去的地点"(起点+任务)两两都算一遍, 优化器直接查表
    points = [(start["lat"], start["lng"])] + [(t["lat"], t["lng"]) for t in tasks]
    time_matrix, route_real, route_total = route.build_matrix(points, mode)
    options["time_matrix"] = time_matrix
    route_info = ("真实路网时间 %d/%d 对" % (route_real, route_total) if route_real
                  else "直线估算(高德路径规划不可用, 已自动降级)")

    # 三个算法各出一条路线(复用同一张路网矩阵, 不重复请求高德)
    # 每个算法都测量: 墙钟耗时(elapsed_ms) + 评价次数(evals, 由 evaluate_order 计数)
    # 供前端"算法对比"可视化展示, 三个指标都是"越小越好".
    def _measure(fn):
        t0 = time.perf_counter()
        counter = {"n": 0}
        algo_opts = dict(options)
        algo_opts["_counter"] = counter
        rr = fn(algo_opts)
        rr["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        rr["evals"] = counter["n"]
        return rr

    route_results = {
        "heuristic": _measure(lambda o: optimize_route(tasks, start, o, fixed_positions=fixed_positions or None)),
        "simanneal": _measure(lambda o: sa_route(tasks, start, o, fixed_positions=fixed_positions or None, seed=20260826)),
        "genetic": _measure(lambda o: ga_route(tasks, start, o, fixed_positions=fixed_positions or None, seed=20260826)),
    }

    def _payload(rr):
        all_stops = insert_base_stops(rr["arrivals"], start, options, prefs=prefs)
        st = []
        for s in all_stops:
            task = s["task"]
            st.append({
                "type": s["type"],
                "name": task["name"],
                "place": task["place"],
                "lat": task["lat"],
                "lng": task["lng"],
                "priority": task["priority"],
                "duration": task["duration"],
                "arrival": s["arrival"],
                "depart": s["depart"],
                "day": task.get("day", 0),
                "src": task.get("src"),
            })
        seq = [start] + st
        lines = []
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            key = (round(a["lat"], 5), round(a["lng"], 5), round(b["lat"], 5), round(b["lng"], 5))
            val = time_matrix.get(key)
            lines.append({
                "from": {"name": a["name"], "lat": a["lat"], "lng": a["lng"]},
                "to": {"name": b["name"], "lat": b["lat"], "lng": b["lng"]},
                "points": val[2] if val else None,
                "km": val[0] if val else None,
                "minutes": val[1] if val else None,
            })
        summary = {"total_km": 0.0, "total_minutes": 0, "end_min": None}
        if st:
            summary["total_km"] = round(sum((leg["km"] or 0) for leg in lines), 1)
            summary["end_min"] = st[-1]["depart"]
            summary["total_minutes"] = st[-1]["depart"] - options["start_min"]
        return {
            "stops": st,
            "route_lines": lines,
            "summary": summary,
            "stats": rr["stats"],
            "method": rr["method"],
            "elapsed_ms": rr.get("elapsed_ms"),
            "evals": rr.get("evals"),
        }

    payloads = {k: _payload(rr) for k, rr in route_results.items()}
    best_key = min(payloads, key=lambda k: payloads[k]["stats"]["total"])
    best_payload = payloads[best_key]
    stops = best_payload["stops"]
    route_lines = best_payload["route_lines"]
    summary = best_payload["summary"]
    result = route_results[best_key]

    # 记住这次实际用到的地点, 下次'去银行'直接补坐标
    for t in tasks:
        kw = t.get("_kw") or t["place"] or extract_keyword(t["name"])
        if kw and t["lat"] is not None and t["lng"] is not None:
            db.remember_place(kw, t["place"] or kw, t["lat"], t["lng"])
    db.save_prefs(prefs)

    # 历史计划入库(以后可以翻看/恢复)
    db.save_plan(
        mode,
        start["name"], start["lat"], start["lng"],
        json.dumps(summary, ensure_ascii=False),
        json.dumps(stops, ensure_ascii=False),
    )

    return jsonify({
        "stops": stops,
        "summary": summary,
        "stats": result["stats"],
        "start": start,
        "mode": mode,
        "filled": filled,
        "route_info": route_info,
        "route_lines": route_lines,
        "method": result["method"],
        "all_routes": payloads,
        "best": best_key,
        "home_prefs": prefs,
        "locked_count": len(fixed_positions),
        "buffer": options["buffer"],
    })


@app.route("/api/places", methods=["GET"])
def places():
    """查'去过的地点', 前端做快捷选择/提示用"""
    keyword = request.args.get("keyword", "").strip()
    if not keyword:
        return jsonify({"places": []})
    return jsonify({"places": db.search_places(keyword, limit=10)})

@app.route("/api/plans", methods=["GET"])
def plans_list():
    """历史计划列表, 最近的在前面"""
    rows = db.list_plans(limit=20)
    plans = []
    for r in rows:
        try:
            summary = json.loads(r["summary"]) if r["summary"] else {}
        except Exception:
            summary = {}
        plans.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "mode": r["mode"],
            "start_name": r["start_name"],
            "summary": summary,
        })
    return jsonify({"plans": plans})


@app.route("/api/plans/<int:plan_id>", methods=["GET"])
def plan_detail(plan_id):
    """单个历史计划详情, 前端可恢复显示"""
    row = db.get_plan(plan_id)
    if not row:
        return jsonify({"error": "没有这个计划"}), 404
    try:
        stops = json.loads(row["stops_json"]) if row["stops_json"] else []
    except Exception:
        stops = []
    try:
        summary = json.loads(row["summary"]) if row["summary"] else {}
    except Exception:
        summary = {}
    return jsonify({
        "id": row["id"],
        "created_at": row["created_at"],
        "mode": row["mode"],
        "start": {"name": row["start_name"], "lat": row["start_lat"], "lng": row["start_lng"]},
        "stops": stops,
        "summary": summary,
        "route_lines": [],   # 历史计划不存轨迹, 地图画直连虚线
    })

@app.route("/api/plans", methods=["DELETE"])
def plans_clear():
    """清空历史计划"""
    db.clear_plans()
    return jsonify({"ok": True})

@app.route("/api/geocode", methods=["POST"])
def geocode():
    """把用户说的地址/地名转成经纬度, 用于自定义'家'.
    多个匹配时返回 candidates, 由前端列出来让用户自己选, 不自动代选"""
    data = request.get_json(force=True)
    address = (data.get("address") or "").strip()
    if not address:
        return jsonify({"error": "请先输入地址"}), 400
    result = resolve_address(address)
    if not result:
        return jsonify({
            "error": "找不到'" + address + "'这个位置。试试写'市+区', 比如'上海市浦东新区'; 或者直接写地标, 比如'陆家嘴'。"
        }), 400
    lng, lat, name, source = result
    candidates = []
    seen = set()
    # 用解析到的坐标做中心, 再搜一遍同名候选, 距离近的排前面
    kw = address if source == "geocode" else name
    for c in search_candidates(kw, {"lat": lat, "lng": lng}, limit=8):
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        candidates.append({
            "name": c["name"],
            "address": c.get("address") or "",
            "lat": c["lat"],
            "lng": c["lng"],
            "distance_m": c.get("distance_m") or 0,
        })
    if not candidates:
        # 没搜到别的候选(比如网络失败/Key失效), 至少给当前解析结果
        candidates.append({
            "name": name, "address": address, "lat": lat, "lng": lng, "distance_m": 0,
        })
    return jsonify({
        "name": name,
        "lat": lat,
        "lng": lng,
        "formatted": name,
        "source": source,
        "candidates": candidates,
    })


@app.route("/sw.js")
def service_worker():
    """PWA 离线外壳缓存脚本, 注册在根路径才能控制整个应用"""
    return app.send_static_file("sw.js")


if __name__ == "__main__":
    # 端口/是否调试都由 config.py 里的环境变量控制(默认 0.0.0.0:5000, debug 关)
    app.run(host=HOST, port=PORT, debug=DEBUG)
