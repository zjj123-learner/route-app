# -*- coding: utf-8 -*-
"""LLM 解析器 v2: 用大模型把一行任务文本解析成结构化字段.

v2 相比 v1 的改动(2026-08-27, 目标 62% -> 90%):
  1. prompt 调优:
     - 明确输出格式: 所有字段都输出, 没有就 null, 只输出 JSON
     - 时长口径对齐语料: 银行办卡/取钱/存钱等 = 30 分钟, 取钱不再按 20
     - 固定动词去掉"取件", 并明确"取快递/取件/收件不是预约"
     - place 要求输出规范关键词(建设银行->银行, 药房->药店, 驿站->快递驿站)
     - latest 允许到 1440(24:00)
  2. 输出规范化(确定性后处理, 不依赖模型自觉):
     - 地点归一: X银行->银行, 药房->药店, 驿站/快递->快递驿站, 接孩子->学校
     - 时长兜底: 办卡/取钱/存钱/交材料/拿药/打疫苗/买点东西 -> 30 分钟
     - 取快递被误标 fixed 时改回时间窗
     - 只有模糊时段(上午/下午/晚上...)时按语料的时段窗口覆盖

OpenAI 兼容接口(DeepSeek/智谱/通义/OpenAI 通用), 配置在 config.py:
  DEEPSEEK_API_KEY(或 LLM_API_KEY) 必填, 没有就自动回退规则解析
  LLM_BASE_URL 默认 https://api.deepseek.com/v1
  LLM_MODEL    默认 deepseek-v4-flash
"""
import json
import re

import requests

import config

SYSTEM_PROMPT = """你是一个中文行程解析助手。把用户的一句话任务解析成 JSON 对象，只输出 JSON，不要解释、不要 markdown 围栏。

必须输出这些字段, 没有就写 null:
{"day":0,"earliest_min":null,"latest_min":null,"fixed_min":null,"deadline_min":null,"priority":2,"duration_min":30,"place":"银行"}

字段规则:
- day: 日期偏移, 今天=0, 明天=1, 后天=2, 大后天=3, 没写日期=0
- 时间都用"当天第几分钟"(0-1440, 1440=24:00): 上午9点=540, 下午3点=900(下午/晚上/傍晚的 12 小时制要 +12, 如 下午6点=1080), 中午12点=720, 凌晨0点=0
- 单个时间且是普通任务(不是预约) -> earliest_min=该时间, latest_min=该时间+180
- 固定预约(动词含 开会/会议/早会/接/接孩子/接娃/放学/到岗/面试/考试/就诊/预约/上课/拜访/面谈/见) -> fixed_min=该时间, earliest_min/latest_min 都 null
- 注意: 取快递/取件/收件/买菜/买药/散步/锻炼 都不是预约, 不要用 fixed_min
- "X点前" -> deadline_min=X, 其他时间字段 null
- "X点到Y点"(到/至/- 连接) -> earliest_min=X, latest_min=Y, duration_min=Y-X
- 只有模糊时段(凌晨/早上/上午/中午/下午/傍晚/晚上) -> 用窗口: 凌晨0-6, 早上5-9, 上午6-12, 中午11-14, 下午12-18, 傍晚17-20, 晚上18-24
- 没写时间 -> 时间字段全 null
- priority: 重要/紧急/必须/加急/优先/务必/一定要=3; 顺便/有空/不急/空闲/不着急/路过/顺路=1; 其他=2
- duration_min(分钟), 严格按下面口径:
  * 60: 开会/会议/早会/看病/就诊/面试/考试/上课/体检
  * 20: 取快递/买菜/买药/缴费/取药/寄/送/领取/收件
  * 30: 银行类(办卡/取钱/存钱/办业务/开户/销户/交材料) 和其他所有(散步/锻炼/理发/剪头发/吃饭/自习/晨跑/接孩子/拿药/打疫苗/买点东西)
  * 有起止时间段 -> duration_min=latest-earliest; 明确写了时长(练两个小时/半小时) -> 按它
- place: 输出规范关键词, 不要品牌名:
  * 建设银行/工商银行/招商银行/农业银行/中国银行等一律 "银行"
  * 药房 -> "药店"; 驿站/取快递/取件 -> "快递驿站"
  * 接孩子/接娃/放学(没写地点) -> "学校"
  * 其他常见: 学校/超市/菜市场/医院/公司/健身房/理发店/家/公园/图书馆/食堂/税务局
  * 没有地点输出 null"""


def llm_available():
    """有没有配好 LLM Key(没有就继续用规则解析)"""
    return bool(config.LLM_API_KEY and config.LLM_BASE_URL)


def _extract_json(content):
    """从模型回复里抠出 JSON: 去掉 ```json 围栏, 取第一个 { 到最后一个 }"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]


def _to_int(value, lo, hi, default):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if lo <= v <= hi else default


# 地点规范关键词: 模型输出在这里面就原样保留, 否则尝试归一
CANON_PLACES = {"银行", "学校", "超市", "快递驿站", "药店", "菜市场", "医院",
                "公司", "健身房", "理发店", "家", "饭店", "公园", "图书馆",
                "食堂", "税务局"}


def canon_place(place, text):
    """把模型给的地点归一成规范关键词(确定性修复, 不依赖模型自觉)"""
    if not place:
        if re.search(r"接孩子|接娃|放学|接人", text):
            return "学校"
        if re.search(r"取快递|取件|收件|快递", text):
            return "快递驿站"
        return None
    p = place.strip()
    if "银行" in p:
        return "银行"
    if "药房" in p or "药店" in p:
        return "药店"
    if "驿站" in p or "快递" in p:
        return "快递驿站"
    if re.search(r"接孩子|接娃|放学", text):
        return "学校"
    if p in CANON_PLACES:
        return p
    return p


def canon_duration(text, duration, earliest, latest):
    """时长口径兜底: 小额业务/杂事按 30 分钟(模型常给 60 或 20)"""
    if re.search(r"办卡|取钱|存钱|办业务|开户|销户|交材料|拿药|打疫苗|买点东西|买东西", text):
        # 有起止时间段时(如 9 点到 12 点), 时长按时间段长度, 不覆盖
        if not (earliest is not None and latest is not None and duration == latest - earliest):
            return 30
    return duration


def _normalize(text, data):
    """把模型返回的 JSON 转成和 parser.py 一致的任务字典(时间抬成绝对分钟)"""
    day = _to_int(data.get("day"), 0, 6, 0)
    base = day * 1440

    def abs_min(v, hi=1439):
        m = _to_int(v, 0, hi, None)
        return None if m is None else m + base

    earliest = abs_min(data.get("earliest_min"))
    latest = abs_min(data.get("latest_min"), hi=1440)
    fixed = abs_min(data.get("fixed_min"))
    deadline = abs_min(data.get("deadline_min"))

    # 兜底3: 只有模糊时段(上午/下午/晚上...)且没写具体几点 -> 用语料统一的时段窗口
    vague_only = (re.search(r"凌晨|早上|上午|中午|下午|傍晚|晚上", text)
                  and not re.search(r"\d+\s*[:：点]|[零一二两俩三四五六七八九十]+点", text))
    if vague_only and fixed is None and deadline is None:
        from parser import vague_period
        period = vague_period(text)
        if period:
            earliest, latest = period[0] + base, period[1] + base

    # 兜底2: 取快递/取件被模型误判成固定预约 -> 改回时间窗
    if fixed is not None and deadline is None and re.search(r"取快递|取件|收件", text):
        earliest = fixed
        latest = fixed + 180
        fixed = None

    # 兜底1: 模型漏了 latest 但给了 earliest(非固定)时, 补默认 3 小时窗口
    if earliest is not None and latest is None and fixed is None:
        latest = earliest + 180

    priority = _to_int(data.get("priority"), 1, 3, 2)
    duration = _to_int(data.get("duration_min"), 10, 600, 30)
    duration = canon_duration(text, duration, earliest, latest)
    place = canon_place(data.get("place"), text)

    return {
        "name": text,
        "place": place,
        "lat": None,
        "lng": None,
        "priority": priority,
        "duration": duration,
        "earliest": earliest,
        "latest": latest,
        "fixed": fixed,
        "deadline": deadline,
        "day": day,
    }


def llm_parse_line(text, timeout=30):
    """用 LLM 解析一行, 失败(没 key/网络错/返回不是合法JSON)返回 None"""
    if not llm_available():
        return None
    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
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
        content = resp.json()["choices"][0]["message"]["content"]
        raw = _extract_json(content)
        if raw is None:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return _normalize(text, data)
    except Exception:
        return None


def parse_with_llm(text, prefer_llm=False):
    """按行解析整段文本.
    prefer_llm=True 且配了 key 时优先 LLM, 某行失败自动回退规则解析."""
    from parser import parse_line

    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        task = None
        if prefer_llm and llm_available():
            task = llm_parse_line(line)
        if task is None:
            task = parse_line(line)
        if task:
            out.append(task)
    return out