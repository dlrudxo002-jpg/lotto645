#!/bin/bash
# PythonAnywhere 초기 설치 스크립트
# 사용법: bash <(curl -s https://raw.githubusercontent.com/ktlee-beep/lotto/main/setup_pythonanywhere.sh)

set -e

REPO="https://github.com/ktlee-beep/lotto.git"
APP_DIR="/home/ktlee/lotto"
WSGI_FILE="/var/www/ktlee_pythonanywhere_com_wsgi.py"

echo "=== 로또 분석기 PythonAnywhere 설치 ==="

# 1. 코드 클론 또는 업데이트
echo "[1/3] 코드 가져오기..."
if [ -d "$APP_DIR/.git" ]; then
  cd "$APP_DIR" && git pull
  echo "  → git pull 완료"
else
  git clone "$REPO" "$APP_DIR"
  echo "  → git clone 완료"
fi

# 2. history.json 초기화 (없는 경우만)
echo "[2/3] 데이터 파일 확인..."
if [ ! -f "$APP_DIR/history.json" ]; then
  echo '{}' > "$APP_DIR/history.json"
  echo "  → history.json 생성"
else
  echo "  → history.json 이미 존재"
fi

# 3. WSGI 파일 설정
echo "[3/3] WSGI 설정..."
if [ -f "$WSGI_FILE" ]; then
  cat > "$WSGI_FILE" << 'WSGIEOF'
import sys
import os

path = '/home/ktlee/lotto'
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application
WSGIEOF
  echo "  → WSGI 파일 업데이트 완료"
else
  echo "  ⚠ WSGI 파일($WSGI_FILE)이 없습니다."
  echo "  → PythonAnywhere Web 탭에서 웹앱을 먼저 생성해주세요."
  echo "  → 생성 후 이 스크립트를 다시 실행하세요."
  exit 1
fi

echo ""
echo "=== 설치 완료! ==="
echo "PythonAnywhere Web 탭에서 [Reload] 버튼을 눌러주세요."
echo "확인: https://ktlee.pythonanywhere.com/api/status"
