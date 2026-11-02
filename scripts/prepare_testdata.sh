#!/usr/bin/env bash
# Download, verify and prepare Multimo test data into testdata/.
#
#   scripts/prepare_testdata.sh                 # mini profile (~1.6 GB)
#   scripts/prepare_testdata.sh --profile full  # complete datasets (~17 GB)
#   scripts/prepare_testdata.sh --verify        # only re-check MD5 of what is on disk
#
# Every file comes from testdata/manifest.tsv. Each row lists one or more
# mirrors (GitHub, HuggingFace, hf-mirror.com, ModelScope ...) tried in order,
# and is checked against the MD5 recorded there. Large archives are not
# downloaded for the mini profile: only the selected members are read from the
# remote zip with HTTP Range requests.
#
# After downloading, image sequences are encoded to MP4 at 25 fps, and WMV/FLV
# copies are made, so all video formats the backend accepts are covered. Those
# derived files depend on the local ffmpeg build and are not MD5-checked; their
# source frames are.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTDATA="${TESTDATA_DIR:-$ROOT/testdata}"
HELPER="$ROOT/scripts/lib/testdata.py"
FPS=25 # benchmark/keyframe_gt.jsonl assumes this frame rate

PROFILE=mini
ONLY=""
VERIFY_ONLY=0
DERIVE=1
EXTRA=()

usage() {
  sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --profile mini|full   which rows of the manifest to fetch (default: mini)
  --only ID[,ID...]     fetch only these manifest rows
  --verify              verify files already on disk, download nothing
  --no-derive           skip MP4/WMV/FLV generation
  --keep-archives       keep downloaded zip files after extraction
  --update-manifest     record checksums for rows marked TBD (maintainers)
  -h, --help            show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --verify) VERIFY_ONLY=1; shift ;;
    --no-derive) DERIVE=0; shift ;;
    --keep-archives|--update-manifest) EXTRA+=("$1"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ "$PROFILE" == mini || "$PROFILE" == full ]] || { echo "--profile must be mini or full" >&2; exit 2; }

say() { printf '\n==> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 1; }; }

need python3
need curl
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { echo "python3 >= 3.8 required" >&2; exit 1; }

mkdir -p "$TESTDATA"
SEL=(--root "$TESTDATA" --profile "$PROFILE")
[[ -n "$ONLY" ]] && SEL+=(--only "$ONLY")

if [[ $VERIFY_ONLY -eq 1 ]]; then
  say "Verifying $PROFILE profile in $TESTDATA"
  exec python3 -I "$HELPER" verify "${SEL[@]}"
fi

say "Downloading $PROFILE profile into $TESTDATA"
fetch_status=0
python3 -I "$HELPER" fetch "${SEL[@]}" "${EXTRA[@]}" || fetch_status=$?

# ---------------------------------------------------------------- derived video

encode_sequence() { # <frames dir> <out.mp4>
  local dir="$1" out="$2" tmp
  if [[ -f "$out" && "$out" -nt "$dir" ]]; then return 0; fi
  mkdir -p "$(dirname "$out")"
  tmp="${out%.mp4}.tmp.mp4"
  ffmpeg -nostdin -loglevel error -y -framerate "$FPS" -pattern_type glob -i "$dir/*.jpg" \
    -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -c:v libx264 -preset veryfast -crf 23 \
    -pix_fmt yuv420p -threads "${FFMPEG_THREADS:-2}" -movflags +faststart "$tmp"
  mv "$tmp" "$out"
  echo "  $out"
}

transcode() { # <in> <out> <ffmpeg codec args...>
  local in="$1" out="$2"; shift 2
  if [[ -f "$out" && "$out" -nt "$in" ]]; then return 0; fi
  mkdir -p "$(dirname "$out")"
  local tmp="${out%.*}.tmp.${out##*.}"
  ffmpeg -nostdin -loglevel error -y -i "$in" "$@" -threads "${FFMPEG_THREADS:-2}" "$tmp"
  mv "$tmp" "$out"
  echo "  $out"
}

if [[ $DERIVE -eq 1 ]]; then
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg not found: skipping MP4/WMV/FLV generation (rerun after installing ffmpeg)" >&2
  else
    say "Encoding image sequences to MP4 (${FPS} fps)"
    shopt -s nullglob
    for d in "$TESTDATA"/video/visdrone_vid_val/sequences/*/; do
      encode_sequence "${d%/}" "$TESTDATA/video/visdrone_vid_val/mp4/$(basename "$d").mp4"
    done
    for d in "$TESTDATA"/video/uavdt/UAV-benchmark-M/*/; do
      encode_sequence "${d%/}" "$TESTDATA/video/uavdt/mp4/$(basename "$d").mp4"
    done

    say "Making WMV / FLV copies"
    src="$TESTDATA/video/visdrone_vid_val/mp4/uav0000339_00001_v.mp4"
    if [[ -f "$src" ]]; then
      transcode "$src" "$TESTDATA/video/formats/uav0000339_00001_v.wmv" -c:v wmv2 -b:v 6M -f asf
      transcode "$src" "$TESTDATA/video/formats/uav0000339_00001_v.flv" -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -f flv
    fi
    src="$TESTDATA/video/uavdt/mp4/M1004.mp4"
    if [[ -f "$src" ]]; then
      transcode "$src" "$TESTDATA/video/formats/M1004.wmv" -c:v wmv2 -b:v 4M -f asf
      transcode "$src" "$TESTDATA/video/formats/M1004.flv" -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p -f flv
    fi
    ffmpeg -version | head -1 > "$TESTDATA/video/DERIVED_WITH.txt"
    shopt -u nullglob
  fi
fi

# ---------------------------------------------------------------- verify + summary

say "Verifying checksums"
verify_status=0
python3 -I "$HELPER" verify "${SEL[@]}" || verify_status=$?

say "Summary"
for d in video image remote_sensing geo docs; do
  [[ -d "$TESTDATA/$d" ]] && printf '  %-16s %s\n' "$d" "$(du -sh "$TESTDATA/$d" | cut -f1)"
done
printf '  %-16s ' "file types"
find "$TESTDATA" -type f ! -path '*/.stamps/*' ! -name '*.tmp.*' ! -name '*.part' \
  | sed -n 's/.*\.\([A-Za-z0-9]*\)$/\1/p' | tr 'A-Z' 'a-z' \
  | grep -E '^(mp4|wmv|flv|png|jpe?g|tif|tiff|doc|docx|pdf)$' | sort | uniq -c | awk '{printf "%s:%s ", $2, $1} END {print ""}'

if [[ $fetch_status -ne 0 || $verify_status -ne 0 ]]; then
  echo "Some items failed; rerun the same command to resume." >&2
  exit 1
fi
echo "Done."
