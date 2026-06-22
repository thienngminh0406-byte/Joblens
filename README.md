# JobLens — Flask 풀스택 버전

서울시 오픈API에서 실시간으로 채용공고를 수집하고
JobLens Score로 정보 품질을 분석하는 HR Analytics 웹앱입니다.

## 폴더 구조

```
joblens-app/
├── app.py               ← Flask 백엔드 서버
├── joblens_scoring.py   ← 스코어링 모듈
├── requirements.txt     ← 패키지 목록
├── README.md
└── static/
    └── index.html       ← 프론트엔드 (자동 서빙)
```

## 실행 방법

### 1. 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. 서버 실행
```bash
python app.py
```

### 3. 브라우저 접속
```
http://localhost:5000
```

## 주요 API 엔드포인트

| 경로 | 설명 |
|------|------|
| GET  /api/status        | 서버 상태 및 캐시 정보 |
| POST /api/refresh       | 수동 데이터 갱신 트리거 |
| GET  /api/stats         | 시장 통계 (기간 필터: ?period=7/30/90/all) |
| GET  /api/jobs          | 채용공고 목록 (페이지네이션 + 필터) |
| GET  /api/jobs/top      | 상위 N개 공고 (?n=100) |
| GET  /api/jobs/recent   | 최근 등록 공고 (?n=5) |
| GET  /api/filters       | 필터 옵션 (직무/경력/등급 목록) |
| GET  /api/top100/stats  | Top100 통계 |

## 데이터 수집 주기

- 서버 시작 시 자동으로 서울시 오픈API 수집 시작
- 캐시 TTL: 1시간 (CACHE_TTL 변수로 조정 가능)
- TTL 만료 시 백그라운드에서 자동 갱신 (서비스 중단 없음)
- 네비게이션 바 "🔄 새로고침" 버튼으로 수동 갱신 가능

## API 키 변경

`app.py` 상단의 API_KEY 변수를 수정하세요:
```python
API_KEY = "여기에_인증키_입력"
```