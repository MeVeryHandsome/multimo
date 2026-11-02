# 评测集

由 `scripts/lib/build_benchmark.py` 根据 mini 档数据生成，重新生成：

```bash
python3 -I scripts/lib/build_benchmark.py
```

生成需要 pdftotext（poppler-utils）和 catdoc。

**所有参考答案和相关性判定都来自数据集自带的人工标注、GPS 真值或文档原文，不使用本系统任何模型的输出。**

## 文件

| 文件 | 条数 | 用途 |
| :--- | ---: | :--- |
| `corpus.json` | 223 | 评测时要接入的素材，带 `collection` 标签 |
| `keyframe_gt.jsonl` | 6 | 带逐帧标注的视频（VisDrone-VID 4 个，UAVDT 2 个），用于关键帧标注一致性 |
| `detection_gt.jsonl` | 100 | VisDrone-DET 验证集（每 5 张取 1 张），用于目标识别准确率和漏检率 |
| `geo_gt.jsonl` | 83 | VisLoc 82 张照片的经纬度，加上 DJI 视频的 GPS 轨迹摘要，用于定位精度 |
| `queries.jsonl` | 50 | 检索 query，带相关素材集合 |
| `qa_pairs.jsonl` | 50 | 问答对，带参考答案和证据 |

## 判定规则

**可见目标。** 框面积 ≥ 600 像素，且不是重度遮挡（VisDrone occlusion < 2，UAVDT occlusion < 3）。检索相关性和图像问答都只算可见目标，免得极小或被遮住的目标影响判断。

**视频包含某类目标。** 该类目标在 ≥ 20% 的帧中可见。

**检索 query（50 条）。**

| 类型 | 条数 | 范围 | 相关性来源 |
| :--- | ---: | :--- | :--- |
| object | 12 | `visdrone_det` 100 张图 | 可见目标计数 |
| video | 6 | `visdrone_vid` + `uavdt` 6 个视频 | 目标可见帧占比；UAVDT 序列属性（夜间、雾天） |
| remote_sensing | 10 | `dota` 20 张图 | DOTA 非困难目标计数 |
| spatial | 8 | `visloc` 82 张图；视频 | 与 GPS 真值的距离或经纬度范围；DJI 飞行轨迹 |
| temporal | 2 | `visloc`；视频 | VisLoc CSV 拍摄时间；DJI 飞行记录时间 |
| document | 12 | 8 篇文档 | 文档全文是否包含指定原文短语 |

每条 query 带 `collections`，检索时限定在这些集合内。原因是不同数据集的类别体系不同（VisDrone 的 car 和 DOTA 的 small-vehicle），跨集合检索会出现标注里没有的"相关"结果。

**问答对（50 个）。**

| 类别 | 条数 | 参考答案来源 |
| :--- | ---: | :--- |
| document | 22 | 中国条例 10、Part 107 法规 10、英国 PDRA01 2；每条附原文引文，生成时会校验引文确实出现在文档中 |
| image | 6 | VisDrone-DET 标注计数 |
| video | 6 | VisDrone-VID 标注、UAVDT 序列属性 |
| remote_sensing | 6 | DOTA 标注计数 |
| geo | 6 | VisLoc GPS 真值（带 100 m 容差的程序化校验）、DJI 飞行记录 |
| cross | 4 | 跨来源：飞行记录 + 法规、视频属性 + 法规、两国法规对比 |

带 `check` 字段的问答（坐标类）由程序计算距离判定，其余由 Decision 模型对照参考答案打分。

## 定位评测的注意事项

`corpus.json` 中 VisLoc 照片只附带拍摄时间，不附带坐标。坐标是定位评测的真值，系统必须通过影像匹配自己推算。DJI 视频附带飞行记录，测的是元数据定位路径。
