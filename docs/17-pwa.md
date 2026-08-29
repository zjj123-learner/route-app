# static/ PWA 资源详解 —— sw.js + manifest.json

## 这三个文件为什么存在

让"行程规划"可以被**添加到手机主屏幕、当 App 用**,并在弱网/断网时能打开外壳——作者的核心使用场景是手机浏览器,不是桌面。

```
static/
├── sw.js            # Service Worker: 离线缓存策略
├── manifest.json    # PWA 元数据: 名称/图标/显示模式
├── icon-192.png     # 主屏幕图标
└── icon-512.png     # 大图标(支持 maskable)
```

## sw.js —— Service Worker

### 缓存策略:网络优先,缓存兜底,接口永不缓存

```js
// 1. 非 GET 请求不处理
// 2. 跨域请求(高德/瓦片)不缓存
// 3. /api/ 接口绝不缓存 —— 保证数据永远最新
// 4. 其余(HTML/CSS/JS/图标): 先请求网络, 成功就更新缓存; 失败用缓存兜底
```

**为什么网络优先而不是缓存优先**:这是**数据型工具**不是内容型网站,离线只是"降级可用"不是主要体验。网络正常时永远拿最新代码;断网时至少能打开页面壳(但接口失败,提示需要网络)。和 `route.py` 的"真实路网优先、直线兜底"是同一个哲学:**优先体验,降级兜底**。

### 版本化缓存清理

```js
const CACHE = 'route-app-v1';
activate 时: 删掉所有不是当前 CACHE 版本的旧缓存
```

发新版时把 `CACHE` 改成 `route-app-v2`,旧缓存自动清掉——避免"更新了但浏览器还用旧文件"的经典 PWA 坑。

### 生命周期

- `install`:直接 `skipWaiting`,不等旧页面关掉就激活新版;
- `activate`:清旧缓存 + `clients.claim`,让已打开的页面立刻受控。

## manifest.json —— 主屏幕体验

```json
{
  "name": "智能行程规划",
  "short_name": "行程规划",
  "display": "standalone",        // 全屏 App 感, 无浏览器地址栏
  "background_color": "#eef1fb",
  "theme_color": "#4f46e5",       // 顶部状态栏颜色
  "icons": [192 + 512, maskable]
}
```

- `standalone` 是 PWA 的"App 感"关键;
- `maskable` 图标适配 Android 的圆形裁剪;
- `start_url: "/"` 保证从主屏幕打开直接进应用。

## 注册方式与限制

`index.html` 尾部注册:

```js
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
```

**两个限制**(代码里如实处理):
1. Service Worker 只在 `localhost` / `127.0.0.1` 或 **HTTPS** 下生效——内网 IP 访问没有离线能力,`.catch(() => {})` 静默失败不影响使用;
2. `/sw.js` 必须挂在**根路径**才能控制整个站(`app.py` 里专门加了 `GET /sw.js` 路由做静态转发)。

## 演进建议

- 断网时现在只能开壳,可以考虑把"最近一次计划结果"存 localStorage,离线时也能看(方案见 docs/16 的 `savePlanState`);
- 缓存版本号建议做成自动(读后端版本接口),现在手动改 `CACHE` 常量。
