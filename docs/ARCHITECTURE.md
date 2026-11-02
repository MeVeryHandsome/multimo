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

- 统一封装三类调用：chat（含图像）、embedding、decision。
- 每个端点有独立的并发上限（`*_MAX_CONCURRENCY`）。
- 失败重试 `AI_MAX_RETRIES` 次，指数退避加抖动；4xx（除 429）不重试。
- 全部失败后按 `AI_FALLBACK_ENABLED` 走本地兜底：
  - 关键帧描述：ffprobe 元数据 + 颜色和亮度统计生成的模板描述
  - 目标检测：返回空结果并把这一帧标记为待重跑
  - embedding：本地特征哈希向量（检索质量会明显下降）
  - decision：规则和关键词匹配
- 每次调用写一条 `ai_call_log`：端点、模型、token 用量、延迟、重试次数、是否兜底、输入输出摘要（截断到 `AI_LOG_PAYLOAD_CHARS`）。

## 中文关键词检索

PostgreSQL 内置的全文检索不支持中文分词，pgvector 镜像里也没有 zhparser。做法：在 Go 里分词，把结果按 `simple` 配置存成 `tsvector`，BM25 分数由 SQL 根据词项文档频率计算。具体实现在 P4 确定。

## 评测（`internal/service/testsuite`）

标准答案全部来自数据集的人工标注，不用本系统的模型输出生成，避免自己评自己。

| 指标 | 数据 | 计算方式 |
| :--- | :--- | :--- |
| 数据类型 ≥3 | testdata 各格式样本 | 解析成功的格式数 |
| 关键帧自动标注一致性 ≥40% | VisDrone2019-VID val | VLM 框与标注按 IoU≥0.5 匹配，再比类别语义相似度 |
| 目标识别准确率 ≥85% / 漏检 ≤5% | VisDrone2019-DET val 抽样 | TP/(TP+FP)，FN/(TP+FN) |
| 定位精度 <100m | 带 GPS 的无人机-卫星配对数据集 | haversine 误差的中位数、P90、达标比例 |
| 检索查全率 ≥80% / 准确率 ≥85% | 50 条标准 query | Precision@K、Recall@K |
| 问答准确率 ≥80% | 50 个问答对 | Decision `score` 裁判 |

输出 `test_report.json` 和 HTML 报告，每项标注是否达标。
