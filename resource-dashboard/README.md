# 资源管理大屏

基于 React、TypeScript、Vite 和 ECharts 的资源管理看板。页面按 2048×1133 参考稿还原，并对窄屏做纵向重排。

## 本地运行

```bash
npm install
npm run dev
```

生产构建：

```bash
npm run build
npm run preview
```

## 接入真实接口

开发模式默认使用 `src/data/mockDashboard.ts`，因此无需后端即可预览。复制环境变量后关闭 mock：

```bash
cp .env.example .env
```

```dotenv
VITE_API_BASE_URL=/api
VITE_USE_MOCKS=false
DASHBOARD_API_TARGET=http://localhost:8000
```

- `VITE_API_BASE_URL`：浏览器请求前缀。
- `DASHBOARD_API_TARGET`：Vite 开发代理转发目标，仅开发环境使用。
- `VITE_USE_MOCKS`：`false` 时调用真实接口。

## 接口约定

### `GET /api/dashboard/snapshot`

页面启动时请求一次，之后每 60 秒轮询。返回完整的 `DashboardSnapshot`：

```json
{
  "serverTime": "2026-07-20T15:00:00+08:00",
  "resourceMetrics": [],
  "operationSummary": [],
  "quotaRanking": [],
  "cpuRanking": [],
  "memoryRanking": [],
  "applicationTrend": { "categories": [], "series": [] },
  "resourceTrends": {
    "cpu": { "categories": [], "series": [] },
    "memory": { "categories": [], "series": [] },
    "disk": { "categories": [], "series": [] }
  },
  "centers": [],
  "cityValues": {}
}
```

所有字段的完整类型定义位于 `src/types/dashboard.ts`。

### `GET /api/dashboard/centers`

返回 `ResourceCenter[]`，用于单独刷新地图中心数据。

### `GET /api/dashboard/trends?metric=cpu&range=12m`

返回 `TrendDataset`。`metric` 支持 `cpu`、`memory`、`disk`。

接口请求、超时和错误处理统一封装在 `src/api/dashboard.ts`。真实接口暂时不可用时，页面保留最近一次数据，不会清空大屏。
