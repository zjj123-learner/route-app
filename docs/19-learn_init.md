# learn_init.py 详解 —— Learning to Optimize(学习引导贪心初始化)

## 为什么存在

把 ML 真正引进来, 而不只是"用 AI 调参": 用暴力最优解当**专家轨迹**,
学一个"下一个该排谁"的打分器, 用它引导贪心构造初始解, 喂给模拟退火/遗传算法。
这就是顶会(NeurIPS/ICLR)里 "learning to construct" 的思路, 落地到带时间窗的 TSP/VRP 上。

## 方法(行为克隆 + 学习引导搜索)

1. **监督数据**: 随机生成 n≤8 小实例, 全排列枚举求出全局最优路线(专家);
2. **行为克隆**: 把专家路线拆成一步步决策。每步状态 + 每个候选任务的 13 维特征
   (`travel, wait, window_width, window_slack, late_overdue, deadline_overdue,
    has_fixed, has_window, has_deadline, priority, duration, remaining_count, avg_dist_others`),
   标签 = 该步的最优选择;
3. **打分器**: 随机森林二分类(类别平衡), 输出"这个任务像不像最优下一步";
4. **学习引导贪心**: 每步选模型分最高的任务, 模型没把握(<0.5)时退化为最近邻;
   `evaluate_order` 剪到 top-8——学习给先验, 优化给最优性, 两者互补;
5. **对比实验**: 三种初始解(随机 / 最近邻 / 学习引导贪心)喂给 SA/GA,
   分别在完整预算和有限预算(15% 迭代)下比最终成本与收敛曲线下面积(AUC)。

## 配套改动

`simanneal.py` / `genetic.py` 新增两个实验参数(默认不影响线上行为):
- `sa_route(..., init_order=..., curve=True)`: 注入初始解 + 返回收敛曲线;
- `ga_route(..., init_solutions=[...], curve=True)`: 注入初始种群种子 + 每代曲线。

## 用法

```bash
python learn_init.py            # 完整跑(生成数据->训练->对比->图表->报告)
python learn_init.py --smoke    # 冒烟
```

输出: `experiment/L2O实验报告.md` + `fig8~fig11`; 训练实例缓存
`experiment/data/l2o_train_instances.json`(带规模签名, 冒烟缓存不污染完整实验)。

## 结果解读

- 训练: 464 个实例 → 8624 条决策样本, 测试集单步准确率 ~81%(随机猜约 1/n);
- 学习引导贪心的初始解成本追平/略优于最近邻(见报告表格);
- 有限预算下初始解价值被放大——"算不动"时, 好起点就是好结果;
- 诚实结论: 单步准 ≠ 整条路线最优, 所以加最近邻兜底 + 束搜索扩展, 最终看成本。

## 面试怎么讲

1. "传统优化保证最优性(最终收敛), 学习提供先验(更快起步)";
2. "行为克隆用暴力最优当专家, 学到的是时间窗松、优先级高、顺路这类人类也认可的排单直觉";
3. "这不是调参, 是 learning to construct——把学习结果真正接进优化流程"。


