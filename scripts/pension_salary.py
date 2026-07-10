# -*- coding: utf-8 -*-
"""
국민연금 사업장 데이터 기반 급여 추정 모듈
- 공공데이터포털(odcloud.kr) "국민연금공단_국민연금 가입 사업장 내역" API 사용
- 회사명을 정규화해서 '완전히 일치'할 때만 매칭 (프랜차이즈/대리점/별도법인 오매칭 방지)
- 한 번 조회한 회사는 CSV 캐시에 저장해두고, 다음 실행부터는 새 회사만 조회 (API 호출 한도 보호)
"""
import os
import re
import time
import requests
import pandas as pd

PENSION_API_KEY = os.environ.get("NPS_API_KEY", "")
PENSION_UUID = "b2243a59-a261-4dc6-a4f3-cfcbc478d231"  # 국민연금 가입 사업장 내역 최신월
PENSION_BASE_URL = f"https://api.odcloud.kr/api/15083277/v1/uddi:{PENSION_UUID}"

# 한 번 실행(GitHub Actions 1회)당 최대 조회할 신규 회사 수.
# 개발계정 일일 호출 한도(10,000) 보호 + 실행시간 관리를 위한 안전장치.
# 하루 2회 실행 기준 3000 × 2 = 6000/일 (한도의 60%) → 회사 16,594개 기준 약 3일이면 전체 완료.
MAX_LOOKUPS_PER_RUN = 3000
# 매칭 실패한 회사도 이 기간(일) 동안은 재조회하지 않음 (같은 실패를 매번 반복 조회하지 않도록)
RECHECK_UNMATCHED_AFTER_DAYS = 30
# 매칭 성공한 회사는 이 기간(일)마다 한 번씩만 갱신 (월간 데이터라 매일 다시 조회할 필요 없음)
RECHECK_MATCHED_AFTER_DAYS = 25


def normalize_company_name(name: str) -> str:
    """법인 표기(주식회사/㈜/(주) 등)와 공백을 제거해서 비교 가능한 형태로 정규화."""
    if not name:
        return ""
    s = str(name)
    s = re.sub(r'\(주\)|\(유\)|\(재\)|\(사\)|㈜|주식회사|유한회사|재단법인|사단법인|합자회사|합명회사', '', s)
    s = re.sub(r'\s+', '', s)
    return s.strip()


def fetch_pension_salary(company_name: str, session: requests.Session):
    """
    회사명으로 국민연금 사업장 데이터를 조회해서, 정규화 후 완전히 일치하는
    사업장이 있으면 추정 급여 정보를 반환한다. 없으면 None.
    """
    if not PENSION_API_KEY or not company_name:
        return None

    params = {
        "page": 1,
        "perPage": 100,
        "cond[사업장명::LIKE]": company_name,
        "serviceKey": PENSION_API_KEY,
    }
    try:
        res = session.get(PENSION_BASE_URL, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        print(f"  [연금 조회 실패] {company_name}: {e}")
        return None

    candidates = data.get("data", [])
    if not candidates:
        return None

    target = normalize_company_name(company_name)
    match = None
    for c in candidates:
        if normalize_company_name(c.get("사업장명", "")) == target:
            match = c
            break

    if not match:
        return None

    subscribers = match.get("가입자수") or 0
    billing = match.get("당월고지금액") or 0
    if subscribers <= 0:
        return None

    per_person_billing = billing / subscribers
    avg_monthly_salary = per_person_billing / 0.09

    return {
        "matched_name": match.get("사업장명", ""),
        "subscribers": int(subscribers),
        "avg_monthly_salary": round(avg_monthly_salary),
        "avg_annual_salary": round(avg_monthly_salary * 12),
        "data_month": match.get("자료생성년월", ""),
    }


def update_pension_cache(df: pd.DataFrame, cache_file: str):
    """
    df(현재 수집된 채용공고)의 회사명 목록을 기준으로 국민연금 급여 추정치 캐시를 갱신한다.
    - 캐시에 없는 회사, 혹은 오래된(재조회 주기 지난) 회사만 새로 조회
    - 조회 결과는 성공/실패 모두 캐시에 기록해서 다음 실행부터는 불필요한 재조회를 하지 않음
    """
    if "CMPNY_NM" not in df.columns:
        print("연금 급여 매칭: 회사명(CMPNY_NM) 컬럼이 없어 건너뜁니다.")
        return

    if not PENSION_API_KEY:
        print("연금 급여 매칭: NPS_API_KEY 환경변수가 없어 건너뜁니다.")
        return

    today = pd.Timestamp.today().normalize()
    companies = sorted(set(df["CMPNY_NM"].dropna().astype(str).str.strip()))
    companies = [c for c in companies if c]

    if os.path.exists(cache_file):
        cache = pd.read_csv(cache_file, encoding="utf-8-sig")
    else:
        cache = pd.DataFrame(columns=[
            "company_name", "matched", "matched_name", "subscribers",
            "avg_monthly_salary", "avg_annual_salary", "data_month", "checked_date"
        ])

    cache_map = {row["company_name"]: row for _, row in cache.iterrows()}

    def needs_lookup(company):
        row = cache_map.get(company)
        if row is None:
            return True
        checked = pd.to_datetime(row.get("checked_date"), errors="coerce")
        if pd.isna(checked):
            return True
        days_since = (today - checked).days
        if row.get("matched"):
            return days_since >= RECHECK_MATCHED_AFTER_DAYS
        return days_since >= RECHECK_UNMATCHED_AFTER_DAYS

    to_lookup = [c for c in companies if needs_lookup(c)][:MAX_LOOKUPS_PER_RUN]
    print(f"연금 급여 매칭: 전체 회사 {len(companies)}개 중 이번 실행에서 {len(to_lookup)}개 신규/재조회")

    if not to_lookup:
        print("연금 급여 매칭: 새로 조회할 회사가 없습니다 (캐시가 최신 상태).")
        return

    session = requests.Session()
    new_rows = []
    matched_count = 0
    for i, company in enumerate(to_lookup):
        result = fetch_pension_salary(company, session)
        if result:
            matched_count += 1
            new_rows.append({
                "company_name": company, "matched": True,
                "matched_name": result["matched_name"],
                "subscribers": result["subscribers"],
                "avg_monthly_salary": result["avg_monthly_salary"],
                "avg_annual_salary": result["avg_annual_salary"],
                "data_month": result["data_month"],
                "checked_date": today.strftime("%Y-%m-%d"),
            })
        else:
            new_rows.append({
                "company_name": company, "matched": False,
                "matched_name": "", "subscribers": None,
                "avg_monthly_salary": None, "avg_annual_salary": None,
                "data_month": "", "checked_date": today.strftime("%Y-%m-%d"),
            })
        if (i + 1) % 50 == 0:
            print(f"  ...{i+1}/{len(to_lookup)}건 조회 완료 (매칭 {matched_count}건)")
        time.sleep(0.15)  # API 과호출 방지

    new_df = pd.DataFrame(new_rows)
    if not cache.empty:
        cache = cache[~cache["company_name"].isin(new_df["company_name"])]
        combined = pd.concat([cache, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(cache_file, index=False, encoding="utf-8-sig")
    total_matched = int(combined["matched"].sum()) if "matched" in combined.columns else 0
    print(f"연금 급여 매칭 완료: 캐시 총 {len(combined)}개 회사, 그중 매칭 성공 {total_matched}개 "
          f"({total_matched/len(combined)*100:.1f}%)")
