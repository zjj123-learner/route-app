# -*- coding: utf-8 -*-
import math
import re
import requests
from config import AMAP_KEY, DEFAULT_CITY

GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
POI_URL = "https://restapi.amap.com/v3/place/text"
AROUND_URL = "https://restapi.amap.com/v3/place/around"

CITY = DEFAULT_CITY

_CACHE = {}


def _auto_city(address):
    """地址里已带省/市/区县信息就全国搜, 否则默认在池州搜, 提高模糊地址命中率"""
    if re.search(r"(省|市|自治区|特别行政区|自治州|盟|地区)", address):
        return None
    return CITY

def _valid_location(lng, lat):
    """过滤高德偶尔返回的 (0,0) 无效坐标"""
    return abs(lng) > 0.1 and abs(lat) > 0.1


def _haversine_m(lat1, lng1, lat2, lng2):
    """两点直线距离(米), 给全市搜索结果算距离/排序用"""
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))

def _split_address(address):
    """把'安徽省池州市贵池区远东国际花园'拆成 (城市名, 核心词).
    拆不出城市就返回 (None, 原词)"""
    core = address
    city = None

    m = re.match(r"^.{2,15}?(?:省|自治区|特别行政区)", core)
    if m:
        core = core[m.end():]
    m = re.match(r"^.{2,15}?(?:市|自治州|盟|地区)", core)
    if m:
        city = core[:m.end()]
        core = core[m.end():]
    m = re.match(r"^.{2,15}?(?:区|县|旗|新区)", core)
    if m:
        core = core[m.end():]

    core = core.strip(" ，,、")
    if not core:
        return None, address
    return city, core


def geocode_address(address, city=None):
    """结构化地址 -> (lng, lat, 完整地址), 失败返回 None
    city 为 None 时自动判断: 地址含省/市就全国搜, 否则默认在池州"""
    if city is None:
        city = _auto_city(address)
    params = {"key": AMAP_KEY, "address": address}
    if city:
        params["city"] = city
    try:
        resp = requests.get(GEOCODE_URL, params=params, timeout=5)
        data = resp.json()
    except Exception:
        return None
    if data.get("status") == "1" and data.get("geocodes"):
        g = data["geocodes"][0]
        lng, lat = g["location"].split(",")
        lng, lat = float(lng), float(lat)
        if _valid_location(lng, lat):
            return lng, lat, g.get("formatted_address", address)
    return None


def _poi_query(keyword, city):
    """真正的POI请求, 带缓存"""
    cache_key = keyword + "|" + str(city)
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    params = {
        "key": AMAP_KEY, "keywords": keyword,
        "citylimit": "false", "offset": 1, "extensions": "base",
    }
    if city:
        params["city"] = city
    try:
        resp = requests.get(POI_URL, params=params, timeout=5)
        data = resp.json()
    except Exception:
        return None
    result = None
    if data.get("status") == "1" and data.get("pois"):
        p = data["pois"][0]
        lng, lat = p["location"].split(",")
        lng, lat = float(lng), float(lat)
        if _valid_location(lng, lat):
            result = (lng, lat, p["name"])
    if result:
        _CACHE[cache_key] = result   # 只缓存成功结果, 失败下次再试
    return result


def search_nearby(keyword, center, radius=3000):
    """以 center 为中心做'附近搜索', 返回第一个POI (lng, lat, 名称), 失败返回 None.
    比如'去附近饭店吃饭' -> 搜家周围3公里内的饭店"""
    cache_key = keyword + "@" + str(center["lat"]) + "," + str(center["lng"])
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    params = {
        "key": AMAP_KEY, "keywords": keyword,
        "location": "{},{}".format(center["lng"], center["lat"]),
        "radius": radius, "offset": 1, "extensions": "base",
    }
    try:
        resp = requests.get(AROUND_URL, params=params, timeout=5)
        data = resp.json()
    except Exception:
        return None
    result = None
    if data.get("status") == "1" and data.get("pois"):
        p = data["pois"][0]
        lng, lat = p["location"].split(",")
        lng, lat = float(lng), float(lat)
        if _valid_location(lng, lat):
            result = (lng, lat, p["name"])
    if result:
        _CACHE[cache_key] = result
    return result


def _text_search_multi(keyword, city, offset=8):
    """全市文本搜索(place/text), 返回多个候选 [{name, address, lat, lng}].
    带缓存; 失败返回 []. 用于给"偏远地点"补候选: 附近搜不到不等于不存在."""
    cache_key = "multi:" + keyword + "|" + str(city)
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    params = {
        "key": AMAP_KEY, "keywords": keyword,
        "citylimit": "true", "offset": offset, "extensions": "base",
    }
    if city:
        params["city"] = city
    try:
        resp = requests.get(POI_URL, params=params, timeout=5)
        data = resp.json()
    except Exception:
        return []
    out = []
    if data.get("status") == "1" and data.get("pois"):
        for p in data["pois"]:
            lng, lat = p["location"].split(",")
            lng, lat = float(lng), float(lat)
            if not _valid_location(lng, lat):
                continue
            out.append({
                "name": p.get("name", keyword),
                "address": p.get("address", ""),
                "lat": lat,
                "lng": lng,
            })
    if out:
        _CACHE[cache_key] = out
    return out


def search_candidates(keyword, center, limit=8, radius=20000):
    """以 center 为中心搜候选地点, 按距离排序.
    1) 先搜 center 周边(radius 内, 默认 20km, 覆盖同城大部分范围);
    2) 近处凑不满 limit 个时, 补一次全市文本搜索(place/text),
       让"去偏远地方办事"也能找到候选——远处的地点按距离排后面, 仍可被选中.
    返回 [{name, address, lat, lng, distance_m}], 失败返回 []"""
    cache_key = "cand:" + keyword + "@" + str(center["lat"]) + "," + str(center["lng"])
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    params = {
        "key": AMAP_KEY, "keywords": keyword,
        "location": "{},{}".format(center["lng"], center["lat"]),
        "radius": radius, "offset": limit, "extensions": "base",
    }
    try:
        resp = requests.get(AROUND_URL, params=params, timeout=5)
        data = resp.json()
    except Exception:
        data = {}
    out = []
    seen = set()
    if data.get("status") == "1" and data.get("pois"):
        for p in data["pois"]:
            lng, lat = p["location"].split(",")
            lng, lat = float(lng), float(lat)
            if not _valid_location(lng, lat):
                continue
            try:
                dist = int(float(p.get("distance") or 0))
            except (TypeError, ValueError):
                dist = 0
            name = p.get("name", keyword)
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "address": p.get("address", ""),
                "lat": lat,
                "lng": lng,
                "distance_m": dist,
            })
    # 近处不够时, 补全市搜索, 偏远地点也能出现在候选里(距离按直线估算, 排后面)
    if len(out) < limit:
        for c in _text_search_multi(keyword, _auto_city(keyword), offset=limit):
            if c["name"] in seen:
                continue
            seen.add(c["name"])
            out.append({
                "name": c["name"],
                "address": c.get("address", ""),
                "lat": c["lat"],
                "lng": c["lng"],
                "distance_m": int(round(_haversine_m(
                    center["lat"], center["lng"], c["lat"], c["lng"]))),
            })
            if len(out) >= limit:
                break
    out.sort(key=lambda c: c["distance_m"])
    out = out[:limit]
    if out:
        _CACHE[cache_key] = out
    return out


def search_poi(keyword, city=None):
    """关键词 -> 第一个POI (lng, lat, 名称), 失败返回 None.
    带省市区前缀的长地址会先拆出城市+核心词搜, 比如
    '安徽省池州市贵池区远东国际花园' -> 用'远东国际花园'在池州市搜;
    本地城市搜不到会再去掉 city 全国搜一次, 兜底'陆家嘴'这类外地地标"""
    if city is None:
        city = _auto_city(keyword)
    city2, core = _split_address(keyword)
    if city2 and core:
        result = _poi_query(core, city2)
        if result:
            return result
        result = _poi_query(core, None)   # 拆词后本地没搜到 -> 全国兜底
        if result:
            return result
    result = _poi_query(keyword, city)
    if result:
        return result
    if city:
        return _poi_query(keyword, None)  # 整词本地没搜到 -> 全国兜底
    return None


def resolve_address(address):
    """定位'家': 先精确地理编码, 失败再用POI搜索兜底.
    返回 (lng, lat, 显示名, 来源) 或 None"""
    result = geocode_address(address)
    if result:
        lng, lat, name = result
        return lng, lat, name, "geocode"
    result = search_poi(address)
    if result:
        lng, lat, name = result
        return lng, lat, name, "poi"
    return None


def extract_keyword(text):
    """从任务文本里粗略提取'地点关键词', 简化版NLP"""
    cleaned = re.sub(
        r"(办税|交税|缴税|办卡|取快递|拿快递|买菜|买药|取药|缴费|交费|寄快递|"
        r"接孩子|送孩子|开会|上课|上班|下班|凌晨|早上|上午|中午|下午|傍晚|晚上|附近|吃饭|吃|"
        r"明天|今天|后天|去|到|顺便|回家|办|买|取|拿|接|送|约|见|逛|看|缴|交|还|领|钱|款|卡|证|单)",
        "", text)
    cleaned = re.sub(r"\d+[:：点]\d*分?|[0-9一二两俩三四五六七八九十]+点", "", cleaned)
    return cleaned.strip("的，。、 ") or text


def fill_missing_coords(tasks):
    """给没有坐标的任务调用POI搜索补坐标, 返回补了几个.
    优先用解析出的地点类型(如'银行'), 再退回从任务文本提取的关键词"""
    count = 0
    for t in tasks:
        if t["lat"] is not None:
            continue
        tried = []
        if t["place"]:
            tried.append(t["place"])
        tried.append(extract_keyword(t["name"]))
        for kw in dict.fromkeys(tried):
            if not kw:
                continue
            result = search_poi(kw)
            if result:
                lng, lat, name = result
                t["lat"] = lat
                t["lng"] = lng
                t["place"] = name
                count += 1
                break
    return count
