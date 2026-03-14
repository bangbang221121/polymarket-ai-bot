# Polymarket AI Betting Bot
# 주의: 이 봇은 실제 자금을 사용합니다. 테스트 후 소액부터 시작하세요.

import os
import json
import time
import sqlite3
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from decimal import Decimal
import asyncio
import aiohttp

# Polymarket CLOB 클라이언트
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

# 설정
CONFIG = {
    "api_key": os.getenv("POLYMARKET_API_KEY", ""),
    "api_secret": os.getenv("POLYMARKET_API_SECRET", ""),
    "passphrase": os.getenv("POLYMARKET_PASSPHRASE", ""),
    "private_key": os.getenv("POLYMARKET_PRIVATE_KEY", ""),  # 0x 프리픽스 제외
    "chain_id": 137,  # Polygon
    "bet_amount": 10,  # USDT
    "max_daily_loss": 100,  # 권장: 일일 최대 손실 (USDT)
    "ai_api_key": os.getenv("OPENAI_API_KEY", ""),  # AI 분석용
    "markets": {
        "bitcoin_up_down": "0x...",  # Bitcoin Up/Down 5min 시장 주소
        "ethereum_up_down": "0x...",  # Ethereum Up/Down 5min 시장 주소
    }
}

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('polymarket_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    """시장 데이터"""
    market_id: str
    market_name: str
    timestamp: datetime
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    spread: float


@dataclass
class TraderPosition:
    """트레이더 포지션 데이터"""
    trader_address: str
    market_id: str
    position: str  # 'YES' or 'NO'
    size: float
    avg_price: float
    pnl: float
    win_rate: float
    total_trades: int


@dataclass
class AIDecision:
    """AI 의사결정"""
    market_id: str
    decision: str  # 'BUY_YES', 'BUY_NO', 'HOLD'
    confidence: float
    reasoning: str
    top_traders_alignment: float  # 상위 트레이더와의 일치도
    technical_signals: Dict
    risk_score: float


class Database:
    """SQLite 데이터베이스 관리"""
    
    def __init__(self, db_path: str = "polymarket_bot.db"):
        self.conn = sqlite3.connect(db_path)
        self._init_tables()
    
    def _init_tables(self):
        """테이블 초기화"""
        cursor = self.conn.cursor()
        
        # 시장 데이터 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                market_name TEXT,
                timestamp TEXT,
                yes_price REAL,
                no_price REAL,
                volume REAL,
                liquidity REAL,
                spread REAL
            )
        ''')
        
        # 트레이더 포지션 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trader_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trader_address TEXT,
                market_id TEXT,
                position TEXT,
                size REAL,
                avg_price REAL,
                pnl REAL,
                win_rate REAL,
                total_trades INTEGER,
                timestamp TEXT
            )
        ''')
        
        # AI 결정 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                decision TEXT,
                confidence REAL,
                reasoning TEXT,
                top_traders_alignment REAL,
                risk_score REAL,
                timestamp TEXT
            )
        ''')
        
        # 거래 기록 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                side TEXT,
                size REAL,
                price REAL,
                order_id TEXT,
                status TEXT,
                pnl REAL,
                timestamp TEXT
            )
        ''')
        
        self.conn.commit()
    
    def save_market_data(self, data: MarketData):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO market_data 
            (market_id, market_name, timestamp, yes_price, no_price, volume, liquidity, spread)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data.market_id, data.market_name, data.timestamp.isoformat(),
              data.yes_price, data.no_price, data.volume, data.liquidity, data.spread))
        self.conn.commit()
    
    def save_ai_decision(self, decision: AIDecision):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO ai_decisions 
            (market_id, decision, confidence, reasoning, top_traders_alignment, risk_score, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (decision.market_id, decision.decision, decision.confidence,
              decision.reasoning, decision.top_traders_alignment, decision.risk_score,
              datetime.now().isoformat()))
        self.conn.commit()
    
    def get_recent_market_data(self, market_id: str, hours: int = 24) -> List[MarketData]:
        cursor = self.conn.cursor()
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        cursor.execute('''
            SELECT * FROM market_data 
            WHERE market_id = ? AND timestamp > ?
            ORDER BY timestamp DESC
        ''', (market_id, since))
        
        rows = cursor.fetchall()
        return [MarketData(
            market_id=row[1], market_name=row[2], timestamp=datetime.fromisoformat(row[3]),
            yes_price=row[4], no_price=row[5], volume=row[6], liquidity=row[7], spread=row[8]
        ) for row in rows]
    
    def get_today_pnl(self) -> float:
        cursor = self.conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT COALESCE(SUM(pnl), 0) FROM trades 
            WHERE timestamp LIKE ? AND status = 'SETTLED'
        ''', (f'{today}%',))
        return cursor.fetchone()[0]


class PolymarketDataCollector:
    """Polymarket 데이터 수집기"""
    
    def __init__(self, client: ClobClient):
        self.client = client
        self.base_url = "https://clob.polymarket.com"
    
    def get_market_data(self, market_id: str) -> Optional[MarketData]:
        """현재 시장 데이터 수집"""
        try:
            # 오더북 가져오기
            orderbook = self.client.get_order_book(market_id)
            
            # 마켓 메타데이터
            market = self.client.get_market(market_id)
            
            # YES/NO 가격 계산
            best_yes_bid = float(orderbook.bids[0].price) if orderbook.bids else 0
            best_yes_ask = float(orderbook.asks[0].price) if orderbook.asks else 1
            
            mid_price = (best_yes_bid + best_yes_ask) / 2
            spread = best_yes_ask - best_yes_bid
            
            return MarketData(
                market_id=market_id,
                market_name=market.get('question', 'Unknown'),
                timestamp=datetime.now(),
                yes_price=mid_price,
                no_price=1 - mid_price,
                volume=market.get('volume', 0),
                liquidity=market.get('liquidity', 0),
                spread=spread
            )
        except Exception as e:
            logger.error(f"Error fetching market data: {e}")
            return None
    
    def get_top_traders(self, market_id: str, limit: int = 20) -> List[TraderPosition]:
        """상위 트레이더 포지션 수집"""
        # 참고: Polymarket 공개 API로는 개별 트레이더 데이터에 직접 접근하기 어려움
        # 대체 방법: Dune Analytics API 또는 The Graph 사용 권장
        
        traders = []
        try:
            # Dune Analytics 쿼리 예시 (API 키 필요)
            # 실제 구현시 Dune API 또는 커스텀 스크래핑 필요
            pass
        except Exception as e:
            logger.error(f"Error fetching top traders: {e}")
        
        return traders
    
    def get_recent_trades(self, market_id: str, limit: int = 100) -> List[Dict]:
        """최근 거래 내역"""
        try:
            trades = self.client.get_trade_history(market_id, limit=limit)
            return trades
        except Exception as e:
            logger.error(f"Error fetching trade history: {e}")
            return []


class AIAnalyzer:
    """AI 분석 에이전트"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.api_url = "https://api.openai.com/v1/chat/completions"
    
    def analyze_market(self, market_data: MarketData, 
                       recent_data: List[MarketData],
                       top_traders: List[TraderPosition]) -> AIDecision:
        """AI가 시장을 분석하고 결정"""
        
        # 기술적 분석 데이터 준비
        prices = [d.yes_price for d in recent_data] if recent_data else [market_data.yes_price]
        volumes = [d.volume for d in recent_data] if recent_data else [market_data.volume]
        
        # RSI 계산
        rsi = self._calculate_rsi(prices)
        
        # 이동평균
        ma5 = sum(prices[-5:]) / min(5, len(prices)) if prices else market_data.yes_price
        ma20 = sum(prices[-20:]) / min(20, len(prices)) if len(prices) >= 20 else ma5
        
        # 상위 트레이더 분석
        yes_votes = sum(1 for t in top_traders if t.position == 'YES')
        no_votes = sum(1 for t in top_traders if t.position == 'NO')
        total_votes = yes_votes + no_votes
        top_trader_alignment = yes_votes / total_votes if total_votes > 0 else 0.5
        
        # AI 프롬프트 구성
        prompt = f"""당신은 Polymarket 전문 트레이딩 AI입니다. Bitcoin/Ethereum 5분 예측 시장을 분석하세요.

현재 시장 데이터:
- 시장: {market_data.market_name}
- YES 가격: {market_data.yes_price:.4f} (상승 예측)
- NO 가격: {market_data.no_price:.4f} (하락 예측)
- 스프레드: {market_data.spread:.4f}
- 유동성: {market_data.liquidity:.2f}

기술적 지표:
- RSI(14): {rsi:.2f}
- MA5: {ma5:.4f}
- MA20: {ma20:.4f}
- 추세: {'상승' if ma5 > ma20 else '하락'}

상위 트레이더 포지션:
- YES 포지션: {yes_votes}명
- NO 포지션: {no_votes}명
- 승률 높은 트레이더들의 선택: {'YES' if top_trader_alignment > 0.6 else 'NO' if top_trader_alignment < 0.4 else '혼합'}

최근 가격 이력: {prices[-10:]}

다음 형식으로 응답하세요:
DECISION: [BUY_YES / BUY_NO / HOLD]
CONFIDENCE: [0-1 사이 값]
REASONING: [결정 이유]
RISK_SCORE: [0-1 사이 값, 높을수록 위험]
"""
        
        try:
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3
                },
                timeout=30
            )
            
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            # 파싱
            decision = 'HOLD'
            confidence = 0.5
            reasoning = ''
            risk_score = 0.5
            
            for line in content.split('\n'):
                if line.startswith('DECISION:'):
                    decision = line.split(':')[1].strip()
                elif line.startswith('CONFIDENCE:'):
                    confidence = float(line.split(':')[1].strip())
                elif line.startswith('REASONING:'):
                    reasoning = line.split(':')[1].strip()
                elif line.startswith('RISK_SCORE:'):
                    risk_score = float(line.split(':')[1].strip())
            
            return AIDecision(
                market_id=market_data.market_id,
                decision=decision,
                confidence=confidence,
                reasoning=reasoning,
                top_traders_alignment=top_trader_alignment,
                technical_signals={'rsi': rsi, 'ma5': ma5, 'ma20': ma20, 'trend': 'UP' if ma5 > ma20 else 'DOWN'},
                risk_score=risk_score
            )
            
        except Exception as e:
            logger.error(f"AI analysis error: {e}")
            return AIDecision(
                market_id=market_data.market_id,
                decision='HOLD',
                confidence=0,
                reasoning=f'AI 분석 오류: {e}',
                top_traders_alignment=top_trader_alignment,
                technical_signals={'rsi': rsi, 'ma5': ma5, 'ma20': ma20},
                risk_score=1.0
            )
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """RSI 계산"""
        if len(prices) < period + 1:
            return 50.0
        
        gains = []
        losses = []
        
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        if len(gains) < period:
            return 50.0
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi


class BettingExecutor:
    """베팅 실행기"""
    
    def __init__(self, client: ClobClient, db: Database):
        self.client = client
        self.db = db
    
    def execute_bet(self, decision: AIDecision, amount: float) -> Optional[str]:
        """베팅 실행"""
        if decision.decision == 'HOLD':
            logger.info("AI decided to HOLD. No bet placed.")
            return None
        
        if decision.confidence < 0.6:
            logger.info(f"Confidence too low ({decision.confidence:.2f}). Skipping bet.")
            return None
        
        if decision.risk_score > 0.7:
            logger.warning(f"Risk score too high ({decision.risk_score:.2f}). Skipping bet.")
            return None
        
        try:
            side = BUY
            token_id = decision.market_id if decision.decision == 'BUY_YES' else f"{decision.market_id}-NO"
            
            # 주문 생성
            order_args = OrderArgs(
                price=0.5,  # 시장가에 가까운 가격
                size=amount,
                side=side,
                token_id=token_id
            )
            
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)
            
            order_id = response.get('orderID', 'unknown')
            
            # DB에 기록
            cursor = self.db.conn.cursor()
            cursor.execute('''
                INSERT INTO trades (market_id, side, size, price, order_id, status, pnl, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (decision.market_id, decision.decision, amount, 0.5, order_id, 'OPEN', 0, datetime.now().isoformat()))
            self.db.conn.commit()
            
            logger.info(f"Bet placed: {decision.decision} on {decision.market_id}, Order ID: {order_id}")
            return order_id
            
        except Exception as e:
            logger.error(f"Error executing bet: {e}")
            return None
    
    def check_and_close_positions(self, market_id: str):
        """포지션 청산 확인"""
        # 5분 후 결과 확인 및 포지션 정리
        # 실제 구현시 시장 결과 오라클 확인 필요
        pass


class PolymarketAIBot:
    """메인 봇 클래스"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.db = Database()
        
        # CLOB 클라이언트 초기화
        api_creds = ApiCreds(
            api_key=config['api_key'],
            api_secret=config['api_secret'],
            passphrase=config['passphrase']
        )
        
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=config['private_key'],
            chain_id=config['chain_id'],
            creds=api_creds
        )
        
        self.collector = PolymarketDataCollector(self.client)
        self.analyzer = AIAnalyzer(config['ai_api_key'])
        self.executor = BettingExecutor(self.client, self.db)
    
    def run_cycle(self, market_id: str, market_name: str):
        """한 사이클 실행"""
        logger.info(f"=== Starting cycle for {market_name} ===")
        
        # 1. 일일 손실 한도 확인
        today_pnl = self.db.get_today_pnl()
        if today_pnl <= -self.config['max_daily_loss']:
            logger.warning(f"Daily loss limit reached ({today_pnl}). Stopping for today.")
            return
        
        # 2. 시장 데이터 수집
        market_data = self.collector.get_market_data(market_id)
        if not market_data:
            logger.error("Failed to get market data")
            return
        
        self.db.save_market_data(market_data)
        logger.info(f"Market data: YES={market_data.yes_price:.4f}, NO={market_data.no_price:.4f}")
        
        # 3. 상위 트레이더 데이터 수집
        top_traders = self.collector.get_top_traders(market_id)
        logger.info(f"Collected data for {len(top_traders)} top traders")
        
        # 4. 과거 데이터 가져오기
        recent_data = self.db.get_recent_market_data(market_id, hours=24)
        
        # 5. AI 분석
        decision = self.analyzer.analyze_market(market_data, recent_data, top_traders)
        self.db.save_ai_decision(decision)
        
        logger.info(f"AI Decision: {decision.decision} (confidence: {decision.confidence:.2f})")
        logger.info(f"Reasoning: {decision.reasoning}")
        
        # 6. 베팅 실행
        if decision.decision in ['BUY_YES', 'BUY_NO']:
            order_id = self.executor.execute_bet(decision, self.config['bet_amount'])
            if order_id:
                logger.info(f"Bet executed successfully: {order_id}")
        
        logger.info(f"=== Cycle completed ===\n")
    
    def run_continuous(self, interval_seconds: int = 300):
        """지속적으로 실행"""
        logger.info("Starting Polymarket AI Bot...")
        logger.info(f"Bet amount: {self.config['bet_amount']} USDT")
        logger.info(f"Max daily loss: {self.config['max_daily_loss']} USDT")
        
        while True:
            try:
                for market_name, market_id in self.config['markets'].items():
                    if market_id and market_id != "0x...":
                        self.run_cycle(market_id, market_name)
                        time.sleep(5)  # 마켓 간 간격
                
                logger.info(f"Waiting {interval_seconds}s until next cycle...")
                time.sleep(interval_seconds)
                
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(60)  # 오류 시 1분 후 재시도


# ==================== 설정 및 실행 ====================

if __name__ == "__main__":
    # 환경 변수에서 설정 로드
    config = {
        "api_key": os.getenv("POLYMARKET_API_KEY"),
        "api_secret": os.getenv("POLYMARKET_API_SECRET"),
        "passphrase": os.getenv("POLYMARKET_PASSPHRASE"),
        "private_key": os.getenv("POLYMARKET_PRIVATE_KEY"),
        "chain_id": 137,
        "bet_amount": float(os.getenv("BET_AMOUNT", "10")),
        "max_daily_loss": float(os.getenv("MAX_DAILY_LOSS", "100")),
        "ai_api_key": os.getenv("OPENAI_API_KEY"),
        "markets": {
            "bitcoin_up_down": os.getenv("BTC_MARKET_ID", ""),
            "ethereum_up_down": os.getenv("ETH_MARKET_ID", ""),
        }
    }
    
    # 필수 설정 확인
    required = ['api_key', 'api_secret', 'passphrase', 'private_key', 'ai_api_key']
    missing = [k for k in required if not config.get(k)]
    
    if missing:
        print(f"❌ Missing required environment variables: {missing}")
        print("\nPlease set the following environment variables:")
        print("  export POLYMARKET_API_KEY='your_key'")
        print("  export POLYMARKET_API_SECRET='your_secret'")
        print("  export POLYMARKET_PASSPHRASE='your_passphrase'")
        print("  export POLYMARKET_PRIVATE_KEY='your_private_key' (without 0x)")
        print("  export OPENAI_API_KEY='your_openai_key'")
        print("  export BTC_MARKET_ID='bitcoin_market_token_id'")
        print("  export ETH_MARKET_ID='ethereum_market_token_id'")
        exit(1)
    
    # 봇 실행
    bot = PolymarketAIBot(config)
    bot.run_continuous(interval_seconds=300)  # 5분마다 실행
