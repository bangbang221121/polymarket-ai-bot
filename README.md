# Polymarket AI Betting Bot

**⚠️ 중요 경고**
- 이 봇은 실제 자금을 사용합니다
- "높은 승률로 지속적인 수익"은 보장되지 않습니다
- 테스트 후 소액부터 시작하세요
- 연속 손실 가능성을 반드시 고려하세요

## 설치

```bash
# 1. 저장소 클론
git clone <repo-url>
cd polymarket-bot

# 2. 가상환경 생성
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 또는: venv\Scripts\activate  # Windows

# 3. 의존성 설치
pip install -r requirements.txt
```

## 환경 변수 설정

`.env` 파일을 생성하거나 환경 변수를 설정하세요:

```bash
# Polymarket API 인증 (https://polymarket.com/settings/api-keys)
export POLYMARKET_API_KEY="your_api_key"
export POLYMARKET_API_SECRET="your_api_secret"
export POLYMARKET_PASSPHRASE="your_passphrase"

# Polygon 지갑 프라이빗 키 (0x 제외)
export POLYMARKET_PRIVATE_KEY="abcdef1234567890..."

# AI 분석용 OpenAI API 키
export OPENAI_API_KEY="sk-..."

# 베팅 설정
export BET_AMOUNT="10"        # 한 번에 베팅할 금액 (USDT)
export MAX_DAILY_LOSS="100"   # 일일 최대 손실 한도 (USDT)

# 시장 ID (Polymarket에서 확인)
export BTC_MARKET_ID="0x..."  # Bitcoin Up/Down 5min 시장 토큰 ID
export ETH_MARKET_ID="0x..."  # Ethereum Up/Down 5min 시장 토큰 ID
```

## 시장 ID 찾기

1. Polymarket에서 원하는 시장으로 이동
2. 브라우저 개발자 도구(F12) → Network 탭
3. `markets` API 호출 확인 또는 주소창의 URL에서 추출
4. `token_id` 또는 `condition_id` 확인

또는 Polymarket CLOB API 사용:
```python
from py_clob_client.client import ClobClient

client = ClobClient("https://clob.polymarket.com")
markets = client.get_markets()
# Bitcoin/Ethereum 5분 시장 검색
```

## 상위 트레이더 데이터 수집

Polymarket은 개별 트레이더 데이터를 공개 API로 제공하지 않습니다. 다음 방법을 사용하세요:

### 방법 1: Dune Analytics
```sql
-- Dune 쿼리 예시
SELECT 
    trader,
    SUM(CASE WHEN outcome = 'Yes' THEN amount ELSE -amount END) as pnl,
    COUNT(*) as total_trades
FROM polymarket_trades
WHERE market_id = 'your_market_id'
GROUP BY trader
ORDER BY pnl DESC
LIMIT 20
```

### 방법 2: The Graph (Polymarket 서브그래프)
```graphql
{
  positions(
    where: { market: "market_address" }
    orderBy: value
    orderDirection: desc
    first: 20
  ) {
    user
    value
    outcome
  }
}
```

## 실행

```bash
# 환경 변수 로드 후 실행
source .env
python polymarket_ai_bot.py
```

## 모의 거래 테스트

실제 자금을 사용하기 전에 반드시 테스트하세요:

1. `BET_AMOUNT`를 매우 작게 설정 (0.01 USDT)
2. `MAX_DAILY_LOSS`를 낮게 설정
3. 24시간 이상 모니터링
4. 로그 확인: `tail -f polymarket_bot.log`

## 프로젝트 구조

```
polymarket-bot/
├── polymarket_ai_bot.py   # 메인 봇 코드
├── requirements.txt       # Python 의존성
├── .env.example          # 환경 변수 예시
├── README.md             # 이 파일
└── polymarket_bot.db     # SQLite 데이터베이스 (실행 후 생성)
```

## 데이터베이스 스키마

봇은 SQLite를 사용하여 다음 데이터를 저장합니다:
- `market_data`: 시장 가격 이력
- `ai_decisions`: AI 판단 기록
- `trades`: 실행된 거래 내역
- `trader_positions`: 상위 트레이더 포지션

## 전략 커스터마이징

`AIAnalyzer.analyze_market()` 메서드를 수정하여 자신만의 전략을 추가하세요:

```python
# 커스텀 지표 추가 예시
def analyze_market(self, ...):
    # 기존 지표...
    
    # 볼린저 밴드
    bb_upper, bb_lower = self._calculate_bollinger_bands(prices)
    
    # 마켓 심리 분석
    sentiment = self._analyze_market_sentiment(recent_data)
    
    # AI 프롬프트에 추가...
```

## 모니터링

```bash
# 실시간 로그 확인
tail -f polymarket_bot.log

# 오늘의 거래 내역 확인
sqlite3 polymarket_bot.db "SELECT * FROM trades WHERE timestamp LIKE '$(date +%Y-%m-%d)%' ORDER BY timestamp DESC;"

# AI 결정 통계
sqlite3 polymarket_bot.db "SELECT decision, COUNT(*), AVG(confidence) FROM ai_decisions GROUP BY decision;"
```

## 알림 설정 (선택사항)

Telegram 알림을 추가하려면:

```python
# bot.py에 추가
import requests

def send_telegram_alert(self, message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": message})
```

## 중요 고려사항

1. **가스비**: Polygon 네트워크에서도 거래마다 가스비(MATIC)가 발생합니다
2. **슬리피지**: 유동성이 낮은 시장에서는 예상 가격과 실제 체결 가격이 다를 수 있습니다
3. **API 제한**: Polymarket API는 rate limit이 있을 수 있습니다
4. **스마트 컨트랙트 리스크**: Polymarket의 컨트랙트 버그 가능성
5. **규제 리스크**: 거주 지역의 예측 시장 관련 법규 확인

## 라이선스

MIT License - 자신의 책임 하에 사용하세요.
