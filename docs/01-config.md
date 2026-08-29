# config.py 详解 —— 集中配置, 敏感项不进代码

## 这个文件为什么存在

早期版本把高德 Key、默认城市、家的坐标散落在各个文件里,改配置要翻代码,还有把 Key 提交进 git 的风险。这个文件把**所有可配置项收口到环境变量**,达到三个目的:

1. **敏感项不进代码**:Key 只在 `config.py` 里从环境变量读取,`.gitignore` 里还专门忽略了 `.env`,防止误提交。
2. **没配 Key 也能跑**:每个配置都有默认值,缺 Key 时后端自动降级(直线估算、规则解析、OSM 地图),不会启动失败。
3. **一份配置全局生效**:所有模块 `import config` 读同一个值,不会出现两处默认值不一致。

## 设计要点

### `_env(name, default)` —— 唯一读取入口

```python
def _env(name, default=""):
    v = os.environ.get(name, "")
    return v.strip() if v else default
```

- 统一做 `strip()`:Windows 上 set 环境变量容易带尾随空格,这是实际踩过的坑。
- 所有配置都走这一个函数,保证行为一致。

### 双变量名兼容

```python
AMAP_KEY = _env("AMAP_KEY") or _env("GAODE_KEY") or ""
```

兼容 `AMAP_KEY` 和 `GAODE_KEY` 两种叫法,老配置不用改。同类还有 `DEEPSEEK_API_KEY` / `LLM_API_KEY`。

### 数值解析用 try/except 兜底

`HOME_LAT`/`HOME_LNG`/`PORT` 这类数值配置,解析失败就退回默认值,绝不让一个写错的环境变量把整个应用搞挂。

## 配置项清单

| 配置 | 默认值 | 说明 |
|---|---|---|
| `AMAP_KEY` / `GAODE_KEY` | 空 | 高德 Web 服务 Key。不配 → 直线估算 |
| `AMAP_JS_KEY` + `AMAP_JS_SECURITY_CODE` | 空 | 高德 JS API(前端地图)。2021 年后新建的 Key 必填安全密钥,否则白屏。不配 → 前端自动回退 OpenStreetMap |
| `DEFAULT_CITY` | 池州 | 地址里没写省市时的默认搜索城市(作者实际使用城市) |
| `DEFAULT_START` | 家(30.66, 117.49) | 前端没设置家时的默认起点 |
| `HOST` | 0.0.0.0 | 允许手机等局域网设备访问 |
| `PORT` | 5000 | 端口 |
| `DEBUG` | 关 | 默认关 debug(生产安全),开发时 `set FLASK_DEBUG=1` 打开 |
| `LLM_API_KEY` / `DEEPSEEK_API_KEY` | 空 | LLM 解析。不配 → 规则解析 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容接口地址 |
| `LLM_MODEL` | `deepseek-v4-flash` | 模型名 |
| `LLM_PARSER` | 0 | 显式设 `1` 才启用 LLM 解析(避免误开多花钱) |

## 为什么默认城市是"池州"

`DEFAULT_CITY` 是作者实际使用场景(安徽省池州市)。这是设计上刻意的取舍:模糊地址在默认城市里搜命中率高得多;地址里写清楚省市时,`geocode.py` 的 `_auto_city` 会检测到并自动切到全国搜索,互不冲突。换城市只需改一个环境变量,不需要改代码。

## 常见问题

**Q: 为什么 `LLM_PARSER` 要显式设 1 才启用,而不是配了 Key 就自动用?**
A: 规则解析免费、毫秒级、离线可用;LLM 解析每次调用都要花钱、有网络延迟。默认不开,是"保守优先":配了 Key 只是"可用",显式设 1 才是"启用"。
