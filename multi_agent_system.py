# Polymarket Multi-Agent Trading System
# 실시간 멀티 에이전트 기반 거래 시스템

import os
import json
import time
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict, field
from decimal import Decimal
from enum import Enum
import threading
from queue import Queue, PriorityQueue
import numpy as np

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

# ==================== 설정 ====================

class Config:
    API_KEY = os.getenv("POLYMARKET_API_KEY", "")
    API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
    PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")
    PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    
    BET_AMOUNT = float(os.getenv("BET_AMOUNT", "10"))
    MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "100"))
    
    MARKETS = {
        "BTC_5MIN": os.getenv("BTC_MARKET_ID", ""),
        "ETH_5MIN": os.getenv("ETH_MARKET_ID", "")
    }

# 로깅
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('agent_system.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== 데이터 모델 ====================

class SignalType(Enum):
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"
    HOLD = "hold"
    CLOSE_POSITION = "close_position"

class AgentRole(Enum):
    TECHNICAL_ANALYST = "technical_analyst"
    SENTIMENT_ANALYST = "sentiment_analyst"
    WHALE_WATCHER = "whale_watcher"
    RISK_MANAGER = "risk_manager"
    EXECUTOR = "executor"
    COORDINATOR = "coordinator"

@dataclass
class MarketSignal:
    agent_id: str
    agent_role: AgentRole
    signal_type: SignalType
    confidence: float  # 0.0 ~ 1.0
    reasoning: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    priority: int = 5  # 1=최우선, 10=최하
    
    def __lt__(self, other):
        return self.priority < other.priority

@dataclass
class MarketState:
    market_id: str
    market_name: str
    timestamp: datetime
    yes_price: float
    no_price: float
    volume_24h: float
    orderbook_depth: Dict[str, float]
    recent_trades: List[Dict]
    volatility: float
    spread: float

@dataclass
class AgentDecision:
    agent_id: str
    role: AgentRole
    decision: SignalType
    confidence: float
    reasoning: str
    suggested_size: float
    max_slippage: float
    time_limit_ms: int

@dataclass
class ConsensusDecision:
    market_id: str
    final_decision: SignalType
    aggregated_confidence: float
    participating_agents: List[str]
    reasoning_summary: str
    risk_assessment: str
    timestamp: datetime

# ==================== 메시지 버스 ====================

class AgentMessageBus:
    """에이전트 간 실시간 통신을 위한 메시지 버스"""
    
    def __init__(self):
        self.subscribers: Dict[str, List[asyncio.Queue]] = {}
        self.signal_queue: PriorityQueue = PriorityQueue()
        self.event_callbacks: Dict[str, List[callable]] = {}
    
    def subscribe(self, event_type: str, queue: asyncio.Queue):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(queue)
    
    async def publish(self, event_type: str, data: Any):
        # 구독자들에게 브로드캐스트
        if event_type in self.subscribers:
            for queue in self.subscribers[event_type]:
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass
        
        # 콜백 실행
        if event_type in self.event_callbacks:
            for callback in self.event_callbacks[event_type]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        asyncio.create_task(callback(data))
                    else:
                        callback(data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")
    
    def on(self, event_type: str, callback: callable):
        if event_type not in self.event_callbacks:
            self.event_callbacks[event_type] = []
        self.event_callbacks[event_type].append(callback)

# ==================== 기반 에이전트 ====================

class BaseAgent:
    """모든 에이전트의 기반 클래스"""
    
    def __init__(self, agent_id: str, role: AgentRole, message_bus: AgentMessageBus):
        self.agent_id = agent_id
        self.role = role
        self.message_bus = message_bus
        self.running = False
        self.inbox: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.message_bus.subscribe("market_update", self.inbox)
        self.message_bus.subscribe(f"agent_{agent_id}", self.inbox)
        self.decision_history: List[AgentDecision] = []
        
    async def start(self):
        self.running = True
        asyncio.create_task(self._process_messages())
        asyncio.create_task(self._analyze_loop())
        logger.info(f"Agent {self.agent_id} ({self.role.value}) started")
    
    async def stop(self):
        self.running = False
        logger.info(f"Agent {self.agent_id} stopped")
    
    async def _process_messages(self):
        while self.running:
            try:
                message = await asyncio.wait_for(self.inbox.get(), timeout=1.0)
                await self.handle_message(message)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Message processing error in {self.agent_id}: {e}")
    
    async def handle_message(self, message: Any):
        """하위 클래스에서 오버라이드"""
        pass
    
    async def _analyze_loop(self):
        """하위 클래스에서 오버라이드 - 주기적 분석"""
        pass
    
    async def emit_signal(self, signal: MarketSignal):
        await self.message_bus.publish("trading_signal", signal)
        await self.message_bus.publish(f"signal_{self.role.value}", signal)

# ==================== 전문 에이전트들 ====================

class TechnicalAnalystAgent(BaseAgent):
    """기술적 분석 에이전트 - 차트 패턴과 지표 분석"""
    
    def __init__(self, message_bus: AgentMessageBus, db_connection):
        super().__init__("tech_analyst_01", AgentRole.TECHNICAL_ANALYST, message_bus)
        self.db = db_connection
        self.price_history: Dict[str, List[float]] = {}
        self.indicators_cache: Dict[str, Dict] = {}
        
    async def handle_message(self, message: Any):
        if isinstance(message, MarketState):
            await self._analyze_technicals(message)
    
    async def _analyze_technicals(self, state: MarketState):
        market_id = state.market_id
        price = state.yes_price
        
        # 가격 이력 업데이트
        if market_id not in self.price_history:
            self.price_history[market_id] = []
        self.price_history[market_id].append(price)
        
        # 100개까지만 유지
        if len(self.price_history[market_id]) > 100:
            self.price_history[market_id] = self.price_history[market_id][-100:]
        
        prices = self.price_history[market_id]
        if len(prices) < 20:
            return
        
        # 지표 계산
        indicators = self._calculate_indicators(prices)
        self.indicators_cache[market_id] = indicators
        
        # 시그널 생성
        signal = self._generate_signal(market_id, indicators, state)
        if signal:
            await self.emit_signal(signal)
    
    def _calculate_indicators(self, prices: List[float]) -> Dict:
        """다양한 기술적 지표 계산"""
        prices_arr = np.array(prices)
        
        # RSI
        rsi = self._rsi(prices_arr, 14)
        
        # 이동평균
        ma5 = np.mean(prices_arr[-5:])
        ma20 = np.mean(prices_arr[-20:])
        ma50 = np.mean(prices_arr[-50:]) if len(prices_arr) >= 50 else ma20
        
        # 볼린저 밴드
        std20 = np.std(prices_arr[-20:])
        bb_upper = ma20 + (2 * std20)
        bb_lower = ma20 - (2 * std20)
        
        # MACD
        ema12 = self._ema(prices_arr, 12)
        ema26 = self._ema(prices_arr, 26)
        macd = ema12 - ema26
        signal_line = self._ema(np.array([macd]), 9) if isinstance(macd, float) else 0
        
        # 볼륨 프로파일 (최근 20개)
        price_range = np.max(prices_arr[-20:]) - np.min(prices_arr[-20:])
        
        return {
            "rsi": rsi,
            "ma5": ma5,
            "ma20": ma20,
            "ma50": ma50,
            "macd": macd,
            "signal_line": signal_line,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "current_price": prices_arr[-1],
            "trend": "UP" if ma5 > ma20 else "DOWN",
            "volatility": price_range
        }
    
    def _rsi(self, prices: np.ndarray, period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _ema(self, prices: np.ndarray, period: int) -> float:
        if len(prices) < period:
            return prices[-1] if len(prices) > 0 else 0
        alpha = 2 / (period + 1)
        ema = prices[0]
        for price in prices[1:]:
            ema = alpha * price + (1 - alpha) * ema
        return ema
    
    def _generate_signal(self, market_id: str, ind: Dict, state: MarketState) -> Optional[MarketSignal]:
        """기술적 지표 기반 시그널 생성"""
        price = ind["current_price"]
        signals = []
        
        # RSI 과매수/과매도
        if ind["rsi"] < 30:
            signals.append(("oversold", 0.7, "RSI 과매도"))
        elif ind["rsi"] > 70:
            signals.append(("overbought", 0.7, "RSI 과매수"))
        
        # 이동평균 교차
        if ind["ma5"] > ind["ma20"] and ind["current_price"] > ind["ma5"]:
            signals.append(("bullish_trend", 0.6, "상승 추세"))
        elif ind["ma5"] < ind["ma20"] and ind["current_price"] < ind["ma5"]:
            signals.append(("bearish_trend", 0.6, "하락 추세"))
        
        # 볼린저 밴드
        if price < ind["bb_lower"]:
            signals.append(("bb_bounce_up", 0.65, "BB 하단 반등 예상"))
        elif price > ind["bb_upper"]:
            signals.append(("bb_bounce_down", 0.65, "BB 상단 하락 예상"))
        
        # MACD
        if ind["macd"] > ind["signal_line"] and ind["macd"] > 0:
            signals.append(("macd_bullish", 0.6, "MACD 상승 신호"))
        elif ind["macd"] < ind["signal_line"] and ind["macd"] < 0:
            signals.append(("macd_bearish", 0.6, "MACD 하락 신호"))
        
        if not signals:
            return None
        
        # 가중치 합산으로 최종 결정
        bullish_score = sum(s[1] for s in signals if s[0] in ["oversold", "bullish_trend", "bb_bounce_up", "macd_bullish"])
        bearish_score = sum(s[1] for s in signals if s[0] in ["overbought", "bearish_trend", "bb_bounce_down", "macd_bearish"])
        
        if bullish_score > bearish_score and bullish_score > 0.5:
            return MarketSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                signal_type=SignalType.BUY_YES,
                confidence=min(bullish_score, 0.95),
                reasoning=f"기술적 지표: {', '.join([s[2] for s in signals])}",
                metadata={"indicators": ind, "signals": signals},
                priority=3
            )
        elif bearish_score > bullish_score and bearish_score > 0.5:
            return MarketSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                signal_type=SignalType.BUY_NO,
                confidence=min(bearish_score, 0.95),
                reasoning=f"기술적 지표: {', '.join([s[2] for s in signals])}",
                metadata={"indicators": ind, "signals": signals},
                priority=3
            )
        
        return None


class SentimentAnalystAgent(BaseAgent):
    """시장 심리 분석 에이전트 - 온체인 데이터 및 주문북 분석"""
    
    def __init__(self, message_bus: AgentMessageBus):
        super().__init__("sentiment_analyst_01", AgentRole.SENTIMENT_ANALYST, message_bus)
        self.orderbook_history: Dict[str, List[Dict]] = {}
        self.trade_history: Dict[str, List[Dict]] = {}
    
    async def handle_message(self, message: Any):
        if isinstance(message, MarketState):
            await self._analyze_sentiment(message)
    
    async def _analyze_sentiment(self, state: MarketState):
        market_id = state.market_id
        
        # 주문북 깊이 분석
        bid_depth = state.orderbook_depth.get("bids", 0)
        ask_depth = state.orderbook_depth.get("asks", 0)
        
        # 거래 흐름 분석
        buy_pressure = 0
        sell_pressure = 0
        
        for trade in state.recent_trades[-50:]:
            if trade.get("side") == "BUY":
                buy_pressure += trade.get("size", 0)
            else:
                sell_pressure += trade.get("size", 0)
        
        # 스프레드 분석
        spread_ratio = state.spread / state.yes_price if state.yes_price > 0 else 0
        
        # 신호 생성
        signals = []
        
        if bid_depth > ask_depth * 1.5:
            signals.append(("strong_bid_support", 0.7, "강한 매수 지지선"))
        elif ask_depth > bid_depth * 1.5:
            signals.append(("strong_ask_resistance", 0.7, "강한 매도 저항선"))
        
        if buy_pressure > sell_pressure * 1.3:
            signals.append(("buy_dominance", 0.65, "매수 우위"))
        elif sell_pressure > buy_pressure * 1.3:
            signals.append(("sell_dominance", 0.65, "매도 우위"))
        
        if spread_ratio < 0.001:
            signals.append(("tight_spread", 0.5, "높은 유동성"))
        elif spread_ratio > 0.01:
            signals.append(("wide_spread", 0.6, "낮은 유동성 주의"))
        
        # 신호 집계
        bullish = sum(s[1] for s in signals if s[0] in ["strong_bid_support", "buy_dominance"])
        bearish = sum(s[1] for s in signals if s[0] in ["strong_ask_resistance", "sell_dominance"])
        
        if bullish > bearish and bullish > 0.5:
            await self.emit_signal(MarketSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                signal_type=SignalType.BUY_YES,
                confidence=min(bullish, 0.9),
                reasoning=f"시장 심리: {', '.join([s[2] for s in signals])}",
                metadata={"bid_depth": bid_depth, "ask_depth": ask_depth, "buy_pressure": buy_pressure},
                priority=4
            ))
        elif bearish > bullish and bearish > 0.5:
            await self.emit_signal(MarketSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                signal_type=SignalType.BUY_NO,
                confidence=min(bearish, 0.9),
                reasoning=f"시장 심리: {', '.join([s[2] for s in signals])}",
                metadata={"bid_depth": bid_depth, "ask_depth": ask_depth, "sell_pressure": sell_pressure},
                priority=4
            ))


class WhaleWatcherAgent(BaseAgent):
    """고래 추적 에이전트 - 대형 거래자 및 스마트 머니 동향 분석"""
    
    def __init__(self, message_bus: AgentMessageBus):
        super().__init__("whale_watcher_01", AgentRole.WHALE_WATCHER, message_bus)
        self.whale_trades: Dict[str, List[Dict]] = {}
        self.whale_threshold = 1000  # 1000 USDT 이상을 고래 거래로 간주
    
    async def handle_message(self, message: Any):
        if isinstance(message, dict) and message.get("type") == "large_trade":
            await self._analyze_whale_activity(message)
        elif isinstance(message, MarketState):
            await self._check_whale_positions(message)
    
    async def _analyze_whale_activity(self, trade: Dict):
        """대형 거래 분석"""
        if trade.get("size", 0) < self.whale_threshold:
            return
        
        market_id = trade.get("market_id")
        side = trade.get("side")
        
        # 고래 거래 저장
        if market_id not in self.whale_trades:
            self.whale_trades[market_id] = []
        self.whale_trades[market_id].append(trade)
        
        # 최근 10개 고래 거래 분석
        recent = self.whale_trades[market_id][-10:]
        whale_buy = sum(t["size"] for t in recent if t["side"] == "BUY")
        whale_sell = sum(t["size"] for t in recent if t["side"] == "SELL")
        
        if whale_buy > whale_sell * 2:
            await self.emit_signal(MarketSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                signal_type=SignalType.BUY_YES,
                confidence=0.75,
                reasoning=f"고래 매수세: {whale_buy:.0f} USDT (최근 10건)",
                metadata={"whale_buy": whale_buy, "whale_sell": whale_sell},
                priority=2  # 고래 신호는 높은 우선순위
            ))
        elif whale_sell > whale_buy * 2:
            await self.emit_signal(MarketSignal(
                agent_id=self.agent_id,
                agent_role=self.role,
                signal_type=SignalType.BUY_NO,
                confidence=0.75,
                reasoning=f"고래 매도세: {whale_sell:.0f} USDT (최근 10건)",
                metadata={"whale_buy": whale_buy, "whale_sell": whale_sell},
                priority=2
            ))
    
    async def _check_whale_positions(self, state: MarketState):
        """고래 포지션 추적 (The Graph 등 외부 데이터 필요)"""
        # TODO: The Graph 또는 Dune API 연동
        pass


class RiskManagerAgent(BaseAgent):
    """리스크 관리 에이전트 - 모든 거래를 검토하고 리스크 평가"""
    
    def __init__(self, message_bus: AgentMessageBus, db_connection):
        super().__init__("risk_manager_01", AgentRole.RISK_MANAGER, message_bus)
        self.db = db_connection
        self.daily_loss = 0
        self.position_exposure = {}  # market_id -> exposure
        self.message_bus.on("trading_signal", self._evaluate_signal)
    
    async def _evaluate_signal(self, signal: MarketSignal):
        """들어오는 모든 시그널 평가"""
        
        # 일일 손실 한도 확인
        if self.daily_loss >= Config.MAX_DAILY_LOSS:
            logger.warning(f"Daily loss limit reached. Blocking signal from {signal.agent_id}")
            await self.message_bus.publish("risk_block", {
                "signal": signal,
                "reason": "Daily loss limit exceeded"
            })
            return
        
        # 신뢰도 체크
        if signal.confidence < 0.6:
            logger.info(f"Low confidence signal from {signal.agent_id}: {signal.confidence}")
            return
        
        # 과도한 노출 체크
        market_id = signal.metadata.get("market_id", "unknown")
        current_exposure = self.position_exposure.get(market_id, 0)
        if current_exposure >= Config.BET_AMOUNT * 3:
            logger.warning(f"Max exposure reached for {market_id}")
            return
        
        # 리스크 승인
        await self.message_bus.publish("risk_approved", {
            "signal": signal,
            "risk_score": self._calculate_risk_score(signal),
            "max_position": Config.BET_AMOUNT
        })
    
    def _calculate_risk_score(self, signal: MarketSignal) -> float:
        """신호의 리스크 점수 계산 (0-1, 높을수록 위험)"""
        base_risk = 1 - signal.confidence
        
        # 에이전트 유형별 조정
        if signal.agent_role == AgentRole.WHALE_WATCHER:
            base_risk *= 0.8  # 고래 추적은 상대적으로 신뢰
        elif signal.agent_role == AgentRole.SENTIMENT_ANALYST:
            base_risk *= 1.1  # 심리 분석은 변동성 있음
        
        return min(base_risk, 1.0)
    
    async def update_daily_loss(self, pnl: float):
        self.daily_loss += pnl
        if self.daily_loss <= -Config.MAX_DAILY_LOSS:
            await self.message_bus.publish("trading_halt", {
                "reason": "Daily loss limit reached",
                "daily_loss": self.daily_loss
            })


class CoordinatorAgent(BaseAgent):
    """코디네이터 에이전트 - 여러 에이전트의 신호를 종합하여 최종 결정"""
    
    def __init__(self, message_bus: AgentMessageBus):
        super().__init__("coordinator_01", AgentRole.COORDINATOR, message_bus)
        self.pending_signals: Dict[str, List[MarketSignal]] = {}
        self.signal_timeout = 5  # 5초 내 모든 에이전트 신호 수집
        self.message_bus.on("risk_approved", self._on_risk_approved)
        
    async def _on_risk_approved(self, data: Dict):
        signal = data.get("signal")
        market_id = signal.metadata.get("market_id", "unknown")
        
        if market_id not in self.pending_signals:
            self.pending_signals[market_id] = []
        self.pending_signals[market_id].append(signal)
        
        # 일정 시간 후 합의 결정
        await asyncio.sleep(self.signal_timeout)
        await self._make_consensus_decision(market_id)
    
    async def _make_consensus_decision(self, market_id: str):
        """합의 결정 생성"""
        signals = self.pending_signals.get(market_id, [])
        if len(signals) < 2:
            return  # 충분한 신호 없음
        
        # 가중치 합산
        yes_score = 0
        no_score = 0
        
        for sig in signals:
            weight = sig.confidence * (10 - sig.priority) / 10  # 우선순위 반영
            if sig.signal_type == SignalType.BUY_YES:
                yes_score += weight
            elif sig.signal_type == SignalType.BUY_NO:
                no_score += weight
        
        total_score = yes_score + no_score
        if total_score == 0:
            return
        
        yes_ratio = yes_score / total_score
        
        # 최종 결정
        if yes_ratio > 0.65:
            final_decision = SignalType.BUY_YES
            confidence = yes_ratio
        elif yes_ratio < 0.35:
            final_decision = SignalType.BUY_NO
            confidence = 1 - yes_ratio
        else:
            final_decision = SignalType.HOLD
            confidence = 0.5
        
        consensus = ConsensusDecision(
            market_id=market_id,
            final_decision=final_decision,
            aggregated_confidence=confidence,
            participating_agents=[s.agent_id for s in signals],
            reasoning_summary=f"YES: {yes_score:.2f}, NO: {no_score:.2f}",
            risk_assessment=f"Consensus confidence: {confidence:.2f}",
            timestamp=datetime.now()
        )
        
        await self.message_bus.publish("consensus_decision", consensus)
        
        # 대기열 비우기
        self.pending_signals[market_id] = []


class ExecutorAgent(BaseAgent):
    """실행 에이전트 - 합의 결정을 실제 거래로 실행"""
    
    def __init__(self, message_bus: AgentMessageBus, clob_client: ClobClient):
        super().__init__("executor_01", AgentRole.EXECUTOR, message_bus)
        self.client = clob_client
        self.active_orders: Dict[str, Dict] = {}
        self.message_bus.on("consensus_decision", self._execute_decision)
    
    async def _execute_decision(self, decision: ConsensusDecision):
        """합의 결정 실행"""
        
        if decision.final_decision == SignalType.HOLD:
            logger.info(f"HOLD decision for {decision.market_id}")
            return
        
        if decision.aggregated_confidence < 0.65:
            logger.info(f"Confidence too low: {decision.aggregated_confidence}")
            return
        
        # 포지션 크기 결정
        size = self._calculate_position_size(decision)
        
        # 주문 실행
        try:
            side = BUY
            token_id = decision.market_id if decision.final_decision == SignalType.BUY_YES else f"{decision.market_id}-NO"
            
            order_args = OrderArgs(
                price=0.5,  # 시장가 주문 (개선 필요)
                size=size,
                side=side,
                token_id=token_id
            )
            
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)
            
            order_id = response.get('orderID')
            self.active_orders[order_id] = {
                "decision": decision,
                "timestamp": datetime.now(),
                "status": "OPEN"
            }
            
            logger.info(f"✅ Order executed: {decision.final_decision.value} {size} USDT on {decision.market_id}")
            
            await self.message_bus.publish("order_executed", {
                "order_id": order_id,
                "decision": decision,
                "size": size
            })
            
        except Exception as e:
            logger.error(f"❌ Order execution failed: {e}")
            await self.message_bus.publish("order_failed", {
                "decision": decision,
                "error": str(e)
            })
    
    def _calculate_position_size(self, decision: ConsensusDecision) -> float:
        """신뢰도 기반 포지션 크기 조정"""
        base_size = Config.BET_AMOUNT
        confidence_multiplier = decision.aggregated_confidence
        return base_size * confidence_multiplier


# ==================== 데이터 수집기 ====================

class RealTimeDataCollector:
    """실시간 시장 데이터 수집기"""
    
    def __init__(self, message_bus: AgentMessageBus, client: ClobClient):
        self.message_bus = message_bus
        self.client = client
        self.running = False
        self.poll_interval = 1  # 1초마다 폴
        
    async def start(self):
        self.running = True
        logger.info("Real-time data collector started")
        
        while self.running:
            try:
                for market_name, market_id in Config.MARKETS.items():
                    if not market_id:
                        continue
                    
                    state = await self._fetch_market_state(market_id, market_name)
                    if state:
                        await self.message_bus.publish("market_update", state)
                
                await asyncio.sleep(self.poll_interval)
                
            except Exception as e:
                logger.error(f"Data collection error: {e}")
                await asyncio.sleep(5)
    
    async def _fetch_market_state(self, market_id: str, market_name: str) -> Optional[MarketState]:
        try:
            orderbook = self.client.get_order_book(market_id)
            market = self.client.get_market(market_id)
            trades = self.client.get_trade_history(market_id, limit=100)
            
            best_bid = float(orderbook.bids[0].price) if orderbook.bids else 0
            best_ask = float(orderbook.asks[0].price) if orderbook.asks else 1
            mid_price = (best_bid + best_ask) / 2
            
            # 오더북 깊이 계산
            bid_depth = sum(float(b.size) for b in orderbook.bids[:10])
            ask_depth = sum(float(a.size) for a in orderbook.asks[:10])
            
            # 변동성 (최근 거래의 표준편차)
            prices = [float(t.price) for t in trades[-20:]] if trades else [mid_price]
            volatility = np.std(prices) if len(prices) > 1 else 0
            
            return MarketState(
                market_id=market_id,
                market_name=market_name,
                timestamp=datetime.now(),
                yes_price=mid_price,
                no_price=1 - mid_price,
                volume_24h=market.get("volume", 0),
                orderbook_depth={"bids": bid_depth, "asks": ask_depth},
                recent_trades=[{"price": t.price, "size": t.size, "side": t.side} for t in trades[:50]],
                volatility=volatility,
                spread=best_ask - best_bid
            )
            
        except Exception as e:
            logger.error(f"Error fetching {market_id}: {e}")
            return None
    
    def stop(self):
        self.running = False


# ==================== 메인 시스템 ====================

class PolymarketMultiAgentSystem:
    """멀티 에이전트 트레이딩 시스템 메인 클래스"""
    
    def __init__(self):
        self.message_bus = AgentMessageBus()
        self.db = sqlite3.connect("agent_trading.db")
        self._init_db()
        
        # CLOB 클라이언트
        creds = ApiCreds(
            api_key=Config.API_KEY,
            api_secret=Config.API_SECRET,
            passphrase=Config.PASSPHRASE
        )
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=Config.PRIVATE_KEY,
            chain_id=137,
            creds=creds
        )
        
        # 에이전트 초기화
        self.agents: List[BaseAgent] = [
            TechnicalAnalystAgent(self.message_bus, self.db),
            SentimentAnalystAgent(self.message_bus),
            WhaleWatcherAgent(self.message_bus),
            RiskManagerAgent(self.message_bus, self.db),
            CoordinatorAgent(self.message_bus),
            ExecutorAgent(self.message_bus, self.client)
        ]
        
        self.data_collector = RealTimeDataCollector(self.message_bus, self.client)
        self.running = False
    
    def _init_db(self):
        cursor = self.db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_signals (
                id INTEGER PRIMARY KEY,
                agent_id TEXT,
                agent_role TEXT,
                signal_type TEXT,
                confidence REAL,
                reasoning TEXT,
                market_id TEXT,
                timestamp TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS consensus_decisions (
                id INTEGER PRIMARY KEY,
                market_id TEXT,
                final_decision TEXT,
                confidence REAL,
                participating_agents TEXT,
                timestamp TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS executed_trades (
                id INTEGER PRIMARY KEY,
                order_id TEXT,
                market_id TEXT,
                side TEXT,
                size REAL,
                price REAL,
                status TEXT,
                pnl REAL,
                timestamp TEXT
            )
        ''')
        self.db.commit()
    
    async def start(self):
        logger.info("🚀 Starting Polymarket Multi-Agent Trading System...")
        logger.info(f"📊 Markets: {Config.MARKETS}")
        logger.info(f"💰 Bet Amount: {Config.BET_AMOUNT} USDT")
        logger.info(f"🛡️ Max Daily Loss: {Config.MAX_DAILY_LOSS} USDT")
        
        self.running = True
        
        # 모든 에이전트 시작
        for agent in self.agents:
            await agent.start()
        
        # 데이터 수집 시작
        await self.data_collector.start()
    
    async def stop(self):
        logger.info("🛑 Stopping system...")
        self.running = False
        self.data_collector.stop()
        
        for agent in self.agents:
            await agent.stop()
        
        self.db.close()


# ==================== 실행 ====================

async def main():
    # 환경 변수 확인
    required = ['POLYMARKET_API_KEY', 'POLYMARKET_API_SECRET', 'POLYMARKET_PASSPHRASE', 
                'POLYMARKET_PRIVATE_KEY', 'OPENAI_API_KEY']
    missing = [r for r in required if not os.getenv(r)]
    
    if missing:
        print(f"❌ Missing environment variables: {missing}")
        print("\nSet them with:")
        print("  export POLYMARKET_API_KEY='your_key'")
        print("  export OPENAI_API_KEY='your_key'")
        return
    
    system = PolymarketMultiAgentSystem()
    
    try:
        await system.start()
        
        # 무한 대기
        while system.running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    finally:
        await system.stop()


if __name__ == "__main__":
    asyncio.run(main())
