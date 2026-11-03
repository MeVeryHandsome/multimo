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

1. **接入**：上传或登记文件，按扩展名和文件头识别类型（视频 MP4/WMV/FLV，图像 PNG/JPEG/TIF，文档 DOC/DOCX/PDF），落盘到 `DATA_DIR`，提取元数据。
2. **视频流水线**（goroutine 池，`WORKER_POOL_SIZE` 限制并发）：
   - ffprobe 读时长、分辨率和元数据，同时提取 GPS（MP4 的位置信息、DJI SRT 字幕轨）
   - ffmpeg 按场景变化抽关键帧（`KEYFRAME_SCENE_THRESHOLD`），保证最小间隔、限制最大帧数
   - 每个关键帧：VLM 生成描述并做结构化目标检测（JSON），描述文本经 embedding 存入 pgvector
3. **空间对齐**：优先用元数据里的 GPS / EXIF；没有就让 VLM 识别地标和场景特征，再到已接入的遥感影像里匹配，估算坐标。
4. **检索**：关键词（BM25）、向量（pgvector HNSW）、时空过滤三路召回，用 RRF 融合排序。
5. **问答**：检索关键帧和文档片段 → Decision 判断问题意图、给每条证据打相关性分 → 取高分证据交给 VLM 生成答案，附引用。

## 模型分工

| 模型 | 接口 | 用途 |
| :--- | :--- | :--- |
| `qwen3.8-max` | OpenAI 兼容 `chat/completions`，支持图像输入 | 关键帧描述、目标检测、地标识别、答案生成 |
| `text-embedding-v4` | OpenAI 兼容 `embeddings`，1024 维 | 关键帧描述与文档片段的向量 |
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
