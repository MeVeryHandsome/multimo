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
| `DETECTOR_SCALES` | 无 | 多尺度推理，如 `1280,1536`：每个尺寸各推理一次，再用加权框融合合并（同类、IoU ≥ 0.55 的框合为一个，位置按分数加权平均，分数取各尺度平均，没检出的尺度记 0）。设置后忽略 `DETECTOR_IMGSZ`；请求里指定 `imgsz` 时只跑该尺寸 |
| `DETECTOR_CONF` | `0.3` | 默认置信度阈值（请求里的 `conf` 优先） |
| `DETECTOR_DEVICE` | 有 GPU 时 `cuda:0`，否则 `cpu` | 推理设备 |
| `DETECTOR_API_KEY` | 无 | 访问密钥 |

## 模型

有两套配置：

| | 默认（随 compose 自动下载） | 推荐（GPU） |
| :--- | :--- | :--- |
| 权重 | [dronefreak/visdrone-yolov9e](https://huggingface.co/dronefreak/visdrone-yolov9e)（YOLOv9e，640 输入训练，AGPL-3.0） | `multimo-visdrone-yolo11x-1280`：在 YOLO11x 上按 1280 输入微调，见下文“训练” |
| 推理 | 1024 单尺度，conf 0.57 | 1024 + 1280 + 1536 三尺度融合，conf 0.46 |
| 评测集准确率 / 漏检率 | 86.4% / 25.7%（系统完整评测） | 85.6% / 22.2%（经检测服务的 HTTP 接口测得） |
| 速度 | CPU 4 核每张约 1.7 秒 | RTX 4090 每张约 54 毫秒（三个尺度合计） |

评测集是评测语料中的 100 张 VisDrone-DET val 图，口径与后端评测相同：只算可见目标（面积 ≥ 600 像素且非重度遮挡），IoU ≥ 0.5 且类别一致才算命中，落在忽略区域或不可见目标上的预测不计。

**阈值怎么定**：都按 VisDrone val 中**不属于评测集**的 448 张图（标定集）上的结果定。默认配置取标定集准确率达到 85% 的最低阈值（YOLOv9e：conf 0.57，标定集 85.4% / 23.0%）。微调模型的各个轮次和组合按 85% 选阈值时，评测集准确率在 84.6%~85.7% 之间，有的低于要求（这是唯一参考了评测集的一步），所以改为在标定集上留 1 个百分点余量、取准确率达到 86% 的最低阈值（conf 0.46，标定集 86.1% / 20.3%）。同样按 86% 的规则，YOLOv9e 要用 conf 0.59，评测集 87.2% / 26.7%；微调模型的漏检率比它低 4.5 个百分点。

**比较过的方案**（除注明外，阈值都取标定集准确率达到 86% 的最低值）：

| 方案 | 推理尺寸 | conf | 标定集漏检率 | 评测集 准确率 / 漏检率 |
| :--- | :--- | ---: | ---: | ---: |
| 公开 YOLOv9e | 1024 | 0.59 | 23.9% | 87.2% / 26.7% |
| 第一轮微调（只用 train，40 轮；按 85% 选阈值） | 1280 | 0.50 | 23.4% | 85.9% / 25.3% |
| 第二轮微调，第 12 轮 | 1280 | 0.52 | 21.8% | 85.8% / 24.2% |
| 第二轮微调，第 12 轮 | 1280 + 1536 | 0.49 | 21.1% | 86.1% / 23.1% |
| **第二轮微调，第 12 轮（采用）** | **1024 + 1280 + 1536** | **0.46** | **20.3%** | **85.7% / 22.2%** |
| 第 8~15 轮权重平均 | 1024 + 1280 + 1536 | 0.46 | 20.6% | 86.0% / 22.5% |

其他试过但没有用的：单独用 1536 输入（漏检更多）；2×2 切片加整图（切片放大后误检增多，同准确率下漏检率反而高 2 个百分点）。第二轮第 8~15 轮之间差别在 0.6 个百分点以内，按标定集选了第 12 轮。

公开模型在评测集上 1024 输入最好、更大输入反而变差，因为它们按 640 训练，大输入让目标尺度偏离训练分布；按 1280 微调之后，多尺度融合才有收益。

## 在 GPU 服务器上部署

推荐把检测服务单独部署在有 GPU 的服务器上，后端通过 HTTP 调用。微调权重 `multimo-visdrone-yolo11x-1280.pt`（114.5 MB，sha256 `b88b4f9cb9d64c39e8f2b412d0d884742395701aa170f6fb702447fc8c863daa`）没有随仓库发布，保存在训练用的 GPU 服务器上，可从那里拷贝，或按下文“训练”重新训练。

1. 服务器上准备 Python 3.10+、CUDA 版 PyTorch 和 `requirements.txt` 里的包，把 `app.py` 和权重放到同一台机器上。
2. 启动（示例用 7 号卡、18010 端口）：

   ```bash
   CUDA_VISIBLE_DEVICES=7 DETECTOR_WEIGHTS=./multimo-visdrone-yolo11x-1280.pt DETECTOR_MODEL_NAME=multimo-visdrone-yolo11x-1280 DETECTOR_SCALES=1024,1280,1536 DETECTOR_CONF=0.46 uvicorn app:app --host 0.0.0.0 --port 18010
   ```

3. 在 Multimo 的 `.env` 中指向它（后端每次请求都会带上自己的 `DETECTOR_CONF`，两边要一致）：

   ```bash
   DETECTOR_BASE_URL_DOCKER=http://<GPU 服务器>:18010
   DETECTOR_MODEL=multimo-visdrone-yolo11x-1280
   DETECTOR_CONF=0.46
   ```

   这时 compose 里的 detector 容器不再被使用，可以 `docker compose up -d --scale detector=0`。

也可以把权重放进 compose 的 `detector-models` 数据卷（文件名 `visdrone.pt`），在 `.env` 中设 `DETECTOR_MODEL=multimo-visdrone-yolo11x-1280`、`DETECTOR_SCALES=1024,1280,1536`、`DETECTOR_CONF=0.46`，再用 `docker-compose.gpu.yml` 启动。只有 CPU 时建议单尺度 `DETECTOR_IMGSZ=1280`、`DETECTOR_CONF=0.52`（评测集 85.8% / 24.2%），三尺度在 CPU 上要慢好几倍。

`tools/check_service.py` 把评测集（或 `SPLIT=calib` 时的标定集）图片逐张发给正在运行的服务，按同一口径打分，用来确认部署结果：

```bash
TESTDATA_DIR=../testdata python tools/check_service.py http://<GPU 服务器>:18010 0.46
```

## 训练

`train/` 下是第二轮微调用的脚本：

```bash
python train/prepare.py /data/VisDrone ../testdata ./visdrone-yolo        # 转成 YOLO 格式；评测集的 100 张图不进训练和验证
```

```bash
python train/train.py ./visdrone-yolo/visdrone.yaml visdrone-yolo11x-640.pt ./runs 1280 16 28 0.002 0,1,2,3,4,5,6
```

- 训练集：VisDrone2019-DET train（6471 张）+ test-dev（1610 张）；验证集：标定集（448 张）。
- 起点：[dronefreak/visdrone-yolo11x](https://huggingface.co/dronefreak/visdrone-yolo11x)（640 输入训练）；1280 输入、16 轮、SGD lr0 0.002 余弦衰减、最后 3 轮关闭 mosaic，7 张 RTX 4090 每轮约 65 秒。
- 标定集 mAP50：第一轮微调（只用 train、以 test-dev 验证、40 轮）0.561，第二轮最高 0.591。第一轮在 test-dev 上第 5 轮后就开始过拟合（mAP50 0.458 → 0.440），所以第二轮把 test-dev 并入训练、缩短轮数，并每轮保存权重，用 `tools/eval_visdrone.py` 和 `tools/select_op.py` 在标定集上按评测口径选轮次和阈值。
- `train/soup.py` 是权重平均，结果见上表，没有采用。

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

`tools/eval_visdrone.py` 用与后端评测相同的口径评测一个权重（见文件开头的说明）。
