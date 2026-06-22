# -*- coding: utf-8 -*-
"""
JobLens 스코어링 모듈
채용공고 DataFrame에 6개 항목 점수 + 종합점수 + 등급 컬럼을 추가합니다.
"""

import pandas as pd
import re

SCORE_COLS = [
    "직무상세성점수",
    "기업소개점수",
    "급여품질점수",
    "복지점수",
    "근무조건점수",
    "출퇴근편의점수",
]


def _score_duty(row) -> float:
    """1. 직무상세성 (0~30점)"""
    text = str(row.get("DTY_CN", "") or "")
    if len(text) < 10:
        return 5.0
    keywords = ["업무", "역할", "담당", "개발", "분석", "관리", "기획", "운영",
                "처리", "협업", "도구", "시스템", "성과", "책임", "보고", "지원"]
    hits = sum(1 for k in keywords if k in text)
    score = 5.0 + hits * 1.5 + (5.0 if len(text) > 200 else 0)
    return min(30.0, round(score, 1))


def _score_bsns(row) -> float:
    """2. 기업소개 (0~20점)"""
    text = str(row.get("BSNS_SUMRY_CN", "") or "")
    if len(text) < 10:
        return 5.0
    keywords = ["회사", "기업", "사업", "제조", "판매", "서비스", "팀",
                "문화", "성장", "규모", "직원", "비전", "조직", "연혁"]
    hits = sum(1 for k in keywords if k in text)
    score = 5.0 + hits * 1.2 + (3.0 if len(text) > 100 else 0)
    return min(20.0, round(score, 1))


def _score_wage(row) -> float:
    """3. 급여품질 (0~20점)"""
    wage = str(row.get("HOPE_WAGE", "") or "")
    if not wage or wage in ("", "nan"):
        return 10.0
    # 구체적 금액 명시
    if re.search(r"\d+만원|\d{3,},\d{3}", wage):
        return 20.0
    if re.search(r"연봉|월급|시급|일급|주급", wage):
        return 18.0
    if re.search(r"협의|면접|추후", wage):
        return 12.0
    return 10.0


def _score_welfare(row) -> float:
    """4. 복지 (0~15점)"""
    texts = [
        str(row.get("RET_GRANTS_NM", "") or ""),
        str(row.get("JO_FEINSR_SBSCRB_NM", "") or ""),
        str(row.get("WELFARE_CN", "") or ""),
    ]
    text = " ".join(texts)
    if len(text.strip()) < 5:
        return 5.0
    keywords = ["보험", "퇴직", "식대", "교통", "연차", "교육", "상여",
                "수당", "복지", "지원", "포인트", "휴가", "건강검진", "경조사"]
    hits = sum(1 for k in keywords if k in text)
    return min(15.0, round(5.0 + hits * 1.2, 1))


def _score_work(row) -> float:
    """5. 근무조건 (0~20점)"""
    score = 0.0
    if len(str(row.get("WORK_TIME_NM", "") or "")) > 5:
        score += 5.0
    if len(str(row.get("HOLIDAY_NM", "") or "")) > 2:
        score += 5.0
    if str(row.get("WEEK_WORK_HR", "") or "").strip():
        score += 4.0
    if len(str(row.get("WORK_PARAR_BASS_ADRES_CN", "") or "")) > 5:
        score += 6.0
    return min(20.0, round(score, 1))


def _score_access(row) -> float:
    """6. 출퇴근편의 (0~10점)"""
    score = 0.0
    method = str(row.get("RCEPT_MTH_NM", "") or "")
    docs   = str(row.get("PRESENTN_PAPERS_NM", "") or "")
    phone  = str(row.get("MNGR_PHON_NO", "") or "")
    addr   = str(row.get("WORK_PARAR_BASS_ADRES_CN", "") or "")
    subway = str(row.get("SUBWAY_NM", "") or "")

    if len(method) > 3:
        score += 3.0
    if len(docs) > 2:
        score += 2.0
    if len(phone) > 5:
        score += 2.0
    if subway.strip():
        score += 3.0
    elif re.search(r"역|정류장|버스|셔틀|지하철|도보", addr):
        score += 2.0

    return min(10.0, round(score, 1))


def _grade(score: float) -> str:
    if score >= 90:
        return "S"
    elif score >= 80:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 55:
        return "C"
    else:
        return "D"


def apply_joblens_scores(df: pd.DataFrame) -> pd.DataFrame:
    """DataFrame에 점수 컬럼과 등급 컬럼 추가"""
    df = df.copy()
    df["직무상세성점수"] = df.apply(_score_duty,    axis=1)
    df["기업소개점수"]   = df.apply(_score_bsns,    axis=1)
    df["급여품질점수"]   = df.apply(_score_wage,    axis=1)
    df["복지점수"]       = df.apply(_score_welfare, axis=1)
    df["근무조건점수"]   = df.apply(_score_work,    axis=1)
    df["출퇴근편의점수"] = df.apply(_score_access,  axis=1)
    df["종합점수"] = (
        df["직무상세성점수"] + df["기업소개점수"] + df["급여품질점수"] +
        df["복지점수"]       + df["근무조건점수"] + df["출퇴근편의점수"]
    ).round(1)
    df["등급"] = df["종합점수"].apply(_grade)
    return df