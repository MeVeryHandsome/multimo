# Multimo 接口文档

> 由 `scripts/gen_api_docs.py` 根据后端的 OpenAPI 文档（`/api.json`，版本 v1.0.0）生成，接口改动后重新生成即可。交互式文档：服务启动后访问 `/swagger`。

## 约定

- 所有接口以 `/api/v1` 开头，`docker compose` 部署时后端在 `http://localhost:8000`，前端的 `/api` 也会转发到这里。
- 请求和响应都是 JSON（上传文件用 `multipart/form-data`，图片接口直接返回 JPEG）。
- 响应统一包在 `{"code": 0, "message": "OK", "data": {...}}` 中，`code` 不为 0 表示失败，`message` 说明原因；下文“返回”列出的是 `data` 的字段。
- 坐标为 WGS84 经纬度（度），距离为米，时间为 ISO 8601 或 `YYYY-MM-DD HH:mm:ss`。
- 服务本身不做鉴权，按内网服务部署；模型密钥只保存在后端的环境变量中。

## 目录

- 系统
  - [AI 调用记录](#get-apiv1systemai-calls)：`GET /api/v1/system/ai-calls`
  - [服务健康检查](#get-apiv1systemhealth)：`GET /api/v1/system/health`
  - [数据概况](#get-apiv1systemstats)：`GET /api/v1/system/stats`
- 素材
  - [素材列表](#get-apiv1assets)：`GET /api/v1/assets`
  - [上传文件](#post-apiv1assets)：`POST /api/v1/assets`
  - [登记服务器上的文件](#post-apiv1assetsregister)：`POST /api/v1/assets/register`
  - [删除素材](#delete-apiv1assetsid)：`DELETE /api/v1/assets/{id}`
  - [素材详情](#get-apiv1assetsid)：`GET /api/v1/assets/{id}`
  - [重新处理素材](#post-apiv1assetsidreprocess)：`POST /api/v1/assets/{id}/reprocess`
  - [缩略图](#get-apiv1assetsidthumbnail)：`GET /api/v1/assets/{id}/thumbnail`
- 关键帧、轨迹与地图
  - [素材的关键帧](#get-apiv1assetsidkeyframes)：`GET /api/v1/assets/{id}/keyframes`
  - [飞行轨迹](#get-apiv1assetsidtrack)：`GET /api/v1/assets/{id}/track`
  - [地图概览](#get-apiv1geooverview)：`GET /api/v1/geo/overview`
  - [关键帧详情](#get-apiv1keyframesid)：`GET /api/v1/keyframes/{id}`
  - [关键帧图片](#get-apiv1keyframesidimage)：`GET /api/v1/keyframes/{id}/image`
  - [批量重跑处理阶段](#post-apiv1pipelinererun)：`POST /api/v1/pipeline/rerun`
- 处理任务
  - [处理任务列表](#get-apiv1tasks)：`GET /api/v1/tasks`
- 检索
  - [混合检索](#post-apiv1search)：`POST /api/v1/search`
- 问答
  - [智能问答](#post-apiv1qa)：`POST /api/v1/qa`
- 评测
  - [历史报告](#get-apiv1testsuitereports)：`GET /api/v1/testsuite/reports`
  - [报告（JSON）](#get-apiv1testsuitereportsid)：`GET /api/v1/testsuite/reports/{id}`
  - [报告文件](#get-apiv1testsuitereportsidfile)：`GET /api/v1/testsuite/reports/{id}/{file}`
  - [运行评测](#post-apiv1testsuiterun)：`POST /api/v1/testsuite/run`
  - [评测进度](#get-apiv1testsuitestatus)：`GET /api/v1/testsuite/status`
- [数据结构](#数据结构)

## 系统

<a id="get-apiv1systemai-calls"></a>
### AI 调用记录

`GET /api/v1/system/ai-calls`

按端点汇总 token、延迟、降级次数，并返回最近的调用

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `endpoint` | 查询，string |  | vlm \| embedding \| decision |
| `status` | 查询，string |  | ok \| error \| fallback |
| `purpose` | 查询，string |  |  |
| `limit` | 查询，integer |  | 默认 `50` |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `summary` | [AICallSummary](#aicallsummary)[] |  |
| `recent` | [AICall](#aicall)[] |  |

<a id="get-apiv1systemhealth"></a>
### 服务健康检查

`GET /api/v1/system/health`

返回服务版本、数据库连通性以及当前配置的模型名称（不含密钥）

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `status` | string | ok \| degraded |
| `version` | string |  |
| `database` | string | up \| down |
| `models` | [ModelInfo](#modelinfo) |  |
| `missing` | string[] | 未设置的必需环境变量 |
| `time` | string |  |

<a id="get-apiv1systemstats"></a>
### 数据概况

`GET /api/v1/system/stats`

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `assetsByType` | [CountRow](#countrow)[] |  |
| `assetsByStatus` | [CountRow](#countrow)[] |  |
| `assetsByCollection` | [CountRow](#countrow)[] |  |
| `tasksByStatus` | [CountRow](#countrow)[] |  |
| `docChunks` | integer |  |
| `keyframes` | integer |  |

## 素材

<a id="get-apiv1assets"></a>
### 素材列表

`GET /api/v1/assets`

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `type` | 查询，string |  | video \| image \| remote_sensing \| document |
| `collection` | 查询，string |  |  |
| `status` | 查询，string |  | pending \| processing \| ready \| failed |
| `keyword` | 查询，string |  | 按文件名模糊匹配 |
| `page` | 查询，integer |  | 默认 `1` |
| `size` | 查询，integer |  | 默认 `20` |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `list` | [AssetItem](#assetitem)[] |  |
| `total` | integer |  |
| `page` | integer |  |
| `size` | integer |  |

<a id="post-apiv1assets"></a>
### 上传文件

`POST /api/v1/assets`

支持 MP4/WMV/FLV/MOV/MKV/AVI、JPEG/PNG/TIFF(GeoTIFF)、PDF/DOC/DOCX/TXT/CSV/GeoJSON。同一集合内内容相同的文件只保存一次

**请求体**（表单）

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `file` | file | 是 | 要上传的文件 |
| `sidecar` | file |  | 配套文件，如 DJI 飞行记录 CSV |
| `collection` | string |  | 集合名；默认 `default` |
| `type` | string |  | 把图像标为遥感影像（或反之）；可选 `image` / `remote_sensing` |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `asset` | [AssetItem](#assetitem) |  |
| `created` | boolean | false 表示已存在相同内容的文件 |

**示例**

```bash
curl -X POST localhost:8000/api/v1/assets \
  -F file=@flight.mp4 -F sidecar=@flight_record.csv -F collection=survey
```

<a id="post-apiv1assetsregister"></a>
### 登记服务器上的文件

`POST /api/v1/assets/register`

登记 TESTDATA_DIR 中已有的文件，不复制文件本身，用于批量导入测试数据

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `items` | [RegisterItem](#registeritem)[] | 是 | 最多 1000 项 |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `results` | [RegisterResult](#registerresult)[] |  |
| `created` | integer |  |
| `failed` | integer |  |

**示例**

```bash
curl -X POST localhost:8000/api/v1/assets/register -H 'Content-Type: application/json' -d '{
  "items": [
    {"path": "video/dji/palos_verdes/drone_palos_verdes_20250217174636.mp4", "collection": "dji",
     "sidecar": "video/dji/palos_verdes/flight_record.csv"},
    {"path": "geo/uav_visloc/01/drone/01_0001.JPG", "collection": "visloc",
     "metadata": {"time": "2018-09-17T12:44:21", "geo_reference": "basemap"}}
  ]}'
```

<a id="delete-apiv1assetsid"></a>
### 删除素材

`DELETE /api/v1/assets/{id}`

删除记录及派生文件；登记的原始文件保留

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，integer | 是 |  |

<a id="get-apiv1assetsid"></a>
### 素材详情

`GET /api/v1/assets/{id}`

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，integer | 是 |  |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `asset` | [AssetItem](#assetitem) |  |
| `tasks` | [Task](#task)[] |  |
| `counts` | [AssetCounts](#assetcounts) |  |

<a id="post-apiv1assetsidreprocess"></a>
### 重新处理素材

`POST /api/v1/assets/{id}/reprocess`

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，integer | 是 |  |

<a id="get-apiv1assetsidthumbnail"></a>
### 缩略图

`GET /api/v1/assets/{id}/thumbnail`

返回 JPEG

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，integer | 是 |  |

**返回**：JPEG 图片。

## 关键帧、轨迹与地图

<a id="get-apiv1assetsidkeyframes"></a>
### 素材的关键帧

`GET /api/v1/assets/{id}/keyframes`

视频为按内容变化抽取的关键帧；图片和遥感影像各有一帧

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，integer | 是 |  |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `list` | [Keyframe](#keyframe)[] |  |

<a id="get-apiv1assetsidtrack"></a>
### 飞行轨迹

`GET /api/v1/assets/{id}/track`

来自配套飞行记录、已对齐到视频时间轴的 GPS 点；最多返回 2000 个（均匀抽样）

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，integer | 是 |  |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `points` | [TrackPoint](#trackpoint)[] |  |

<a id="get-apiv1geooverview"></a>
### 地图概览

`GET /api/v1/geo/overview`

所有已定位的关键帧位置，以及带地理参考的底图范围（用于在地图上叠加概览图）

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `collection` | 查询，string |  | 限定集合 |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `points` | [GeoPoint](#geopoint)[] |  |
| `basemaps` | [Basemap](#basemap)[] |  |
| `tileUrl` | string | 背景瓦片地址模板（MAP_TILE_URL），为空时不加载在线瓦片 |

<a id="get-apiv1keyframesid"></a>
### 关键帧详情

`GET /api/v1/keyframes/{id}`

含描述、标签、检测框（关键帧像素坐标）、定位候选

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，integer | 是 |  |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `keyframe` | [Keyframe](#keyframe) |  |
| `detections` | [Detection](#detection)[] |  |

<a id="get-apiv1keyframesidimage"></a>
### 关键帧图片

`GET /api/v1/keyframes/{id}/image`

返回 JPEG

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，integer | 是 |  |

**返回**：JPEG 图片。

<a id="post-apiv1pipelinererun"></a>
### 批量重跑处理阶段

`POST /api/v1/pipeline/rerun`

对符合条件的素材重新排队某个阶段，后续阶段会自动接着运行

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `stage` | string | 是 | ingest \| keyframes \| analyze \| geo \| basemap \| index；可选 `ingest` / `keyframes` / `analyze` / `geo` / `basemap` / `index` |
| `collection` | string |  |  |
| `type` | string |  |  |
| `assetIds` | integer[] |  |  |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `queued` | integer |  |

**示例**

```bash
curl -X POST localhost:8000/api/v1/pipeline/rerun -H 'Content-Type: application/json' -d '{"stage": "analyze", "type": "image"}'
```

## 处理任务

<a id="get-apiv1tasks"></a>
### 处理任务列表

`GET /api/v1/tasks`

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `assetId` | 查询，integer |  |  |
| `kind` | 查询，string |  | ingest 等 |
| `status` | 查询，string |  | pending \| running \| done \| failed |
| `page` | 查询，integer |  | 默认 `1` |
| `size` | 查询，integer |  | 默认 `50` |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `list` | [Task](#task)[] |  |
| `total` | integer |  |

## 检索

<a id="post-apiv1search"></a>
### 混合检索

`POST /api/v1/search`

关键词（BM25）+ 文本向量 + 以文搜图向量 + 时空过滤，RRF 融合后由 Decision 模型判断相关性。查询中的经纬度、半径、经纬度范围、日期时间和地名会自动解析为过滤条件

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `query` | string | 是 | 自然语言检索语句 |
| `collections` | string[] |  | 限定集合 |
| `types` | string[] |  | 限定类型：video \| image \| remote_sensing \| document |
| `assetIds` | integer[] |  | 限定素材（如在某个文档内检索） |
| `topK` | integer |  | 默认 `10`；最大 100 |
| `groupBy` | string |  | asset：每个素材只返回最相关的一条；unit：返回关键帧/文档片段；默认 `asset`；可选 `asset` / `unit` |
| `noRerank` | boolean |  | 跳过 Decision 精排 |
| `filters` | [Filters](#filters) |  | 显式过滤条件，优先于从查询中解析出的条件 |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `query` | string |  |
| `filters` | [Filters](#filters) | 生效的过滤条件（解析结果与显式条件合并） |
| `objects` | [ObjectQuery](#objectquery) | 从查询中识别出的目标条件 |
| `reranked` | boolean | 是否经过 Decision 精排 |
| `results` | [Result](#result)[] | 结果（按 score 降序） |
| `timingsMs` | object | 各阶段耗时（毫秒） |

**示例**

```bash
curl -X POST localhost:8000/api/v1/search -H 'Content-Type: application/json' -d '{
  "query": "以北纬29.7253°、东经115.9748°为中心，半径300米内拍摄的航拍图像", "topK": 10}'
```

## 问答

<a id="post-apiv1qa"></a>
### 智能问答

`POST /api/v1/qa`

RAG：问题中点名的素材 + 混合检索的关键帧和文档片段作为证据；Decision 模型判断问题类型并筛选证据；VLM 根据证据（视觉类问题附关键帧图片）生成带引用的答案

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `question` | string | 是 |  |
| `collections` | string[] |  | 限定检索范围 |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `question` | string |  |
| `answer` | string | 带引用编号的答案 |
| `citations` | integer[] | 答案引用的证据编号 |
| `confidence` | number | 置信度（0~1） |
| `intent` | string | 问题类型：document / visual / location / metadata / cross |
| `mentioned` | string[] | 问题中点名的素材 |
| `evidence` | [Evidence](#evidence)[] | 证据列表 |
| `fallback` | boolean | 视觉模型不可用、返回最相关证据原文时为 true |
| `timingsMs` | object | 各阶段耗时（毫秒） |

**示例**

```bash
curl -X POST localhost:8000/api/v1/qa -H 'Content-Type: application/json' -d '{
  "question": "Palos Verdes 那段飞行的最高高度是多少？是否超过美国 Part 107 的限制？"}'
```

## 评测

<a id="get-apiv1testsuitereports"></a>
### 历史报告

`GET /api/v1/testsuite/reports`

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `list` | [RunInfo](#runinfo)[] |  |

<a id="get-apiv1testsuitereportsid"></a>
### 报告（JSON）

`GET /api/v1/testsuite/reports/{id}`

id 为 latest 时返回最近一次

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，string | 是 |  |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `generatedAt` | string |  |
| `durationSec` | number |  |
| `version` | string |  |
| `models` | object |  |
| `sections` | string[] |  |
| `metrics` | [Metric](#metric)[] |  |
| `formats` | [FormatsSection](#formatssection) |  |
| `keyframe` | [KeyframeSection](#keyframesection) |  |
| `detection` | [DetectionSection](#detectionsection) |  |
| `geo` | [GeoSection](#geosection) |  |
| `retrieval` | [RetrievalSection](#retrievalsection) |  |
| `qa` | [QASection](#qasection) |  |
| `definitions` | object |  |
| `errors` | object |  |

<a id="get-apiv1testsuitereportsidfile"></a>
### 报告文件

`GET /api/v1/testsuite/reports/{id}/{file}`

file 为 html、md 或 json，直接返回文件内容

**参数**

| 名称 | 位置与类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | 路径，string | 是 |  |
| `file` | 路径，string | 是 |  |

<a id="post-apiv1testsuiterun"></a>
### 运行评测

`POST /api/v1/testsuite/run`

在后台按 testdata/benchmark 运行评测（需先登记并处理完评测语料）。同一时间只运行一次，进度见 /testsuite/status

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `sections` | string[] |  | 只运行这些部分，默认全部：formats keyframe detection geo retrieval qa |

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `running` | boolean |  |
| `runId` | string |  |
| `sections` | string[] |  |
| `progress` | string |  |
| `startedAt` | string |  |
| `error` | string |  |
| `last` | [RunInfo](#runinfo) |  |

**示例**

```bash
curl -X POST localhost:8000/api/v1/testsuite/run -H 'Content-Type: application/json' -d '{"sections": ["retrieval", "qa"]}'
```

<a id="get-apiv1testsuitestatus"></a>
### 评测进度

`GET /api/v1/testsuite/status`

当前运行的进度和最近一次报告的指标

**返回**

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `running` | boolean |  |
| `runId` | string |  |
| `sections` | string[] |  |
| `progress` | string |  |
| `startedAt` | string |  |
| `error` | string |  |
| `last` | [RunInfo](#runinfo) |  |

## 数据结构

### AICallSummary

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `endpoint` | string |  |
| `model` | string |  |
| `calls` | integer |  |
| `ok` | integer |  |
| `fallback` | integer |  |
| `error` | integer |  |
| `promptTokens` | integer |  |
| `completionTokens` | integer |  |
| `totalTokens` | integer |  |
| `avgLatencyMs` | number |  |
| `p95LatencyMs` | number |  |

### AICall

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | integer |  |
| `endpoint` | string |  |
| `model` | string |  |
| `purpose` | string |  |
| `status` | string |  |
| `attempts` | integer |  |
| `latencyMs` | integer |  |
| `promptTokens` | integer |  |
| `completionTokens` | integer |  |
| `totalTokens` | integer |  |
| `inputSummary` | string |  |
| `outputSummary` | string |  |
| `error` | string |  |
| `assetId` | integer |  |
| `createdAt` | string |  |

### ModelInfo

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `vlm` | string | 视觉模型 |
| `embedding` | string | 向量模型 |
| `embeddingDim` | integer | 向量维度 |
| `decision` | string | 决策模型 |
| `vision` | string | 图像向量模型 |
| `detector` | string | 目标检测模型（未配置时为空，由 VLM 画框） |

### CountRow

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `key` | string |  |
| `count` | integer |  |

### AssetItem

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | integer |  |
| `uid` | string |  |
| `type` | string |  |
| `format` | string |  |
| `collection` | string |  |
| `filename` | string |  |
| `source` | string |  |
| `sizeBytes` | integer |  |
| `md5` | string |  |
| `status` | string |  |
| `error` | string |  |
| `meta` | object |  |
| `width` | integer |  |
| `height` | integer |  |
| `durationSec` | number |  |
| `fps` | number |  |
| `pageCount` | integer |  |
| `capturedAt` | string |  |
| `lat` | number |  |
| `lon` | number |  |
| `geoSource` | string |  |
| `geoAccuracyM` | number |  |
| `createdAt` | string |  |
| `updatedAt` | string |  |
| `thumbnailUrl` | string | 缩略图地址 |

### RegisterItem

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `path` | string | TESTDATA_DIR 下的相对路径 |
| `collection` | string | 集合名，默认 default |
| `type` | string | image 或 remote_sensing，用于覆盖自动识别 |
| `sidecar` | string | 配套文件路径（TESTDATA_DIR 下） |
| `metadata` | object | 附加元数据，目前支持 time（拍摄时间） |

### RegisterResult

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `path` | string |  |
| `assetId` | integer |  |
| `created` | boolean |  |
| `error` | string |  |

### Task

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | integer |  |
| `assetId` | integer |  |
| `kind` | string |  |
| `status` | string |  |
| `attempts` | integer |  |
| `maxAttempts` | integer |  |
| `progress` | number |  |
| `message` | string |  |
| `error` | string |  |
| `startedAt` | string |  |
| `finishedAt` | string |  |
| `createdAt` | string |  |
| `updatedAt` | string |  |

### AssetCounts

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `keyframes` | integer |  |
| `detections` | integer |  |
| `chunks` | integer |  |

### Keyframe

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | integer |  |
| `assetId` | integer |  |
| `idx` | integer |  |
| `tsSec` | number |  |
| `frameNo` | integer |  |
| `width` | integer |  |
| `height` | integer |  |
| `description` | string |  |
| `descriptionSource` | string |  |
| `tags` | string[] |  |
| `capturedAt` | string |  |
| `lat` | number |  |
| `lon` | number |  |
| `geoSource` | string |  |
| `geoAccuracyM` | number |  |
| `meta` | object |  |
| `detections` | integer |  |
| `imageUrl` | string |  |

### TrackPoint

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `tSec` | number |  |
| `ts` | string |  |
| `lat` | number |  |
| `lon` | number |  |
| `altM` | number |  |

### GeoPoint

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `keyframeId` | integer |  |
| `assetId` | integer |  |
| `assetType` | string |  |
| `collection` | string |  |
| `filename` | string |  |
| `tsSec` | number |  |
| `lat` | number |  |
| `lon` | number |  |
| `geoSource` | string |  |
| `geoAccuracyM` | number |  |
| `imageUrl` | string |  |

### Basemap

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `assetId` | integer |  |
| `filename` | string |  |
| `collection` | string |  |
| `west` | number |  |
| `south` | number |  |
| `east` | number |  |
| `north` | number |  |
| `thumbnailUrl` | string |  |

### Detection

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | integer |  |
| `label` | string |  |
| `labelZh` | string |  |
| `score` | number |  |
| `x` | number |  |
| `y` | number |  |
| `w` | number |  |
| `h` | number |  |
| `source` | string |  |

### Filters

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `lat` | number |  |
| `lon` | number |  |
| `radiusM` | number |  |
| `south` | number |  |
| `north` | number |  |
| `west` | number |  |
| `east` | number |  |
| `from` | string |  |
| `to` | string |  |
| `place` | string |  |
| `source` | object |  |

### ObjectQuery

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `labels` | string[] |  |
| `min` | integer |  |
| `absent` | boolean |  |

### Result

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `kind` | string | keyframe（关键帧）或 chunk（文档片段） |
| `unitId` | integer | 关键帧或文档片段的 id |
| `assetId` | integer |  |
| `assetType` | string | 素材类型 |
| `collection` | string |  |
| `filename` | string |  |
| `path` | string | 登记路径（TESTDATA_DIR 下）或文件名 |
| `score` | number | 排序分（0~1） |
| `relevant` | boolean | 系统是否判为相关 |
| `snippet` | string | 描述或文档片段原文 |
| `tsSec` | number | 视频内的时间（秒） |
| `page` | integer | 文档页码 |
| `lat` | number |  |
| `lon` | number |  |
| `capturedAt` | string |  |
| `imageUrl` | string | 关键帧图片地址 |
| `thumbnailUrl` | string | 素材缩略图地址 |
| `signals` | [Signals](#signals) | 各路召回的名次和得分 |

### Evidence

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `n` | integer | 证据编号，与答案中的 [n] 对应 |
| `kind` | string | asset（素材概况）、keyframe（关键帧）或 chunk（文档片段） |
| `assetId` | integer |  |
| `unitId` | integer |  |
| `filename` | string |  |
| `page` | integer |  |
| `tsSec` | number |  |
| `text` | string | 证据内容（素材概况、关键帧描述或文档原文） |
| `imageUrl` | string |  |
| `relevance` | number | Decision 判断的有用程度 |
| `mentioned` | boolean | 是否为问题中点名的素材 |

### RunInfo

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | string |  |
| `generatedAt` | string |  |
| `durationSec` | number |  |
| `sections` | string[] |  |
| `passed` | integer |  |
| `primary` | integer |  |
| `allPassed` | boolean |  |
| `metrics` | [Metric](#metric)[] |  |

### Metric

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | string |  |
| `name` | string |  |
| `value` | number |  |
| `display` | string |  |
| `target` | string |  |
| `pass` | boolean |  |
| `primary` | boolean |  |
| `note` | string |  |

### FormatsSection

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `rows` | [FormatRow](#formatrow)[] |  |
| `types` | integer |  |
| `formats` | integer |  |
| `failedAssets` | string[] |  |

### KeyframeSection

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `videos` | [KeyframeVideo](#keyframevideo)[] |  |
| `match` | [Match](#match) |  |
| `f1` | number |  |
| `categoryJaccard` | number |  |
| `semanticCosine` | number |  |
| `keyframes` | integer |  |
| `skipped` | string[] |  |

### DetectionSection

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `images` | integer |  |
| `exact` | [Match](#match) |  |
| `grouped` | [Match](#match) |  |
| `rawPrecision` | number |  |
| `precision` | number |  |
| `missRate` | number |  |
| `groupedPrecision` | number |  |
| `groupedMissRate` | number |  |
| `perClass` | [ClassRow](#classrow)[] |  |
| `boxSources` | object |  |
| `skipped` | string[] |  |

### GeoSection

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `visual` | [Stats](#stats) |  |
| `within100` | number |  |
| `within250` | number |  |
| `located` | integer |  |
| `total` | integer |  |
| `items` | [GeoItem](#geoitem)[] |  |
| `dji` | [DJICheck](#djicheck) |  |

### RetrievalSection

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `queries` | [QueryResult](#queryresult)[] |  |
| `byType` | [TypeRow](#typerow)[] |  |
| `precision` | number |  |
| `recall` | number |  |
| `p5` | number |  |
| `p10` | number |  |
| `r5` | number |  |
| `r10` | number |  |
| `setPrecision` | number |  |
| `setRecall` | number |  |
| `latencyMs` | [Stats](#stats) |  |

### QASection

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `items` | [QAItem](#qaitem)[] |  |
| `byCategory` | [CategoryRow](#categoryrow)[] |  |
| `correct` | integer |  |
| `total` | integer |  |
| `accuracy` | number |  |
| `meanGrade` | number |  |
| `latencyMs` | [Stats](#stats) |  |

### Signals

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `bm25Rank` | integer | 关键词（BM25）召回中的名次 |
| `textRank` | integer | 文本向量召回中的名次 |
| `imageRank` | integer | 以文搜图召回中的名次 |
| `objectRank` | integer | 目标计数召回中的名次 |
| `rrf` | number | RRF 融合分 |
| `decision` | number | Decision 模型给出的相关概率 |
| `spatialDepthM` | number | 到空间过滤边界的距离（米），正数在范围内，负数为定位误差内的范围外结果 |

### FormatRow

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `type` | string |  |
| `format` | string |  |
| `ready` | integer |  |
| `failed` | integer |  |

### KeyframeVideo

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | string |  |
| `keyframes` | integer |  |
| `evaluated` | integer |  |
| `f1` | number |  |
| `precision` | number |  |
| `recall` | number |  |
| `categoryJaccard` | number |  |
| `semanticCosine` | number |  |
| `match` | [Match](#match) |  |

### Match

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `TP` | integer |  |
| `FP` | integer |  |
| `FN` | integer |  |
| `Ignored` | integer |  |

### ClassRow

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `label` | string |  |
| `gt` | integer |  |
| `pred` | integer |  |
| `recall` | number |  |
| `precision` | number |  |

### Stats

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `n` | integer |  |
| `mean` | number |  |
| `median` | number |  |
| `p90` | number |  |
| `max` | number |  |

### GeoItem

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `image` | string |  |
| `source` | string |  |
| `errorM` | number |  |

### DJICheck

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `keyframes` | integer |  |
| `fromTelemetry` | integer |  |
| `maxDeviationM` | number |  |
| `alignment` | string |  |

### QueryResult

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | string |  |
| `type` | string |  |
| `query` | string |  |
| `relevant` | integer |  |
| `precision` | number |  |
| `recall` | number |  |
| `p5` | number |  |
| `p10` | number |  |
| `r5` | number |  |
| `r10` | number |  |
| `setPrecision` | number |  |
| `setRecall` | number |  |
| `flagged` | integer |  |
| `latencyMs` | integer |  |
| `top` | string[] |  |
| `error` | string |  |

### TypeRow

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `type` | string |  |
| `n` | integer |  |
| `precision` | number |  |
| `recall` | number |  |
| `setPrecision` | number |  |
| `setRecall` | number |  |

### QAItem

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | string |  |
| `category` | string |  |
| `question` | string |  |
| `reference` | string |  |
| `answer` | string |  |
| `correct` | boolean |  |
| `grade` | number |  |
| `geoErrorM` | number |  |
| `method` | string |  |
| `intent` | string |  |
| `latencyMs` | integer |  |
| `error` | string |  |

### CategoryRow

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `category` | string |  |
| `n` | integer |  |
| `correct` | integer |  |
| `accuracy` | number |  |
