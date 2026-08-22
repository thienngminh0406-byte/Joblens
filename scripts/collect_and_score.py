# -*- coding: utf-8 -*-
"""
GitHub Actions에서 실행되는 데이터 수집 + 스코어링 스크립트
결과를 JobLens_Scores.csv로 저장합니다.
추가: 요구역량/근무조건 키워드 빈도를 keyword_trends.csv에 날짜별로 누적 저장합니다.
"""
import os
import sys
import time
import requests
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")

# joblens_scoring.py 경로 추가 (scripts/ 폴더든 저장소 최상위든 둘 다 찾도록)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # scripts/ 폴더
_ROOT_DIR = os.path.dirname(_THIS_DIR)                    # 저장소 최상위 폴더
sys.path.insert(0, _THIS_DIR)
sys.path.insert(0, _ROOT_DIR)
from joblens_scoring import apply_joblens_scores
from pension_salary import update_pension_cache

API_KEY = os.environ.get("SEOUL_API_KEY", "544e70416d6d696e32397761575144")
OUTPUT_FILE = os.path.join(_ROOT_DIR, "JobLens_Scores.csv")
# ── 키워드 트렌드 누적 파일 (매일 append, 절대 덮어쓰지 않음) ──
KEYWORD_TRENDS_FILE = os.path.join(_ROOT_DIR, "keyword_trends.csv")
PENSION_CACHE_FILE = os.path.join(_ROOT_DIR, "pension_salary_cache.csv")

# ── 신규 API(recMntList) 필드 ──
KEEP_COLS_NEW = [
    "COMPANY", "TITLE", "CAREER", "REG_DT", "CLOSE_DT", "REGION",
    "MIN_EDUBG", "MAX_EDUBG", "IND_TP_CD_NM", "CORP_ADDR", "JOBS_NM",
    "JOB_CONT", "EMP_TP_NM", "COLLECT_PSNCNT", "SAL_TP_NM", "PF_COND",
    "SEL_MTHD", "RCPT_MTHD", "SUBMIT_DOC", "WORK_REGION",
    "WORKDAY_WORKHR_CONT", "FOUR_INS", "RETIREPAY", "ETC_WELFARE",
    "CONTACT_TELNO",
]

# 신규 필드명 → 기존 필드명 매핑 (joblens_scoring.py, app.py를 그대로 재사용하기 위함)
FIELD_MAP = {
    "COMPANY": "CMPNY_NM",
    "TITLE": "JO_SJ",
    "CAREER": "CAREER_CND_NM",
    "REG_DT": "JO_REG_DT",
    "CLOSE_DT": "RCEPT_CLOS_NM",
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

# ── 추적할 키워드: 카테고리별로 분리 ──
KEYWORD_CATEGORIES = {
    "기술/역량": [
        "인공지능", "AI", "ChatGPT", "챗GPT", "생성형AI", "LLM",
        "머신러닝", "딥러닝", "데이터분석", "빅데이터", "클라우드",
        "Python", "파이썬", "React", "MLOps", "RAG", "프롬프트엔지니어링",
        "RPA", "자동화",
    ],
    "근무조건/복지": [
        "재택근무", "하이브리드근무", "유연근무", "주4일", "워라밸",
        "재택", "원격근무", "복지포인트", "스톡옵션", "성과급", "인센티브",
        "경력무관", "신입환영", "수습기간", "정규직전환", "MZ세대",
    ],
}
TREND_KEYWORDS = [kw for kws in KEYWORD_CATEGORIES.values() for kw in kws]
KEYWORD_TO_CATEGORY = {kw: cat for cat, kws in KEYWORD_CATEGORIES.items() for kw in kws}


def _strip_spaces(s: str) -> str:
    """비교용 정규화: 모든 공백(일반 공백 + 전각 공백) 제거."""
    return str(s).replace(" ", "").replace("\u3000", "")


def _parse_yy_date(series: pd.Series) -> pd.Series:
    """'26-08-05' / '채용시까지 26-10-04' 같은 2자리 연도 문자열을 datetime으로 변환."""
    raw = series.astype(str).str.extract(r"(\d{2}-\d{2}-\d{2})")[0]
    return pd.to_datetime("20" + raw, format="%Y-%m-%d", errors="coerce")


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
        filtered = [{k: r.get(k, "") for k in KEEP_COLS_NEW} for r in rows]
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
    오늘 날짜의 카테고리별 키워드 등장 빈도를 계산해서 keyword_trends.csv에 누적 저장한다.
    - 하루 여러 번 실행돼도 같은 날짜 데이터는 덮어써서 중복이 쌓이지 않는다.
    - 기존 날짜 데이터는 그대로 보존한다 (append 방식).
    - "주4일"/"주 4일"처럼 공백만 다른 표기는 매칭 전 정규화해서 하나로 합산한다.
    """
    today_str = pd.Timestamp.today().strftime("%Y-%m-%d")
    total_postings = len(df)

    if total_postings == 0 or "DTY_CN" not in df.columns:
        print("키워드 트렌드: 직무내용(DTY_CN) 데이터가 없어 건너뜁니다.")
        return

    text_norm = df["DTY_CN"].fillna("").astype(str).map(_strip_spaces)

    rows = []
    for kw in TREND_KEYWORDS:
        kw_norm = _strip_spaces(kw)
        count = int(text_norm.str.contains(kw_norm, case=False, regex=False, na=False).sum())
        rows.append({
            "date": today_str,
            "keyword": kw,
            "category": KEYWORD_TO_CATEGORY.get(kw, "기타"),
            "frequency": count,
            "total_postings": total_postings,
        })

    new_df = pd.DataFrame(rows)

    if os.path.exists(KEYWORD_TRENDS_FILE):
        old_df = pd.read_csv(KEYWORD_TRENDS_FILE, encoding="utf-8-sig")
        old_df = old_df[old_df["date"] != today_str]
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.sort_values(["date", "keyword"]).reset_index(drop=True)
    combined.to_csv(KEYWORD_TRENDS_FILE, index=False, encoding="utf-8-sig")

    days_collected = combined["date"].nunique()
    print(f"키워드 트렌드 저장 완료: {KEYWORD_TRENDS_FILE}")
    print(f"  누적 일수: {days_collected}일 / 총 {len(combined)}행")

def save_to_db(df: pd.DataFrame):
    engine = create_engine(DATABASE_URL)
    db_df = df.copy()
    db_df["collected_at"] = pd.Timestamp.today().normalize().date()

    # DB 테이블에 없는 컬럼은 자동으로 제외 (예: JO_REQST_NO 등)
    db_cols = [
        "CMPNY_NM", "JO_SJ", "CAREER_CND_NM", "JO_REG_DT", "RCEPT_CLOS_NM",
        "JOBCODE_NM", "DTY_CN", "EMPLYM_STLE_CMMN_MM", "HOPE_WAGE",
        "WORK_PARAR_BASS_ADRES_CN", "WORK_TIME_NM", "RET_GRANTS_NM",
        "WELFARE_CN", "MNGR_PHON_NO", "JO_FEINSR_SBSCRB_NM",
        "직무상세성점수", "기업소개점수", "급여품질점수", "복지점수",
        "근무조건점수", "출퇴근편의점수", "종합점수", "등급",
        "연금매칭", "연금평균연봉", "연금가입자수", "collected_at",
    ]
    existing_cols = [c for c in db_cols if c in db_df.columns]
    db_df = db_df[existing_cols]

    with engine.begin() as conn:
        conn.execute(text('TRUNCATE TABLE jobs'))
        db_df.to_sql("jobs", conn, if_exists="append", index=False, method="multi", chunksize=500)

    print(f"DB 저장 완료: {len(db_df)}건")

def main():
    rows = fetch_all_jobs()
    if not rows:
        print("수집 실패 — 기존 CSV 유지")
        sys.exit(0)

    df = pd.DataFrame(rows)
    df = df.rename(columns=FIELD_MAP)
    df["JO_REG_DT"] = _parse_yy_date(df.get("JO_REG_DT", pd.Series(dtype=str)))

    # 마감 공고 필터링
    close_date_parsed = _parse_yy_date(df["RCEPT_CLOS_NM"])
    today = pd.Timestamp.today().normalize()
    mask_keep = close_date_parsed.isna() | (close_date_parsed >= today)
    df = df[mask_keep].copy()
    print(f"마감 필터 후: {len(df)}건")

    # 스코어링
    print("스코어링 시작...")
    df = apply_joblens_scores(df)
    print(f"스코어링 완료: {len(df)}건")

    # ── 키워드 트렌드 누적 저장 (CSV 저장 전, 필터링된 df 기준) ──
    update_keyword_trends(df)

    # ── 국민연금 급여 데이터 매칭 (신규/재조회 대상만, 호출 한도 보호) ──
    try:
        update_pension_cache(df, PENSION_CACHE_FILE)
    except Exception as e:
        print(f"연금 급여 매칭 중 오류 (건너뜀): {e}")

        # ── 국민연금 급여 데이터 매칭 (신규/재조회 대상만, 호출 한도 보호) ──
    try:
        update_pension_cache(df, PENSION_CACHE_FILE)
    except Exception as e:
        print(f"연금 급여 매칭 중 오류 (건너뜀): {e}")

    # ── 갱신된 연금 캐시를 df에 반영 (CSV/DB 저장 전) ──  ← 여기부터 새로 추가
    if os.path.exists(PENSION_CACHE_FILE):
        pension_cache = pd.read_csv(PENSION_CACHE_FILE, encoding="utf-8-sig")
        pension_cache = pension_cache.rename(columns={
            "company_name": "CMPNY_NM",
            "matched": "연금매칭",
            "avg_annual_salary": "연금평균연봉",
            "subscribers": "연금가입자수",
        })[["CMPNY_NM", "연금매칭", "연금평균연봉", "연금가입자수"]]
        df = df.merge(pension_cache, on="CMPNY_NM", how="left")
        df["연금매칭"] = df["연금매칭"].fillna(False)
        print(f"연금 데이터 병합 완료: 매칭 {int(df['연금매칭'].sum())}건 / 전체 {len(df)}건")
    else:
        df["연금매칭"] = False
        df["연금평균연봉"] = None
        df["연금가입자수"] = None
    # ── 여기까지 새로 추가 ──

    # CSV 저장 (기존 로직 그대로, 매번 덮어씀 — 현재 스냅샷용)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_FILE}")
    print(f"평균 점수: {df['종합점수'].mean():.2f}")
    print(f"등급 분포:\n{df['등급'].value_counts()}")

    # ── DB 저장 ──
    if DATABASE_URL:
        try:
            save_to_db(df)
        except Exception as e:
            print(f"DB 저장 중 오류 (CSV는 정상 저장됨): {e}")
    else:
        print("DATABASE_URL 미설정 — DB 저장 건너뜀")
    # CSV 저장 (기존 로직 그대로, 매번 덮어씀 — 현재 스냅샷용)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_FILE}")
    print(f"평균 점수: {df['종합점수'].mean():.2f}")
    print(f"등급 분포:\n{df['등급'].value_counts()}")

    # ── DB 저장 (신규 — CSV와 병행, 안전장치) ──
    if DATABASE_URL:
        try:
            save_to_db(df)
        except Exception as e:
            print(f"DB 저장 중 오류 (CSV는 정상 저장됨): {e}")
    else:
        print("DATABASE_URL 미설정 — DB 저장 건너뜀")


if __name__ == "__main__":
    main()
