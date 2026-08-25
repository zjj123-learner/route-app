import re
import datetime

PLACES = [
    ("菜市场", "菜市场"),
    ("快递",   "快递驿站"),
    ("驿站",   "快递驿站"),
    ("银行",   "银行"),
    ("超市",   "超市"),
    ("药店",   "药店"),
    ("学校",   "学校"),
    ("医院",   "医院"),
    ("公司",   "公司"),
    ("健身房", "健身房"),
    ("理发",   "理发店"),
    ("家",     "家"),
]

TIME_RE = re.compile(
    r"(凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*(\d{1,2})\s*(?:[:：点]\s*(\d{1,2})\s*分?|点)"
)

FIXED_VERBS = "接 见 开会 会议 到岗 到公司 面试 考试 面谈 拜访 取件 上课 就诊 预约".split()

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "俩": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

CN_TIME_RE = re.compile(r"([零一二两俩三四五六七八九十]+)(?:点|时)(半|[零一二两俩三四五六七八九十]+\s*分)?")


# ===== 多日支持: 把'明天/后天/周几/X月X日'转成第几天 =====
DAY_WORDS = [("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0), ("昨天", -1)]
WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
WEEKDAY_RE = re.compile(r"(?:周|星期|礼拜)([一二三四五六日天])")
DATE_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")


def extract_day(text, today=None):
    """任务落在第几天: 今天=0, 明天=1, 后天=2, 周X按今天算(过期的周X=下周), X月X日按真实日期差算"""
    if today is None:
        today = datetime.date.today()
    for word, off in DAY_WORDS:
        if word in text:
            return off
    m = WEEKDAY_RE.search(text)
    if m:
        target = WEEKDAY_CN[m.group(1)]
        return (target - today.weekday()) % 7
    m = DATE_RE.search(text)
    if m:
        try:
            d = datetime.date(today.year, int(m.group(1)), int(m.group(2)))
            return (d - today).days
        except ValueError:
            return 0
    return 0


PERIOD_WINDOWS = [
    ("凌晨", 0, 6),
    ("早上", 5, 9),
    ("上午", 6, 12),
    ("中午", 11, 14),
    ("下午", 12, 18),
    ("傍晚", 17, 20),
    ("晚上", 18, 24),
]


def vague_period(text):
    """只有'晚上/上午'这种模糊时段词、没有具体几点时, 返回时段窗口(分钟)"""
    for word, start, end in PERIOD_WINDOWS:
        if word in text:
            return start * 60, end * 60
    return None

def find_place(text):
    for keyword, name in PLACES:
        if keyword in text:
            return {"name": name, "lat": None, "lng": None}
    return None


def cn_to_int(cn):
    if "十" not in cn:
        return CN_DIGITS.get(cn, 0)
    parts = cn.split("十")
    tens = CN_DIGITS.get(parts[0], 1) if parts[0] else 1
    ones = CN_DIGITS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
    return tens * 10 + ones


def cn_time_to_str(text):
    def repl(m):
        hour = cn_to_int(m.group(1))
        tail = m.group(2)
        if tail == "半":
            return f"{hour}点30分"
        if tail:
            minute = cn_to_int(tail.replace("分", "").strip())
            return f"{hour}点{minute}分"
        return f"{hour}点"
    return CN_TIME_RE.sub(repl, text)


def parse_time_tokens(text):
    tokens = []
    last_prefix = ""
    for m in TIME_RE.finditer(text):
        prefix = m.group(1) or ""
        hour = int(m.group(2))
        minute = int(m.group(3) or 0)
        if not prefix:
            prefix = last_prefix   # '下午3点到5点'的'5点'继承'下午', 变成17点而不是凌晨5点
        else:
            last_prefix = prefix
        if prefix in ("下午", "晚上", "傍晚") and hour < 12:
            hour += 12
        elif prefix == "中午" and hour < 11:
            hour += 12
        elif prefix == "凌晨" and hour == 12:
            hour = 0
        tokens.append({
            "value": hour * 60 + minute,
            "start": m.start(),
            "end": m.end(),
            "after": text[m.end():m.end() + 4],
        })
    return tokens


def build_time(text, tokens):
    earliest = latest = fixed = deadline = None
    if not tokens:
        return earliest, latest, fixed, deadline
    if len(tokens) >= 2:
        between = text[tokens[0]["end"]:tokens[1]["start"]]
        if re.search(r"到|至|-|~", between):
            return tokens[0]["value"], tokens[1]["value"], None, None
    last = tokens[-1]
    if "前" in last["after"]:
        return None, None, None, last["value"]
    if len(tokens) == 1:
        if any(v in text for v in FIXED_VERBS):
            fixed = last["value"]
        else:
            earliest = last["value"]
            latest = last["value"] + 180
    else:
        earliest = tokens[0]["value"]
        latest = tokens[-1]["value"]
    return earliest, latest, fixed, deadline


def parse_line(line, today=None):
    text = line.strip()
    if not text:
        return None
    day = extract_day(text, today)
    text = cn_time_to_str(text)
    text = re.sub(r"(\d)\s*点半", r"\1点30分", text)   # 阿拉伯数字的'9点半' -> '9点30分'
    tokens = parse_time_tokens(text)
    place = find_place(text)
    priority = 2
    if re.search(r"紧急|重要|必须|加急|优先|务必|一定要", text):
        priority = 3
    elif re.search(r"顺便|有空|不急|空闲|不着急|路过", text):
        priority = 1
    duration = 30
    if re.search(r"开会|会议|办事|办理|看病|就诊|面试|考试|上课|体检", text):
        duration = 60
    elif re.search(r"取快递|买菜|买药|缴费|取药|寄|送|领取|取件", text):
        duration = 20
    earliest, latest, fixed, deadline = build_time(text, tokens)
    # 用户写了明确的起止时间段(如'上午9点到12点'), 用时段的长度当任务时长, 不再固定半小时
    if len(tokens) >= 2 and earliest is not None and latest is not None:
        duration = max(20, min(latest - earliest, 480))
    if not tokens:
        period = vague_period(text)
        if period:
            earliest, latest = period
    base = day * 1440
    if day:
        # 有日期但没具体时间: 默认在这个自然日里完成(6:00-24:00)
        if earliest is None and latest is None and fixed is None and deadline is None:
            earliest, latest = 360, 1439
    # 时间从'当天分钟'抬到'绝对分钟'(跨天)
    if earliest is not None:
        earliest += base
    if latest is not None:
        latest += base
    if fixed is not None:
        fixed += base
    if deadline is not None:
        deadline += base
    return {
        "name": text,
        "place": place["name"] if place else None,
        "lat": place["lat"] if place else None,
        "lng": place["lng"] if place else None,
        "priority": priority,
        "duration": duration,
        "earliest": earliest,
        "latest": latest,
        "fixed": fixed,
        "deadline": deadline,
        "day": day,
    }


def parse_tasks(text, today=None):
    tasks = []
    for line in text.splitlines():
        task = parse_line(line, today)
        if task:
            tasks.append(task)
    return tasks