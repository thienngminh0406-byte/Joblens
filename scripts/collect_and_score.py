# -*- coding: utf-8 -*-
"""
GitHub Actions에서 실행되는 데이터 수집 + 스코어링 스크립트
결과를 JobLens_Scores.csv로 저장합니다.
"""

import os
import sys
import time
import requests
import pandas as pd

# 프로젝트 루트 경로 추가 (scripts/ 상위 폴더)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from joblens_scoring import apply_joblens_scores

API_KEY = os.environ.get("SEOUL_API_KEY", "7578426b6d6d696e383454526e6471")
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "JobLens_Scores.csv")

KEEP_COLS = [
    "JO_REQST_NO", "CMPNY_NM", "JO_SJ", "JOBCODE_NM", "CAREER_CND_NM",
    "ACDMCR_NM", "EMPLYM_STLE_CMMN_MM", "HOPE_WAGE",
    "WORK_PARAR_BASS_ADRES_CN", "SUBWAY_NM", "WORK_TIME_NM",
    "HOLIDAY_NM", "WEEK_WORK_HR", "RCEPT_CLOS_NM", "RCEPT_MTH_NM",
    "PRESENTN_PAPERS_NM", "MNGR_PHON_NO", "BSNS_SUMRY_CN", "DTY_CN",
    "RET_GRANTS_NM", "JO_FEINSR_SBSCRB_NM", "JO_REG_DT", "WELFARE_CN",
]


def fetch_all_jobs():
    print("서울시 Open API 수집 시작...")
    all_rows = []
    start = 1
    BASE_URL = f"http://openapi.seoul.go.kr:8088/{API_KEY}/json/GetJobInfo"

    while True:
        end = start + 999
        url = f"{BASE_URL}/{start}/{end}"
        try:
            res = requests.get(url, timeout=30)
            data = res.json()
        except Exception as e:
            print(f"  오류 ({start}~{end}): {e}")
            break

        if "GetJobInfo" not in data or "row" not in data.get("GetJobInfo", {}):
            result = data.get("GetJobInfo", {}).get("RESULT", {})
            print(f"  종료: {result.get('MESSAGE', '데이터 없음')}")
            break

        rows = data["GetJobInfo"]["row"]
        if not rows:
            break

        filtered = [{k: r.get(k, "") for k in KEEP_COLS} for r in rows]
        all_rows.extend(filtered)
        print(f"  수집 누적: {len(all_rows)}건")
        start += 1000

        if start > 100_000:
            break

        time.sleep(0.3)

    print(f"총 {len(all_rows)}건 수집 완료")
    return all_rows


def main():
    rows = fetch_all_jobs()
    if not rows:
        print("수집 실패 — 기존 CSV 유지")
        sys.exit(0)

    df = pd.DataFrame(rows)
    df["JO_REG_DT"] = pd.to_datetime(df.get("JO_REG_DT", pd.Series(dtype=str)), errors="coerce")

    # 마감 공고 필터링
    close_date = df["RCEPT_CLOS_NM"].astype(str).str.extract(r"(\d{4}-\d{2}-\d{2})")[0]
    close_date = pd.to_datetime(close_date, errors="coerce")
    today = pd.Timestamp.today().normalize()
    df = df[close_date.isna() | (close_date >= today)].copy()
    print(f"마감 필터 후: {len(df)}건")

    # 스코어링
    print("스코어링 시작...")
    df = apply_joblens_scores(df)
    print(f"스코어링 완료: {len(df)}건")

    # CSV 저장
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_FILE}")
    print(f"평균 점수: {df['종합점수'].mean():.2f}")
    print(f"등급 분포:\n{df['등급'].value_counts()}")


if __name__ == "__main__":
    main()
