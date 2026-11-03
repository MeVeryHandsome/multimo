# Multimo 架构说明

> 本文是 P0 阶段的设计，后续实现有调整时同步更新。

## 组件

```
┌──────────────┐   /api    ┌─────────────────────────────────────────────┐
│ multimo-     │ ───────▶  │ multimo-backend (GoFrame)                   │
│ frontend     │  nginx    │                                             │
│ React + Vite │           │  ingest → pipeline(goroutine 池) ──┐         │
└──────────────┘           │     keyframe  detect  geo          │         │
                           │  search (BM25 + 向量 + 时空) ◀──────┘         │
                           │  qa (RAG)        testsuite                  │
                           │        │                                    │
                           │   internal/pkg/llm 网关（重试 / 降级 / 日志） │
                           └────┬──────────────┬──────────────┬──────────┘
                                │              │              │
                     PostgreSQL 16      阿里云 MaaS        StartLux Decision
                     + pgvector         qwen3.8-max        /v1/decisions
                                        text-embedding-v4
```

## 处理流程

任务链：`ingest → keyframes → analyze → geo`；卫星底图另加 `basemap`。每一步是 `tasks` 表里的一行，失败按退避重试，依赖未就绪时延后（不消耗重试次数）。

1. **接入（ingest）**：按文件头识别类型（视频 MP4/WMV/FLV/MOV/MKV/AVI，图像 PNG/JPEG/TIF，文档 PDF/DOC/DOCX/TXT/CSV/GeoJSON），读元数据（ffprobe、EXIF、GeoTIFF 地理参考），生成缩略图；文档抽文本、按页切块。
2. **关键帧（keyframes）**：
   - 视频按内容变化选帧：每秒 2 帧的 64×36 灰度签名，与上一关键帧差异超过 `KEYFRAME_CHANGE_THRESHOLD`（0.08）即取新帧；最小间隔 2 秒、最大间隔 15 秒、最多 60 帧。连续航拍没有镜头切换，ffmpeg 场景分数最高只有 0.012，所以不用场景检测。长视频只解码关键帧（466 秒 1080p 从 45 秒降到 13 秒）。
   - 图片和遥感影像各一帧；大幅 GeoTIFF 用自带读取器生成概览图。
   - 每帧计算多模态向量（`tongyi-embedding-vision-plus`，1152 维）；相邻视频关键帧相似度 ≥ 0.97 视为重复画面并丢弃。
3. **分析（analyze）**：每帧一次 VLM 调用，返回 JSON：中文描述、场景标签、昼夜/天气/视角/高度、目标框（VisDrone + DOTA 共 28 类，`bbox_2d` 为 0~1000 相对坐标，已实测与像素标注吻合到 1~3 px）、各类计数、地标、画面文字、地点猜测。框换算成关键帧像素坐标存入 `detections`。描述、标签、计数、地标、文字拼成检索文本并计算文本向量。VLM 不可用时用本地模板描述（尺寸、亮度、时间、位置），并标记待重跑。
4. **空间对齐（geo）**，按可靠性依次尝试：
   - 配套飞行记录（如 DJI CSV）：按整点时差把文件名上的本地时间对齐到日志的 UTC 时间（自动找到 UTC-8），逐关键帧插值出经纬度和高度。
   - EXIF / 视频容器里的 GPS。
   - VLM 读到的地名（置信度 ≥ 0.5）在 GeoJSON 地名库（Natural Earth，含中文名）里查城市级粗位置。
   - 视觉定位：素材登记时指定了参考底图（`geo_reference`），或粗位置落在某张底图范围内时，把关键帧做 12 个角度的旋转查询，与底图切块（250/300/400 m 三种尺度）的多模态向量比对，取前 30 个候选；同一集合的照片全部完成后，按拍摄时间用 Viterbi 做航迹平滑（相邻照片位移不超过 `GEO_MAX_SPEED_MPS × Δt`）。
5. **底图索引（basemap）**：WGS84 地理参考、两个方向跨度都超过 1 km 的栅格，按三种尺度、半步长切块并计算多模态向量（UAV-VisLoc 01 区约 2000 块）。

### 视觉定位的实测依据（UAV-VisLoc 01 区，82 张无 GPS 照片）

| 方案 | 中位误差 | <100 m | <250 m |
| :--- | ---: | ---: | ---: |
| 单张检索，4 个旋转，300 m 切块 | 472 m | 28% | 40% |
| + 航迹平滑 | 232 m | 32% | 51% |
| 12 个旋转 + 三种尺度 + 航迹平滑 | 128 m | 48% | 62% |

- 切块尺度用 5 张不在评测集里的照片标定。最大速度 25 m/s 是事先定的默认值，15 和 40 也试过，25 最好，所以这一项在评测集上有轻微的调参成分。
- 试过 SIFT/ORB + RANSAC 做几何校验：即使在真实位置，内点也只有 5~9 个（照片和卫星图拍摄年份、季节不同），无法使用，已放弃。
- 失败的主要是纯江面照片，单张无法定位。

### P3 在评测语料上的实测（2026-11-03，正式评测在 P5）

全部 223 个素材跑完 `ingest → keyframes → analyze → geo`，869 个任务全部完成，生成 291 个关键帧。

| 项目 | 结果 |
| :--- | :--- |
| DJI 视频定位（元数据） | 51 个关键帧全部由飞行记录定位；文件名时间按 UTC-8 对齐，与日志重合 |
| VisLoc 视觉定位（82 张，无元数据） | 中位误差 129 m，<100 m 40%，<250 m 57%（Python 原型同口径 48% / 62%，差距来自候选质量的细微差别，见下） |
| 目标检测（VisDrone-DET 100 张，可见目标，IoU≥0.5） | 类别一致：精确率 0.766、召回率 0.661；按大类：精确率 0.836、召回率 0.724 |
| 关键帧描述 | 287 个来自 VLM，4 个是本地兜底（模型输出过长或格式异常，已标记待重跑） |

- 检测精确率采用 VisDrone/COCO 的惯例：与"不可见"标注（面积 < 600 px 或重度遮挡）或忽略区域匹配的预测不计入正确也不计入误报。不做这一步时精确率为 0.503，因为大量正确框出的小目标会被当成误报。
- 漏检率 34%（按大类 28%），离"漏检 ≤5%"差距很大，主要漏在小目标和三轮车、自行车等少见类别。
- 视觉定位候选：Go 实现 top-1 <100 m 34%、前 30 召回 79%，Python 原型 37%、82%。原型送入向量模型的图片是 448 px，Go 是 512 px，这一点尚未对齐，留作 P5 调优项。

## 模型分工

| 模型 | 接口 | 用途 |
| :--- | :--- | :--- |
| `qwen3.8-max` | OpenAI 兼容 `chat/completions`，支持图像输入 | 关键帧描述、目标检测、地标识别、答案生成 |
| `text-embedding-v4` | OpenAI 兼容 `embeddings`，1024 维 | 关键帧描述与文档片段的向量 |
| `tongyi-embedding-vision-plus-2026-03-06` | DashScope 原生 `multimodal-embedding`，1152 维，每批 4 张 | 关键帧图像向量、底图切块、视觉定位、以文搜图 |
| `startlux-decision-27b-autothink` | 专有 `/v1/decisions` | 意图分类、证据相关性打分、问答裁判 |

Decision 模型只做判断和打分，不生成文本；该模型的 `chat/completions` 通道在网关上不可用（返回 `model_not_found`）。

### Decision 接口约定（2026-11-02 实测）

```http
POST {DECISION_BASE_URL}/decisions
Authorization: Bearer <DECISION_API_KEY>

{
  "model": "startlux-decision-27b-autothink",
  "state": "背景信息，纯文本",
  "questions": {
    "<id>": { "type": "choice", "instructions": "问题", "criteria": {"<选项id>": "选项描述", ...} },
    "<id>": { "type": "score",  "instructions": "评分要求", "criteria": ["等级0描述", "等级1描述", ...] },
    "<id>": { "type": "noul",   "instructions": "..." }
  }
}
```

- 一次请求可以带多个问题。
- `choice`：criteria 可以是对象或数组。返回 `choice`、`confidence`、`probabilities`、`none_probability`。
- `score`：criteria 需要 2 到 10 个等级。返回 `score`（按概率加权的期望分）、`probabilities`、`legend`。
- `noul`：返回一个 0 到 1 的数，含义未确认，系统中不使用。
- 返回的 `usage` 里有 `input_tokens` / `output_tokens`。

## AI 网关（`internal/pkg/llm`）

- 统一封装三类调用：chat（含图像，支持 JSON 模式）、embedding、decision。
- 每个端点有独立的并发上限（`*_MAX_CONCURRENCY`）。
- 每次调用最多尝试 `1 + AI_MAX_RETRIES` 次：网络错误、超时、429、5xx 按指数退避加抖动重试，429 优先遵循 `Retry-After`；其他 4xx 不重试；JSON 模式下回复里没有 JSON 对象也会重试。
- 全部失败后按 `AI_FALLBACK_ENABLED` 走本地兜底，调用日志里状态记为 `fallback`：
  - embedding：本地特征哈希向量，模型名 `local-hash-v1`。一批向量不会混用两种来源，入库时记录模型名，只和同一模型的向量比较。
  - decision：按关键词重合度判断。
  - chat：由调用方提供兜底函数。P3 计划给关键帧描述配"元数据 + 颜色亮度统计"的模板描述；目标检测兜底返回空结果，并把该帧标记为待重跑。
- 每次调用写一条 `ai_call_log`：端点、模型、用途、token 用量、延迟、尝试次数、状态、输入输出摘要（截断到 `AI_LOG_PAYLOAD_CHARS`）。图片只记尺寸和大小，API key 不进日志。`GET /api/v1/system/ai-calls` 汇总查看。
- 实测（2026-11-03）：`qwen3.8-max` 支持图像输入和 JSON 模式，关闭思考（`enable_thinking=false`）后单张图描述约 5～8 秒；`text-embedding-v4` 约 0.6 秒；Decision 约 0.5 秒。

## 数据模型要点

- 图片和遥感影像在 `keyframes` 表里各占一行（`idx = 0`），和视频关键帧放在一起，检索、检测、问答只面对一种画面单元。
- 大幅 GeoTIFF（如 UAV-VisLoc 卫星图，9774×26762，805 MB）用自带的 `internal/pkg/geotiff` 读取：只解析文件头即可得到经纬度范围，缩略图只读采样到的像素行（约 0.02 秒）。ffmpeg 会把这种分块 TIFF 渲染成全黑，而且要读完整个文件。
- 任务队列存放在 `tasks` 表，用 `FOR UPDATE SKIP LOCKED` 领取，重启后自动恢复；临时错误按退避重试，永久错误直接标记失败。

## 中文关键词检索

PostgreSQL 内置的全文检索不支持中文分词，pgvector 镜像里也没有 zhparser。做法：在 Go 里分词，把结果按 `simple` 配置存成 `tsvector`，BM25 分数由 SQL 根据词项文档频率计算。具体实现在 P4 确定。

## 评测（`internal/service/testsuite`）

标准答案全部来自数据集的人工标注，不用本系统的模型输出生成，避免自己评自己。

| 指标 | 数据 | 计算方式 |
| :--- | :--- | :--- |
| 数据类型 ≥3 | testdata 各格式样本 | 解析成功的格式数 |
| 关键帧自动标注一致性 ≥40% | VisDrone2019-VID val 4 段 + UAVDT 2 段 | VLM 框与标注按 IoU≥0.5 匹配，再比类别语义相似度 |
| 目标识别准确率 ≥85% / 漏检 ≤5% | VisDrone2019-DET val 100 张 | TP/(TP+FP)，FN/(TP+FN) |
| 定位精度 <100m | UAV-VisLoc 82 张（无元数据，靠卫星 GeoTIFF 匹配）+ DJI 飞行记录（元数据路径） | haversine 误差的中位数、P90、达标比例 |
| 检索查全率 ≥80% / 准确率 ≥85% | 50 条标准 query | Precision@K、Recall@K |
| 问答准确率 ≥80% | 50 个问答对 | Decision `score` 裁判；坐标类按距离程序判定 |

输出 `test_report.json` 和 HTML 报告，每项标注是否达标。
