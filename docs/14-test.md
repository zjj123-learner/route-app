# test.py 详解 —— 185 条断言的自测套件

## 这个文件为什么存在

一个 3000+ 行、涉及外部 API(高德/DeepSeek)的项目,改一处可能崩一片。这个文件用**零依赖的自制断言框架**把核心逻辑全部锁住:一行 `python test.py`,185 条断言全绿才算可发布。

**为什么不用 pytest/unittest**:个人项目不想引入额外依赖(requirements.txt 只有 flask + requests),`python test.py` 双击即跑,对使用者零门槛。代价是断言粒度自己管,`check(condition, message)` 一个函数全包。

## 框架机制

### `check(condition, message)` + 全局计数

```python
def check(condition, message):
    if condition:
        # 打 PASS, 计数
    else:
        # 打 FAIL, 计数
    ...
# 结尾汇总: 总 N 条, 通过 N, 失败 0; 有失败就 sys.exit(1)
```

- 每个断言一行 `check(实际值 == 期望值, "中文说明")`,失败时最后汇总 + 非零退出码(可接 CI);
- 测试说明全是中文,跑完输出可读性高。

### `_FakeResp` —— 离线模拟 HTTP 响应

```python
class _FakeResp:
    """模拟 requests.Response: .json() 返回预设数据, 用于测 geocode/route/llm 的降级逻辑"""
```

测 geocode/route/llm 时**不发真实网络请求**,而是 monkeypatch `requests.get/post` 返回预设的假响应:
- 测"成功解析"的路径;
- 测"返回垃圾/超时/Key 失效"的降级路径(降级是这套系统的核心卖点,必须测);
- 这样测试全离线、秒级跑完、CI 友好。

## 26 组测试的分类结构

### 解析层(parser + llm_parser)
- `test_parser`:规则解析的时间/地点/优先级/时长基础行为;
- `test_duration_window`:明确起止时间段 → 时长 = 段长;
- `test_keyword`:地点关键词提取;
- `test_llm_parser`:LLM 解析可用性、失败回退、JSON 抽取;
- `test_llm_normalize`:三个确定性兜底(模糊时段/取快递误判/补 latest);
- `test_corpus`:50 训练语料全对 + 泛化集 6/15 以上。

### 优化层(optimizer + 两个元启发式)
- `test_optimizer`:评价函数总分 = 路程 + 等待 + 惩罚;
- `test_buffer`:预留时间参数生效;
- `test_priority_order`:高优先级任务被排到前面;
- `test_heuristic`:启发式结果 ≤ 暴力最优 + 一定比例;
- `test_simanneal` / `test_genetic`:method 正确、结果不比暴力最优差、锁定任务不动、同 seed 可复现;
- `test_locked`:锁定槽位校验与冲突报错;
- `test_multiday`:跨天任务的绝对分钟排序。

### 记忆层(db)
- `test_db`:建表/记地点/查地点/偏好存取;
- `test_plan_memory` / `test_plan_memory_multi`:地点记忆如何影响候选(常用排前)。

### 地理与路线(geocode + route,全用 _FakeResp)
- `test_route_api` / `test_route_matrix` / `test_route_polyline`:真实路网解析、矩阵键、轨迹解析;
- `test_route_fail_retry`:失败短时缓存,60 秒内不重试;
- `test_optimizer_uses_route`:优化器确实查了路网矩阵(不是偷偷用直线)。

### 应用层(Flask API,Flask test client)
- `test_plans_api` / `test_plan_api_route_lines`:`/api/plans` 的列表/详情/恢复,路线连线数量正确;
- `test_config`:环境变量读取与默认值。

## 测试哲学

1. **测行为不测实现**:断言的是"结果对不对",不是"用了哪个函数";
2. **外部依赖全部打桩**:网络层用 `_FakeResp`,测试永远离线可复现;
3. **降级路径和正常路径同等重要**:没 Key、Key 失效、返回垃圾,都是必须覆盖的分支——这正是这个项目在真实环境里能跑的原因;
4. **算法测试用"相对最优"而非"精确值"**:元启发式是随机算法,断言"不比暴力最优差超过 X%"而不是写死一个数字,避免测试被随机种子搞挂。

## 局限与演进

- 断言数量多但覆盖不了前端(没有浏览器自动化);
- 语料类断言(50 全对)会把规则解析器"焊死"在现有行为上,以后改口径要先改语料——这是刻意选择:语料即规格。
- 若要上 CI,建议加 `pytest` 兼容层(现有 `check` 函数可以包装成 pytest 断言),或直接 `python test.py` 作为 CI 的 smoke job。
