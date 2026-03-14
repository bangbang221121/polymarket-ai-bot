# Polymarket AI Betting Bot

**⚠️ 중요 경고**
- 이 봇은 실제 자금을 사용합니다
- "높은 승률로 지속적인 수익"은 보장되지 않습니다
- 테스트 후 소액부터 시작하세요
- 연속 손실 가능성을 반드시 고려하세요

## 버전 선택

이 프로젝트는 세 가지 버전을 제공합니다:

| 버전 | 파일 | 특징 | 사용 사례 |
|------|------|------|----------|
| **단순 봇** | `polymarket_ai_bot.py` | 규칙 기반 + GPT 보조 | 안정적, 예측 가능 |
| **멀티 에이전트** | `multi_agent_system.py` | 실시간 협력 에이전트 | 빠른 반응, 합의 결정 |
| **완전 자율 AI** | `autonomous_ai_agents.py` | 스스로 학습/결정하는 AI | 최첨단, 자율적 |

## 설치

```bash
# 1. 저장소 클론
git clone https://github.com/bangbang221121/polymarket-ai-bot.git
cd polymarket-ai-bot

# 2. 가상환경 생성
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 또는: venv\Scripts\activate  # Windows

# 3. 의존성 설치
pip install -r requirements.txt
```

## 환경 변수 설정

`.env` 파일을 생성하세요:

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

## 버전별 실행 방법

### 1. 단순 봇 버전 (규칙 기반)

```bash
python polymarket_ai_bot.py
```

- 5분 간격으로 실행
- 기술적 지표(RSI, MACD) + GPT 분석
- 단일 AI가 모든 결정

### 2. 멀티 에이전트 버전 (실시간 협력)

```bash
python multi_agent_system.py
```

- 1초 간격 실시간 모니터링
- 여러 전문 에이전트가 협력
- 메시지 버스로 신호 교환
- 합의(Consensus) 기반 최종 결정

### 3. 완전 자율 AI 버전 (스스로 학습)

```bash
python autonomous_ai_agents.py
```

**에이전트 구성:**
- **MarketAnalystAgent**: 기술적/펀더멘털 분석
- **TopTraderLearningAgent**: 성공 트레이더 전략 학습/모방
- **NewsSentimentAgent**: 뉴스/소셜 감성 분석
- **MetaLearningAgent**: 모든 에이전트 결정 종합 및 전략 개선

**특징:**
- 각 에이전트가 **독립적으로 사고** (LLM 호출)
- 거래 결과로부터 **스스로 학습**
- 전략을 **자율적으로 개선**
- 탑 트레이더 전략 **모방 및 진화**

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

## 모의 거래 테스트

실제 자금을 사용하기 전에 반드시 테스트하세요:

1. `BET_AMOUNT`를 매우 작게 설정 (0.01 USDT)
2. `MAX_DAILY_LOSS`를 낮게 설정
3. 24시간 이상 모니터링
4. 로그 확인: `tail -f autonomous_agents.log`

## 프로젝트 구조

```
polymarket-ai-bot/
├── polymarket_ai_bot.py        # 단순 봇 버전
├── multi_agent_system.py        # 멀티 에이전트 버전
├── autonomous_ai_agents.py      # 완전 자율 AI 버전
├── requirements.txt             # Python 의존성
├── .env.example                 # 환경 변수 예시
└── README.md                    # 이 파일
```

## 모니터링

```bash
# 실시간 로그 확인 (버전별)
tail -f polymarket_bot.log
tail -f agent_system.log
tail -f autonomous_agents.log

# 데이터베이스 확인
sqlite3 autonomous_agents.db "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT 10;"

# AI 결정 통계
sqlite3 autonomous_agents.db "SELECT agent_role, action, COUNT(*), AVG(confidence) FROM agent_decisions GROUP BY agent_role, action;"
```

## 버전 비교 상세

### 단순 봇
- **장점**: 안정적, 빠름, 디버깅 쉬움
- **단점**: 유연성 낮음, 새로운 패턴 적응 불가

### 멀티 에이전트
- **장점**: 실시간 반응, 역할 분리, 합의 결정
- **단점**: 복잡함, 예측 불가능한 상호작용

### 완전 자율 AI ⭐
- **장점**: 스스로 학습, 전략 진화, 최적화
- **단점**: LLM 비용 발생, 예측 불가능, 할루시네이션 위험

## 알림 설정 (선택사항)

Telegram 알림을 추가하려면 코드에 다음을 추가:

```python
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
3. **API 비용**: 자율 AI 버전은 OpenAI API를 많이 호출합니다 (월 $50-200 예상)
4. **LLM 할루시네이션**: AI가 현실과 무관한 결정을 내릴 수 있습니다
5. **규제 리스크**: 거주 지역의 예측 시장 관련 법규 확인

## 라이선스

MIT License - 자신의 책임 하에 사용하세요.
