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

from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")

# joblens_scoring.py 경로 추가
sys.path.insert(
    0,
    os.path.dirname(os.path.abspath(__file__))
)
from joblens_scoring import apply_joblens_scores

API_KEY = os.environ.get("SEOUL_API_KEY", "544e70416d6d696e32397761575144")
OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "JobLens_Scores.csv"
)

KEEP_COLS = [
    "COMPANY", "TITLE", "CAREER", "REG_DT", "CLOSE_DT", "REGION",
    "MIN_EDUBG", "MAX_EDUBG", "IND_TP_CD_NM", "CORP_ADDR", "JOBS_NM",
    "JOB_CONT", "EMP_TP_NM", "COLLECT_PSNCNT", "SAL_TP_NM", "PF_COND",
    "SEL_MTHD", "RCPT_MTHD", "SUBMIT_DOC", "WORK_REGION",
    "WORKDAY_WORKHR_CONT", "FOUR_INS", "RETIREPAY", "ETC_WELFARE",
    "CONTACT_TELNO",
]

FIELD_MAP = {
    "COMPANY": "CMPNY_NM",
    "TITLE": "JO_SJ",
    "CAREER": "CAREER_CND_NM",
    "REG_DT": "JO_REG_DT",
    "CLOSE_DT": "RCEPT_CLOS_NM",
    "REGION": "SUBWAY_NM",              # 근무지역(구 단위) — 기존 지하철 필드와는 성격이 다르니 검토 필요
    "MAX_EDUBG": "ACDMCR_NM",
    "JOBS_NM": "JOBCODE_NM",
    "JOB_CONT": "DTY_CN",
    "EMP_TP_NM": "EMPLYM_STLE_CMMN_MM",
    "SAL_TP_NM": "HOPE_WAGE",
    "RCPT_MTHD": "RCEPT_MTH_NM",
    "SUBMIT_DOC": "PRESENTN_PAPERS_NM",
    "WORK_REGION": "WORK_PARAR_BASS_ADRES_CN",
    "WORKDAY_WORKHR_CONT": "WORK_TIME_NM",
    "RETIREPAY": "RET_GRANTS_NM",
    "ETC_WELFARE": "WELFARE_CN",
    "CONTACT_TELNO": "MNGR_PHON_NO",
    "FOUR_INS": "JO_FEINSR_SBSCRB_NM",
}

API_KEY = os.environ.get("SEOUL_API_KEY", "544e70416d6d696e32397761575144")

# 기존 KEEP_COLS를 신규 필드명으로 교체
KEEP_COLS = [
    "COMPANY", "TITLE", "CAREER", "REG_DT", "CLOSE_DT", "REGION",
    "MIN_EDUBG", "MAX_EDUBG", "IND_TP_CD_NM", "CORP_ADDR", "JOBS_NM",
    "JOB_CONT", "EMP_TP_NM", "COLLECT_PSNCNT", "SAL_TP_NM", "PF_COND",
    "SEL_MTHD", "RCPT_MTHD", "SUBMIT_DOC", "WORK_REGION",
    "WORKDAY_WORKHR_CONT", "FOUR_INS", "RETIREPAY", "ETC_WELFARE",
    "CONTACT_TELNO",
]

# 기존 코드 전체 로직과 호환되도록, 수집 직후 컬럼명을 예전 이름으로 매핑
FIELD_MAP = {
    "COMPANY": "CMPNY_NM",
    "TITLE": "JO_SJ",
    "CAREER": "CAREER_CND_NM",
    "REG_DT": "JO_REG_DT",
    "CLOSE_DT": "RCEPT_CLOS_NM",
    "REGION": "SUBWAY_NM",              # 근무지역(구 단위) — 기존 지하철 필드와는 성격이 다르니 검토 필요
    "MAX_EDUBG": "ACDMCR_NM",
    "JOBS_NM": "JOBCODE_NM",
    "JOB_CONT": "DTY_CN",
    "EMP_TP_NM": "EMPLYM_STLE_CMMN_MM",
    "SAL_TP_NM": "HOPE_WAGE",
    "RCPT_MTHD": "RCEPT_MTH_NM",
    "SUBMIT_DOC": "PRESENTN_PAPERS_NM",
    "WORK_REGION": "WORK_PARAR_BASS_ADRES_CN",
    "WORKDAY_WORKHR_CONT": "WORK_TIME_NM",
    "RETIREPAY": "RET_GRANTS_NM",
    "ETC_WELFARE": "WELFARE_CN",
    "CONTACT_TELNO": "MNGR_PHON_NO",
    "FOUR_INS": "JO_FEINSR_SBSCRB_NM",
}


def fetch_all_jobs():
    print("서울시 Open API 수집 시작 (recMntList)...")
    all_rows = []
    start = 1
    BASE_URL = f"http://openapi.seoul.go.kr:8088/{API_KEY}/json/recMntList"
    while True:
        end = start + 999
        url = f"{BASE_URL}/{start}/{end}"
        try:
            res = requests.get(url, timeout=30)
            data = res.json()
        except Exception as e:
            print(f"  오류 ({start}~{end}): {e}")
            break
        if "recMntList" not in data or "row" not in data.get("recMntList", {}):
            result = data.get("recMntList", {}).get("RESULT", {})
            print(f"  종료: {result.get('MESSAGE', '데이터 없음')}")
            break
        rows = data["recMntList"]["row"]
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

    # 필드명을 기존 스코어링/프론트 코드가 기대하는 이름으로 변환
    df = pd.DataFrame(all_rows)
    df = df.rename(columns=FIELD_MAP)
    return df.to_dict(orient="records")


def main():
    rows = fetch_all_jobs()
    if not rows:
        print("수집 실패 — 기존 CSV 유지")
        sys.exit(0)

    df = pd.DataFrame(rows)
    df["JO_REG_DT"] = pd.to_datetime("20" + df.get("JO_REG_DT", pd.Series(dtype=str)).astype(str), format="%Y-%m-%d", errors="coerce")

    # 마감 공고 필터링
    close_date_raw = df["RCEPT_CLOS_NM"].astype(str).str.extract(r"(\d{2}-\d{2}-\d{2})")[0]
    close_date = pd.to_datetime("20" + close_date_raw, format="%Y-%m-%d", errors="coerce")
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
