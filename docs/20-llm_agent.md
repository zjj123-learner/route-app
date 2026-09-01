# llm_agent.py 详解 —— LLM 智能体化(混合架构 + ReAct 工具调用)

## 为什么存在

现在 LLM 只做"解析"(llm_parser.py)。升级成真正的 LLM 应用:

1. **混合架构**: LLM 粗排顺序(常识偏好)→ 求解器精排(全局最优)。
   一句话: "LLM 负责语义理解, 传统优化负责最优性";
2. **ReAct 工具调用**: LLM 遇到地点含糊/偏远的任务时, 自己决定调用地图工具
   (geocode / search_nearby), 拿结果再规划——呼应 geocode.py 的"偏远地点候选"能力。

## 混合架构

```
用户任务 -> 解析器 -> [LLM 粗排(常识)] -> [SA/GA 精排(最优)] -> 路线
                      \__ 语义理解 __/    \___ 最优性 ___/
```

- `coarse_rank(tasks)`: 真 LLM 输出任务下标排列(JSON, 校验是合法排列才用);
  没配 key / 网络失败 / 格式错 → 自动回退**常识代理**(确定性规则: 固定预约先、
  时间窗早的先、优先级高的先), 管线永不因 LLM 挂掉;
- `hybrid_plan(tasks, start, algo=...)`: LLM 顺序当初始解喂给 SA/GA
  (用 simanneal.py/genetic.py 新增的 `init_order` / `init_solutions` 参数)。

## ReAct 工具调用

- `react_decide_actions(tasks)`: LLM 决定工具动作
  `[{"tool":"geocode","keyword":"银行"},{"tool":"nearby","keyword":"饭店"}]`;
- `execute_tools(actions, center)`: 执行(复用 geocode.py, 带缓存), 无 AMAP_KEY 返回空;
- `react_plan(tasks, start)`: 工具结果合并回任务 → 求解器精排。
  缺坐标且无法补全时优雅降级, 不会崩。

## 用法

```bash
python llm_agent.py --demo                            # 演示混合架构
python llm_agent.py --bench --limit 10                # 离线对比(常识代理模拟 LLM)
python llm_agent.py --bench --limit 10 --llm          # 配 key 后用真 LLM
python llm_agent.py --react "明天上午去银行, 下午去郊区看仓库"   # ReAct 演示
```

`--bench` 输出 `experiment/实验报告3-LLM混合架构.md`, 报告会明确标注粗排来源
(真 LLM / 常识代理), 不混着说。

## 集成到 app.py

把 `/api/plan` 里 simanneal 分支换成:

```python
from llm_agent import hybrid_plan
rr = _measure(lambda o: hybrid_plan(tasks, start, algo="simanneal",
                                    seed=20260826, options=o))
```

即可让线上路线吃到 LLM 的常识 + 算法的全局最优; LLM 失败自动回退, 不影响用户。

## 面试怎么讲

1. "LLM 负责语义理解(粗排表达人的偏好), 传统优化负责最优性(精排保证全局);
   混合架构, 任一环节失败都不影响系统——这是工程落地的关键";
2. "ReAct 式: 模型自己决定何时调用工具, 工具结果回流再规划,
   把'去偏远地方办事'这类模糊需求变成可执行路线";
3. "对比实验明确标注 LLM 来源(真 LLM / 代理), 不夸大结果"。
