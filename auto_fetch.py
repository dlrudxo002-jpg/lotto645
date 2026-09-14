# -*- coding: utf-8 -*-
"""
자동 수집 진입점 (CI 워크플로·스케줄러용).

과거에는 네이버 검색결과 HTML을 정규식으로 긁었다. 그 방식은
  - 공식 데이터가 아니고(검색 페이지 마크업이 바뀌면 조용히 멈춘다),
  - 당첨금·성적표를 누적하지 못하며,
  - 같은 코드가 .github/workflows/lotto-fetch.yml에 한 벌 더 복사돼 있었다.
2026-09-13, 수집 경로를 accumulate.sync() 하나로 통합했다.

사용법:
    python -X utf8 auto_fetch.py
종료 코드: 0 = 정상(새 회차 유무 무관), 1 = 수집 실패(형식 변경·네트워크 등)
"""
import sys
from datetime import datetime

import accumulate


def fetch_latest():
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    try:
        added = accumulate.sync(verbose=True)
    except Exception as e:
        # 실패를 0으로 끝내면 CI가 초록불인 채로 수집이 멈춘다. 반드시 실패로 알린다.
        print(f'[{stamp}] 수집 실패 — {type(e).__name__}: {e}')
        return 1

    if added:
        last = added[-1]['회차']
        print(f'[{stamp}] {len(added)}회차 수집 완료 (최신: {last}회)')
        accumulate.report_board(accumulate.load_board())
    else:
        print(f'[{stamp}] 새 회차 없음')
    return 0


if __name__ == '__main__':
    sys.exit(fetch_latest())
