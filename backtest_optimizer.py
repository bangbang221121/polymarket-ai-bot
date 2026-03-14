# Polymarket Backtesting & Strategy Optimizer
# 과거 데이터로 여러 전략 테스트 및 최적 전략 도출

import os
import json
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import numpy as np
import pandas as pd

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

# 로깅
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== 데이터 모델 ====================

class StrategyType(Enum):
    RSI_MEAN_REVERSION = "rsi_mean_reversion"
    TREND_FOLLOWING = "trend_following"
    BREAKOUT = "breakout"
    SENTIMENT_FOLLOW = "sentiment_follow"
    COMBINED = "combined"
    ADAPTIVE = "adaptive"

@dataclass
class HistoricalData:
    """역사적 시장 데이터"""
    timestamp: datetime
    market_id: str
    yes_price: float
    no_price: float
    volume: float
    bid_depth: float
    ask_depth: float
    trades: List[Dict]  # 거래 내역
    
    def get_mid_price(self) -> float:
        return (self.yes_price + self.no_price) / 2

@dataclass
class Trade:
    """백테스트용 거래 기록"""
    entry_time: datetime
    exit_time: Optional[datetime]
    side: str  # 'YES' or 'NO'
    entry_price: float
    exit_price: Optional[float]
    size: float
    pnl: float
    result: str  # 'WIN', 'LOSS', 'OPEN'

@dataclass
class BacktestResult:
    """백테스트 결과"""
    strategy_name: str
    strategy_type: StrategyType
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl_per_trade: float
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float
    trades: List[Trade]
    equity_curve: List[float]
    monthly_returns: Dict[str, float]
    best_month: str
    worst_month: str
    parameters: Dict

# ==================== 전략 클래스들 ====================

class BaseStrategy:
    """기반 전략 클래스"""
    
    def __init__(self, name: str, strategy_type: StrategyType):
        self.name = name
        self.strategy_type = strategy_type
        self.parameters: Dict = {}
    
    def analyze(self, data: List[HistoricalData], current_idx: int) -> Optional[str]:
        """
        현재 인덱스에서 거래 신호 생성
        Returns: 'YES', 'NO', or None (거래 없음)
        """
        raise NotImplementedError
    
    def should_exit(self, data: List[HistoricalData], entry_idx: int, 
                    current_idx: int, side: str) -> bool:
        """
        포지션 청산 여부 결정
        """
        return False


class RSIMeanReversionStrategy(BaseStrategy):
    """RSI 과매수/과매도 전략"""
    
    def __init__(self, rsi_period: int = 14, oversold: float = 30, 
                 overbought: float = 70, exit_after: int = 5):
        super().__init__("RSI Mean Reversion", StrategyType.RSI_MEAN_REVERSION)
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.exit_after = exit_after  # n개 캔들 후 강제 청산
        self.parameters = {
            "rsi_period": rsi_period,
            "oversold": oversold,
            "overbought": overbought,
            "exit_after": exit_after
        }
    
    def _calculate_rsi(self, prices: List[float]) -> float:
        if len(prices) < self.rsi_period + 1:
            return 50.0
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-self.rsi_period:])
        avg_loss = np.mean(losses[-self.rsi_period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def analyze(self, data: List[HistoricalData], current_idx: int) -> Optional[str]:
        if current_idx < self.rsi_period + 1:
            return None
        
        prices = [d.yes_price for d in data[:current_idx+1]]
        rsi = self._calculate_rsi(prices)
        
        if rsi < self.oversold:
            return 'YES'  # 과매도 → 상승 예상
        elif rsi > self.overbought:
            return 'NO'   # 과매수 → 하띭 예상
        
        return None
    
    def should_exit(self, data: List[HistoricalData], entry_idx: int, 
                    current_idx: int, side: str) -> bool:
        # n개 캔들 후 강제 청산 (5분 캔들 기준)
        if current_idx - entry_idx >= self.exit_after:
            return True
        
        # RSI 반대极단 도달 시 청산
        prices = [d.yes_price for d in data[:current_idx+1]]
        rsi = self._calculate_rsi(prices)
        
        if side == 'YES' and rsi > 50:
            return True
        if side == 'NO' and rsi < 50:
            return True
        
        return False


class TrendFollowingStrategy(BaseStrategy):
    """추세 추종 전략"""
    
    def __init__(self, short_ma: int = 5, long_ma: int = 20, 
                 exit_after: int = 5):
        super().__init__("Trend Following", StrategyType.TREND_FOLLOWING)
        self.short_ma = short_ma
        self.long_ma = long_ma
        self.exit_after = exit_after
        self.parameters = {
            "short_ma": short_ma,
            "long_ma": long_ma,
            "exit_after": exit_after
        }
    
    def analyze(self, data: List[HistoricalData], current_idx: int) -> Optional[str]:
        if current_idx < self.long_ma:
            return None
        
        prices = [d.yes_price for d in data[:current_idx+1]]
        
        short_avg = np.mean(prices[-self.short_ma:])
        long_avg = np.mean(prices[-self.long_ma:])
        
        # 골든크로스
        if short_avg > long_avg * 1.001:  # 0.1% 여유
            return 'YES'
        # 데드크로스
        elif short_avg < long_avg * 0.999:
            return 'NO'
        
        return None
    
    def should_exit(self, data: List[HistoricalData], entry_idx: int,
                    current_idx: int, side: str) -> bool:
        if current_idx - entry_idx >= self.exit_after:
            return True
        
        prices = [d.yes_price for d in data[:current_idx+1]]
        short_avg = np.mean(prices[-self.short_ma:])
        long_avg = np.mean(prices[-self.long_ma:])
        
        # 추세 반전 시 청산
        if side == 'YES' and short_avg < long_avg:
            return True
        if side == 'NO' and short_avg > long_avg:
            return True
        
        return False


class BreakoutStrategy(BaseStrategy):
    """돌파 전략"""
    
    def __init__(self, lookback: int = 20, exit_after: int = 5):
        super().__init__("Breakout", StrategyType.BREAKOUT)
        self.lookback = lookback
        self.exit_after = exit_after
        self.parameters = {
            "lookback": lookback,
            "exit_after": exit_after
        }
    
    def analyze(self, data: List[HistoricalData], current_idx: int) -> Optional[str]:
        if current_idx < self.lookback:
            return None
        
        prices = [d.yes_price for d in data[current_idx-self.lookback:current_idx+1]]
        current_price = data[current_idx].yes_price
        
        high = max(prices[:-1])
        low = min(prices[:-1])
        
        # 저항선 돌파 (상승)
        if current_price > high * 1.002:
            return 'YES'
        # 지지선 하향 돌파 (하락)
        elif current_price < low * 0.998:
            return 'NO'
        
        return None
    
    def should_exit(self, data: List[HistoricalData], entry_idx: int,
                    current_idx: int, side: str) -> bool:
        return current_idx - entry_idx >= self.exit_after


class SentimentStrategy(BaseStrategy):
    """오더북 심리 전략"""
    
    def __init__(self, depth_ratio: float = 1.5, exit_after: int = 5):
        super().__init__("Orderbook Sentiment", StrategyType.SENTIMENT_FOLLOW)
        self.depth_ratio = depth_ratio
        self.exit_after = exit_after
        self.parameters = {
            "depth_ratio": depth_ratio,
            "exit_after": exit_after
        }
    
    def analyze(self, data: List[HistoricalData], current_idx: int) -> Optional[str]:
        current = data[current_idx]
        
        bid_depth = current.bid_depth
        ask_depth = current.ask_depth
        
        # 매수세가 매도세보다 강하면 상승 예상
        if bid_depth > ask_depth * self.depth_ratio:
            return 'YES'
        # 매도세가 매수세보다 강하면 하락 예상
        elif ask_depth > bid_depth * self.depth_ratio:
            return 'NO'
        
        return None
    
    def should_exit(self, data: List[HistoricalData], entry_idx: int,
                    current_idx: int, side: str) -> bool:
        return current_idx - entry_idx >= self.exit_after


class CombinedStrategy(BaseStrategy):
    """다중 전략 결합"""
    
    def __init__(self, strategies: List[BaseStrategy], threshold: float = 0.6):
        super().__init__("Combined Multi-Strategy", StrategyType.COMBINED)
        self.strategies = strategies
        self.threshold = threshold
        self.parameters = {
            "strategies": [s.name for s in strategies],
            "threshold": threshold
        }
    
    def analyze(self, data: List[HistoricalData], current_idx: int) -> Optional[str]:
        votes = {'YES': 0, 'NO': 0}
        total_weight = 0
        
        for strategy in self.strategies:
            signal = strategy.analyze(data, current_idx)
            if signal:
                votes[signal] += 1
                total_weight += 1
        
        if total_weight == 0:
            return None
        
        yes_ratio = votes['YES'] / total_weight
        
        if yes_ratio >= self.threshold:
            return 'YES'
        elif yes_ratio <= (1 - self.threshold):
            return 'NO'
        
        return None
    
    def should_exit(self, data: List[HistoricalData], entry_idx: int,
                    current_idx: int, side: str) -> bool:
        # 대다수 전략이 반대 신호를 볼 때 청산
        opposite_count = 0
        opposite_signal = 'NO' if side == 'YES' else 'YES'
        
        for strategy in self.strategies:
            signal = strategy.analyze(data, current_idx)
            if signal == opposite_signal:
                opposite_count += 1
        
        if opposite_count >= len(self.strategies) * 0.6:
            return True
        
        return current_idx - entry_idx >= 5


class AdaptiveStrategy(BaseStrategy):
    """시장 상황에 따라 전략을 자동 전환"""
    
    def __init__(self):
        super().__init__("Adaptive Market Regime", StrategyType.ADAPTIVE)
        self.sub_strategies = {
            'trending': TrendFollowingStrategy(),
            'ranging': RSIMeanReversionStrategy(),
            'volatile': BreakoutStrategy()
        }
        self.current_regime = 'ranging'
        self.parameters = {"adaptive": True}
    
    def _detect_regime(self, data: List[HistoricalData], current_idx: int) -> str:
        """시장 상황 감지"""
        if current_idx < 20:
            return 'ranging'
        
        prices = [d.yes_price for d in data[current_idx-20:current_idx+1]]
        volatility = np.std(prices)
        trend_strength = abs(prices[-1] - prices[0]) / np.mean(prices)
        
        if volatility > 0.05:  # 높은 변동성
            return 'volatile'
        elif trend_strength > 0.02:  # 강한 추세
            return 'trending'
        else:
            return 'ranging'
    
    def analyze(self, data: List[HistoricalData], current_idx: int) -> Optional[str]:
        self.current_regime = self._detect_regime(data, current_idx)
        active_strategy = self.sub_strategies[self.current_regime]
        return active_strategy.analyze(data, current_idx)
    
    def should_exit(self, data: List[HistoricalData], entry_idx: int,
                    current_idx: int, side: str) -> bool:
        active_strategy = self.sub_strategies[self.current_regime]
        return active_strategy.should_exit(data, entry_idx, current_idx, side)


# ==================== 백테스터 ====================

class Backtester:
    """백테스팅 엔진"""
    
    def __init__(self, initial_capital: float = 1000, bet_amount: float = 10):
        self.initial_capital = initial_capital
        self.bet_amount = bet_amount
        self.db = sqlite3.connect("backtest_data.db")
        self._init_db()
    
    def _init_db(self):
        cursor = self.db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historical_data (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                market_id TEXT,
                yes_price REAL,
                no_price REAL,
                volume REAL,
                bid_depth REAL,
                ask_depth REAL,
                trades TEXT
            )
        ''')
        self.db.commit()
    
    def fetch_historical_data(self, market_id: str, days: int = 30) -> List[HistoricalData]:
        """Polymarket에서 과거 데이터 수집"""
        # TODO: 실제 API 연동
        # 지금은 더미 데이터 생성
        return self._generate_dummy_data(market_id, days)
    
    def _generate_dummy_data(self, market_id: str, days: int) -> List[HistoricalData]:
        """테스트용 더미 데이터 생성"""
        data = []
        base_price = 0.5
        
        # 5분 캔들 = 288개/일
        total_candles = days * 288
        
        for i in range(total_candles):
            timestamp = datetime.now() - timedelta(minutes=5*(total_candles-i))
            
            # 랜덤 워크 + 추세 + 평균회귀 혼합
            noise = np.random.normal(0, 0.01)
            trend = 0.0001 * np.sin(i / 100)  # 주기적 추세
            mean_reversion = (0.5 - base_price) * 0.01
            
            base_price += noise + trend + mean_reversion
            base_price = max(0.01, min(0.99, base_price))  # 0.01~0.99 제한
            
            yes_price = base_price
            no_price = 1 - yes_price
            
            data.append(HistoricalData(
                timestamp=timestamp,
                market_id=market_id,
                yes_price=yes_price,
                no_price=no_price,
                volume=np.random.uniform(1000, 10000),
                bid_depth=np.random.uniform(5000, 20000),
                ask_depth=np.random.uniform(5000, 20000),
                trades=[]
            ))
        
        return data
    
    def run_backtest(self, strategy: BaseStrategy, data: List[HistoricalData],
                     market_id: str) -> BacktestResult:
        """단일 전략 백테스트"""
        
        trades: List[Trade] = []
        equity_curve = [self.initial_capital]
        current_capital = self.initial_capital
        
        position = None  # {'side': 'YES'/'NO', 'entry_idx': index, 'entry_price': price}
        
        for i in range(len(data)):
            current_data = data[i]
            
            # 현재 포지션이 있으면 청산 확인
            if position:
                if strategy.should_exit(data, position['entry_idx'], i, position['side']):
                    # 청산
                    exit_price = current_data.yes_price if position['side'] == 'YES' else current_data.no_price
                    
                    # 수익 계산 (0.5 기준)
                    if position['side'] == 'YES':
                        pnl = (exit_price - position['entry_price']) * self.bet_amount
                    else:
                        pnl = (position['entry_price'] - exit_price) * self.bet_amount
                    
                    result = 'WIN' if pnl > 0 else 'LOSS'
                    current_capital += pnl
                    
                    trades.append(Trade(
                        entry_time=data[position['entry_idx']].timestamp,
                        exit_time=current_data.timestamp,
                        side=position['side'],
                        entry_price=position['entry_price'],
                        exit_price=exit_price,
                        size=self.bet_amount,
                        pnl=pnl,
                        result=result
                    ))
                    
                    position = None
                    equity_curve.append(current_capital)
            
            # 새 포지션 진입
            if not position:
                signal = strategy.analyze(data, i)
                if signal:
                    position = {
                        'side': signal,
                        'entry_idx': i,
                        'entry_price': current_data.yes_price if signal == 'YES' else current_data.no_price
                    }
        
        # 열린 포지션 정리
        if position:
            final_price = data[-1].yes_price if position['side'] == 'YES' else data[-1].no_price
            
            if position['side'] == 'YES':
                pnl = (final_price - position['entry_price']) * self.bet_amount
            else:
                pnl = (position['entry_price'] - final_price) * self.bet_amount
            
            trades.append(Trade(
                entry_time=data[position['entry_idx']].timestamp,
                exit_time=data[-1].timestamp,
                side=position['side'],
                entry_price=position['entry_price'],
                exit_price=final_price,
                size=self.bet_amount,
                pnl=pnl,
                result='WIN' if pnl > 0 else 'LOSS'
            ))
            
            current_capital += pnl
        
        # 결과 계산
        winning_trades = len([t for t in trades if t.result == 'WIN'])
        losing_trades = len([t for t in trades if t.result == 'LOSS'])
        total_pnl = current_capital - self.initial_capital
        
        # 최대 낙폭
        max_drawdown = 0
        peak = self.initial_capital
        for equity in equity_curve:
            if equity > peak:
                peak = equity
            drawdown = (peak - equity) / peak
            max_drawdown = max(max_drawdown, drawdown)
        
        # 샤프 비율
        returns = np.diff(equity_curve) / equity_curve[:-1]
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if len(returns) > 1 else 0
        
        # 손익비
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # 월별 수익률
        monthly_returns = self._calculate_monthly_returns(trades, self.initial_capital)
        
        return BacktestResult(
            strategy_name=strategy.name,
            strategy_type=strategy.strategy_type,
            total_trades=len(trades),
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=winning_trades / len(trades) if trades else 0,
            total_pnl=total_pnl,
            avg_pnl_per_trade=total_pnl / len(trades) if trades else 0,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
            trades=trades,
            equity_curve=equity_curve,
            monthly_returns=monthly_returns,
            best_month=max(monthly_returns.items(), key=lambda x: x[1])[0] if monthly_returns else "N/A",
            worst_month=min(monthly_returns.items(), key=lambda x: x[1])[0] if monthly_returns else "N/A",
            parameters=strategy.parameters
        )
    
    def _calculate_monthly_returns(self, trades: List[Trade], initial_capital: float) -> Dict[str, float]:
        """월별 수익률 계산"""
        monthly_pnl = defaultdict(float)
        
        for trade in trades:
            month_key = trade.exit_time.strftime('%Y-%m')
            monthly_pnl[month_key] += trade.pnl
        
        return dict(monthly_pnl)
    
    def optimize_parameters(self, strategy_class, param_grid: Dict, 
                           data: List[HistoricalData]) -> Tuple[BacktestResult, Dict]:
        """파라미터 최적화 (그리드 서치)"""
        best_result = None
        best_params = None
        best_score = -float('inf')
        
        # 간단한 그리드 서치 예시
        if strategy_class == RSIMeanReversionStrategy:
            for rsi_period in param_grid.get('rsi_period', [14]):
                for oversold in param_grid.get('oversold', [30]):
                    for overbought in param_grid.get('overbought', [70]):
                        strategy = RSIMeanReversionStrategy(rsi_period, oversold, overbought)
                        result = self.run_backtest(strategy, data, "optimization")
                        
                        # 샤프 비율 기준 평가
                        score = result.sharpe_ratio
                        if score > best_score:
                            best_score = score
                            best_result = result
                            best_params = {
                                'rsi_period': rsi_period,
                                'oversold': oversold,
                                'overbought': overbought
                            }
        
        return best_result, best_params


# ==================== 리포트 생성기 ====================

class ReportGenerator:
    """백테스트 결과 리포트 생성"""
    
    @staticmethod
    def generate_console_report(results: List[BacktestResult]) -> str:
        """콘솔용 텍스트 리포트"""
        lines = []
        lines.append("=" * 80)
        lines.append("📊 POLYMARKET STRATEGY BACKTEST RESULTS")
        lines.append("=" * 80)
        lines.append("")
        
        # 결과 정렬 (총 수익 기준)
        sorted_results = sorted(results, key=lambda x: x.total_pnl, reverse=True)
        
        for i, result in enumerate(sorted_results, 1):
            lines.append(f"\n{'─' * 80}")
            lines.append(f"#{i} {result.strategy_name}")
            lines.append(f"{'─' * 80}")
            lines.append(f"  📈 총 거래: {result.total_trades}회")
            lines.append(f"  ✅ 승률: {result.win_rate:.1%} ({result.winning_trades}승 / {result.losing_trades}패)")
            lines.append(f"  💰 총 수익: ${result.total_pnl:,.2f}")
            lines.append(f"  📊 평균 수익/거래: ${result.avg_pnl_per_trade:,.2f}")
            lines.append(f"  📉 최대 낙폭: {result.max_drawdown:.1%}")
            lines.append(f"  🎯 샤프 비율: {result.sharpe_ratio:.2f}")
            lines.append(f"  ⚖️ 손익비: {result.profit_factor:.2f}")
            lines.append(f"  🗓️ 최고 월: {result.best_month} / 최저 월: {result.worst_month}")
            lines.append(f"  ⚙️ 파라미터: {json.dumps(result.parameters, indent=2)}")
        
        lines.append("\n" + "=" * 80)
        lines.append(f"🏆 최고 전략: {sorted_results[0].strategy_name}")
        lines.append(f"   수익: ${sorted_results[0].total_pnl:,.2f}, 승률: {sorted_results[0].win_rate:.1%}")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    @staticmethod
    def export_to_csv(results: List[BacktestResult], filename: str = "backtest_results.csv"):
        """CSV로 결과 낳ㅄ기"""
        import csv
        
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Strategy', 'Type', 'Total Trades', 'Win Rate', 'Total PnL',
                'Avg PnL/Trade', 'Max Drawdown', 'Sharpe Ratio', 'Profit Factor'
            ])
            
            for result in results:
                writer.writerow([
                    result.strategy_name,
                    result.strategy_type.value,
                    result.total_trades,
                    f"{result.win_rate:.2%}",
                    f"{result.total_pnl:.2f}",
                    f"{result.avg_pnl_per_trade:.2f}",
                    f"{result.max_drawdown:.2%}",
                    f"{result.sharpe_ratio:.2f}",
                    f"{result.profit_factor:.2f}"
                ])
        
        logger.info(f"Results exported to {filename}")


# ==================== 메인 실행 ====================

def main():
    """메인 백테스트 실행"""
    logger.info("🚀 Starting Polymarket Strategy Backtester")
    
    # 백테스터 초기화
    backtester = Backtester(initial_capital=1000, bet_amount=10)
    
    # 데이터 수집 (30일)
    logger.info("📊 Fetching historical data...")
    data = backtester.fetch_historical_data("BTC_5MIN", days=30)
    logger.info(f"Loaded {len(data)} candles ({len(data)/288:.1f} days)")
    
    # 테스트할 전략들
    strategies = [
        RSIMeanReversionStrategy(rsi_period=14, oversold=30, overbought=70),
        RSIMeanReversionStrategy(rsi_period=10, oversold=25, overbought=75),
        TrendFollowingStrategy(short_ma=5, long_ma=20),
        TrendFollowingStrategy(short_ma=8, long_ma=21),
        BreakoutStrategy(lookback=20),
        SentimentStrategy(depth_ratio=1.5),
        CombinedStrategy([
            RSIMeanReversionStrategy(),
            TrendFollowingStrategy(),
            SentimentStrategy()
        ], threshold=0.6),
        AdaptiveStrategy(),
    ]
    
    # 각 전략 백테스트
    results = []
    for strategy in strategies:
        logger.info(f"Testing {strategy.name}...")
        result = backtester.run_backtest(strategy, data, "BTC_5MIN")
        results.append(result)
        logger.info(f"  → Win rate: {result.win_rate:.1%}, PnL: ${result.total_pnl:.2f}")
    
    # 리포트 생성
    report = ReportGenerator.generate_console_report(results)
    print(report)
    
    # CSV 저장
    ReportGenerator.export_to_csv(results)
    
    # 최고 전략 상세 분석
    best = max(results, key=lambda x: x.total_pnl)
    logger.info(f"\n🏆 Best Strategy: {best.strategy_name}")
    logger.info(f"   Parameters: {best.parameters}")
    
    # 파라미터 최적화 (RSI 전략 예시)
    logger.info("\n🔧 Optimizing RSI parameters...")
    param_grid = {
        'rsi_period': [10, 14, 20],
        'oversold': [25, 30, 35],
        'overbought': [65, 70, 75]
    }
    
    opt_result, opt_params = backtester.optimize_parameters(
        RSIMeanReversionStrategy, param_grid, data
    )
    
    if opt_result and opt_params:
        logger.info(f"Optimized RSI: {opt_params}")
        logger.info(f"  → Win rate: {opt_result.win_rate:.1%}, PnL: ${opt_result.total_pnl:.2f}")


if __name__ == "__main__":
    main()
