# -*- coding: utf-8 -*-
"""LLM 解析器: 用大模型把一行任务文本解析成结构化字段.

OpenAI 兼容接口(DeepSeek/智谱/通义/OpenAI 通用), 配置在 config.py:
  LLM_API_KEY   必填, 没有就自动回退规则解析
  LLM_BASE_URL  默认 https://api.openai.com/v1; DeepSeek 填 https://api.deepseek.com/v1
  LLM_MODEL     默认 gpt-4o-mini; DeepSeek 填 deepseek-chat
"""
import json
import re

import requests

import config

SYSTEM_PROMPT = """你是一个中文行程解析助手。把用户的一句话任务解析成 JSON，只输出 JSON，不要解释。

字段说明:
- day: 日期偏移, 今天=0, 明天=1, 后天=2, 大后天=3, 没写日期=0
- 时间都用"当天第几分钟"(0-1439): 上午9点=540, 下午3点=900(下午/晚上/傍晚的 12 小时制要 +12, 如 下午6点=1080), 中午12点=720, 凌晨0点=0
- 单个时间(如"上午9点去银行") -> earliest_min=540, latest_min=720(默认 3 小时窗口)
- 固定动词(开会/接/考试/到岗/面试/就诊/预约/上课/拜访/取件/面谈/见) -> fixed_min=该时间, earliest_min/latest_min 都 null
- "X点前" -> deadline_min=X, 其他三个时间字段 null
- "X点到Y点"(到/至/- 连接) -> earliest_min=X, latest_min=Y, duration_min=Y-X
- 只有模糊时段(凌晨/早上/上午/中午/下午/傍晚/晚上) -> 用窗口: 凌晨0-6, 早上5-9, 上午6-12, 中午11-14, 下午12-18, 傍晚17-20, 晚上18-24
- 没写时间 -> 四个时间字段全 null
- priority: 重要/紧急/必须/加急/优先/务必/一定要=3; 顺便/有空/不急/空闲/不着急/路过/顺路=1; 其他=2
- duration_min(分钟): 开会/会议/办事/办理/看病/就诊/面试/考试/上课/体检=60; 取快递/买菜/买药/缴费/取药/寄/送/领取/取件/取钱=20; 其他=30; 有起止时间段时按时长
- 明确写了时长("练两个小时"/"半小时"/"三小时") -> duration_min 按它来
- place: 地点关键词(银行/学校/超市/驿站/药店/菜市场/医院/公司/健身房/理发店/家/饭店/公园/图书馆/税务局等); 没有地点输出 null"""


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


def _normalize(text, data):
    """把模型返回的 JSON 转成和 parser.py 一致的任务字典(时间抬成绝对分钟)"""
    day = _to_int(data.get("day"), 0, 6, 0)
    base = day * 1440

    def abs_min(v):
        m = _to_int(v, 0, 1439, None)
        return None if m is None else m + base

    earliest = abs_min(data.get("earliest_min"))
    latest = abs_min(data.get("latest_min"))
    fixed = abs_min(data.get("fixed_min"))
    deadline = abs_min(data.get("deadline_min"))

    # 兜底: 模型漏了 latest 但给了 earliest(非固定)时, 补默认 3 小时窗口
    if earliest is not None and latest is None and fixed is None:
        latest = earliest + 180

    priority = _to_int(data.get("priority"), 1, 3, 2)
    duration = _to_int(data.get("duration_min"), 10, 600, 30)

    place = data.get("place")
    place = place.strip() if isinstance(place, str) and place.strip() else None

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
