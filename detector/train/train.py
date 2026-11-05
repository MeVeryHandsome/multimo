"""Fine-tune a VisDrone YOLO model at a higher input size.

    python train.py DATA_YAML BASE_WEIGHTS RUN_DIR [IMGSZ EPOCHS BATCH LR0 DEVICES]

The released model: python train.py visdrone-yolo/visdrone.yaml
visdrone-yolo11x-640.pt runs 1280 16 28 0.002 0,1,3,4,5,6,7
(dronefreak/visdrone-yolo11x as the base, 7 x RTX 4090, about 65 s per epoch).
A checkpoint is kept every epoch, so the epoch can be chosen afterwards with
the benchmark protocol on the calibration split (../tools/select_op.py)."""
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
        project=run_dir, name=f"visdrone-{imgsz}", exist_ok=True,
    )


if __name__ == "__main__":
    main()
