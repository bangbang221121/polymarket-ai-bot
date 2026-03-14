# Autonomous AI Trading Agents for Polymarket
# 완전 자율 AI 트레이딩 에이전트 시스템

import os
import json
import time
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
import openai
import numpy as np
from collections import deque

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType

# ==================== 설정 ====================

class Config:
    # API 키들
    POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
    POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
    POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")
    POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    
    # Dune Analytics (탑 트레이더 분석용)
    DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")
    
    # 트레이딩 설정
    INITIAL_BET_AMOUNT = float(os.getenv("INITIAL_BET_AMOUNT", "5"))  # 초기 소액 시작
    MAX_BET_AMOUNT = float(os.getenv("MAX_BET_AMOUNT", "50"))  # 최대 베팅
    MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "100"))
    
    # 학습 설정
    LEARNING_RATE = 0.1
    STRATEGY_MEMORY_SIZE = 100  # 최근 N개 거래로 전략 평가
    
    # 마켓 설정
    MARKETS = {
        "BTC_5MIN": os.getenv("BTC_MARKET_ID", ""),
        "ETH_5MIN": os.getenv("ETH_MARKET_ID", "")
    }

# 로깅
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('autonomous_agents.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

openai.api_key = Config.OPENAI_API_KEY

# ==================== 데이터 모델 ====================

class ActionType(Enum):
    BET_YES = "bet_yes"
    BET_NO = "bet_no"
    HOLD = "hold"
    CLOSE_POSITION = "close_position"
    ADJUST_STRATEGY = "adjust_strategy"

@dataclass
class MarketObservation:
    """에이전트가 관찰하는 시장 데이터"""
    timestamp: datetime
    market_id: str
    market_name: str
    
    # 가격 데이터
    current_price: float
    price_history: List[float]
    volatility: float
    
    # 오더북 데이터
    bid_ask_spread: float
    bid_depth: float
    ask_depth: float
    
    # 거래 데이터
    recent_trades: List[Dict]
    volume_24h: float
    
    # 외부 데이터
    bitcoin_price: Optional[float] = None
    ethereum_price: Optional[float] = None
    fear_greed_index: Optional[int] = None
    news_sentiment: Optional[float] = None
    
    def to_text(self) -> str:
        """LLM에 입력할 텍스트 형식"""
        return f"""[시장 관찰]
시간: {self.timestamp}
시장: {self.market_name}
현재 가격 (YES): {self.current_price:.4f}
변동성: {self.volatility:.4f}
스프레드: {self.bid_ask_spread:.4f}
24시간 거래량: {self.volume_24h:.2f}
최근 가격 추이: {self.price_history[-10:] if len(self.price_history) >= 10 else self.price_history}
BTC 현재가: {self.bitcoin_price if self.bitcoin_price else 'N/A'}
ETH 현재가: {self.ethereum_price if self.ethereum_price else 'N/A'}
뉴스 감성: {self.news_sentiment if self.news_sentiment else 'N/A'}
"""

@dataclass
class TradeResult:
    """거래 결과 (학습용)"""
    trade_id: str
    market_id: str
    action: ActionType
    entry_price: float
    exit_price: Optional[float]
    size: float
    pnl: float
    timestamp: datetime
    market_outcome: Optional[str] = None  # "YES", "NO", 또는 미정
    
    def is_win(self) -> bool:
        return self.pnl > 0

@dataclass
class StrategyMemory:
    """에이전트의 학습된 전략 메모리"""
    strategy_id: str
    description: str
    success_count: int = 0
    failure_count: int = 0
    total_pnl: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    last_used: Optional[datetime] = None
    
    def win_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.5
        return self.success_count / total
    
    def confidence_score(self) -> float:
        """전략 신뢰도 점수"""
        win_rate = self.win_rate()
        sample_size = min((self.success_count + self.failure_count) / 100, 1.0)
        return win_rate * sample_size + 0.5 * (1 - sample_size)

@dataclass
class AgentDecision:
    """에이전트의 결정"""
    agent_id: str
    agent_role: str
    observation: MarketObservation
    thought_process: str  # AI의 사고 과정
    action: ActionType
    confidence: float
    reasoning: str
    strategy_used: Optional[str] = None
    suggested_size: float = 0.0

# ==================== 기반 클래스 ====================

class AutonomousAgent(ABC):
    """자율 AI 에이전트 기반 클래스"""
    
    def __init__(self, agent_id: str, role: str, model: str = "gpt-4o-mini"):
        self.agent_id = agent_id
        self.role = role
        self.model = model
        self.memory: List[Dict] = []  # 대화/사건 기억
        self.strategies: List[StrategyMemory] = []  # 학습된 전략들
        self.trade_history: deque = deque(maxlen=Config.STRATEGY_MEMORY_SIZE)
        self.db = sqlite3.connect("autonomous_agents.db")
        self._init_db()
        
    def _init_db(self):
        cursor = self.db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_memories (
                id INTEGER PRIMARY KEY,
                agent_id TEXT,
                role TEXT,
                memory_type TEXT,
                content TEXT,
                timestamp TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY,
                agent_id TEXT,
                strategy_id TEXT,
                description TEXT,
                success_count INTEGER,
                failure_count INTEGER,
                total_pnl REAL,
                created_at TEXT,
                last_used TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_decisions (
                id INTEGER PRIMARY KEY,
                agent_id TEXT,
                market_id TEXT,
                thought_process TEXT,
                action TEXT,
                confidence REAL,
                reasoning TEXT,
                timestamp TEXT
            )
        ''')
        self.db.commit()
    
    async def think(self, observation: MarketObservation, context: Dict) -> AgentDecision:
        """LLM을 사용해 독립적으로 사고하고 결정"""
        
        # 컨텍스트 구성
        system_prompt = self._get_system_prompt()
        user_prompt = self._build_observation_prompt(observation, context)
        
        # LLM 호출
        response = await self._call_llm(system_prompt, user_prompt)
        
        # 응답 파싱
        decision = self._parse_decision(response, observation)
        
        # 기록
        self._save_decision(decision)
        
        return decision
    
    @abstractmethod
    def _get_system_prompt(self) -> str:
        """에이전트별 시스템 프롬프트"""
        pass
    
    @abstractmethod
    def _build_observation_prompt(self, observation: MarketObservation, context: Dict) -> str:
        """관찰 데이터를 프롬프트로 변환"""
        pass
    
    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """OpenAI API 호출"""
        try:
            response = await openai.ChatCompletion.acreate(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=1500
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM call error in {self.agent_id}: {e}")
            return "DECISION: HOLD\nCONFIDENCE: 0.0\nREASONING: LLM error"
    
    def _parse_decision(self, response: str, observation: MarketObservation) -> AgentDecision:
        """LLM 응답을 결정으로 파싱"""
        lines = response.strip().split('\n')
        
        action = ActionType.HOLD
        confidence = 0.0
        reasoning = ""
        thought = ""
        
        for line in lines:
            if line.startswith("THOUGHT:"):
                thought = line.replace("THOUGHT:", "").strip()
            elif line.startswith("DECISION:"):
                decision_str = line.replace("DECISION:", "").strip().upper()
                if "YES" in decision_str:
                    action = ActionType.BET_YES
                elif "NO" in decision_str:
                    action = ActionType.BET_NO
                elif "HOLD" in decision_str:
                    action = ActionType.HOLD
            elif line.startswith("CONFIDENCE:"):
                try:
                    confidence = float(line.replace("CONFIDENCE:", "").strip())
                except:
                    confidence = 0.0
            elif line.startswith("REASONING:"):
                reasoning = line.replace("REASONING:", "").strip()
        
        return AgentDecision(
            agent_id=self.agent_id,
            agent_role=self.role,
            observation=observation,
            thought_process=thought,
            action=action,
            confidence=max(0.0, min(1.0, confidence)),
            reasoning=reasoning
        )
    
    def _save_decision(self, decision: AgentDecision):
        cursor = self.db.cursor()
        cursor.execute('''
            INSERT INTO agent_decisions 
            (agent_id, market_id, thought_process, action, confidence, reasoning, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (self.agent_id, decision.observation.market_id, decision.thought_process,
              decision.action.value, decision.confidence, decision.reasoning,
              datetime.now().isoformat()))
        self.db.commit()
    
    def learn_from_result(self, decision: AgentDecision, result: TradeResult):
        """거래 결과로부터 학습"""
        self.trade_history.append({
            "decision": decision,
            "result": result,
            "timestamp": datetime.now()
        })
        
        # 전략 업데이트
        for strategy in self.strategies:
            if strategy.strategy_id == decision.strategy_used:
                strategy.last_used = datetime.now()
                strategy.total_pnl += result.pnl
                if result.is_win():
                    strategy.success_count += 1
                else:
                    strategy.failure_count += 1
                self._update_strategy_db(strategy)
                break
        
        # 메모리에 기록
        self.memory.append({
            "type": "trade_result",
            "market": result.market_id,
            "action": result.action.value,
            "pnl": result.pnl,
            "lesson": f"{'성공' if result.is_win() else '실패'}: {decision.reasoning}"
        })
    
    def _update_strategy_db(self, strategy: StrategyMemory):
        cursor = self.db.cursor()
        cursor.execute('''
            UPDATE strategies SET 
            success_count = ?, failure_count = ?, total_pnl = ?, last_used = ?
            WHERE strategy_id = ?
        ''', (strategy.success_count, strategy.failure_count, strategy.total_pnl,
              strategy.last_used.isoformat(), strategy.strategy_id))
        self.db.commit()

# ==================== 전문 에이전트들 ====================

class MarketAnalystAgent(AutonomousAgent):
    """시장 분석 에이전트 - 기술적/펀더멘털 분석 담당"""
    
    def __init__(self):
        super().__init__("market_analyst", "Technical & Fundamental Analyst")
    
    def _get_system_prompt(self) -> str:
        return """당신은 암호화폐 예측 시장 전문 분석가입니다.

당신의 역할:
1. 기술적 지표(추세, 지지/저항, 변동성) 분석
2. 펀더멘털 요소(BTC/ETH 가격, 시장 심리) 분석
3. 5분 후 가격 방향 예측

응답 형식:
THOUGHT: [당신의 분석 사고 과정을 상세히 기술]
DECISION: [BET_YES / BET_NO / HOLD]
CONFIDENCE: [0.0 ~ 1.0]
REASONING: [결정의 핵심 근거 1-2문장]

중요:
- 확신이 없으면 HOLD를 선택하세요
- 변동성이 높을 때는 신중하게 접근하세요
- 과거 패턴을 참고하되, 현재 상황에 집중하세요"""
    
    def _build_observation_prompt(self, observation: MarketObservation, context: Dict) -> str:
        recent_trades = context.get("recent_trades", [])
        top_traders = context.get("top_traders", [])
        
        return f"""{observation.to_text()}

[추가 컨텍스트]
최근 거래 흐름:
{json.dumps(recent_trades[-5:], indent=2)}

상위 트레이더 포지션:
{json.dumps(top_traders, indent=2)}

과거 성공/실패 패턴:
{self._get_recent_lessons()}

위 데이터를 바탕으로 5분 후 가격이 상승할지(YES) 하락할지(NO) 예측하고, 
베팅 여부를 결정하세요."""
    
    def _get_recent_lessons(self) -> str:
        """최근 학습한 교훈"""
        recent = [m for m in self.memory if m["type"] == "trade_result"][-5:]
        if not recent:
            return "아직 학습 데이터 없음"
        return "\n".join([f"- {r['lesson']}" for r in recent])


class TopTraderLearningAgent(AutonomousAgent):
    """탑 트레이더 학습 에이전트 - 성공한 트레이더들의 전략 모방"""
    
    def __init__(self):
        super().__init__("top_trader_learner", "Top Trader Strategy Learner")
        self.trader_patterns: Dict[str, Dict] = {}
    
    def _get_system_prompt(self) -> str:
        return """당신은 성공한 트레이더들의 전략을 분석하고 모방하는 AI입니다.

당신의 역할:
1. 상위 수익 트레이더들의 거래 패턴 식별
2. 그들의 타이밍, 포지션 크기, 리스크 관리 전략 학습
3. 현재 시장 상황에서 그들이 어떤 결정을 내릴지 예측

응답 형식:
THOUGHT: [탑 트레이더 전략 분석 과정]
DECISION: [BET_YES / BET_NO / HOLD]
CONFIDENCE: [0.0 ~ 1.0]
REASONING: [어떤 탑 트레이더 스타일을 따르는지 설명]

주의: 단순히 따라하기보다, 그들이 왜 그런 결정을 내리는지 이해하세요."""
    
    def _build_observation_prompt(self, observation: MarketObservation, context: Dict) -> str:
        top_traders = context.get("top_traders", [])
        
        # 탑 트레이더 행동 분석
        trader_analysis = self._analyze_top_traders(top_traders)
        
        return f"""{observation.to_text()}

[탑 트레이더 분석]
{trader_analysis}

[학습된 전략 패턴]
{self._get_learned_patterns()}

위 정보를 바탕으로, 성공한 트레이더들이 현재 상황에서 어떤 결정을 내릴지 예측하세요."""
    
    def _analyze_top_traders(self, traders: List[Dict]) -> str:
        if not traders:
            return "탑 트레이더 데이터 없음"
        
        yes_count = sum(1 for t in traders if t.get("position") == "YES")
        no_count = sum(1 for t in traders if t.get("position") == "NO")
        
        analysis = f"""- 총 분석 대상: {len(traders)}명
- YES 포지션: {yes_count}명
- NO 포지션: {no_count}명
- 평균 수익률: {sum(t.get('pnl', 0) for t in traders) / len(traders):.2f}%
"""
        return analysis
    
    def _get_learned_patterns(self) -> str:
        """학습된 패턴 요약"""
        if not self.strategies:
            return "아직 학습된 전략 없음"
        
        top_strategy = max(self.strategies, key=lambda s: s.win_rate())
        return f"""- 가장 성공한 전략: {top_strategy.description}
- 승률: {top_strategy.win_rate():.1%}
- 누적 수익: {top_strategy.total_pnl:.2f} USDT"""


class NewsSentimentAgent(AutonomousAgent):
    """뉴스/소셜 감성 분석 에이전트"""
    
    def __init__(self):
        super().__init__("news_sentiment", "News & Social Media Analyst")
    
    def _get_system_prompt(self) -> str:
        return """당신은 암호화폐 뉴스와 소셜 미디어 감성 분석 전문가입니다.

당신의 역할:
1. 최신 뉴스 헤드라인의 시장 영향 분석
2. 소셜 미디어(트위터, 레딧 등) 감성 파악
3. FOMO/FUD 심리 식별

응답 형식:
THOUGHT: [뉴스/소셜 분석 과정]
DECISION: [BET_YES / BET_NO / HOLD]
CONFIDENCE: [0.0 ~ 1.0]
REASONING: [감성 분석 결과 요약]

주의: 소문에 휘둘리지 말고, 검증된 정보에 기반하세요."""
    
    def _build_observation_prompt(self, observation: MarketObservation, context: Dict) -> str:
        news = context.get("news", [])
        social_sentiment = context.get("social_sentiment", {})
        
        return f"""{observation.to_text()}

[뉴스 헤드라인]
{json.dumps(news[:5], indent=2)}

[소셜 미디어 감성]
{json.dumps(social_sentiment, indent=2)}

뉴스와 소셜 미디어의 감성이 5분 후 가격에 미칠 영향을 분석하세요."""


class MetaLearningAgent(AutonomousAgent):
    """메타 학습 에이전트 - 다른 에이전트들의 결정을 종합하고 전략 개선"""
    
    def __init__(self):
        super().__init__("meta_learner", "Meta Strategy Optimizer")
        self.agent_performance: Dict[str, Dict] = {}
    
    def _get_system_prompt(self) -> str:
        return """당신은 여러 AI 에이전트의 결정을 종합하고, 최적의 전략을 설계하는 메타 학습 AI입니다.

당신의 역할:
1. 다양한 에이전트의 분석과 결정 검토
2. 각 에이전트의 과거 성과 고려
3. 최종 베팅 결정 및 포지션 크기 조정
4. 실패한 전략 개선 제안

응답 형식:
THOUGHT: [에이전트 결정 종합 분석]
DECISION: [BET_YES / BET_NO / HOLD]
CONFIDENCE: [0.0 ~ 1.0]
REASONING: [왜 이 결정을 내리는지, 어떤 에이전트를 신뢰하는지]
STRATEGY_ADJUSTMENT: [개선된 전략 제안 (선택사항)]

당신의 결정이 곧 실제 거래로 이어집니다. 신중하게 결정하세요."""
    
    def _build_observation_prompt(self, observation: MarketObservation, context: Dict) -> str:
        agent_decisions = context.get("agent_decisions", [])
        
        decisions_text = "\n\n".join([
            f"[{d.agent_role}]\n결정: {d.action.value}\n신뢰도: {d.confidence:.2f}\n근거: {d.reasoning}"
            for d in agent_decisions
        ])
        
        return f"""{observation.to_text()}

[각 에이전트의 결정]
{decisions_text}

[에이전트 과거 성과]
{self._get_agent_performance_summary()}

위 에이전트들의 결정을 종합하여 최종 베팅 결정을 내리세요."""
    
    def _get_agent_performance_summary(self) -> str:
        summary = []
        for agent_id, perf in self.agent_performance.items():
            win_rate = perf.get("wins", 0) / max(perf.get("total", 1), 1)
            summary.append(f"- {agent_id}: 승률 {win_rate:.1%}, 수익 {perf.get('pnl', 0):.2f} USDT")
        return "\n".join(summary) if summary else "아직 성과 데이터 없음"
    
    def update_agent_performance(self, agent_id: str, result: TradeResult):
        if agent_id not in self.agent_performance:
            self.agent_performance[agent_id] = {"wins": 0, "losses": 0, "pnl": 0, "total": 0}
        
        perf = self.agent_performance[agent_id]
        perf["total"] += 1
        perf["pnl"] += result.pnl
        if result.is_win():
            perf["wins"] += 1
        else:
            perf["losses"] += 1


# ==================== 데이터 수집기 ====================

class DataCollector:
    """모든 데이터 소스 수집"""
    
    def __init__(self, clob_client: ClobClient):
        self.client = clob_client
        self.price_history: Dict[str, deque] = {}
        
    async def collect(self, market_id: str, market_name: str) -> MarketObservation:
        """모든 데이터 수집"""
        
        # 1. Polymarket 데이터
        orderbook = self.client.get_order_book(market_id)
        market = self.client.get_market(market_id)
        trades = self.client.get_trade_history(market_id, limit=100)
        
        best_bid = float(orderbook.bids[0].price) if orderbook.bids else 0
        best_ask = float(orderbook.asks[0].price) if orderbook.asks else 1
        current_price = (best_bid + best_ask) / 2
        
        # 가격 히스토리 업데이트
        if market_id not in self.price_history:
            self.price_history[market_id] = deque(maxlen=100)
        self.price_history[market_id].append(current_price)
        
        # 변동성 계산
        prices = list(self.price_history[market_id])
        volatility = np.std(prices) if len(prices) > 1 else 0
        
        # 2. 외부 데이터 (CoinGecko 등)
        btc_price = await self._get_crypto_price("bitcoin")
        eth_price = await self._get_crypto_price("ethereum")
        
        # 3. 뉴스/감성 (CryptoPanic API 등)
        news = await self._get_news()
        sentiment = await self._get_social_sentiment()
        
        return MarketObservation(
            timestamp=datetime.now(),
            market_id=market_id,
            market_name=market_name,
            current_price=current_price,
            price_history=list(self.price_history[market_id]),
            volatility=volatility,
            bid_ask_spread=best_ask - best_bid,
            bid_depth=sum(float(b.size) for b in orderbook.bids[:10]),
            ask_depth=sum(float(a.size) for a in orderbook.asks[:10]),
            recent_trades=[{"price": t.price, "size": t.size} for t in trades[:20]],
            volume_24h=market.get("volume", 0),
            bitcoin_price=btc_price,
            ethereum_price=eth_price,
            news_sentiment=sentiment.get("score")
        )
    
    async def get_top_traders(self, market_id: str) -> List[Dict]:
        """상위 트레이더 데이터 수집 (Dune Analytics 또는 The Graph)"""
        # TODO: Dune API 또는 서브그래프 쿼리 구현
        return []
    
    async def _get_crypto_price(self, coin: str) -> Optional[float]:
        """CoinGecko API로 현재가 조회"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
                ) as resp:
                    data = await resp.json()
                    return data.get(coin, {}).get("usd")
        except:
            return None
    
    async def _get_news(self) -> List[Dict]:
        """CryptoPanic API로 뉴스 수집"""
        # TODO: API 키 필요
        return []
    
    async def _get_social_sentiment(self) -> Dict:
        """소셜 미디어 감성 분석"""
        # TODO: 구현
        return {}


# ==================== 실행 엔진 ====================

class AutonomousTradingEngine:
    """자율 트레이딩 실행 엔진"""
    
    def __init__(self):
        # CLOB 클라이언트
        creds = ApiCreds(
            api_key=Config.POLYMARKET_API_KEY,
            api_secret=Config.POLYMARKET_API_SECRET,
            passphrase=Config.POLYMARKET_PASSPHRASE
        )
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=Config.PRIVATE_KEY,
            chain_id=137,
            creds=creds
        )
        
        # 데이터 수집기
        self.collector = DataCollector(self.client)
        
        # AI 에이전트들
        self.agents: List[AutonomousAgent] = [
            MarketAnalystAgent(),
            TopTraderLearningAgent(),
            NewsSentimentAgent(),
        ]
        self.meta_agent = MetaLearningAgent()
        
        # 실행 상태
        self.running = False
        self.daily_pnl = 0.0
        self.trade_count = 0
        
    async def run_cycle(self, market_id: str, market_name: str):
        """한 사이클 실행"""
        logger.info(f"=== {market_name} 사이클 시작 ===")
        
        # 1. 데이터 수집
        observation = await self.collector.collect(market_id, market_name)
        top_traders = await self.collector.get_top_traders(market_id)
        
        context = {
            "recent_trades": observation.recent_trades,
            "top_traders": top_traders,
            "news": [],  # TODO
            "social_sentiment": {}
        }
        
        # 2. 각 에이전트가 독립적으로 사고/결정
        agent_decisions = []
        for agent in self.agents:
            try:
                decision = await agent.think(observation, context)
                agent_decisions.append(decision)
                logger.info(f"🤖 {agent.role}: {decision.action.value} (confidence: {decision.confidence:.2f})")
            except Exception as e:
                logger.error(f"Agent {agent.agent_id} error: {e}")
        
        # 3. 메타 에이전트가 종합 결정
        meta_context = {"agent_decisions": agent_decisions}
        final_decision = await self.meta_agent.think(observation, meta_context)
        
        logger.info(f"🎯 최종 결정: {final_decision.action.value} (confidence: {final_decision.confidence:.2f})")
        logger.info(f"💭 사고: {final_decision.thought_process[:100]}...")
        
        # 4. 실행 (신뢰도와 리스크 체크)
        if final_decision.confidence >= 0.6 and final_decision.action != ActionType.HOLD:
            if self.daily_pnl > -Config.MAX_DAILY_LOSS:
                await self._execute_trade(final_decision, market_id)
            else:
                logger.warning("일일 손실 한도 도달 - 거래 중단")
        else:
            logger.info("신뢰도 부족 또는 HOLD 결정 - 거래 없음")
        
        logger.info("=== 사이클 완료 ===\n")
    
    async def _execute_trade(self, decision: AgentDecision, market_id: str):
        """거래 실행"""
        try:
            # 포지션 크기 계산 (신뢰도 기반)
            size = Config.INITIAL_BET_AMOUNT * decision.confidence
            size = min(size, Config.MAX_BET_AMOUNT)
            
            # 실행
            side = "BUY"
            token_id = market_id if decision.action == ActionType.BET_YES else f"{market_id}-NO"
            
            order_args = OrderArgs(
                price=0.5,
                size=size,
                side=side,
                token_id=token_id
            )
            
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)
            
            logger.info(f"✅ 거래 실행: {decision.action.value} {size:.2f} USDT")
            
            # 결과 추적용 저장
            self.trade_count += 1
            
        except Exception as e:
            logger.error(f"❌ 거래 실행 실패: {e}")
    
    async def update_and_learn(self, trade_result: TradeResult):
        """거래 결과로부터 모든 에이전트 학습"""
        for agent in self.agents:
            # 해당 에이전트의 관련 결정 찾기
            # TODO: 결정-결과 매핑 구현
            pass
        
        self.meta_agent.update_agent_performance("combined", trade_result)
        self.daily_pnl += trade_result.pnl
    
    async def run_continuous(self, interval: int = 60):
        """지속 실행"""
        logger.info("🚀 자율 AI 트레이딩 시스템 시작")
        logger.info(f"에이전트 수: {len(self.agents) + 1}개")
        
        self.running = True
        while self.running:
            try:
                for name, market_id in Config.MARKETS.items():
                    if market_id:
                        await self.run_cycle(market_id, name)
                
                await asyncio.sleep(interval)
                
            except KeyboardInterrupt:
                logger.info("사용자 중단")
                break
            except Exception as e:
                logger.error(f"메인 루프 오류: {e}")
                await asyncio.sleep(10)


# ==================== 실행 ====================

async def main():
    # 환경 변수 체크
    required = ['POLYMARKET_API_KEY', 'OPENAI_API_KEY']
    missing = [r for r in required if not os.getenv(r)]
    
    if missing:
        print(f"❌ 필수 환경 변수 누락: {missing}")
        return
    
    engine = AutonomousTradingEngine()
    await engine.run_continuous(interval=60)  # 1분마다 분석


if __name__ == "__main__":
    asyncio.run(main())
