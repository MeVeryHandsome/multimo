"""Fine-tune a VisDrone YOLO model at a higher input size.

    RUN_NAME=yolo11x-1280 python train.py DATA_YAML BASE_WEIGHTS RUN_DIR [IMGSZ EPOCHS BATCH LR0 DEVICES]

The released models (see ../README.md): YOLO11x from dronefreak/visdrone-yolo11x
at 1280 (16 epochs, batch 28, lr0 0.002), continued at 1536 (20 epochs, batch
18, lr0 0.001), and YOLOv9e from dronefreak/visdrone-yolov9e at 1280 (16
epochs, batch 18, lr0 0.002). A checkpoint is kept every epoch, so the epoch
can be chosen afterwards with the benchmark protocol on the calibration split
(../tools/select_op.py). The run is saved in RUN_DIR/RUN_NAME (default
<base weights name>-<imgsz>)."""
import os
import sys

from ultralytics import YOLO


def main():
    data, base, run_dir = sys.argv[1:4]
    imgsz, epochs, batch, lr0, devices = (sys.argv[4:9] + ["1280", "16", "28", "0.002", "0"][len(sys.argv[4:9]):])
    YOLO(base).train(
        data=data, imgsz=int(imgsz), epochs=int(epochs), batch=int(batch),
        device=[int(d) for d in devices.split(",")], workers=8,
        optimizer="SGD", lr0=float(lr0), lrf=0.1, cos_lr=True, warmup_epochs=0.5,
        close_mosaic=3, patience=0, amp=True, plots=False, save_period=1,
        project=run_dir, name=os.environ.get("RUN_NAME") or f"{os.path.splitext(os.path.basename(base))[0]}-{imgsz}",
        exist_ok=True,
    )


if __name__ == "__main__":
    main()
