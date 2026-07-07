# -*- coding: utf-8 -*-
"""
JobLens Flask 백엔드 서버
- 서울시 오픈API 실시간 채용공고 수집
- JobLens 스코어링
- REST API 제공
"""

import os
from flask import Flask, jsonify, request, send_from_directory, make_response
from flask_cors import CORS
from flask_compress import Compress
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
from datetime import datetime, timedelta
import threading
import time
import logging

from joblens_scoring import apply_joblens_scores

# ──────────────────────────────────────
# 초기화
# ──────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
CORS(app)
Compress(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = "514b4b685a6d696e39366469694276"
CACHE_TTL = 3600
CSV_FALLBACK = os.path.join(BASE_DIR, "JobLens_Scores.csv")
KEYWORD_TRENDS_FILE = os.path.join(BASE_DIR, "keyword_trends.csv")

def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_cache = {
    "df": None,
    "last_updated": None,
    "is_loading": False,
    "data_source": "none",
}


def load_from_csv() -> pd.DataFrame:
    if not os.path.exists(CSV_FALLBACK):
        logger.warning(f"CSV 파일 없음: {CSV_FALLBACK}")
        return pd.DataFrame()
    logger.info(f"CSV 폴백 로드: {CSV_FALLBACK}")
    df = pd.read_csv(CSV_FALLBACK, encoding="utf-8-sig")
    df["JO_REG_DT"] = pd.to_datetime(df.get("JO_REG_DT", pd.Series(dtype=str)), errors="coerce")

    if "종합점수" not in df.columns:
        df = apply_joblens_scores(df)

    close_date = df["RCEPT_CLOS_NM"].astype(str).str.extract(r"(\d{4}-\d{2}-\d{2})")[0]
    close_date = pd.to_datetime(close_date, errors="coerce")
    today = pd.Timestamp.today().normalize()
    df = df[close_date.isna() | (close_date >= today)]

    valid_dates = df["JO_REG_DT"].notna().sum()
    logger.info(f"CSV 로드 완료: {len(df)}건 (등록일 유효: {valid_dates}건, 결측: {len(df) - valid_dates}건)")
    return df


def fetch_seoul_jobs() -> pd.DataFrame:
    logger.info("서울 Open API 수집 시작")

    KEEP_COLS = [
        "JO_REQST_NO","CMPNY_NM","JO_SJ","JOBCODE_NM","CAREER_CND_NM",
        "ACDMCR_NM","EMPLYM_STLE_CMMN_MM","HOPE_WAGE",
        "WORK_PARAR_BASS_ADRES_CN","SUBWAY_NM","WORK_TIME_NM",
        "HOLIDAY_NM","WEEK_WORK_HR","RCEPT_CLOS_NM","RCEPT_MTH_NM",
        "PRESENTN_PAPERS_NM","MNGR_PHON_NO","BSNS_SUMRY_CN","DTY_CN",
        "RET_GRANTS_NM","JO_FEINSR_SBSCRB_NM","JO_REG_DT","WELFARE_CN",
    ]

    import socket, gc
    try:
        s = socket.create_connection(("openapi.seoul.go.kr", 8088), timeout=5)
        s.close()
    except OSError:
        logger.warning("포트 8088 차단 감지 → CSV 폴백으로 전환합니다.")
        return load_from_csv()

    all_rows = []
    start = 1
    session = make_session()
    BASE_URL = f"http://openapi.seoul.go.kr:8088/{API_KEY}/json/GetJobInfo"

    while True:
        end = start + 999
        url = f"{BASE_URL}/{start}/{end}"
        try:
            res = session.get(url, timeout=30)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            logger.error(f"API 요청 실패 ({start}~{end}): {e}")
            break

        if "GetJobInfo" not in data or "row" not in data.get("GetJobInfo", {}):
            result = data.get("GetJobInfo", {}).get("RESULT", {})
            code = result.get("CODE", "")
            msg = result.get("MESSAGE", "")
            if code and code != "INFO-000":
                logger.warning(f"API 응답 오류: {code} — {msg}")
            break

        rows = data["GetJobInfo"]["row"]
        if not rows:
            break

        filtered = [{k: r.get(k, "") for k in KEEP_COLS} for r in rows]
        all_rows.extend(filtered)
        logger.info(f"  수집 누적: {len(all_rows)}건")
        start += 1000

        if start > 100_000:
            break

        time.sleep(0.2)
        gc.collect()

    if not all_rows:
        logger.warning("API 수집 실패 → CSV 폴백으로 전환합니다.")
        return load_from_csv()

    logger.info("DataFrame 변환 중...")
    df = pd.DataFrame(all_rows)
    del all_rows
    gc.collect()

    df["JO_REG_DT"] = pd.to_datetime(df.get("JO_REG_DT", pd.Series(dtype=str)), errors="coerce")
    close_date = df["RCEPT_CLOS_NM"].astype(str).str.extract(r"(\d{4}-\d{2}-\d{2})")[0]
    close_date = pd.to_datetime(close_date, errors="coerce")
    today = pd.Timestamp.today().normalize()
    df = df[close_date.isna() | (close_date >= today)].copy()
    gc.collect()

    logger.info(f"스코어링 시작... ({len(df)}건)")
    df = apply_joblens_scores(df)
    gc.collect()

    valid_dates = df["JO_REG_DT"].notna().sum()
    logger.info(f"스코어링 완료: {len(df)}건 (등록일 유효: {valid_dates}건, 결측: {len(df) - valid_dates}건)")
    return df


def refresh_cache(csv_first: bool = False):
    logger.info(f"refresh_cache 시작: csv_first={csv_first}")

    if _cache["is_loading"]:
        return
    _cache["is_loading"] = True
    try:
        if csv_first and os.path.exists(CSV_FALLBACK):
            logger.info("CSV 즉시 로드 시작...")
            df_csv = load_from_csv()
            if not df_csv.empty:
                _cache["df"] = df_csv
                _cache["last_updated"] = datetime.now()
                _cache["data_source"] = "csv"
                _cache["is_loading"] = False
                logger.info(f"캐시 저장 완료: total={len(_cache['df'])}, source={_cache['data_source']}")
                logger.info(f"CSV 로드 완료: {len(df_csv)}건 → 웹사이트 즉시 서비스 가능")
                threading.Thread(target=_refresh_from_api, daemon=True).start()
                return

        _refresh_from_api()

    except Exception as e:
        logger.error(f"캐시 갱신 실패: {e}")
        _cache["is_loading"] = False


def _refresh_from_api():
    try:
        logger.info("API 백그라운드 수집 시작...")
        df = fetch_seoul_jobs()
        if not df.empty:
            _cache["df"] = df
            _cache["last_updated"] = datetime.now()
            _cache["data_source"] = "api"
            logger.info(f"API 수집 완료: {len(df)}건 → 캐시 교체")
    except Exception as e:
        logger.error(f"API 수집 실패: {e}")
    finally:
        _cache["is_loading"] = False


def get_df(period: str = "all") -> pd.DataFrame:
    now = datetime.now()
    if _cache["df"] is None:
        refresh_cache(csv_first=True)
    elif (now - _cache["last_updated"]).seconds > CACHE_TTL:
        threading.Thread(target=refresh_cache, kwargs={"csv_first": True}, daemon=True).start()

    df = _cache["df"]
    if df is None or df.empty:
        return pd.DataFrame()

    # ── 기간 필터 ──
    # ⚠️ 이전 버전은 날짜가 없는 행(isna)을 무조건 포함시켜서
    #    "최근 7일/30일" 필터를 걸어도 결과가 거의 안 바뀌는 버그가 있었음.
    #    날짜 없는 행은 더 이상 무조건 통과시키지 않는다.
    if period != "all" and "JO_REG_DT" in df.columns:
        days = int(period)
        cutoff = pd.Timestamp.today() - pd.Timedelta(days=days)
        valid_dates = df["JO_REG_DT"].notna().sum()
        logger.info(f"[get_df] period={period} 적용 전 {len(df)}건, 유효 날짜 {valid_dates}건, cutoff={cutoff.date()}")
        df = df[df["JO_REG_DT"] >= cutoff]
        logger.info(f"[get_df] period={period} 적용 후 {len(df)}건")

    return df


def df_to_records(df: pd.DataFrame, limit: int = None) -> list:
    cols = [
        "CMPNY_NM", "JO_SJ", "JOBCODE_NM", "CAREER_CND_NM", "ACDMCR_NM",
        "EMPLYM_STLE_CMMN_MM", "HOPE_WAGE", "WORK_PARAR_BASS_ADRES_CN",
        "WORK_TIME_NM", "HOLIDAY_NM", "WEEK_WORK_HR", "RCEPT_CLOS_NM",
        "RCEPT_MTH_NM", "PRESENTN_PAPERS_NM", "MNGR_PHON_NO",
        "BSNS_SUMRY_CN", "DTY_CN", "RET_GRANTS_NM", "JO_FEINSR_SBSCRB_NM",
        "JO_REG_DT", "JO_REQST_NO",
        "직무상세성점수", "기업소개점수", "급여품질점수",
        "복지점수", "근무조건점수", "출퇴근편의점수",
        "종합점수", "등급",
    ]
    existing = [c for c in cols if c in df.columns]
    sub = df[existing].copy()

    if "JO_REG_DT" in sub.columns:
        sub["JO_REG_DT"] = sub["JO_REG_DT"].dt.strftime("%Y-%m-%d").fillna("")

    sub = sub.fillna("")
    if limit:
        sub = sub.head(limit)

    return sub.to_dict(orient="records")


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html", mimetype="text/html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/status")
def api_status():
    logger.info(
        f"STATUS 호출: total={0 if _cache['df'] is None else len(_cache['df'])}, "
        f"source={_cache['data_source']}, loading={_cache['is_loading']}"
    )
    return jsonify({
        "status": "ok",
        "last_updated": _cache["last_updated"].strftime("%Y-%m-%d %H:%M:%S") if _cache["last_updated"] else None,
        "total": len(_cache["df"]) if _cache["df"] is not None else 0,
        "is_loading": _cache["is_loading"],
        "data_source": _cache["data_source"],
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if _cache["is_loading"]:
        return jsonify({"status": "already_loading"})
    threading.Thread(target=refresh_cache, kwargs={"csv_first": True}, daemon=True).start()
    return jsonify({"status": "refresh_started"})


@app.route("/api/stats")
def api_stats():
    period = request.args.get("period", "all")
    df = get_df(period)
    if df.empty:
        return jsonify({"error": "no data"}), 503

    today = pd.Timestamp.today()
    recent7 = int((df["JO_REG_DT"] >= today - pd.Timedelta(days=7)).sum()) if "JO_REG_DT" in df.columns else 0

    score_cols = {
        "직무상세성": "직무상세성점수", "기업소개": "기업소개점수", "급여품질": "급여품질점수",
        "복지": "복지점수", "근무조건": "근무조건점수", "출퇴근편의": "출퇴근편의점수",
    }
    score_avgs = {k: round(float(df[v].mean()), 2) for k, v in score_cols.items() if v in df.columns}
    grade_dist = df["등급"].value_counts().to_dict() if "등급" in df.columns else {}
    job_top10 = df["JOBCODE_NM"].value_counts().head(10).to_dict() if "JOBCODE_NM" in df.columns else {}
    career_dist = df["CAREER_CND_NM"].value_counts().to_dict() if "CAREER_CND_NM" in df.columns else {}

    hist = {}
    if "종합점수" in df.columns:
        scores = df["종합점수"].dropna()
        for lo in range(30, 100, 5):
            hist[f"{lo}-{lo+5}"] = int(((scores >= lo) & (scores < lo+5)).sum())

    trend = []
    if "JO_REG_DT" in df.columns:
        cutoff = today - pd.Timedelta(days=90)
        daily = (
            df[df["JO_REG_DT"] >= cutoff]
            .groupby(df["JO_REG_DT"].dt.date)
            .size()
            .reset_index(name="count")
        )
        trend = [{"date": str(r["JO_REG_DT"]), "count": int(r["count"])} for _, r in daily.iterrows()]

    resp = make_response(jsonify({
        "total": len(df),
        "avg_score": round(float(df["종합점수"].mean()), 2) if "종합점수" in df.columns else 0,
        "recent7": recent7,
        "grade_dist": grade_dist,
        "job_top10": job_top10,
        "career_dist": career_dist,
        "score_cols_avg": score_avgs,
        "hist": hist,
        "trend": trend,
        "last_updated": _cache["last_updated"].strftime("%Y-%m-%d %H:%M") if _cache["last_updated"] else "",
        "data_source": _cache["data_source"],
    }))
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@app.route("/api/jobs")
def api_jobs():
    period = request.args.get("period", "all")
    keyword = request.args.get("keyword", "").strip().lower()
    grade = request.args.get("grade", "")
    career = request.args.get("career", "")
    job = request.args.get("job", "")
    page = int(request.args.get("page", 1))
    per = int(request.args.get("per", 20))

    df = get_df(period)
    if df.empty:
        return jsonify({"total": 0, "jobs": [], "pages": 0})

    if keyword:
        mask = (
            df["CMPNY_NM"].astype(str).str.lower().str.contains(keyword, na=False) |
            df["JO_SJ"].astype(str).str.lower().str.contains(keyword, na=False)
        )
        df = df[mask]
    if grade:
        df = df[df["등급"] == grade]
    if career:
        df = df[df["CAREER_CND_NM"] == career]
    if job:
        df = df[df["JOBCODE_NM"] == job]

    total = len(df)
    start = (page - 1) * per
    page_df = df.iloc[start:start + per]

    return jsonify({
        "total": total,
        "pages": (total + per - 1) // per,
        "page": page,
        "jobs": df_to_records(page_df),
    })


@app.route("/api/jobs/top")
def api_jobs_top():
    n = int(request.args.get("n", 100))
    period = request.args.get("period", "all")
    df = get_df(period)
    if df.empty:
        return jsonify([])
    top = df.sort_values("종합점수", ascending=False).head(n)
    return jsonify(df_to_records(top))


@app.route("/api/jobs/recent")
def api_jobs_recent():
    n = int(request.args.get("n", 5))
    df = get_df()
    if df.empty or "JO_REG_DT" not in df.columns:
        return jsonify([])
    recent = df.sort_values("JO_REG_DT", ascending=False).head(n)
    return jsonify(df_to_records(recent))


@app.route("/api/filters")
def api_filters():
    df = get_df()
    if df.empty:
        return jsonify({"jobs": [], "careers": [], "grades": []})
    return jsonify({
        "jobs": sorted(df["JOBCODE_NM"].dropna().unique().tolist()),
        "careers": sorted(df["CAREER_CND_NM"].dropna().unique().tolist()),
        "grades": sorted(df["등급"].dropna().unique().tolist()),
    })

@app.route("/api/keyword_trends")
def api_keyword_trends():
    if not os.path.exists(KEYWORD_TRENDS_FILE):
        return jsonify({"days_collected": 0, "keywords": [], "top_snapshot": [], "data": []})

    trends_df = pd.read_csv(KEYWORD_TRENDS_FILE, encoding="utf-8-sig")
    if trends_df.empty:
        return jsonify({"days_collected": 0, "keywords": [], "top_snapshot": [], "data": []})

    trends_df["frequency_per_1000"] = (
        trends_df["frequency"] / trends_df["total_postings"].replace(0, 1) * 1000
    ).round(2)

    dates = sorted(trends_df["date"].unique().tolist())
    keywords = sorted(trends_df["keyword"].unique().tolist())

    latest_date = dates[-1]
    latest = trends_df[trends_df["date"] == latest_date].sort_values("frequency", ascending=False)
    top_snapshot = latest[["keyword", "frequency", "frequency_per_1000"]].to_dict(orient="records")

    resp = make_response(jsonify({
        "days_collected": len(dates),
        "date_range": {"from": dates[0], "to": dates[-1]},
        "keywords": keywords,
        "top_snapshot": top_snapshot,
        "data": trends_df.to_dict(orient="records"),
    }))
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@app.route("/api/top100/stats")
def api_top100_stats():
    df = get_df()
    if df.empty:
        return jsonify({})
    top100 = df.sort_values("종합점수", ascending=False).head(100)

    score_dist = {}
    for lo in range(80, 100, 5):
        hi = lo + 5
        score_dist[f"{lo}-{hi}"] = int(((top100["종합점수"] >= lo) & (top100["종합점수"] < hi)).sum())

    return jsonify({
        "avg": round(float(top100["종합점수"].mean()), 1),
        "S_count": int((top100["등급"] == "S").sum()),
        "job_types": int(top100["JOBCODE_NM"].nunique()),
        "job_top10": top100["JOBCODE_NM"].value_counts().head(10).to_dict(),
        "co_top10": top100["CMPNY_NM"].value_counts().head(10).to_dict(),
        "score_dist": score_dist,
    })


@app.before_request
def init_cache_once():
    if _cache["df"] is None and not _cache["is_loading"]:
        logger.info("첫 요청 → CSV 캐시 초기화")
        refresh_cache(csv_first=True)


if __name__ == "__main__":
    logger.info("JobLens 서버 시작 — CSV 즉시 로드 후 API 백그라운드 갱신")
    threading.Thread(target=lambda: refresh_cache(csv_first=True), daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)
