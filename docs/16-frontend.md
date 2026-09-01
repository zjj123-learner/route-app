# templates/index.html 详解 —— 单文件前端

## 这个文件为什么存在

1529 行的单文件前端,零框架、零构建,直接用原生 JS + 少量 CSS。**为什么这么做**:
1. **个人工具不需要工程化**:没有多页面、没有状态管理需求,单文件部署即用,`flask` 直接 render;
2. **与后端交互极简**:全站只有 5 个 API,`fetch` 一把梭;
3. **手机可用**:移动端适配 + PWA(见 docs/17),作者的核心使用场景是手机浏览器;
4. 代价是维护性差——所有逻辑在一个文件里,但作为个人项目这是"够用"的取舍。

## 结构概览

```html
<head> 地图 CSS(高德/Leaflet) + 样式
<body>
  <header>  输入框 + 设置 + 运行按钮
  <main>
    候选选择区(#pickBox)    ← 地点有歧义时出现
    计划展示区(时间轴/条形图)
    地图容器
    路线切换 Tab(三算法)
    历史记录弹窗
  </main>
  <script> 全部 JS(下方按模块拆解)
```

## 功能模块与重点函数

### 设置与持久化(浏览器 localStorage)
- `loadPlanOpts` / `savePlanOpts`:出行方式、出发时间、buffer、时段偏好——存 localStorage,下次打开还在;
- `saveHome` / `loadHome` / `locateHome` / `applyHome`:家的设置(手动选坐标或输入地址定位);
- `savePlanState` / `restorePlanState` / `clearPlanState`:自动保存上一次的输入与结果,刷新不丢。

### 地图(高德优先,失败回退 Leaflet/OSM)
- `ensureAmap` / `amapKeyBroken`:按需加载高德 JS SDK,加载失败自动切 Leaflet(OpenStreetMap)——和后端"没 Key 也能跑"一个思路;
- `initMap` / `createLeafletMap` / `dropHomeMarker` / `onMapClick`:初始化地图、拖放/点击设家;
- `whenMapReady`:地图初始化是异步的,把"地图就绪后才执行"的回调排队,避免时序竞态。

### 计划主流程
- `runPlan(places, forcedLocks, textOverride)`:核心函数,组装请求体(文本/模式/起点/偏好/锁定/已选地点)POST `/api/plan`,渲染结果;
- `showPicker(needPick)`:后端返回歧义地点候选时,渲染"选哪个"列表,用户选完带 `places` 重发——两轮交互的第二步;
- `renderStops` / `renderTimeline` / `renderVisuals`:三种可视化——卡片列表、时间轴(按时段分布)、条形图(各段耗时占比);
- `replanRemove(srcIdx, postpone)`:对已完成/要跳过的任务标记处理,重新规划剩余任务;
- `stopsToPlaces`:把当前排序的地点原样带回后端,拖拽重排后不会重新弹候选。

### 三算法切换
- `renderRouteTabs` / `switchRoute`:把 `all_routes`(启发式/退火/遗传)渲染成三个 Tab,点击切换地图和时间轴;
- Tab 上带算法配色圆点(`ROUTE_COLORS`:启发式蓝/退火橙/遗传绿),配色与地图一致;
- 顶部提示条:"⚡ 三个算法结果一致,已是最优"(暴力枚举给出)或"三算法结果一致"。

### 三算法路线同屏(地图)
- `renderMapNow` 在有 `all_routes` 时把**三条路线同时画在地图上**:选中的算法深色粗线(weight 5~6,opacity 0.95),其他算法同色浅细线(weight 2~2.5,opacity 0.25~0.3),一眼看出三条路线的差异;
- 序号标记只画在**当前选中路线**上(用选中算法的颜色),避免三套序号互相遮挡;
- 地图下方图例(`renderMapLegend`)列出三算法色块,点击图例也能切换路线;历史计划没有 `all_routes` 时自动退化为单线画法。

### 算法对比可视化
- `renderVisuals` 末尾追加 `algoCompareHTML`:对比**耗时(ms)/评价次数/总成本**三个指标,用同色系条形图展示,越小越好;
- 数据来源:后端为每个算法测量 `elapsed_ms`(墙钟)与 `evals`(候选顺序评估次数,见 docs/09-app.md),随 `all_routes` 返回;
- 底部附理论复杂度说明(暴力 O(n!·n) / 启发式 O(n²·k) / 退火 O(迭代·n) / 遗传 O(种群·代数·n))。

### 锁定与拖拽
- 任务卡片上"🔒 锁定"按钮 → 把锁定位置传给后端;
- 拖拽排序即最终顺序:全量锁定当前顺序,让后端用真实路网重算时间和轨迹。

### 历史记录
- `showHistory` / `restorePlan`:拉取 `/api/plans` 列表,点开恢复整条计划(地图画直连虚线,因为历史计划不存轨迹)。

## 与后端交互的 5 个 API

| 调用 | 方法/路径 | 时机 |
|---|---|---|
| 运行计划 | POST `/api/plan` | 点"智能排序" |
| 家定位 | POST `/api/geocode` | 输入地址定位家 |
| 常用地点 | GET `/api/places?keyword=` | 输入框快捷提示 |
| 历史计划 | GET/DELETE `/api/plans` | 打开历史/清空 |
| 计划详情 | GET `/api/plans/<id>` | 恢复单条 |

## 设计取舍

- **"选地点"是弹层不是自动**:机器可以猜,但错了用户会很烦——歧义地点列出候选(常用记忆排最前),用户点一下,两轮交互成本最低;
- **离线可用优先**:地图回退 OSM、PWA 缓存外壳(见 docs/17),弱网/断网时页面壳能打开;
- **所有显示的数字都有来源**:路程分钟、等待、总耗时都来自后端 stats/summary,前端不自己算,避免两套口径打架。
