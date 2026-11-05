"""Average the weights of several checkpoints of one run (a "model soup").

    python soup.py OUT.pt CKPT.pt [CKPT.pt ...]

The first checkpoint provides the structure and metadata; floating-point
tensors are replaced by the mean over all checkpoints. On VisDrone the soup of
epochs 8-15 was no better than a single late epoch, so the released model is a
single checkpoint; this is kept for reproducing that comparison."""
import sys

import torch


def main():
    out, paths = sys.argv[1], sys.argv[2:]
    cks = [torch.load(p, map_location="cpu", weights_only=False) for p in paths]
    key = "ema" if cks[0].get("ema") is not None else "model"
    sds = [c[key].float().state_dict() for c in cks]
    avg = {k: (sum(sd[k] for sd in sds) / len(sds) if v.is_floating_point() else v) for k, v in sds[0].items()}
    base = cks[0]
    model = base[key].float()
    model.load_state_dict(avg)
    base.update(model=model.half(), ema=None, optimizer=None, updates=None, train_metrics={})
    torch.save(base, out)
    print(f"averaged {len(paths)} checkpoints ({key}) -> {out}")


if __name__ == "__main__":
    main()
