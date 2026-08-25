# -*- coding: utf-8 -*-
"""route.py —— 高德路径规划: 真实步行/驾车时间

上一版 optimizer 用'两点直线距离 x 估算速度'算路程时间, 误差大.
这一版调高德'路径规划'Web服务, 拿到真实路网的距离和时间:

  步行: https://restapi.amap.com/v3/direction/walking
  驾车: https://restapi.amap.com/v3/direction/driving
  参数: origin=经度,纬度  destination=经度,纬度  key=你的Key

设计:
  1. 缓存: 同一对地点只请求一次, 免得排序时反复刷 API
  2. 限速: 高德免费配额约 3 次/秒, 两次请求之间至少隔 0.35 秒
  3. 兜底: 请求失败/Key 无效/解析不了, 自动回退直线估算, 程序不会崩
  4. 轨迹: 顺手把每段路线的 polyline 坐标也取出来, 前端画真实路线
  5. 标记: 缓存值带'是不是真实路网'标记, 缓存命中也算真实, 不误报降级
  6. 失败临时缓存: 失败结果 60 秒内不重试, 到期自动重试, 降级是临时的
"""
import math
import time
import requests
from geocode import AMAP_KEY  # 复用 geocode.py 里的 Key(已带 .strip())

# 直线兜底速度 (km/h), 只在拿不到真实路网时用
FALLBACK_SPEED = {"walk": 4.5, "drive": 35.0}

# 两次请求最小间隔 (秒), 高德免费 Key 约 3 次/秒
REQUEST_INTERVAL = 0.35

# 失败结果短时缓存(秒): 这段时间内不重复请求, 到期自动重试, 降级是临时的
FAIL_TTL = 60

_cache = {}          # key -> (km, minutes, polyline, is_real, fetched_at)
_last_request = 0.0  # 上次发请求的时间, 用来限速


def _haversine_km(a, b):
    """a, b 是 (lat, lng) 元组, 返回直线公里数(兜底用)"""
    R = 6371.0
    dlat = math.radians(b[0] - a[0])
    dlng = math.radians(b[1] - a[1])
    la1 = math.radians(a[0])
    la2 = math.radians(b[0])
    h = (math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def _straight(mode, a, b):
    """直线估算, 返回 (km, minutes, None, False)"""
    km = _haversine_km(a, b)
    return round(km, 1), round(km / FALLBACK_SPEED[mode] * 60), None, False


def _pair_key(mode, a, b):
    """同一对地点(坐标5位小数约1米)只请求一次"""
    return (mode, round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))


def _parse_polyline(path):
    """把高德每步的 polyline 拼成 [(lat, lng), ...], 没有就返回 None.
    高德格式是 "lng,lat;lng,lat;...", 相邻点经常重复, 顺手去重"""
    try:
        pts = []
        for step in path.get("steps") or []:
            for seg in (step.get("polyline") or "").split(";"):
                if not seg:
                    continue
                lng, lat = seg.split(",")
                p = (float(lat), float(lng))
                if not pts or pts[-1] != p:
                    pts.append(p)
        if not pts:
            return None
        if len(pts) > 300:   # 路线太长就抽稀一半, 免得前端画太多点
            pts = pts[::2]
        return pts
    except Exception:
        return None


def _fetch_route(mode, a, b):
    """调一次高德路径规划, 成功返回 (km, minutes, polyline, True), 失败返回 None"""
    global _last_request
    wait = REQUEST_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()

    url = ("https://restapi.amap.com/v3/direction/walking" if mode == "walk"
           else "https://restapi.amap.com/v3/direction/driving")
    params = {
        "key": AMAP_KEY,
        "origin": "%s,%s" % (a[1], a[0]),        # 高德要求 经度,纬度
        "destination": "%s,%s" % (b[1], b[0]),
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json()
        if data.get("status") != "1":
            return None
        path = data["route"]["paths"][0]
        km = float(path["distance"]) / 1000.0
        minutes = float(path["duration"]) / 60.0
        polyline = _parse_polyline(path)
        return round(km, 1), round(minutes), polyline, True
    except Exception:
        return None


def _get_cached(mode, a, b):
    """读缓存: 成功结果长期缓存; 失败结果短时缓存(FAIL_TTL内不重试, 到期自动重试).
    返回 (km, minutes, polyline, is_real, fetched_at)"""
    key = _pair_key(mode, a, b)
    hit = _cache.get(key)
    now = time.time()
    if hit is not None and (hit[3] or now - hit[4] < FAIL_TTL):
        return hit
    got = _fetch_route(mode, a, b)
    if got is None:
        got = _straight(mode, a, b)
    got = tuple(got) + (now,)
    _cache[key] = got
    return got


def route_minutes(mode, a, b):
    """a, b 是 (lat, lng) 元组. 返回 (km, minutes), 拿不到真实路网就直线估算"""
    return _get_cached(mode, a, b)[:2]


def build_matrix(points, mode):
    """把'要去的地点'两两之间都算一遍真实时间, 供优化器查表.
    points: [(lat, lng), ...], 第0个一般是起点(家).
    返回 (matrix, real_ok, total):
      matrix  {(la1,ln1,la2,ln2): (km, minutes, polyline)}
      real_ok 用了真实路网的有向对数(含缓存命中)
      total   需要的有向对数 (不含自己到自己)
    """
    # 先去重: 同一坐标只算一次(比如两条任务都在家), 能少调一堆 API
    uniq = []
    seen = set()
    for p in points:
        k = (round(p[0], 5), round(p[1], 5))
        if k not in seen:
            seen.add(k)
            uniq.append(k)

    matrix = {}
    real_ok = 0
    total = 0
    n = len(uniq)
    for i in range(n):
        for j in range(n):
            a, b = uniq[i], uniq[j]
            # 矩阵键不带 mode(一次规划只用一种方式), 和优化器查表的键保持一致
            mkey = (a[0], a[1], b[0], b[1])
            if i == j:
                matrix[mkey] = (0.0, 0, [])
                continue
            total += 1
            # 缓存命中也算真实路网(第4位标记), 不然第二次规划会误报'直线估算'
            val = _get_cached(mode, a, b)
            if val[3]:
                real_ok += 1
            matrix[mkey] = val[:3]
    return matrix, real_ok, total


def clear_cache():
    _cache.clear()