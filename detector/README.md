# Multimo 目标检测服务

基于 Ultralytics YOLO 的小目标检测服务，模型在 VisDrone2019-DET 上训练，类别为 pedestrian、people、bicycle、car、van、truck、tricycle、awning-tricycle、bus、motor。后端在处理无人机照片和视频关键帧时调用它画框，视觉模型（VLM）只负责描述、场景属性和检测类别之外的目标。服务不可用时后端自动改由 VLM 画框。

## 接口

`GET /health` 返回模型名、设备、输入尺寸、默认阈值和类别。

`POST /v1/detect`：

```json
{"image": "<base64 或 data URL>", "conf": 0.57, "imgsz": 1024}
```

返回目标框（所发图片的像素坐标）：

```json
{"model": "visdrone-yolov9e", "width": 1920, "height": 1080, "latency_ms": 1750.2,
 "objects": [{"label": "car", "score": 0.92, "bbox": [196.5, 915.7, 360.5, 1061.0]}]}
```

设置 `DETECTOR_API_KEY` 后需带 `Authorization: Bearer <key>`。

## 配置

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DETECTOR_WEIGHTS` | `/models/visdrone.pt` | 权重文件路径 |
| `DETECTOR_WEIGHTS_URL` | 无 | 权重不存在时从这里下载 |
| `DETECTOR_MODEL_NAME` | 权重文件名 | 日志和报告中显示的模型名 |
| `DETECTOR_IMGSZ` | `1280` | 推理输入尺寸 |
| `DETECTOR_CONF` | `0.3` | 默认置信度阈值（请求里的 `conf` 优先） |
| `DETECTOR_DEVICE` | 有 GPU 时 `cuda:0`，否则 `cpu` | 推理设备 |
| `DETECTOR_API_KEY` | 无 | 访问密钥 |

## 当前模型和阈值

- 权重：[dronefreak/visdrone-yolov9e](https://huggingface.co/dronefreak/visdrone-yolov9e)（YOLOv9e，在 VisDrone2019-DET train 上训练，AGPL-3.0）。
- 在评测语料的 100 张 VisDrone-DET val 图上比较了 YOLO11x、YOLO26x、YOLOv9e 和 YOLOv8x，输入 640/1024/1280/1536：四个模型在相同准确率下漏检率相差 2~3 个百分点。这些模型是按 640 训练的，1024 输入时漏检最少，更大的输入反而变差，因为目标尺度偏离了训练分布。
- 阈值在 VisDrone val 中**不属于评测集**的 448 张图上标定：取类别一致准确率达到 85% 的最低阈值。YOLOv9e 在 conf 0.57 时准确率 85.4%、漏检率 23.0%；YOLO26x 要到 conf 0.62 才达到 85%，漏检率 23.6%。所以选 YOLOv9e、1024 输入、conf 0.57。
- CPU（4 核）上每张约 1.7 秒，GPU 上约 50 毫秒。

更高分辨率训练（1280 输入）的模型可以直接替换权重：设置 `DETECTOR_WEIGHTS_URL` 或把权重放进 `detector-models` 数据卷，再按同样方法重新标定阈值。

## 运行

随 `docker compose up` 一起启动（CPU）。有 NVIDIA GPU 时：

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

不用 Docker：

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements.txt
```

```bash
DETECTOR_WEIGHTS=./visdrone-yolov9e.pt DETECTOR_IMGSZ=1024 DETECTOR_CONF=0.57 uvicorn app:app --port 8010
```
