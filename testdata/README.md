# testdata

测试数据目录。数据本体不提交到 git，由 `scripts/prepare_testdata.sh` 下载并按 `manifest.tsv` 校验 MD5（P1 实现）。

计划的子目录：

| 目录 | 内容 |
| :--- | :--- |
| `video/` | 无人机视频（VisDrone2019-VID、UAVDT） |
| `image/` | 无人机图像（VisDrone2019-DET） |
| `remote_sensing/` | 遥感影像（DOTA-v2.0） |
| `geo/` | 带 GPS 真值的无人机-卫星配对数据，用于测定位精度 |
| `docs/` | 文档样本（FAA 无人机法规 PDF、地理标注文本） |
| `benchmark/` | 评测集：关键帧标注真值、50 条检索 query、50 个问答对（提交到 git） |
