#!/usr/bin/env bash
# Downloads the free, self-hosted models the worker uses. All are from public GitHub releases.
#   Kokoro-82M ONNX (Apache-2.0)                 - narration voice (free tier)
#   YuNet face detector (OpenCV Zoo, MIT)        - face detection for the blur pipeline
#   YOLOv9-t license plate detector (open-image-models, MIT) - plate detection for the blur pipeline
set -euo pipefail
DEST="${1:-./models}"
mkdir -p "$DEST"
fetch() { [ -s "$DEST/$2" ] || curl -fsSL --retry 3 -o "$DEST/$2" "$1"; }
fetch https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx kokoro-v1.0.onnx
fetch https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin voices-v1.0.bin
fetch https://raw.githubusercontent.com/thewh1teagle/kokoro-onnx/main/src/kokoro_onnx/config.json kokoro-config.json
python3 -c "import json,sys; json.dump({'vocab': json.load(open('$DEST/kokoro-config.json'))['vocab']}, open('$DEST/kokoro-vocab.json','w'))"
fetch https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx face_detection_yunet_2023mar.onnx
fetch https://github.com/ankandrew/open-image-models/releases/download/assets/yolo-v9-t-384-license-plates-end2end.onnx yolo-v9-t-384-license-plates-end2end.onnx
ls -la "$DEST"
