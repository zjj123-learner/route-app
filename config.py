# -*- coding: utf-8 -*-
"""集中配置: 所有敏感项/可选项都从环境变量读取, 不再散落在代码里.
用法: 在系统环境变量里设置(或 cmd 里 set XXX=yyy 再启动), 不设置就用下面的默认值."""
import os


def _env(name, default=""):
    v = os.environ.get(name, "")
    return v.strip() if v else default


# 高德 Web服务 Key(后端: 地点搜索/路径规划用)
# 兼容 AMAP_KEY 和 GAODE_KEY 两种变量名; 不配则后端无法用高德, 会回退直线估算
AMAP_KEY = _env("AMAP_KEY") or _env("GAODE_KEY") or ""

# 高德 Web端(JS API) Key(前端地图显示用; 留空则前端自动回退 OpenStreetMap)
AMAP_JS_KEY = _env("AMAP_JS_KEY") or ""

# 高德安全密钥(2021年12月后新建的 Key 必填, 否则前端地图白屏; 控制台 Key 设置里能看到)
AMAP_JS_SECURITY_CODE = _env("AMAP_JS_SECURITY_CODE") or ""

# 默认搜索城市: 地址里没写省市时, 在这个城市里搜
DEFAULT_CITY = _env("DEFAULT_CITY") or "池州"

# 默认起点(家): 前端没设置家的时候用这里
try:
    _home_lat = float(_env("HOME_LAT") or 30.66)
except ValueError:
    _home_lat = 30.66
try:
    _home_lng = float(_env("HOME_LNG") or 117.49)
except ValueError:
    _home_lng = 117.49
DEFAULT_START = {"name": _env("HOME_NAME") or "家", "lat": _home_lat, "lng": _home_lng}

# 运行参数
HOST = _env("HOST") or "0.0.0.0"      # 0.0.0.0 = 允许手机等局域网设备访问
PORT = int(_env("PORT") or 5000)
DEBUG = _env("FLASK_DEBUG") == "1"    # 默认关 debug(生产安全); 开发时 set FLASK_DEBUG=1

# LLM 解析(可选后端): 不配 DEEPSEEK_API_KEY/LLM_API_KEY 就继续用规则解析.
# 默认已配好 DeepSeek: 填上 Key 并把 LLM_PARSER 设为 1 即启用
LLM_API_KEY = _env("DEEPSEEK_API_KEY") or _env("LLM_API_KEY") or ""
LLM_BASE_URL = _env("LLM_BASE_URL") or "https://api.deepseek.com/v1"
LLM_MODEL = _env("LLM_MODEL") or "deepseek-v4-flash"
LLM_PARSER = _env("LLM_PARSER") == "1"  # 显式设 1 才用 LLM 解析