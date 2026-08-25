from parser import parse_tasks
from optimizer import optimize_route
from geocode import extract_keyword
import os
import tempfile
import app
import route
import db
from unittest import mock

failures = 0
passed = 0


def check(condition, message):
    global failures, passed
    if condition:
        passed += 1
        print("PASS:", message)
    else:
        failures += 1
        print("FAIL:", message)



class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_parser():
    text = (
        "明天上午9点去银行办卡\n"
        "下午3点去学校接孩子放学（重要）\n"
        "顺便去超市买菜\n"
        "晚上7点前从驿站取快递回家"
    )
    tasks = parse_tasks(text)
    check(len(tasks) == 4, "解析出4条任务")

    bank = tasks[0]
    check(bank["place"] == "银行", "银行地点识别")
    check(bank["lat"] is None, "解析器不硬编码坐标(坐标交给高德)")
    check(bank["day"] == 1, "银行是明天的任务(day=1)")
    check(bank["earliest"] == 1980, "银行最早明天9:00 (1980)")
    check(bank["latest"] == 2160, "银行最晚明天12:00 (2160)")

    school = tasks[1]
    check(school["priority"] == 3, "学校高优先级(重要)")
    check(school["fixed"] == 900, "学校固定15:00 (900)")

    market = tasks[2]
    check(market["priority"] == 1, "超市低优先级(顺便)")
    check(market["duration"] == 20, "买菜20分钟")

    exp = tasks[3]
    check(exp["deadline"] == 1140, "驿站截止19:00 (1140)")


def test_optimizer():
    text = (
        "明天上午9点去银行办卡\n"
        "下午3点去学校接孩子放学（重要）\n"
        "顺便去超市买菜\n"
        "晚上7点前从驿站取快递回家"
    )
    tasks = parse_tasks(text)
    # 解析器不再硬编码坐标, 测试里统一给个同地坐标即可
    for t in tasks:
        if t["lat"] is None:
            t["lat"], t["lng"] = 31.23, 121.47
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    result = optimize_route(tasks, start)

    check(len(result["order"]) == 4, "排序返回4条任务")

    for s in result["arrivals"]:
        task = s["task"]
        if task["fixed"] is not None:
            check(s["arrival"] <= task["fixed"], "固定预约不迟到: " + task["name"])
            check(s["arrival"] >= task["fixed"] - 60, "固定预约不过早到: " + task["name"])
        if task["latest"] is not None:
            check(s["arrival"] <= task["latest"], "时间窗内到达: " + task["name"])
        if task["deadline"] is not None:
            check(s["depart"] <= task["deadline"], "截止前完成: " + task["name"])


def test_config():
    """config.py: 配置从环境变量读, 默认关 debug"""
    import config
    import importlib
    from unittest import mock
    check(config.DEBUG is False, "config: 默认 debug 关闭")
    check(config.HOST in ("0.0.0.0", "127.0.0.1"), "config: 默认监听地址合法")
    check(isinstance(config.DEFAULT_START.get("lat"), float), "config: 默认起点带坐标")
    with mock.patch.dict(os.environ, {"PORT": "8080", "FLASK_DEBUG": "1", "DEFAULT_CITY": "上海"}, clear=False):
        importlib.reload(config)
        check(config.PORT == 8080, "config: PORT 从环境变量读取")
        check(config.DEBUG is True, "config: FLASK_DEBUG=1 打开调试")
        check(config.DEFAULT_CITY == "上海", "config: DEFAULT_CITY 可配置")
    importlib.reload(config)
    check(config.DEBUG is False, "config: 还原后默认 debug 仍关闭")


def test_duration_window():
    """用户写清楚起止时间段时, 时长用时段的长度, 而不是固定半小时"""
    t = parse_tasks("上午9点到12点去银行办事")[0]
    check(t["duration"] == 180, "时长窗: 9点到12点办事=180分钟")
    check(t["earliest"] == 540 and t["latest"] == 720, "时长窗: 9点到12点=最早9:00最晚12:00")

    t = parse_tasks("上午9点到9点半取快递")[0]
    check(t["duration"] == 30, "时长窗: 9点到9点半=30分钟")
    check(t["latest"] == 570, "时长窗: '点半'解析为30分(9:30)")

    t = parse_tasks("上午9点到晚上8点去公司上班")[0]
    check(t["duration"] == 480, "时长窗: 9点到20点封顶8小时(480分钟)")

    t = parse_tasks("明天上午9点去银行办卡")[0]
    check(t["duration"] == 30, "时长窗: 单时间点不猜时长, 保持默认30")

    t = parse_tasks("下午3点到5点去学校上学")[0]
    check(t["earliest"] == 900 and t["latest"] == 1020, "时段继承: 下午3点到5点=15:00-17:00")
    check(t["duration"] == 120, "时段继承: 下午3点到5点上学=120分钟")

    t = parse_tasks("晚上8点到10点去超市")[0]
    check(t["earliest"] == 1200 and t["latest"] == 1320, "时段继承: 晚上8点到10点=20:00-22:00")

    t = parse_tasks("中午12点到2点去吃饭")[0]
    check(t["earliest"] == 720 and t["latest"] == 840, "时段继承: 中午12点到2点=12:00-14:00")

    t = parse_tasks("晚上7点前从驿站取快递回家")[0]
    check(t["duration"] == 20, "时长窗: 截止时间按关键词取快递=20")

    t = parse_tasks("晚上去超市买菜")[0]
    check(t["duration"] == 20, "时长窗: 模糊时段按关键词买菜=20")


def test_buffer():
    from optimizer import travel_minutes, DEFAULTS
    a = {"name": "A", "lat": 31.23, "lng": 121.47}
    b = {"name": "B", "lat": 31.24, "lng": 121.48}
    base_opts = {"mode": "walk", "walk_speed": 4.5}
    t0 = travel_minutes(a, b, base_opts)
    check(DEFAULTS.get("buffer") == 0, "默认余量为0(不改变原有行为)")
    check(travel_minutes(a, b, dict(base_opts, buffer=0)) == t0, "余量0时路程时间不变")
    check(travel_minutes(a, b, dict(base_opts, buffer=10)) == t0 + 10, "余量10: 每段路程加10分钟")
    tm = {(round(a["lat"], 5), round(a["lng"], 5), round(b["lat"], 5), round(b["lng"], 5)): (3.0, 30)}
    check(travel_minutes(a, b, dict(base_opts, time_matrix=tm)) == 30, "余量: 矩阵真实时间优先")
    check(travel_minutes(a, b, dict(base_opts, time_matrix=tm, buffer=5)) == 35, "余量: 矩阵真实时间+5")

    tasks = [
        {"name": "去银行", "place": None, "lat": 31.23, "lng": 121.47,
         "priority": 2, "duration": 30, "earliest": None, "latest": None, "fixed": None, "deadline": None, "day": 0},
        {"name": "去学校", "place": None, "lat": 31.25, "lng": 121.49,
         "priority": 2, "duration": 30, "earliest": None, "latest": None, "fixed": None, "deadline": None, "day": 0},
    ]
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    r0 = optimize_route(tasks, start, {"mode": "walk", "start_min": 480, "buffer": 0})
    r1 = optimize_route(tasks, start, {"mode": "walk", "start_min": 480, "buffer": 10})
    check(r1["stats"]["travel"] == r0["stats"]["travel"] + 20, "余量: 两段路总共多出20分钟")
    a0 = {s["task"]["name"]: s for s in r0["arrivals"]}
    a1 = {s["task"]["name"]: s for s in r1["arrivals"]}
    check(all(a1[n]["arrival"] > a0[n]["arrival"] for n in a0), "余量: 所有任务到达时间都后移")


def test_priority_order():
    tasks = [
        {"name": "低优先级小事", "place": "同地", "lat": 31.23, "lng": 121.47,
         "priority": 1, "duration": 20, "earliest": None, "latest": None, "fixed": None, "deadline": None},
        {"name": "高优先级要事", "place": "同地", "lat": 31.23, "lng": 121.47,
         "priority": 3, "duration": 30, "earliest": None, "latest": None, "fixed": None, "deadline": None},
        {"name": "中优先级事项", "place": "同地", "lat": 31.23, "lng": 121.47,
         "priority": 2, "duration": 25, "earliest": None, "latest": None, "fixed": None, "deadline": None},
    ]
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    result = optimize_route(tasks, start)
    order = [t["name"] for t in result["order"]]
    check(order[0] == "高优先级要事", "高优先级排最前, 实际: " + " → ".join(order))
    check(order[2] == "低优先级小事", "低优先级排最后, 实际: " + " → ".join(order))


def test_heuristic():
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    many = [
        {"name": f"任务{i}", "place": None, "lat": 31.20 + i * 0.003, "lng": 121.44 + (i * 7 % 5) * 0.004,
         "priority": 2, "duration": 20, "earliest": None, "latest": None, "fixed": None, "deadline": None}
        for i in range(12)
    ]
    result = optimize_route(many, start)
    check(result["method"] == "heuristic", "12个任务走启发式算法")
    check(len(result["order"]) == 12, "12个任务全部排序")
    check({id(t) for t in result["order"]} == {id(t) for t in many}, "没有任务丢失或重复")

    from optimizer import nearest_neighbor, evaluate_order, DEFAULTS
    opts = dict(DEFAULTS)
    nn = nearest_neighbor(many, start, opts)
    nn_total = evaluate_order(nn, start, opts)["total"]
    check(result["stats"]["total"] <= nn_total,
          "2-opt 不劣化 (最近邻 %d -> %d)" % (nn_total, result["stats"]["total"]))

    res = optimize_route(many[:8], start)
    check(res["method"] == "brute-force", "8个任务仍是暴力枚举")


def test_home_prefs():
    from optimizer import period_of, insert_base_stops

    check(period_of(480) == "morning", "8:00 属于上午")
    check(period_of(720) == "noon", "12:00 属于中午")
    check(period_of(900) == "afternoon", "15:00 属于下午")
    check(period_of(1200) == "evening", "20:00 属于晚上")
    check(period_of(300) is None, "凌晨5点不属于任何时段")

    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    opts = {"mode": "walk", "start_min": 480}
    t1 = {"name": "a", "place": "a", "lat": 31.23, "lng": 121.47, "priority": 2, "duration": 30,
          "earliest": None, "latest": None, "fixed": None, "deadline": None}
    t2 = {"name": "b", "place": "b", "lat": 31.23, "lng": 121.47, "priority": 2, "duration": 30,
          "earliest": None, "latest": None, "fixed": None, "deadline": None}

    # 8:30 走 -> 11:30 到, 空隙180分钟, 中点10:00 -> 上午
    arr = [
        {"task": t1, "arrival": 480, "depart": 510, "travel": 5},
        {"task": t2, "arrival": 690, "depart": 720, "travel": 5},
    ]
    stops = insert_base_stops(arr, start, opts)
    check(any(s["type"] == "base" for s in stops), "auto: 长空隙插入回家休息")

    stops = insert_base_stops(arr, start, opts, prefs={"morning": "stay"})
    check(not any(s["type"] == "base" for s in stops), "stay: 上午不回家, 长空隙也不插")

    stops = insert_base_stops(arr, start, opts, prefs={"morning": "home"})
    check(any(s["type"] == "base" for s in stops), "home: 强制回家")

    # 55分钟的小空隙: auto 不插(不到60分钟), home 插
    arr2 = [
        {"task": t1, "arrival": 480, "depart": 510, "travel": 5},
        {"task": t2, "arrival": 565, "depart": 595, "travel": 5},
    ]
    stops = insert_base_stops(arr2, start, opts)
    check(not any(s["type"] == "base" for s in stops), "auto: 55分钟空隙不回家")
    stops = insert_base_stops(arr2, start, opts, prefs={"morning": "home"})
    check(any(s["type"] == "base" for s in stops), "home: 55分钟空隙也回家")


def test_locked():
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    tasks = [
        {"name": f"任务{i}", "place": None, "lat": 31.20 + i * 0.003, "lng": 121.44 + (i * 7 % 5) * 0.004,
         "priority": 2, "duration": 20, "earliest": None, "latest": None, "fixed": None, "deadline": None}
        for i in range(7)
    ]
    # 暴力分支: 锁定任务2到第1个位置
    res = optimize_route(tasks, start, fixed_positions={2: 0})
    check(res["order"][0] is tasks[2], "锁定: 任务2固定在开头")

    res = optimize_route(tasks, start, fixed_positions={0: 6})
    check(res["order"][6] is tasks[0], "锁定: 任务0固定在末尾")

    # 启发式分支(12个任务)带锁
    many = [
        {"name": f"任务{i}", "place": None, "lat": 31.20 + i * 0.003, "lng": 121.44 + (i * 7 % 5) * 0.004,
         "priority": 2, "duration": 20, "earliest": None, "latest": None, "fixed": None, "deadline": None}
        for i in range(12)
    ]
    res = optimize_route(many, start, fixed_positions={0: 0, 11: 11})
    check(res["method"] == "heuristic", "启发式+锁: method")
    check(res["order"][0] is many[0], "启发式+锁: 任务0固定在开头")
    check(res["order"][11] is many[11], "启发式+锁: 任务11固定在末尾")
    check(len({id(t) for t in res["order"]}) == 12, "启发式+锁: 任务不丢不重")

    # 位置冲突
    try:
        optimize_route(tasks, start, fixed_positions={0: 1, 1: 1})
        check(False, "锁定位置冲突应报错")
    except ValueError:
        check(True, "锁定位置冲突正确报错")

def test_multiday():
    import datetime
    from optimizer import insert_base_stops

    today = datetime.date(2026, 8, 19)  # 2026-08-19 是周三

    # 明天/后天/今天
    tasks = parse_tasks("明天上午9点去银行办卡\n后天下午3点去学校接孩子放学（重要）\n今天去超市买菜", today=today)
    check(len(tasks) == 3, "多日: 解析出3条任务")
    check(tasks[0]["day"] == 1 and tasks[0]["earliest"] == 1980, "多日: 明天9点 = 第1天1980")
    check(tasks[1]["day"] == 2 and tasks[1]["fixed"] == 3780, "多日: 后天15点 = 第2天3780")
    check(tasks[2]["day"] == 0, "多日: 今天 = 第0天")

    # 有日期没具体时间: 默认当天完成
    t = parse_tasks("明天去银行取钱", today=today)[0]
    check(t["day"] == 1 and t["earliest"] == 1800 and t["latest"] == 2879,
          "多日: 明天(没写几点)默认6:00-24:00")

    # 周几
    check(parse_tasks("周三下午2点面试", today=today)[0]["day"] == 0, "多日: 今天是周三, 周三=今天")
    check(parse_tasks("周五上午10点开会", today=today)[0]["day"] == 2, "多日: 周三说周五=后天")
    check(parse_tasks("周一下午2点开会", today=today)[0]["day"] == 5, "多日: 周三说周一=下周一")

    # 数字日期
    check(parse_tasks("8月20日上午10点开会", today=today)[0]["day"] == 1, "多日: 8月20日=明天")

    # 排序: 今天的先做, 明天的后做
    tasks2 = parse_tasks("明天上午9点去银行办卡\n上午8点去学校开会（重要）", today=today)
    for t in tasks2:
        t["lat"], t["lng"] = 31.23, 121.47
    start = {"name": "家", "lat": 31.235, "lng": 121.47}
    res = optimize_route(tasks2, start)
    check(res["arrivals"][0]["arrival"] < 1440 and res["arrivals"][1]["arrival"] >= 1440,
          "多日: 今天的任务排在明天任务前面")

    # 过夜空隙不插'回家休息'
    t1 = {"name": "a", "place": "a", "lat": 31.23, "lng": 121.47, "priority": 2, "duration": 30,
          "earliest": None, "latest": None, "fixed": None, "deadline": None}
    t2 = {"name": "b", "place": "b", "lat": 31.23, "lng": 121.47, "priority": 2, "duration": 30,
          "earliest": None, "latest": None, "fixed": None, "deadline": None}
    arr = [
        {"task": t1, "arrival": 17 * 60, "depart": 18 * 60, "travel": 5},
        {"task": t2, "arrival": 9 * 60 + 1440, "depart": 10 * 60 + 1440, "travel": 5},
    ]
    stops = insert_base_stops(arr, start, {"mode": "walk", "start_min": 480})
    check(len(stops) == 2 and not any(s["type"] == "base" for s in stops),
          "多日: 过夜空隙不插休息且任务不丢")

    # 同日长空隙仍然会插
    arr3 = [
        {"task": t1, "arrival": 480, "depart": 510, "travel": 5},
        {"task": t2, "arrival": 690, "depart": 720, "travel": 5},
    ]
    stops3 = insert_base_stops(arr3, start, {"mode": "walk", "start_min": 480})
    check(any(s["type"] == "base" for s in stops3), "多日: 同一天的长空隙仍会插回家休息")

def test_db():
    import os
    import tempfile
    import db

    db.DB_PATH = os.path.join(tempfile.gettempdir(), "route_test_%d.db" % os.getpid())
    db.init_db()

    db.remember_place("银行", "招商银行", 31.23, 121.47, "上海南京东路")
    db.remember_place("银行", "中国银行", 31.24, 121.48, "上海人民广场")
    rows = db.search_places("银行")
    check(len(rows) == 2, "db: 记住两个银行")
    check(rows[0]["name"] in ("招商银行", "中国银行"), "db: 按最近使用排序")

    # 再次用到'中国银行'会把它顶到最前面
    db.remember_place("银行", "中国银行", 31.24, 121.48, "上海人民广场")
    rows = db.search_places("银行")
    check(rows[0]["name"] == "中国银行", "db: 最近使用的地点排最前")

    db.remember_place("超市", "大润发", 31.25, 121.49)
    check(len(db.search_places("超市")) == 1, "db: 不同关键词分开记")
    check(len(db.search_places("银行")) == 2, "db: 超市不影响银行记录")

    db.save_prefs({"morning": "stay", "noon": "home"})
    prefs = db.load_prefs()
    check(prefs.get("morning") == "stay", "db: prefs可保存读取")

    try:
        os.remove(db.DB_PATH)
    except OSError:
        pass


def test_plan_memory():
    """B2: 记住的地点进候选列表(标常用), 不自动代选; 选中后生效"""
    import os
    import tempfile
    import app
    import route
    import db
    from unittest import mock

    db.DB_PATH = os.path.join(tempfile.gettempdir(), "route_test_%d.db" % os.getpid())
    db.init_db()
    db.remember_place("银行", "招商银行", 31.23, 121.47, "上海南京东路")

    def fake_get(url, params=None, timeout=None):
        if "walking" in url:
            return _FakeResp({"status": "1", "route": {"paths": [{"distance": "3000", "duration": "1800", "steps": []}]}})
        return _FakeResp({"status": "0", "info": "INVALID_USER_KEY"})

    route.REQUEST_INTERVAL = 0
    route.clear_cache()
    try:
        with mock.patch.object(route.requests, "get", side_effect=fake_get):
            client = app.app.test_client()
            text = "上午9点去银行办卡"
            resp = client.post("/api/plan", json={
                "text": text,
                "mode": "walk", "start_time": "08:00", "start_name": "家",
                "start_lat": 31.235, "start_lng": 121.47,
            })
            data = resp.get_json() or {}
            check(resp.status_code == 200, "记忆: /api/plan 返回200")
            check("need_pick" in data, "记忆: 记住一家也弹候选")
            cands = data["need_pick"][0]["candidates"]
            check(any(c["name"] == "招商银行" and c["tag"] == "常用" for c in cands),
                  "记忆: 记住的地点标常用")

            # 选招商银行后带 places 再来 → 不弹, 生效
            names = [t["name"] for t in parse_tasks(text)]
            resp2 = client.post("/api/plan", json={
                "text": text,
                "mode": "walk", "start_time": "08:00", "start_name": "家",
                "start_lat": 31.235, "start_lng": 121.47,
                "places": {names[0]: {"place": "招商银行", "lat": 31.23, "lng": 121.47}},
            })
            d2 = resp2.get_json() or {}
            check(resp2.status_code == 200 and any(s["place"] == "招商银行" for s in d2["stops"]),
                  "记忆: 选中后生效")

            # 查 /api/places 能看到记住的地点
            r2 = client.get("/api/places?keyword=" + "银行")
            d3 = r2.get_json() or {}
            check(len(d3.get("places") or []) >= 1, "记忆: /api/places 返回记住的地点")
    finally:
        route.clear_cache()
        try:
            os.remove(db.DB_PATH)
        except OSError:
            pass


def test_plan_memory_multi():
    """记住多家银行时弹候选, 记忆排最前; 选另一家后记忆更新"""
    import os
    import tempfile
    import app
    import route
    import db
    import geocode
    from unittest import mock

    db.DB_PATH = os.path.join(tempfile.gettempdir(), "route_test_%d.db" % os.getpid())
    db.init_db()
    db.remember_place("银行", "招商银行", 31.23, 121.47, "上海南京东路")
    db.remember_place("银行", "中国银行", 31.24, 121.48, "上海人民广场")

    def fake_get(url, params=None, timeout=None):
        if "walking" in url:
            return _FakeResp({"status": "1", "route": {"paths": [{"distance": "3000", "duration": "1800", "steps": []}]}})
        return _FakeResp({"status": "0", "info": "INVALID_USER_KEY"})

    def fake_geo(url, params=None, timeout=None):
        if "place/around" in url:
            return _FakeResp({"status": "1", "pois": [
                {"name": "建设银行", "address": "上海徐汇", "location": "121.46,31.22", "distance": "1200"},
            ]})
        return _FakeResp({"status": "0", "info": "INVALID_USER_KEY"})

    route.REQUEST_INTERVAL = 0
    route.clear_cache()
    try:
        with mock.patch.object(route.requests, "get", side_effect=fake_get), \
             mock.patch.object(geocode.requests, "get", side_effect=fake_geo):
            client = app.app.test_client()
            resp = client.post("/api/plan", json={
                "text": "上午9点去银行办卡",
                "mode": "walk", "start_time": "08:00", "start_name": "家",
                "start_lat": 31.235, "start_lng": 121.47,
            })
            data = resp.get_json() or {}
            check(resp.status_code == 200, "多记忆: /api/plan 返回200")
            check("need_pick" in data, "多记忆: 记住多家时弹候选")
            cands = data["need_pick"][0]["candidates"]
            check(cands[0]["name"] == "中国银行" and cands[0]["tag"] == "常用",
                  "多记忆: 最近用的银行排最前并标常用")
            check(any(c["tag"] == "搜索" for c in cands), "多记忆: 高德搜索候选也保留")

            # 选'中国银行'后带 places 再来 → 不弹, 生效, 记忆更新
            names = [t["name"] for t in parse_tasks("上午9点去银行办卡")]
            resp2 = client.post("/api/plan", json={
                "text": "上午9点去银行办卡",
                "mode": "walk", "start_time": "08:00", "start_name": "家",
                "start_lat": 31.235, "start_lng": 121.47,
                "places": {names[0]: {"place": "中国银行", "lat": 31.24, "lng": 121.48}},
            })
            d2 = resp2.get_json() or {}
            check(resp2.status_code == 200 and any(s["place"] == "中国银行" for s in d2["stops"]),
                  "多记忆: 选中中国银行生效")
            rows = db.search_places("银行")
            check(rows[0]["name"] == "中国银行", "多记忆: 中国银行更新为最近使用")
    finally:
        route.clear_cache()
        try:
            os.remove(db.DB_PATH)
        except OSError:
            pass

def test_plans_api():
    """B3: 历史计划列表/详情接口"""
    import os
    import tempfile
    import app
    import route
    import db
    from unittest import mock

    db.DB_PATH = os.path.join(tempfile.gettempdir(), "route_plans_%d.db" % os.getpid())
    db.init_db()

    def fake_get(url, params=None, timeout=None):
        if "walking" in url:
            return _FakeResp({"status": "1", "route": {"paths": [{"distance": "3000", "duration": "1800", "steps": []}]}})
        return _FakeResp({"status": "0", "info": "INVALID_USER_KEY"})

    route.REQUEST_INTERVAL = 0
    route.clear_cache()
    try:
        with mock.patch.object(route.requests, "get", side_effect=fake_get):
            client = app.app.test_client()
            text = "上午9点去银行办卡"
            names = [t["name"] for t in parse_tasks(text)]
            resp = client.post("/api/plan", json={
                "text": text, "mode": "walk", "start_time": "08:00",
                "start_name": "家", "start_lat": 31.235, "start_lng": 121.47,
                "places": {names[0]: {"place": "银行", "lat": 31.23, "lng": 121.48}},
            })
            check(resp.status_code == 200, "历史: 排一次计划返回200")

            r = client.get("/api/plans")
            plans = (r.get_json() or {}).get("plans") or []
            check(len(plans) >= 1, "历史: /api/plans 返回列表")
            check(plans[0]["summary"].get("end_min") is not None, "历史: 列表带总览")

            rid = plans[0]["id"]
            r2 = client.get("/api/plans/%d" % rid)
            d2 = r2.get_json() or {}
            check(r2.status_code == 200 and len(d2.get("stops") or []) >= 1, "历史: 详情带 stops")
            check(d2["start"]["name"] == "家", "历史: 详情带起点")
            check(any(s["place"] == "银行" for s in d2.get("stops") or []), "历史: 详情地点正确")

            r3 = client.get("/api/plans/999999")
            check(r3.status_code == 404, "历史: 不存在的计划返回404")
            r4 = client.delete("/api/plans")
            check(r4.status_code == 200, "历史: 清空接口可用")
            r5 = client.get("/api/plans")
            check(len((r5.get_json() or {}).get("plans") or []) == 0, "历史: 清空后列表为空")
    finally:
        route.clear_cache()
        try:
            os.remove(db.DB_PATH)
        except OSError:
            pass



def test_keyword():
    check(extract_keyword("去附近饭店吃饭") == "饭店", "附近饭店 -> 饭店")
    check(extract_keyword("中午去银行取钱") == "银行", "取钱 -> 银行")


def test_route_api():
    import route
    from unittest import mock

    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        if "walking" in url:
            return _FakeResp({"status": "1", "route": {"paths": [{"distance": "3000", "duration": "1800"}]}})
        return _FakeResp({"status": "0", "info": "INVALID_USER_KEY"})

    route.REQUEST_INTERVAL = 0  # 测试时不限速
    with mock.patch.object(route.requests, "get", side_effect=fake_get):
        route.clear_cache()
        km, mins = route.route_minutes("walk", (31.23, 121.47), (31.24, 121.48))
        check(abs(km - 3.0) < 1e-6, "route: 解析真实距离3km")
        check(mins == 30, "route: 解析真实时间30分钟")
        route.route_minutes("walk", (31.23, 121.47), (31.24, 121.48))
        check(calls["n"] == 1, "route: 同一对地点走缓存, 不重复请求")
        km2, mins2 = route.route_minutes("drive", (31.23, 121.47), (31.24, 121.48))
        check(km2 > 0 and mins2 > 0, "route: 驾车失败自动回退直线估算")
    route.clear_cache()


def test_route_matrix():
    import route
    from unittest import mock

    def fake_get(url, params=None, timeout=None):
        return _FakeResp({"status": "1", "route": {"paths": [{"distance": "2000", "duration": "600"}]}})

    route.REQUEST_INTERVAL = 0
    with mock.patch.object(route.requests, "get", side_effect=fake_get):
        route.clear_cache()
        pts = [(31.23, 121.47), (31.24, 121.48), (31.25, 121.49)]
        matrix, real_ok, total = route.build_matrix(pts, "walk")
        check(total == 6, "route: 3个点共有6对有向对")
        check(real_ok == 6, "route: 6对全部取到真实时间")
        check(matrix[(round(31.23, 5), round(121.47, 5), round(31.24, 5), round(121.48, 5))][1] == 10,
              "route: 矩阵里2000米/600秒=10分钟")
        check(matrix[(round(31.23, 5), round(121.47, 5), round(31.23, 5), round(121.47, 5))][1] == 0,
              "route: 自己到自己0分钟")
        # 第二次(缓存全命中)也必须算真实路网, 不然误报'直线估算'
        matrix2, real_ok2, total2 = route.build_matrix(pts, "walk")
        check(real_ok2 == 6, "route: 缓存命中也算真实路网, 不误报直线估算")
        check(total2 == 6, "route: 缓存命中仍返回6对有向对")
    route.clear_cache()


def test_optimizer_uses_route():
    from optimizer import travel_minutes, DEFAULTS

    opts = dict(DEFAULTS)
    opts["time_matrix"] = {
        (round(31.235, 5), round(121.47, 5), round(31.23, 5), round(121.47, 5)): (1.0, 99),
    }
    a = {"lat": 31.235, "lng": 121.47}
    b = {"lat": 31.23, "lng": 121.47}
    check(travel_minutes(a, b, opts) == 99, "optimizer: 矩阵里的路用真实时间99分钟")
    c = {"lat": 31.25, "lng": 121.49}
    tt = travel_minutes(a, c, opts)
    check(0 < tt < 99, "optimizer: 矩阵外回退直线估算")


def test_route_polyline():
    import route
    from unittest import mock

    def fake_get(url, params=None, timeout=None):
        return _FakeResp({"status": "1", "route": {"paths": [{
            "distance": "2000", "duration": "600",
            "steps": [
                {"polyline": "121.47,31.23;121.48,31.24"},
                {"polyline": "121.49,31.25"},
            ],
        }]}})

    route.REQUEST_INTERVAL = 0
    with mock.patch.object(route.requests, "get", side_effect=fake_get):
        route.clear_cache()
        km, mins, poly, real = route._fetch_route("walk", (31.23, 121.47), (31.25, 121.49))
        check(km == 2.0 and mins == 10, "polyline: 距离/时间照常解析")
        check(real is True, "polyline: 真实路网标记为True")
        check(poly == [(31.23, 121.47), (31.24, 121.48), (31.25, 121.49)],
              "polyline: 轨迹按(纬度,经度)拼接")
        km2, mins2 = route.route_minutes("walk", (31.23, 121.47), (31.25, 121.49))
        check(km2 == 2.0 and mins2 == 10, "polyline: route_minutes 仍返回 (km, minutes)")
        matrix, real_ok, total = route.build_matrix([(31.23, 121.47), (31.25, 121.49)], "walk")
        key = (round(31.23, 5), round(121.47, 5), round(31.25, 5), round(121.49, 5))
        check(matrix[key][2] == poly, "polyline: 矩阵里存了轨迹坐标")
        check(matrix[(round(31.23, 5), round(121.47, 5), round(31.23, 5), round(121.47, 5))][2] == [],
              "polyline: 自环轨迹为空列表")
    route.clear_cache()


def test_route_fail_retry():
    import route
    from unittest import mock

    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return _FakeResp({"status": "0", "info": "BUSY"})

    route.REQUEST_INTERVAL = 0
    with mock.patch.object(route.requests, "get", side_effect=fake_get):
        route.clear_cache()
        km, mins = route.route_minutes("walk", (31.23, 121.47), (31.24, 121.48))
        check(km > 0 and mins > 0, "fail: 失败时回退直线估算")
        route.route_minutes("walk", (31.23, 121.47), (31.24, 121.48))
        check(calls["n"] == 1, "fail: 60秒内失败不重试")

        # 假装这单失败是很久以前的事, 过期后应该重新请求
        key = route._pair_key("walk", (31.23, 121.47), (31.24, 121.48))
        old = list(route._cache[key])
        old[4] = old[4] - 9999
        route._cache[key] = tuple(old)
        route.route_minutes("walk", (31.23, 121.47), (31.24, 121.48))
        check(calls["n"] == 2, "fail: 过期后自动重试")
    route.clear_cache()


def test_plan_api_route_lines():
    """e2e: /api/plan 返回的响应里真的带轨迹坐标, 前端能画真实路线"""
    import app
    import route
    from unittest import mock

    import db
    # 用独立临时库, 避免被其它测试的 DB_PATH 影响
    db.DB_PATH = os.path.join(tempfile.gettempdir(), "route_e2e_%d.db" % os.getpid())
    db.init_db()

    def fake_get(url, params=None, timeout=None):
        if "walking" in url:
            return _FakeResp({"status": "1", "route": {"paths": [{
                "distance": "3000", "duration": "1800",
                "steps": [
                    {"polyline": "121.47,31.23;121.48,31.24"},
                    {"polyline": "121.49,31.25"},
                ],
            }]}})
        return _FakeResp({"status": "0", "info": "INVALID_USER_KEY"})

    route.REQUEST_INTERVAL = 0
    route.clear_cache()
    try:
        with mock.patch.object(route.requests, "get", side_effect=fake_get):
            client = app.app.test_client()
            text = "上午9点去银行办卡\n下午3点去学校接孩子放学"
            # places 的 key 必须是解析后的任务名, 这里直接复用解析器生成
            names = [t["name"] for t in parse_tasks(text)]
            resp = client.post("/api/plan", json={
                "text": text,
                "mode": "walk",
                "start_time": "08:00",
                "buffer": 10,
                "start_name": "家",
                "start_lat": 31.235,
                "start_lng": 121.47,
                "places": {
                    names[0]: {"place": "银行", "lat": 31.23, "lng": 121.48},
                    names[1]: {"place": "学校", "lat": 31.25, "lng": 121.49},
                },
            })
            data = resp.get_json() or {}
            check(resp.status_code == 200, "e2e: /api/plan 返回200")
            check(len(data.get("route_lines") or []) >= 2, "e2e: 响应带route_lines(至少两段)")

            polies = [seg["points"] for seg in data.get("route_lines") or [] if seg.get("points")]
            check(len(polies) >= 1, "e2e: 有真实轨迹坐标, 不是全None")
            # JSON 序列化后是 list, 不是元组
            check(polies[0] == [[31.23, 121.47], [31.24, 121.48], [31.25, 121.49]],
                  "e2e: 轨迹坐标按(纬度,经度)拼接")

            ok_range = all(
                abs(p[0]) <= 90 and abs(p[1]) <= 180
                for seg in data.get("route_lines") or []
                for p in (seg.get("points") or [])
            )
            check(ok_range, "e2e: 轨迹坐标都在合法经纬度范围")

            # 每段带真实距离/时间, 前端显示'← 从家来 · 3km · 步行30分钟'
            seg0 = data["route_lines"][0]
            check(seg0.get("km") == 3.0 and seg0.get("minutes") == 30,
                  "e2e: 每段带真实km和minutes")

            # 计划总览: 总路程/总耗时/预计结束
            summary = data.get("summary") or {}
            check(summary.get("total_km") is not None, "e2e: summary带总路程")
            check(summary.get("total_minutes") is not None and summary["total_minutes"] > 0,
                  "e2e: summary带总耗时")
            check(summary.get("end_min") is not None, "e2e: summary带预计结束时间")
            check(data.get("buffer") == 10, "e2e: buffer余量参数生效(10)")
            task_stops = [s for s in data.get("stops") or [] if s["type"] == "task"]
            check(sorted(s.get("src") for s in task_stops) == [0, 1], "e2e: 任务带原始行索引src")
            base_stops = [s for s in data.get("stops") or [] if s["type"] == "base"]
            if base_stops:
                check(all(s.get("src") is None for s in base_stops), "e2e: 回家休息不带src")
    finally:
        route.clear_cache()


if __name__ == "__main__":
    test_parser()
    test_optimizer()
    test_priority_order()
    test_heuristic()
    test_duration_window()
    test_config()
    test_buffer()
    test_home_prefs()
    test_multiday()
    test_locked()
    test_db()
    test_plan_memory()
    test_plan_memory_multi()
    test_keyword()
    test_route_api()
    test_route_matrix()
    test_optimizer_uses_route()
    test_route_polyline()
    test_route_fail_retry()
    test_plan_api_route_lines()
    test_plans_api()
    print(f"\n共 {passed + failures} 项, 通过 {passed}, 失败 {failures}")
    if failures:
        exit(1)