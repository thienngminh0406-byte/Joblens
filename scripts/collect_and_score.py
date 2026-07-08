# -*- coding: utf-8 -*-
"""
GitHub Actions에서 실행되는 데이터 수집 + 스코어링 스크립트
결과를 JobLens_Scores.csv로 저장합니다.
추가: 요구역량 키워드 빈도를 keyword_trends.csv에 날짜별로 누적 저장합니다.
"""
import os
import sys
import time
import requests
import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/ 폴더
_ROOT_DIR = os.path.dirname(_THIS_DIR)                    # 저장소 최상위 폴더
sys.path.insert(0, _THIS_DIR)
sys.path.insert(0, _ROOT_DIR)
from joblens_scoring import apply_joblens_scores

API_KEY = os.environ.get("SEOUL_API_KEY", "514b4b685a6d696e39366469694276")
OUTPUT_FILE = os.path.join(_ROOT_DIR, "JobLens_Scores.csv")           # ← scripts/ 아니라 최상위로
KEYWORD_TRENDS_FILE = os.path.join(_ROOT_DIR, "keyword_trends.csv")   # ← 최상위로

KEEP_COLS = [
    "JO_REQST_NO", "CMPNY_NM", "JO_SJ", "JOBCODE_NM", "CAREER_CND_NM",
    "ACDMCR_NM", "EMPLYM_STLE_CMMN_MM", "HOPE_WAGE",
    "WORK_PARAR_BASS_ADRES_CN", "SUBWAY_NM", "WORK_TIME_NM",
    "HOLIDAY_NM", "WEEK_WORK_HR", "RCEPT_CLOS_NM", "RCEPT_MTH_NM",
    "PRESENTN_PAPERS_NM", "MNGR_PHON_NO", "BSNS_SUMRY_CN", "DTY_CN",
    "RET_GRANTS_NM", "JO_FEINSR_SBSCRB_NM", "JO_REG_DT", "WELFARE_CN",
]

# ── 추적할 요구역량/트렌드 키워드 목록 ──
# 형태소 분석기(konlpy 등) 없이 단순 포함 여부로 세기 때문에,
# 너무 짧거나 흔한 단어(예: "AI"만 단독으로 쓰면 다른 단어 안에 우연히 포함될 수 있음)는
# 오탐을 줄이기 위해 문맥이 있는 형태로 등록합니다.
TREND_KEYWORDS = [
    # AI / 신기술
    "인공지능", "AI", "ChatGPT", "챗GPT", "생성형AI", "생성형 AI", "LLM",
    "머신러닝", "딥러닝", "데이터분석", "데이터 분석", "빅데이터", "클라우드",
    "Python", "파이썬", "React", "MLOps", "RAG", "프롬프트엔지니어링",
    "RPA", "자동화",
    # 근무 형태 / 워라밸
    "재택근무", "하이브리드근무", "유연근무", "주4일", "주 4일",
    "워라밸", "재택", "원격근무",
    # 복지 / 보상
    "복지포인트", "스톡옵션", "성과급", "인센티브",
    # 채용 조건
    "경력무관", "신입환영", "수습기간", "정규직전환", "MZ세대",
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


def update_keyword_trends(df: pd.DataFrame):
    """
    오늘 날짜의 키워드별 등장 빈도를 계산해서 keyword_trends.csv에 누적 저장한다.
    - 하루 2번(9시/12시) 실행돼도 같은 날짜 데이터는 덮어써서 중복이 쌓이지 않는다.
    - 기존 날짜 데이터는 그대로 보존한다 (append 방식).
    """
    today_str = pd.Timestamp.today().strftime("%Y-%m-%d")
    total_postings = len(df)

    if total_postings == 0 or "DTY_CN" not in df.columns:
        print("키워드 트렌드: 직무내용(DTY_CN) 데이터가 없어 건너뜁니다.")
        return

    text_series = df["DTY_CN"].fillna("").astype(str)

    rows = []
    for kw in TREND_KEYWORDS:
        # 대소문자 무시, 정규식 아님(순수 문자열 포함 여부)
        count = int(text_series.str.contains(kw, case=False, regex=False, na=False).sum())
        rows.append({
            "date": today_str,
            "keyword": kw,
            "frequency": count,
            "total_postings": total_postings,
        })

    new_df = pd.DataFrame(rows)

    if os.path.exists(KEYWORD_TRENDS_FILE):
        old_df = pd.read_csv(KEYWORD_TRENDS_FILE, encoding="utf-8-sig")
        # 오늘 날짜 데이터는 새로 계산한 값으로 교체 (같은 날 재실행 시 중복 방지)
        old_df = old_df[old_df["date"] != today_str]
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.sort_values(["date", "keyword"]).reset_index(drop=True)
    combined.to_csv(KEYWORD_TRENDS_FILE, index=False, encoding="utf-8-sig")

    days_collected = combined["date"].nunique()
    print(f"키워드 트렌드 저장 완료: {KEYWORD_TRENDS_FILE}")
    print(f"  누적 일수: {days_collected}일 / 총 {len(combined)}행")


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

    # ── 키워드 트렌드 누적 저장 (CSV 저장 전, 필터링된 df 기준) ──
    update_keyword_trends(df)

    # CSV 저장 (기존 로직 그대로, 매번 덮어씀 — 현재 스냅샷용)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_FILE}")
    print(f"평균 점수: {df['종합점수'].mean():.2f}")
    print(f"등급 분포:\n{df['등급'].value_counts()}")


if __name__ == "__main__":
    main()
