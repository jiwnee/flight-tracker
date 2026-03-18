# ✈️ 직항 모니터링 (Flight Tracker)

원하는 구간의 항공편을 12시간마다 자동으로 확인하고,
Discord + ntfy로 즉시 알림을 보냅니다.

- **데이터 소스**: SerpApi Google Flights
- **기본 설정**: BIO(빌바오) → OPO(포르투), 2026-06-29, 15:00 이전 출발, 직항만
- **실행 주기**: .github/workflows/check-flight.yml 설정 파일 확인
- **알림 방식**: GitHub Actions에서 `--force` 모드로 실행 (매 실행마다 알림 발송)
- **설정 파일**: `config.json`으로 노선/조건 변경 가능

---

## 동작 확인 (수동 테스트)

GitHub 레포 → **Actions** 탭 → **BIO→OPO 직항 모니터링** → **Run workflow**

로그에서 아래처럼 나오면 정상:
```
[2026-03-17 10:00 KST] BIO→OPO 직항 확인 시작
  이전 상태: UNKNOWN
  현재 상태: NOT_FOUND (0편)
  [--force 모드] 상태 무관하게 알림 발송
  [Discord] 알림 전송 완료 ✓
  [ntfy] 알림 전송 완료 ✓
  완료
```

---

## 🔔 알림 예시 (3가지 케이스)

GitHub Actions는 `--force` 모드로 실행되어 매번 알림을 발송합니다.

### 케이스 1: 직항이 생김
> **[ROUTE OPEN] BIO → OPO 직항 감지**
>
> 🛫 **BIO → OPO 직항이 생겼습니다!**
>
> 📅 날짜: 2026-06-29
> ⏰ 15:00 이전 출발 편수: 1편
>
> **VY 1234** | 10:30 → 12:10 | 89.99 EUR

### 케이스 2: 직항이 사라짐
> **[ROUTE CLOSED] BIO → OPO 직항 사라짐**
>
> BIO → OPO (2026-06-29) 직항이 더 이상 검색되지 않습니다.
> (15:00 이전 출발 기준)

### 케이스 3: 직항 없음 (체크용)
> **[CHECK] BIO → OPO 직항 없음**
>
> BIO → OPO (2026-06-29) 직항편이 검색되지 않습니다.
> (15:00 이전 출발 기준)

---

## 📁 파일 구조

```
flight-tracker/
├── check_flight.py              # 메인 스크립트
├── config.json                  # 노선 설정 (git 제외)
├── config.example.json          # 설정 파일 템플릿
├── state.json                   # 실행 상태 저장 (git 제외, 자동 생성)
├── state.example.json           # 상태 파일 템플릿
├── .env                         # 환경변수 (git 제외)
├── .env.example                 # 환경변수 템플릿
├── requirements.txt
├── README.md
└── .github/
    └── workflows/
        └── check-flight.yml     # GitHub Actions 스케줄 설정
```

**주의:** `config.json`, `state.json`, `.env`는 `.gitignore`에 포함되어 git에 올라가지 않습니다.

---

## 로컬 테스트

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 설정 파일 생성
cp config.example.json config.json
cp .env.example .env

# 3. config.json 편집 (원하는 노선으로 변경)
{
  "origin": "BIO",
  "destination": "OPO",
  "target_date": "2026-06-29",
  "departure_before": "15:00",
  "type": "2",    // 1=round-trip, 2=one-way, 3=multi-city
  "stops": "1"    // 0=any, 1=nonstop only, 2=1 stop max, 3=2 stops max
}

# 4. .env 편집 (API 키 입력)
SERPAPI_KEY=여기에_발급받은_키_입력
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
NTFY_TOPIC=bio-opo-alert

# 테스트 실행
python check_flight.py --mock-found --force  # 알림 연동 테스트
python check_flight.py --test                # API 조회만 (알림 없음)
python check_flight.py                       # 정상 실행
```

---

## 🔄 GitHub Actions 동작 방식

- **실행 주기**: 12시간마다 (01:00, 13:00 UTC)
- **실행 모드**: `--force` (매번 알림 발송)
- **알림 발송**: 상태와 무관하게 매 실행마다 현재 상황 알림
  - 직항 있음 → "직항 생김" 알림
  - 직항 없음 (이전에 있었음) → "직항 사라짐" 알림
  - 직항 없음 (계속 없음) → "직항 없음" 체크 알림
