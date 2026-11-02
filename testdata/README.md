# testdata

测试数据目录。数据本体不提交到 git，由脚本下载并校验 MD5：

```bash
scripts/prepare_testdata.sh                 # mini：评测用子集，约 1.6 GB
```

```bash
scripts/prepare_testdata.sh --profile full  # 完整数据集，下载约 15 GB
```

```bash
scripts/prepare_testdata.sh --verify        # 只校验已下载的文件
```

依赖：bash、curl、python3（3.8+，只用标准库）、ffmpeg（生成 MP4/WMV/FLV）。中断后重跑同一条命令即可续传。

## 下载方式

`manifest.tsv` 每一行是一项数据，列出一个或多个镜像，按顺序尝试，下载后核对 MD5：

- 每个 HuggingFace 地址都配了 `hf-mirror.com` 镜像，国内网络也能下载。
- DOTA 从 ModelScope 下载。
- `mini` 档不下载大压缩包，用 HTTP Range 请求只读取需要的成员文件。例如 DOTA 只取 20 张图，传输约 36 MB，不用下载 1.26 GB 的整包。
- 选择性解压的数据记录的是 "tree MD5"：对所选文件按路径排序，逐行写成 `<md5>  <相对路径>`，再对整段文本取 MD5。

## 目录与来源

| 目录 | 内容 | 来源 | 许可 |
| :--- | :--- | :--- | :--- |
| `image/visdrone_det_val/` | VisDrone2019-DET 验证集，548 张图 + 标注 | Ultralytics GitHub Release，备用 HF | VisDrone，仅限学术研究 |
| `video/visdrone_vid_val/` | VisDrone2019-VID 验证集。mini 取 4 个序列（1286 帧），full 取全部 7 个；`mp4/` 为本地编码 | HuggingFace（两个镜像） | VisDrone，仅限学术研究 |
| `video/uavdt/` | UAVDT-M。mini 取 M0202（夜间）和 M1004（雾天），附标注和序列属性；`mp4/` 为本地编码 | HuggingFace | UAVDT，仅限学术研究 |
| `video/dji/palos_verdes/` | DJI Mini 4 Pro 实拍视频 + 飞行记录（逐帧 GPS、高度） | HuggingFace `nominal-io/dji-drone-telemetry` | MIT |
| `video/formats/` | 由 MP4 转码的 WMV、FLV | 本地生成 | 同源数据 |
| `remote_sensing/dota_v2_val/` | DOTA-v2.0 验证集。mini 取 20 张，按目标类别覆盖度挑选（12 类） | ModelScope `houhuawei/DOTA-v2.0` | DOTA，仅限学术研究 |
| `remote_sensing/geotiff/` | GeoTIFF 样例 `cea.tif` | download.osgeo.org | OSGeo 公开样例 |
| `geo/uav_visloc/01/` | UAV-VisLoc 第 01 区。无人机照片（mini 每 10 张取 1 张，共 82 张）+ GPS 真值 CSV + 0.3 m 分辨率 WGS84 卫星 GeoTIFF | HuggingFace `mvidem/UAV-VisLoc` | MIT |
| `docs/faa/` | 14 CFR Part 107 法规原文、AC 107-2A、Part 107 摘要、远程驾驶员学习指南 | faa.gov、govinfo.gov | 美国政府公有领域 |
| `docs/cn/` | 《无人驾驶航空器飞行管理暂行条例》（`.doc`） | pds.gov.cn | 法规文本 |
| `docs/uk/` | 英国 CAA PDRA01 运行手册模板（`.docx`） | caa.co.uk | 英国 CAA 公开模板 |
| `docs/geo_text/` | Natural Earth 人口聚居点、机场（GeoJSON，固定 v5.1.2） | GitHub、jsDelivr | 公有领域 |
| `benchmark/` | 评测集，见 [benchmark/README.md](benchmark/README.md) | 由标注生成 | — |

mini 档覆盖的文件格式：MP4、WMV、FLV、PNG、JPEG、TIF、DOC、DOCX、PDF。

几点说明：

- VisLoc 照片里没有 EXIF GPS，卫星图是带地理参考的 GeoTIFF，适合测"没有元数据时靠影像匹配定位"。
- DJI 视频的 GPS 来自飞行记录，用来测"有元数据时的定位"。
- 生成的 MP4/WMV/FLV 依赖本机 ffmpeg 版本，不做 MD5 校验；它们的源帧会校验。序列统一按 25 fps 编码，`benchmark/keyframe_gt.jsonl` 也按这个帧率换算帧号。
