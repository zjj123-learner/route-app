# -*- coding: utf-8 -*-
"""50 条训练语料 + 15 条泛化集, 用于评测 规则解析 vs LLM 解析.

每一条 = 一句任务文本 + 期望解析结果, 字段约定和 parser.py 保持一致:
  day      日期偏移(今天=0, 明天=1, 后天=2...)
  时间字段  当天第几分钟(0-1439), 没有写就是 None
  priority 1低/2中/3高
  duration 分钟
  place    地点关键词(parser.py 的 PLACES 里没有的用自然名称, 如 饭店/公园/图书馆)
对比时 benchmark_llm.py 会把 day*1440 加到时间字段上, 变成和解析结果一致的绝对分钟.
"""
ENTRIES = [
    {"text": "明天上午9点去银行办卡", "day": 1, "earliest": 540, "latest": 720, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "下午3点去学校接孩子放学（重要）", "day": 0, "earliest": None, "latest": None, "fixed": 900, "deadline": None, "priority": 3, "duration": 30, "place": "学校"},
    {"text": "顺便去超市买菜", "day": 0, "earliest": None, "latest": None, "fixed": None, "deadline": None, "priority": 1, "duration": 20, "place": "超市"},
    {"text": "晚上7点前从驿站取快递回家", "day": 0, "earliest": None, "latest": None, "fixed": None, "deadline": 1140, "priority": 2, "duration": 20, "place": "快递驿站"},
    {"text": "明天上午9点去建设银行取钱", "day": 1, "earliest": 540, "latest": 720, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "上午九点到十二点去图书馆自习", "day": 0, "earliest": 540, "latest": 720, "fixed": None, "deadline": None, "priority": 2, "duration": 180, "place": "图书馆"},
    {"text": "顺路去趟药店买药", "day": 0, "earliest": None, "latest": None, "fixed": None, "deadline": None, "priority": 1, "duration": 20, "place": "药店"},
    {"text": "傍晚六点半去公园散步", "day": 0, "earliest": 1110, "latest": 1290, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "公园"},
    {"text": "明天早上八点去公司开早会", "day": 1, "earliest": None, "latest": None, "fixed": 480, "deadline": None, "priority": 2, "duration": 60, "place": "公司"},
    {"text": "下午两点到四点去健身房", "day": 0, "earliest": 840, "latest": 960, "fixed": None, "deadline": None, "priority": 2, "duration": 120, "place": "健身房"},
    {"text": "中午去食堂吃饭", "day": 0, "earliest": 660, "latest": 840, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "食堂"},
    {"text": "明天下午去医院看病", "day": 1, "earliest": 720, "latest": 1080, "fixed": None, "deadline": None, "priority": 2, "duration": 60, "place": "医院"},
    {"text": "晚上有空去健身房", "day": 0, "earliest": 1080, "latest": 1440, "fixed": None, "deadline": None, "priority": 1, "duration": 30, "place": "健身房"},
    {"text": "务必明天上午10点前交作业", "day": 1, "earliest": None, "latest": None, "fixed": None, "deadline": 600, "priority": 3, "duration": 30, "place": None},
    {"text": "下午五点去接孩子放学（重要）", "day": 0, "earliest": None, "latest": None, "fixed": 1020, "deadline": None, "priority": 3, "duration": 30, "place": "学校"},
    {"text": "上午九点半去银行取钱", "day": 0, "earliest": 570, "latest": 750, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "下午三点半到四点半去超市买菜", "day": 0, "earliest": 930, "latest": 990, "fixed": None, "deadline": None, "priority": 2, "duration": 60, "place": "超市"},
    {"text": "明天傍晚去菜市场买菜", "day": 1, "earliest": 1020, "latest": 1200, "fixed": None, "deadline": None, "priority": 2, "duration": 20, "place": "菜市场"},
    {"text": "后天上午9点去医院体检", "day": 2, "earliest": 540, "latest": 720, "fixed": None, "deadline": None, "priority": 2, "duration": 60, "place": "医院"},
    {"text": "大后天下午去理发店理发", "day": 3, "earliest": 720, "latest": 1080, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "理发店"},
    {"text": "明天中午十二点去取快递", "day": 1, "earliest": 720, "latest": 900, "fixed": None, "deadline": None, "priority": 2, "duration": 20, "place": "快递驿站"},
    {"text": "上午去银行办卡", "day": 0, "earliest": 360, "latest": 720, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "今天下午5点去健身房锻炼", "day": 0, "earliest": 1020, "latest": 1200, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "健身房"},
    {"text": "明天上午十点去药房买药", "day": 1, "earliest": 600, "latest": 780, "fixed": None, "deadline": None, "priority": 2, "duration": 20, "place": "药店"},
    {"text": "下午四点去学校接孩子（重要）", "day": 0, "earliest": None, "latest": None, "fixed": 960, "deadline": None, "priority": 3, "duration": 30, "place": "学校"},
    {"text": "明天晚上八点去超市买点东西", "day": 1, "earliest": 1200, "latest": 1380, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "超市"},
    {"text": "上午十一点半去菜市场买菜", "day": 0, "earliest": 690, "latest": 870, "fixed": None, "deadline": None, "priority": 2, "duration": 20, "place": "菜市场"},
    {"text": "下午三点去银行办业务", "day": 0, "earliest": 900, "latest": 1080, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "明天上午去医院拿药", "day": 1, "earliest": 360, "latest": 720, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "医院"},
    {"text": "晚上七点去学校接孩子", "day": 0, "earliest": None, "latest": None, "fixed": 1140, "deadline": None, "priority": 2, "duration": 30, "place": "学校"},
    {"text": "明天下午两点半去银行存钱", "day": 1, "earliest": 870, "latest": 1050, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "上午八点到九点去医院体检", "day": 0, "earliest": 480, "latest": 540, "fixed": None, "deadline": None, "priority": 2, "duration": 60, "place": "医院"},
    {"text": "顺路去超市买点东西", "day": 0, "earliest": None, "latest": None, "fixed": None, "deadline": None, "priority": 1, "duration": 30, "place": "超市"},
    {"text": "明天下午去税务局交材料", "day": 1, "earliest": 720, "latest": 1080, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "税务局"},
    {"text": "上午十点去公司开会", "day": 0, "earliest": None, "latest": None, "fixed": 600, "deadline": None, "priority": 2, "duration": 60, "place": "公司"},
    {"text": "下午去健身房练两个小时", "day": 0, "earliest": 720, "latest": 1080, "fixed": None, "deadline": None, "priority": 2, "duration": 120, "place": "健身房"},
    {"text": "明天早上六点去公园晨跑", "day": 1, "earliest": 360, "latest": 540, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "公园"},
    {"text": "晚上九点前回家", "day": 0, "earliest": None, "latest": None, "fixed": None, "deadline": 1260, "priority": 2, "duration": 30, "place": "家"},
    {"text": "明天下午四点去理发店剪头发", "day": 1, "earliest": 960, "latest": 1140, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "理发店"},
    {"text": "明天上午十点半去学校上课", "day": 1, "earliest": None, "latest": None, "fixed": 630, "deadline": None, "priority": 2, "duration": 60, "place": "学校"},
    {"text": "下午两点半去超市买菜", "day": 0, "earliest": 870, "latest": 1050, "fixed": None, "deadline": None, "priority": 2, "duration": 20, "place": "超市"},
    {"text": "中午去银行取钱", "day": 0, "earliest": 660, "latest": 840, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "明天下午五点去药店买药", "day": 1, "earliest": 1020, "latest": 1200, "fixed": None, "deadline": None, "priority": 2, "duration": 20, "place": "药店"},
    {"text": "上午十点去医院打疫苗", "day": 0, "earliest": 600, "latest": 780, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "医院"},
    {"text": "晚上去公园散步", "day": 0, "earliest": 1080, "latest": 1440, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "公园"},
    {"text": "明天上午九点去学校接孩子", "day": 1, "earliest": None, "latest": None, "fixed": 540, "deadline": None, "priority": 2, "duration": 30, "place": "学校"},
    {"text": "下午三点半去银行办卡", "day": 0, "earliest": 930, "latest": 1110, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "明天中午去食堂吃饭", "day": 1, "earliest": 660, "latest": 840, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "食堂"},
    {"text": "上午九点到十点去健身房", "day": 0, "earliest": 540, "latest": 600, "fixed": None, "deadline": None, "priority": 2, "duration": 60, "place": "健身房"},
    {"text": "明天晚上八点前去驿站取快递", "day": 1, "earliest": None, "latest": None, "fixed": None, "deadline": 1200, "priority": 2, "duration": 20, "place": "快递驿站"},
]


# 泛化集(不在训练语料里): 评测"没见过的新表达", 证明 LLM 不是背题.
# 故意加入了规则解析器处理不了的写法: 今晚/明晚、顺路、药房、菜鸟驿站、接娃、
# 游泳馆/书店/饭店等新地点、晚上12点等.
GEN_ENTRIES = [
    {"text": "明天下午两点半去招商银行办卡", "day": 1, "earliest": 870, "latest": 1050, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "顺路去药房买个药", "day": 0, "earliest": None, "latest": None, "fixed": None, "deadline": None, "priority": 1, "duration": 20, "place": "药店"},
    {"text": "晚上八点去菜鸟驿站取快递", "day": 0, "earliest": 1200, "latest": 1380, "fixed": None, "deadline": None, "priority": 2, "duration": 20, "place": "快递驿站"},
    {"text": "明天早上七点去游泳馆游泳", "day": 1, "earliest": 420, "latest": 600, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "游泳馆"},
    {"text": "后天下午三点半去学校接娃", "day": 2, "earliest": None, "latest": None, "fixed": 930, "deadline": None, "priority": 2, "duration": 30, "place": "学校"},
    {"text": "明天中午去食堂打饭", "day": 1, "earliest": 660, "latest": 840, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "食堂"},
    {"text": "今晚八点前回公司交报告", "day": 0, "earliest": None, "latest": None, "fixed": None, "deadline": 1200, "priority": 2, "duration": 30, "place": "公司"},
    {"text": "明天下午去银行取号", "day": 1, "earliest": 720, "latest": 1080, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "银行"},
    {"text": "上午十点前去医院抽血", "day": 0, "earliest": None, "latest": None, "fixed": None, "deadline": 600, "priority": 2, "duration": 30, "place": "医院"},
    {"text": "明天傍晚六点去公园遛狗", "day": 1, "earliest": 1080, "latest": 1260, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "公园"},
    {"text": "后天上午九点到十点去医院复查", "day": 2, "earliest": 540, "latest": 600, "fixed": None, "deadline": None, "priority": 2, "duration": 60, "place": "医院"},
    {"text": "明天晚上九点去饭店吃夜宵", "day": 1, "earliest": 1260, "latest": 1440, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "饭店"},
    {"text": "明晚八点去健身房练一个小时", "day": 1, "earliest": 1200, "latest": 1380, "fixed": None, "deadline": None, "priority": 2, "duration": 60, "place": "健身房"},
    {"text": "大后天晚上七点去图书馆自习", "day": 3, "earliest": 1140, "latest": 1320, "fixed": None, "deadline": None, "priority": 2, "duration": 30, "place": "图书馆"},
    {"text": "下午有空去书店逛逛", "day": 0, "earliest": 720, "latest": 1080, "fixed": None, "deadline": None, "priority": 1, "duration": 30, "place": "书店"},
]

