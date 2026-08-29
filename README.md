# 智能行程规划 (route-app)

把"明天上午9点去银行办卡、下午3点去学校接孩子放学、顺便去超市买菜、晚上7点前从驿站取快递"这类中文自然语言任务丢进来,自动帮你排出**最优顺序**——一个带时间窗、优先级、多日约束的行程规划系统。

- **后端**: Python + Flask(零重型框架依赖, 只有 flask + requests)
- **前端**: 单文件 HTML + 原生 JS(无构建), 支持地图、三算法切换、历史记录、PWA
- **算法**: 暴力枚举 / 最近邻+2-opt 启发式 / 模拟退火 / 遗传算法
- **外部服务**: 高德(地理编码 + 真实路网时间)、DeepSeek(可选 LLM 解析),**全部可降级**,不配 Key 也能跑

## 功能

- 🗣️ 中文自然语言解析:规则解析(离线) + 可选 LLM 解析(DeepSeek,逐行失败自动回退)
- ⏰ 四类时间约束:固定预约(不能迟到)、时间窗、截止时间、优先级(重要的事排前面)
- 📅 多日规划:今天/明天/后天/周几/X月X日
- 🗺️ 真实路网时间:高德步行/驾车路径规划,直线估算兜底
- 🧮 三个求解器 + 客观评测:≤8 任务暴力找最优,9~30 任务退火/遗传/启发式
- 🔒 任务锁定、拖拽重排、回基地休息插入、路线轨迹绘制
- 💾 地点记忆 + 历史计划(SQLite),"去银行"下次直接给坐标
- 📱 移动端适配 + PWA 可添加到主屏幕

## 架构

```
输入文本 → parser.py / llm_parser.py(解析)
        → geocode.py / route.py(补坐标 + 真实路网矩阵)
        → optimizer.py / simanneal.py / genetic.py(三个求解器)
        → app.py(选最优 + 插入休息停靠)
        → db.py(地点记忆 / 历史计划) → 前端展示(地图 + 时间轴)
```

## 快速开始

```bash
pip install -r requirements.txt
python app.py
# 浏览器打开 http://localhost:5000
```

可选环境变量(不配会自动降级):

| 变量 | 作用 | 默认 |
|---|---|---|
| `AMAP_KEY` / `GAODE_KEY` | 高德 Web 服务 Key(搜索/路径规划) | 无 → 直线估算 |
| `AMAP_JS_KEY` + `AMAP_JS_SECURITY_CODE` | 高德 JS 前端地图 | 无 → OpenStreetMap |
| `DEEPSEEK_API_KEY` / `LLM_API_KEY` | LLM 解析 | 无 → 规则解析 |
| `LLM_BASE_URL` / `LLM_MODEL` | LLM 接口地址/模型 | deepseek-v4-flash |
| `LLM_PARSER` | 设为 `1` 启用 LLM 解析 | 0 |
| `DEFAULT_CITY` / `HOME_LAT` / `HOME_LNG` | 默认城市/家 | 池州 |
| `PORT` / `HOST` / `FLASK_DEBUG` | 运行参数 | 0.0.0.0:5000 |

详情见 [docs/01-config.md](docs/01-config.md)。

## 测试与评测

```bash
python test.py              # 185 条断言, 一键自测
python benchmark.py         # 三算法 vs 暴力最优(40 实例, 可复现)
python benchmark.py smoke   # 快速冒烟
python benchmark_llm.py --rule   # 规则解析 50 语料 + 15 泛化集
```

## 文档索引(逐文件详解)

| 文件 | 文档 | 一句话职责 |
|---|---|---|
| `config.py` | [docs/01-config.md](docs/01-config.md) | 全部配置集中在环境变量 |
| `parser.py` | [docs/02-parser.md](docs/02-parser.md) | 规则式中文任务解析(离线) |
| `llm_parser.py` | [docs/03-llm_parser.md](docs/03-llm_parser.md) | LLM 解析 + 确定性后处理 |
| `geocode.py` | [docs/04-geocode.md](docs/04-geocode.md) | 地址/关键词 → 经纬度 |
| `route.py` | [docs/05-route.md](docs/05-route.md) | 真实路网时间矩阵 |
| `optimizer.py` | [docs/06-optimizer.md](docs/06-optimizer.md) | 问题建模 + 评价函数 + 暴力/启发式 |
| `simanneal.py` | [docs/07-simanneal.md](docs/07-simanneal.md) | 模拟退火求解器 |
| `genetic.py` | [docs/08-genetic.md](docs/08-genetic.md) | 遗传算法求解器 |
| `app.py` | [docs/09-app.md](docs/09-app.md) | Flask 路由 + 全流程组装 |
| `db.py` | [docs/10-db.md](docs/10-db.md) | SQLite 地点记忆/历史计划 |
| `corpus.py` | [docs/11-corpus.md](docs/11-corpus.md) | 解析评测语料(50+15) |
| `benchmark.py` | [docs/12-benchmark.md](docs/12-benchmark.md) | 三算法对比实验 |
| `benchmark_llm.py` | [docs/13-benchmark_llm.md](docs/13-benchmark_llm.md) | 规则 vs LLM 对比实验 |
| `test.py` | [docs/14-test.md](docs/14-test.md) | 185 条自测断言 |
| `main.py` | [docs/15-main.md](docs/15-main.md) | 命令行入口(不依赖 Web/Key) |
| `templates/index.html` | [docs/16-frontend.md](docs/16-frontend.md) | 单文件前端 |
| `static/sw.js` + `manifest.json` | [docs/17-pwa.md](docs/17-pwa.md) | PWA 离线外壳 |
| `算法详解.md` | 算法原理长文 | 从问题定义到每段代码的讲解 |

## 目录结构

```
route-app/
├── app.py              # Flask 入口与 API
├── parser.py           # 规则解析
├── llm_parser.py       # LLM 解析(可选)
├── geocode.py          # 高德地理编码/POI
├── route.py            # 高德路径规划矩阵
├── optimizer.py        # 建模 + 暴力/启发式
├── simanneal.py        # 模拟退火
├── genetic.py          # 遗传算法
├── db.py               # SQLite 记忆
├── corpus.py           # 评测语料
├── benchmark.py        # 算法评测
├── benchmark_llm.py    # 解析评测
├── test.py             # 自测
├── main.py             # CLI 入口
├── config.py           # 环境变量配置
├── requirements.txt
├── templates/index.html  # 前端
├── static/             # PWA 资源(sw.js/manifest/icons)
└── docs/               # 本目录: 逐文件详解
```

## 说明

- `route.db`(运行产生的数据)与 `*.bak` 备份已 gitignore,不进入仓库
- 代码与文档中的注释为中文,与仓库语言保持一致
