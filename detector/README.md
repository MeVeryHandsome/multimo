# Multimo 目标检测服务

基于 Ultralytics YOLO 的小目标检测服务，模型在 VisDrone2019-DET 上训练，类别为 pedestrian、people、bicycle、car、van、truck、tricycle、awning-tricycle、bus、motor。后端在处理无人机照片和视频关键帧时调用它画框，视觉模型（VLM）只负责描述、场景属性和检测类别之外的目标。服务不可用时后端自动改由 VLM 画框。

默认配置是仓库里的微调模型（`weights/`，Git LFS）：YOLO11x 和 YOLOv9e 各按 1024、1280、1536 三个尺寸推理，6 次结果做加权框融合，再由一个小的重打分模型给出每个框的分数。在评测集上准确率 86.1%、漏检率 16.6%（公开模型 86.4% / 25.7%）。

## 接口

`GET /health` 返回模型名、权重、设备、尺寸、是否镜像、重打分模型、默认阈值和类别。

`POST /v1/detect`：

```json
{"image": "<base64 或 data URL>", "conf": 0.36}
```

返回目标框（所发图片的像素坐标）：

```json
{"model": "multimo-visdrone-ensemble", "width": 1920, "height": 1080, "scales": [1024, 1280, 1536], "latency_ms": 133.0,
 "objects": [{"label": "car", "score": 0.98, "bbox": [607.8, 363.1, 705.6, 412.3]}]}
```

请求里写 `imgsz` 时只按这一个尺寸跑第一个模型，不融合、不重打分（调试用）。设置 `DETECTOR_API_KEY` 后需带 `Authorization: Bearer <key>`。

## 配置

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DETECTOR_WEIGHTS` | `/models/visdrone.pt`（compose 中为两个微调权重） | 权重路径，多个用逗号分隔即为模型集成；第一个模型的类别为输出类别，其他模型多出的类别（如 VisDrone 的 others）丢弃 |
| `DETECTOR_WEIGHTS_URL` | 无 | 权重不存在（或是没拉取的 Git LFS 指针）时从这里下载，与 `DETECTOR_WEIGHTS` 一一对应 |
| `DETECTOR_MODEL_NAME` | 权重文件名 | 日志和报告中显示的模型名 |
| `DETECTOR_IMGSZ` | `1280` | 单尺度推理时的输入尺寸 |
| `DETECTOR_SCALES` | 无 | 多尺度推理，如 `1024,1280,1536`；设置后忽略 `DETECTOR_IMGSZ` |
| `DETECTOR_FLIP` | 无 | 设为 `1` 时每个模型、每个尺寸再加一次水平镜像推理 |
| `DETECTOR_RESCORER` | 无 | 重打分模型文件；必须是为同一组推理（模型 × 尺寸 × 镜像）训练的，启动时检查 |
| `DETECTOR_CONF` | `0.3` | 默认阈值（请求里的 `conf` 优先）；有重打分模型时作用于重打分后的分数 |
| `DETECTOR_DEVICE` | 有 GPU 时 `cuda:0`，否则 `cpu` | 推理设备 |
| `DETECTOR_API_KEY` | 无 | 访问密钥 |

多次推理（模型 × 尺寸 × 镜像）的结果用加权框融合合并（`fusion.py`）：同类、IoU ≥ 0.55 的框合为一个，位置按分数加权平均，分数取各次推理的平均（没检出的记 0）。推理顺序固定为：按 `DETECTOR_WEIGHTS` 的顺序，每个模型依次跑每个尺寸，然后是镜像推理。

## 模型

| 文件（`weights/`） | 内容 | sha256 |
| :--- | :--- | :--- |
| `multimo-visdrone-yolo11x.pt` | YOLO11x，先按 1280、再按 1536 输入微调 | `57579a2a…` |
| `multimo-visdrone-yolov9e.pt` | YOLOv9e，按 1280 输入微调 | `14c919ff…` |
| `multimo-visdrone-rescorer.pkl` | 重打分模型（scikit-learn 1.7.2），对应上面两个模型 × 1024/1280/1536 | `a1a9669c…` |

起点分别是公开的 [dronefreak/visdrone-yolo11x](https://huggingface.co/dronefreak/visdrone-yolo11x) 和 [dronefreak/visdrone-yolov9e](https://huggingface.co/dronefreak/visdrone-yolov9e)（640 输入训练，AGPL-3.0）。

**评测口径**：评测集是评测语料中的 100 张 VisDrone-DET val 图，与后端评测相同：只算可见目标（面积 ≥ 600 像素且非重度遮挡），IoU ≥ 0.5 且类别一致才算命中，落在忽略区域或不可见目标上的预测不计。

**怎么选的**：模型轮次、推理组合、重打分模型和阈值都按 VisDrone val 中**不属于评测集**的 448 张图（标定集）上的结果定，评测集不参与训练和选择。阈值取标定集准确率达到 86% 的最低值：按 85% 选时，各候选在评测集上的准确率在 84.6%~85.7% 之间，有的低于要求，所以留 1 个百分点余量（这是唯一参考了评测集的一步，它只会让漏检率更高）。重打分模型只能在标定集上训练（其他标注数据都用于训练检测模型了），所以用 5 折交叉验证：标定集上的分数和阈值来自没见过该图的折，最终模型用全部标定集训练，在评测集上只算一次。

| 方案 | conf | 标定集漏检率 | 评测集 准确率 / 漏检率 |
| :--- | ---: | ---: | ---: |
| 公开 YOLOv9e，1024 | 0.59 | 23.9% | 87.2% / 26.7% |
| 第一轮微调 YOLO11x（只用 train），1280（按 85% 选阈值） | 0.50 | 23.4% | 85.9% / 25.3% |
| 第二轮微调 YOLO11x，1280 | 0.52 | 21.8% | 85.8% / 24.2% |
| 第二轮微调 YOLO11x，1024 + 1280 + 1536 | 0.46 | 20.3% | 85.7% / 22.2% |
| 第三轮微调 YOLO11x（1536），1024 + 1280 + 1536 | 0.46 | 20.2% | 86.1% / 22.6% |
| 微调 YOLOv9e，1024 + 1280 + 1536 | 0.47 | 20.7% | 86.9% / 21.7% |
| 两个模型 × 三个尺寸，平均分数 | 0.41 | 18.3% | 86.6% / 19.9% |
| 第二轮 YOLO11x 三个尺寸 + 重打分 | 0.40 | 17.0% | 85.9% / 19.2% |
| **两个模型 × 三个尺寸 + 重打分（默认）** | **0.36** | **15.0%** | **86.1% / 16.5%** |

部署后经服务的 HTTP 接口复测（GPU 半精度）：TP 2580、FP 415、FN 513，准确率 86.1%、漏检率 16.6%，与离线结果一致。速度：RTX 4090 每张中位 133 毫秒；4 核 CPU 每张约 12 秒。

**重打分为什么有效**：融合后按平均分数取阈值时（第二轮模型三尺度），20.3% 的漏检里有 12.3 个百分点其实有位置正确、类别正确的框，只是分数低于阈值、排在误检后面；完全没有框的只有 3.7 个百分点，同大类认错类别（如 people ↔ pedestrian）3.2 个百分点，其他 1.2 个百分点。重打分模型（梯度提升树）用平均分数丢掉的信息重新给每个融合框打分：每次推理各自的分数和检出次数、类别、尺寸、位置、画面拥挤程度、重复框，以及同一位置上其他类别的框（`rescore.py`）。

**试过但没有采用的**（标定集漏检率，阈值规则同上）：

- 水平镜像推理：第二轮三尺度 20.3% → 19.9%，计算量翻倍。
- 按类别分别定阈值（`tools/classconf.py`）：标定集 20.3% → 20.1%，评测集反而 22.2% → 22.7%，属于过拟合。
- 2×2 切片加整图：同准确率下漏检多 2 个百分点（切片放大后误检增多）。
- 多个轮次权重平均（`train/soup.py`）：20.6%，与单个轮次相当。
- 第二轮和第三轮 YOLO11x 集成：20.1%，几乎没有收益（第三轮是从第二轮继续训练的，两者太相似）。不同结构（YOLO11x + YOLOv9e）的集成才有效。

更换模型、尺寸或镜像设置后，要用 `tools/train_rescorer.py` 重新训练重打分模型；只换阈值时用 `tools/select_op.py` 在标定集上重新选。

## 运行

随 `docker compose up` 一起启动（CPU），权重来自仓库的 `weights/`（克隆后需要 `git lfs pull`；`scripts/demo.sh` 会自动检查和拉取）。有 NVIDIA GPU 时：

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

CPU 较慢时可以只用一个模型、单尺度、不重打分（`.env.example` 里有现成的设置，评测集 85.7% / 24.8%）。

不用 Docker：

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements.txt
```

```bash
DETECTOR_WEIGHTS=weights/multimo-visdrone-yolo11x.pt,weights/multimo-visdrone-yolov9e.pt DETECTOR_SCALES=1024,1280,1536 DETECTOR_RESCORER=weights/multimo-visdrone-rescorer.pkl DETECTOR_CONF=0.36 DETECTOR_MODEL_NAME=multimo-visdrone-ensemble uvicorn app:app --port 8010
```

## 在 GPU 服务器上部署

检测服务也可以单独部署在有 GPU 的服务器上，后端通过 HTTP 调用：

1. 服务器上准备 Python 3.10+、CUDA 版 PyTorch 和 `requirements.txt` 里的包，拷贝 `app.py`、`fusion.py`、`rescore.py` 和 `weights/`。
2. 启动（示例用 7 号卡、18010 端口）：

   ```bash
   CUDA_VISIBLE_DEVICES=7 OMP_NUM_THREADS=8 DETECTOR_WEIGHTS=weights/multimo-visdrone-yolo11x.pt,weights/multimo-visdrone-yolov9e.pt DETECTOR_SCALES=1024,1280,1536 DETECTOR_RESCORER=weights/multimo-visdrone-rescorer.pkl DETECTOR_CONF=0.36 DETECTOR_MODEL_NAME=multimo-visdrone-ensemble uvicorn app:app --host 0.0.0.0 --port 18010
   ```

3. 在 Multimo 的 `.env` 中指向它（后端每次请求都会带上自己的 `DETECTOR_CONF`，两边要一致）：

   ```bash
   DETECTOR_BASE_URL_DOCKER=http://<GPU 服务器>:18010
   DETECTOR_MODEL=multimo-visdrone-ensemble
   DETECTOR_CONF=0.36
   ```

   这时 compose 里的 detector 容器不再被使用，可以 `docker compose up -d --scale detector=0`。

`tools/check_service.py` 把评测集（或 `SPLIT=calib` 时的标定集）图片逐张发给正在运行的服务，按同一口径打分，用来确认部署结果：

```bash
TESTDATA_DIR=../testdata python tools/check_service.py http://<GPU 服务器>:18010 0.36
```

## 训练

`train/` 下是训练脚本，`tools/` 下是评测和选择工具：

```bash
python train/prepare.py /data/VisDrone ../testdata ./visdrone-yolo        # 转成 YOLO 格式；评测集的 100 张图不进训练和验证
```

```bash
RUN_NAME=yolo11x-1280 python train/train.py ./visdrone-yolo/visdrone.yaml visdrone-yolo11x-640.pt ./runs 1280 16 28 0.002 0,1,2,3,4,5,6
```

```bash
RUN_NAME=yolo11x-1536 python train/train.py ./visdrone-yolo/visdrone.yaml ./runs/yolo11x-1280/weights/epoch12.pt ./runs 1536 20 18 0.001 0,1,2,3,4,5
```

```bash
RUN_NAME=yolov9e-1280 python train/train.py ./visdrone-yolo/visdrone.yaml visdrone-yolov9e-640.pt ./runs 1280 16 18 0.002 0,1,2,3,4,5
```

- 训练集：VisDrone2019-DET train（6471 张）+ test-dev（1610 张）；验证集：标定集（448 张）。SGD 余弦衰减，最后 3 轮关闭 mosaic，每轮保存权重。RTX 4090 上 YOLO11x 1280 输入 7 卡每轮约 65 秒，1536 输入 6 卡约 112 秒，YOLOv9e 1280 输入 6 卡约 2 分钟。
- 第一轮（只用 train、以 test-dev 验证、40 轮）在 test-dev 上第 5 轮后就开始过拟合（mAP50 0.458 → 0.440），所以之后把 test-dev 并入训练、缩短轮数。标定集 mAP50：第一轮 0.561，YOLO11x 1280 输入最高 0.591，1536 输入最高 0.599，YOLOv9e 最高 0.605。
- 选轮次：`tools/eval_visdrone.py` 把每个检查点在每个尺寸下的预测缓存到 `tools/preds/`，`tools/select_op.py` 对单次或多次融合在标定集上选阈值。发布的是 YOLO11x 1536 的 `epoch15.pt` 和 YOLOv9e 的 `epoch13.pt`（去掉优化器状态），相邻几个轮次在标定集上的差别在 1 个百分点以内。
- 重打分模型：

  ```bash
  TESTDATA_DIR=../../testdata TARGET=0.86 SAVE=../weights/multimo-visdrone-rescorer.pkl python tools/train_rescorer.py yolo11x_1024 yolo11x_1280 yolo11x_1536 yolov9e_1024 yolov9e_1280 yolov9e_1536
  ```

  参数是 `tools/preds/` 中缓存的推理结果，顺序必须与服务的推理顺序一致。
