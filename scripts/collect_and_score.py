    trend_df = pd.DataFrame(rows)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM keyword_trends WHERE date = :today"), {"today": today_str})
        trend_df.to_sql(
            "keyword_trends",
            conn,
            if_exists="append",
            index=False,
            method="multi",
            dtype={"date": Date()},
        )
    print(f"키워드 트렌드 DB 저장 완료: {len(trend_df)}행")
    
def save_to_db(df: pd.DataFrame):
    engine = create_engine(DATABASE_URL)
    db_df = df.copy()
    db_df["collected_at"] = pd.Timestamp.today().normalize().date()

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

    today_str = pd.Timestamp.today().normalize().date()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM jobs WHERE collected_at = :today"), {"today": today_str})
        db_df.to_sql("jobs", conn, if_exists="append", index=False, method="multi", chunksize=500)

    print(f"DB 저장 완료: {len(db_df)}건 (collected_at={today_str})")

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
    update_keyword_trends_db(df)
    
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
