#!/usr/bin/env bash
# One-click Multimo demo: test data, services, corpus processing, sample
# searches and questions, and the full evaluation with its report.
#
#   scripts/demo.sh                  # everything, CPU detector
#   scripts/demo.sh --gpu            # detector on an NVIDIA GPU (docker-compose.gpu.yml)
#   scripts/demo.sh --skip-data      # testdata/ is already prepared
#   scripts/demo.sh --no-compose     # services already running; only talk to MULTIMO_URL
#   scripts/demo.sh --no-eval        # stop after the sample searches and questions
#
# Environment: MULTIMO_URL (default http://localhost:8000), FRONTEND_URL
# (default http://localhost:8080), DEMO_OUT (default ./demo-output).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
URL="${MULTIMO_URL:-http://localhost:8000}"
FRONTEND="${FRONTEND_URL:-http://localhost:8080}"
OUT="${DEMO_OUT:-$ROOT/demo-output}"
HELPER="$ROOT/scripts/lib/demo.py"

GPU=0 SKIP_DATA=0 COMPOSE=1 EVAL=1
for arg in "$@"; do
  case "$arg" in
    --gpu) GPU=1 ;;
    --skip-data) SKIP_DATA=1 ;;
    --no-compose) COMPOSE=0 ;;
    --no-eval) EVAL=0 ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$arg（--help 查看用法）" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "需要 $1：$2" >&2; exit 1; }; }

need curl "用于检查服务"
need python3 "用于调用接口和准备测试数据"

if [ "$COMPOSE" = 1 ]; then
  need docker "安装 Docker 和 Docker Compose v2"
  docker compose version >/dev/null 2>&1 || { echo "需要 Docker Compose v2（docker compose）" >&2; exit 1; }

  step "检查配置"
  if [ ! -f .env ]; then
    cp .env.example .env
    echo "已从 .env.example 生成 .env。请填好 POSTGRES_PASSWORD、VLM_API_KEY、DECISION_API_KEY 后重新运行。"
    exit 1
  fi
  missing=()
  for key in POSTGRES_PASSWORD VLM_API_KEY DECISION_API_KEY; do
    val="$(grep -E "^${key}=" .env | tail -1 | cut -d= -f2- || true)"
    if [ -z "$val" ] || [[ "$val" == *your* ]] || [[ "$val" == *change-me* ]]; then missing+=("$key"); fi
  done
  if [ ${#missing[@]} -gt 0 ]; then
    echo ".env 中这些变量还没有填写：${missing[*]}" >&2
    exit 1
  fi
  echo ".env 已就绪"

  # Detector weights shipped in detector/weights are stored with Git LFS; a
  # clone without git-lfs only has small pointer files.
  real_file() { [ -s "$1" ] && ! head -c 64 "$1" | grep -q "version https://git-lfs"; }
  # Value from .env, or the docker-compose.yml default when the key is absent.
  env_or() { if grep -qE "^$1=" .env; then grep -E "^$1=" .env | tail -1 | cut -d= -f2-; else echo "$2"; fi; }
  files="$(env_or DETECTOR_WEIGHTS /weights/multimo-visdrone-yolo11x.pt,/weights/multimo-visdrone-yolov9e.pt),$(env_or DETECTOR_RESCORER /weights/multimo-visdrone-rescorer.pkl)"
  for f in ${files//,/ }; do
    case "$f" in /weights/*) file="detector/weights/${f#/weights/}" ;; *) continue ;; esac
    if ! real_file "$file" && git lfs version >/dev/null 2>&1; then
      echo "拉取检测模型权重（Git LFS）"
      git lfs pull --include "detector/weights/*"
    fi
    real_file "$file" || { echo "缺少检测模型文件 $file：安装 git-lfs 后在仓库中运行 git lfs pull" >&2; exit 1; }
  done
fi

if [ "$SKIP_DATA" = 0 ]; then
  step "准备测试数据（mini，约 1.6 GB，已下载的文件只做校验）"
  scripts/prepare_testdata.sh
fi
[ -f testdata/benchmark/corpus.json ] || { echo "缺少 testdata/benchmark/corpus.json，请先运行 scripts/prepare_testdata.sh" >&2; exit 1; }

if [ "$COMPOSE" = 1 ]; then
  step "启动服务"
  files=(-f docker-compose.yml)
  [ "$GPU" = 1 ] && files+=(-f docker-compose.gpu.yml)
  docker compose "${files[@]}" up -d --build
fi

step "等待后端就绪"
python3 "$HELPER" health "$URL" 900

step "登记评测语料"
python3 "$HELPER" register "$URL" testdata/benchmark/corpus.json

step "处理素材：抽取关键帧、目标检测、画面描述、空间对齐、建立索引"
echo "首次处理需要较长时间（约 300 个关键帧的视觉模型调用、2361 个底图切片的向量化）。中断后重新运行会接着处理。"
python3 "$HELPER" wait "$URL" 21600

step "示例检索和问答"
python3 "$HELPER" showcase "$URL"

if [ "$EVAL" = 1 ]; then
  step "运行完整评测"
  python3 "$HELPER" evaluate "$URL" "$OUT"
fi

step "完成"
echo "前端：$FRONTEND"
echo "接口文档：$URL/swagger"
[ "$EVAL" = 1 ] && echo "评测报告：$OUT/test_report.html"
exit 0
