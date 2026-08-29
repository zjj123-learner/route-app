# optimizer.py 详解 —— 问题建模 + 评价函数 + 暴力/启发式求解

## 这个文件为什么存在

这是整个项目的**算法心脏**。它回答两个问题:

1. **问题是什么**:这是一个"带时间窗、优先级和惩罚的 TSP(旅行商问题)变体"——决定访问地点的顺序,让总代价最小。
2. **怎么评分**:所有算法(暴力/启发式/模拟退火/遗传)用**同一把尺子** `evaluate_order` 比较候选顺序的好坏。

三个求解器(暴力、启发式在这里;退火在 `simanneal.py`;遗传在 `genetic.py`)共用这套模型,互不重复。

## 问题建模:四个现实约束

| 约束 | 字段 | 代价体现 |
|---|---|---|
| 路程 | `travel` | 每 1 分钟 = 1 分 |
| 早到等待 | `wait` | ×0.5 权重 |
| 迟到(固定预约) | `fixed_late_penalty` | 每分钟 ×100 |
| 迟到(时间窗) | `window_late_penalty` | 每分钟 ×30 |
| 超过截止 | `deadline_penalty` | 每分钟 ×50 |
| 高优先级被排后 | `priority_weight` | 每靠后一位 ×3 |

`total = travel + wait × 0.5 + penalty`。注意 `total` 是**加权代价**,不是真实分钟数——它只用来比较顺序好坏,前端显示的真实耗时另算(`summary.total_minutes`)。

## 重点函数逐个说明

### `haversine_km(a, b)` + `travel_minutes(a, b, opts)`

`travel_minutes` 是路程时间的唯一入口:**有真实路网矩阵就查表**(键四舍五入到 5 位小数,和 `route.build_matrix` 一致),没有就回退直线估算。这样优化器完全不知道"用的是真实时间还是估算",降级对算法透明。

### `evaluate_order(order, start, opts)` —— 评价函数(核心中的核心)

模拟按给定顺序执行一遍,累计代价:

1. 到达任务前先加上一段 `travel`;
2. **等待**:早到了就等到最早可开始时间(fixed 任务提前 `fixed_early_buffer=30` 分钟到);
3. **惩罚**:fixed 迟到 ×100/分钟,latest 迟到 ×30/分钟,deadline 前没完成 ×50/分钟;
4. **优先级惩罚**:`(priority - 1) × priority_weight × i`——高优先级任务被排得越靠后,扣分越多;
5. 累加输出 `{total, travel, wait, penalty, arrivals}`,其中 `arrivals` 含每个任务的到达/离开时间,前端渲染全靠它。

**为什么权重这么设**:fixed(接孩子)绝不能迟到所以罚最重;deadline(取快递)次之;时间窗只是"希望",最轻。这三个数字是经验值,`test.py` 里有专门测试保证权重相对大小正确。

### `nearest_neighbor(tasks, start, opts)` —— 最近邻贪心

从起点出发,每次选"路程 + 等待 + 优先级惩罚"综合代价最小的下一个。**不追求最优**,只用来构造一个还不错的初始解,后面交给 2-opt 精修。

### `two_opt(order, start, opts, max_passes=20, fixed_slots=())` —— 局部搜索

两个邻域动作,评分变好就接受:
- **2-opt**:反转一段(经典 TSP 操作,对开环路线等价于反转中间一段);
- **交换**:交换两个位置。

`fixed_slots` 是锁定的槽位集合,反转/交换不能碰到锁定任务。评分永远用 `evaluate_order`,所以结果只会越来越接近暴力最优。

### `heuristic_route(tasks, start, opts)` —— 启发式完整流程

最近邻构造初始解 → 2-opt 精修 → 输出。毫秒级,但会卡局部最优(benchmark 实测小规模平均差 2.24%)。

### `_solve_with_locks(...)` —— 带锁定任务的求解

锁定任务放进固定槽位,自由任务填剩余槽位:
- 自由任务 ≤8 → 对自由部分暴力枚举;
- 否则 → 最近邻 + 2-opt(fixed_slots 保护锁定槽)。

锁定冲突(两个任务钉同一个位置)抛 `ValueError`,由上层转成 400 错误。

### `optimize_route(tasks, start, options, fixed_positions)` —— 统一入口

调度规则:
1. 空任务 → 空结果;
2. 有锁定 → `_solve_with_locks`;
3. 无锁定且任务 ≤8 → **暴力枚举全排列**(`max_brute_force=8`,8! = 40320 次评价,毫秒级,保证最优);
4. 否则 → 启发式。

三种情况都返回同一个结构 `{order, arrivals, stats, method}`,`method` 标记 "brute-force"/"heuristic",前端据此提示"三个算法结果一致,已是最优"。

### `period_of(minute)` / `insert_base_stops(arrivals, base, options, ...)` —— 回基地休息

`insert_base_stops` 在长空闲间隙里插入"回家休息"停靠,处理"下午没事做,先回家歇会儿"的真实需求:
- **自动模式**:间隙 ≥60 分钟且来回够休息(≥30 分钟)才回;
- **home/stay 偏好**:按时段强制回家或强制不回家,偏好存在 `db.py` 的 prefs 表;
- 过夜空隙(≥8 小时)不插,那是第二天重新出发。

## 为什么暴力上限是 8

8! = 40320 次 `evaluate_order`,每次是线性扫描,实测毫秒级;9! = 362880 就明显慢了。所以 8 是"前端能实时响应"的阈值,9~30 交给元启发式。这个数字写在 `DEFAULTS["max_brute_force"]`,是实验和实测权衡的结果,不是拍脑袋。
