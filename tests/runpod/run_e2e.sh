#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="docker compose -f $DIR/docker-compose.e2e.yml"
BUCKET="ff-test"
MINIO_LOCAL="http://localhost:9000"
WORKER_LOCAL="http://localhost:8000"
KEEP_UP="${KEEP_UP:-0}"

cleanup() {
  if [ "$KEEP_UP" = "1" ]; then
    echo "[cleanup] KEEP_UP=1, leaving stack running"
    return
  fi
  echo "[cleanup] tearing down stack"
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[1/8] fetching fixtures"
bash "$DIR/fetch_fixtures.sh"

echo "[2/8] starting minio"
$COMPOSE up -d minio

echo "[3/8] waiting for minio to be live"
for i in $(seq 1 60); do
  if curl -fsS "$MINIO_LOCAL/minio/health/live" >/dev/null 2>&1; then
    echo "  minio is live"
    break
  fi
  sleep 1
  if [ "$i" = "60" ]; then
    echo "  minio did not become live in 60s"
    $COMPOSE logs minio | tail -n 40
    exit 1
  fi
done

echo "[4/8] creating bucket and uploading target"
docker run --rm --network host \
  -e MC_HOST_local="http://minioadmin:minioadmin@localhost:9000" \
  -v "$DIR/fixtures:/fixtures:ro" \
  --entrypoint sh minio/mc -c "
    mc mb -p local/$BUCKET >/dev/null 2>&1 || true
    mc anonymous set download local/$BUCKET
    mc cp /fixtures/target-240p.mp4 local/$BUCKET/templates/target-240p.mp4
  "

echo "[5/8] building & starting worker (first build can take 10-30 min)"
$COMPOSE up -d --build worker

echo "[6/8] waiting for worker API on :8000"
ready=""
for i in $(seq 1 240); do
  if curl -fsS -m 2 "$WORKER_LOCAL/ping" >/dev/null 2>&1 \
     || curl -fsS -m 2 "$WORKER_LOCAL/" >/dev/null 2>&1; then
    ready=1
    echo "  worker API is up"
    break
  fi
  sleep 2
done
if [ -z "$ready" ]; then
  echo "  worker API did not come up"
  $COMPOSE logs worker | tail -n 120
  exit 1
fi

echo "[7/8] sending /runsync request"
BODY_FILE="$(mktemp)"
RESP_FILE="$(mktemp)"
python3 - "$DIR/sample_input.json" "$DIR/fixtures/source.b64" "$BODY_FILE" <<'PY'
import json, sys
src_path, b64_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src_path) as f:
    body = json.load(f)
with open(b64_path) as f:
    body["input"]["source_image_base64"] = f.read().strip()
with open(out_path, "w") as f:
    json.dump(body, f)
PY

HTTP_CODE=$(curl -sS -o "$RESP_FILE" -w "%{http_code}" -X POST "$WORKER_LOCAL/runsync" \
  -H 'Content-Type: application/json' \
  --data-binary "@$BODY_FILE" \
  --max-time 1800)

echo "[response] HTTP $HTTP_CODE"
cat "$RESP_FILE"
echo

if [ "$HTTP_CODE" != "200" ]; then
  echo "FAIL: non-200 response"
  exit 1
fi

echo "[8/8] validating response and downloading output"
get_field() {
  python3 - "$RESP_FILE" "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
o = d.get("output", d)
print(o.get(sys.argv[2], "") or "")
PY
}

ERR="$(get_field error)"
if [ -n "$ERR" ]; then
  echo "FAIL: handler returned error: $ERR"
  exit 1
fi
OUT_URL="$(get_field output_url)"
OUT_KEY="$(get_field output_key)"
OUT_BUCKET="$(get_field bucket)"
if [ -z "$OUT_URL" ] || [ -z "$OUT_KEY" ] || [ "$OUT_BUCKET" != "$BUCKET" ]; then
  echo "FAIL: response missing expected fields (url=$OUT_URL key=$OUT_KEY bucket=$OUT_BUCKET)"
  exit 1
fi

LOCAL_URL="${OUT_URL/minio:9000/localhost:9000}"
RESULT_FILE="$DIR/fixtures/result.mp4"
echo "  downloading $LOCAL_URL"
curl -fSL "$LOCAL_URL" -o "$RESULT_FILE"

SIZE=$(wc -c <"$RESULT_FILE" | tr -d ' ')
if [ "$SIZE" -lt 1024 ]; then
  echo "FAIL: result.mp4 is suspiciously small ($SIZE bytes)"
  exit 1
fi

if command -v ffprobe >/dev/null 2>&1; then
  if ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$RESULT_FILE" | grep -q video; then
    echo "  ffprobe: video stream OK"
  else
    echo "FAIL: ffprobe found no video stream in result.mp4"
    exit 1
  fi
else
  echo "  ffprobe not installed; size check only ($SIZE bytes)"
fi

echo ""
echo "SUCCESS"
echo "  output_url=$OUT_URL"
echo "  output_key=$OUT_KEY"
echo "  bucket=$OUT_BUCKET"
echo "  local file=$RESULT_FILE"
