# -*- coding: utf-8 -*-
import csv, os, re, urllib.request, urllib.parse
from datetime import datetime

BASE     = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, 'lotto645_전체.csv')
NAVER_URL = 'https://search.naver.com/search.naver?query={}'

def fetch_from_naver(rnd):
    query = urllib.parse.quote(f'로또 {rnd}회')
    url = NAVER_URL.format(query)
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'ko-KR,ko;q=0.9'
    })
    with urllib.request.urlopen(req, timeout=10) as res:
        html = res.read().decode('utf-8', errors='ignore')

    # 실제로 해당 회차가 선택됐는지 확인
    if f'data-kgs-option="{rnd}" aria-selected="true"' not in html:
        return None

    win_idx = html.find('win_number_box')
    if win_idx < 0:
        return None

    section = html[win_idx:win_idx + 800]
    balls = re.findall(r'class="ball type\d+">(\d+)<', section)
    if len(balls) < 7:
        return None

    nums  = sorted([int(x) for x in balls[:6]])
    bonus = int(balls[6])
    date_m = re.search(rf'{rnd}회차 \((\d{{4}}\.\d{{2}}\.\d{{2}})\.\)', html)
    date   = date_m.group(1).replace('.', '-') if date_m else ''
    return nums, bonus, date

def fetch_latest():
    latest = 0
    try:
        with open(CSV_PATH, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                latest = max(latest, int(row['회차']))
    except Exception as e:
        print(f'CSV 읽기 오류: {e}')
        return

    added, rnd = 0, latest + 1
    rows = []
    while True:
        try:
            result = fetch_from_naver(rnd)
            if not result:
                break
            nums, bonus, date = result
            rows.append([rnd] + nums + [bonus])
            added += 1
            rnd += 1
        except:
            break

    if rows:
        with open(CSV_PATH, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] {added}회차 수집 완료 (최신: {rnd-1}회)')
    else:
        print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M")}] 새 회차 없음 (현재 최신: {latest}회)')

if __name__ == '__main__':
    fetch_latest()
