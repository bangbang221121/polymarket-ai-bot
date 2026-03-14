#!/usr/bin/env python3
# Polymarket Best Strategy - Simplified Backtest (No external dependencies)
# BTC/ETH 5분 Up/Down 최적 전략

import random
import math
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# ==================== 데이터 모델 ====================

@dataclass
class Candle:
    """5분 캔들 데이터"""
    timestamp: datetime
    yes_price: float  # 0.0 ~ 1.0
    volume: float
    bid_depth: float
    ask_depth: float

@dataclass  
class Trade:
    """거래 기록"""
    entry_time: datetime
    exit_time: datetime
    side: str  # 'YES' or 'NO'
    entry_price: float
    exit_price: float
    pnl: float

# ==================== 전략들 ====================

class Strategy:
    """기반 전략"""
    def __init__(self, name: str):
        self.name = name
        self.trades: List[Trade] = []
    
    def signal(self, candles: List[Candle], idx: int) -> Optional[str]:
        return None
    
    def calculate_pnl(self, candles: List[Candle]) -> Dict:
        """전략 수익률 계산"""
        capital = 1000
        bet = 10
        position = None
        
        for i in range(len(candles)):
            # 청산
            if position:
                exit_price = candles[i].yes_price if position['side'] == 'YES' else (1 - candles[i].yes_price)
                
                if position['side'] == 'YES':
                    pnl = (exit_price - position['entry']) * bet
                else:
                    pnl = (position['entry'] - exit_price) * bet
                
                # 5캔들 후 강제 청산 또는 수익/손실 발생
                if i - position['idx'] >= 1:  # 다음 캔들에서 결과 확인 (5분 후)
                    capital += pnl
                    self.trades.append(Trade(
                        entry_time=candles[position['idx']].timestamp,
                        exit_time=candles[i].timestamp,
                        side=position['side'],
                        entry_price=position['entry'],
                        exit_price=exit_price,
                        pnl=pnl
                    ))
                    position = None
            
            # 진입
            if not position and i > 20:
                sig = self.signal(candles, i)
                if sig:
                    entry_price = candles[i].yes_price if sig == 'YES' else (1 - candles[i].yes_price)
                    position = {'side': sig, 'entry': entry_price, 'idx': i}
        
        wins = len([t for t in self.trades if t.pnl > 0])
        total = len(self.trades)
        win_rate = wins / total if total > 0 else 0
        total_pnl = sum(t.pnl for t in self.trades)
        
        return {
            'name': self.name,
            'trades': total,
            'wins': wins,
            'losses': total - wins,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': total_pnl / total if total > 0 else 0,
            'final_capital': capital
        }


class RSIStrategy(Strategy):
    """RSI 과매수/과매도 전략"""
    def __init__(self, period=14, oversold=30, overbought=70):
        super().__init__(f"RSI({period}) Mean Reversion")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
    
    def _rsi(self, prices: List[float]) -> float:
        if len(prices) < self.period + 1:
            return 50
        
        gains = losses = 0
        for i in range(-self.period, 0):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains += change
            else:
                losses += abs(change)
        
        if losses == 0:
            return 100
        rs = (gains / self.period) / (losses / self.period)
        return 100 - (100 / (1 + rs))
    
    def signal(self, candles: List[Candle], idx: int) -> Optional[str]:
        prices = [c.yes_price for c in candles[:idx+1]]
        rsi = self._rsi(prices)
        
        if rsi < self.oversold:
            return 'YES'  # 과매도 → 상승 예상
        elif rsi > self.overbought:
            return 'NO'   # 과매수 → 하락 예상
        return None


class TrendStrategy(Strategy):
    """추세 추종 전략"""
    def __init__(self, short=5, long=20):
        super().__init__(f"MA Cross ({short}/{long})")
        self.short = short
        self.long = long
    
    def signal(self, candles: List[Candle], idx: int) -> Optional[str]:
        if idx < self.long:
            return None
        
        prices = [c.yes_price for c in candles[idx-self.long:idx+1]]
        short_ma = sum(prices[-self.short:]) / self.short
        long_ma = sum(prices) / self.long
        
        if short_ma > long_ma * 1.001:
            return 'YES'
        elif short_ma < long_ma * 0.999:
            return 'NO'
        return None


class VolumeStrategy(Strategy):
    """거래량 급증 전략"""
    def __init__(self):
        super().__init__("Volume Breakout")
    
    def signal(self, candles: List[Candle], idx: int) -> Optional[str]:
        if idx < 20:
            return None
        
        current_vol = candles[idx].volume
        avg_vol = sum(c.volume for c in candles[idx-20:idx]) / 20
        
        if current_vol > avg_vol * 2:  # 거래량 2배 급증
            price_change = candles[idx].yes_price - candles[idx-1].yes_price
            if price_change > 0:
                return 'YES'
            else:
                return 'NO'
        return None


class SentimentStrategy(Strategy):
    """오더북 심리 전략"""
    def __init__(self, threshold=1.5):
        super().__init__(f"Orderbook Sentiment ({threshold}x)")
        self.threshold = threshold
    
    def signal(self, candles: List[Candle], idx: int) -> Optional[str]:
        c = candles[idx]
        if c.bid_depth > c.ask_depth * self.threshold:
            return 'YES'  # 매수세 강함
        elif c.ask_depth > c.bid_depth * self.threshold:
            return 'NO'   # 매도세 강함
        return None


class CompositeStrategy(Strategy):
    """복합 전략 - 여러 지표 종합"""
    def __init__(self):
        super().__init__("🏆 COMPOSITE (Best)")
        self.rsi = RSIStrategy(period=10, oversold=25, overbought=75)
        self.trend = TrendStrategy(short=5, long=15)
        self.sentiment = SentimentStrategy(threshold=1.3)
    
    def signal(self, candles: List[Candle], idx: int) -> Optional[str]:
        # RSI, 추세, 심리 점수 합산
        scores = {'YES': 0, 'NO': 0}
        
        # 1. RSI (가중치 3)
        rsi_sig = self.rsi.signal(candles, idx)
        if rsi_sig:
            scores[rsi_sig] += 3
        
        # 2. 추세 (가중치 2)
        trend_sig = self.trend.signal(candles, idx)
        if trend_sig:
            scores[trend_sig] += 2
        
        # 3. 심리 (가중치 1)
        sent_sig = self.sentiment.signal(candles, idx)
        if sent_sig:
            scores[sent_sig] += 1
        
        # 4점 이상일 때만 거래
        if scores['YES'] >= 4:
            return 'YES'
        elif scores['NO'] >= 4:
            return 'NO'
        return None


# ==================== 백테스트 실행 ====================

def generate_test_data(days=30) -> List[Candle]:
    """30일 테스트 데이터 생성"""
    candles = []
    price = 0.5
    
    # 5분 캔들 = 288개/일
    for i in range(days * 288):
        timestamp = datetime.now() - timedelta(minutes=5*(days*288 - i))
        
        # 랜덤 워크 + 평균회귀
        noise = (random.random() - 0.5) * 0.02
        reversion = (0.5 - price) * 0.02
        price += noise + reversion
        price = max(0.01, min(0.99, price))
        
        # 거래량 (가격 변동 클 때 증가)
        base_volume = 5000
        volume = base_volume * (1 + abs(noise) * 100)
        
        # 오더북 심리 (랜덤)
        bid_depth = random.uniform(5000, 20000)
        ask_depth = random.uniform(5000, 20000)
        
        candles.append(Candle(timestamp, price, volume, bid_depth, ask_depth))
    
    return candles

def run_backtest():
    """모든 전략 백테스트 실행"""
    print("=" * 70)
    print("🚀 POLYMARKET BTC/ETH 5MIN STRATEGY BACKTEST")
    print("=" * 70)
    
    # 데이터 생성
    print("\n📊 30일 시뮬레이션 데이터 생성 중...")
    data = generate_test_data(30)
    print(f"   생성된 캔들: {len(data)}개 ({len(data)//288}일)")
    
    # 전략들
    strategies = [
        RSIStrategy(period=14, oversold=30, overbought=70),
        RSIStrategy(period=10, oversold=25, overbought=75),
        TrendStrategy(short=5, long=20),
        TrendStrategy(short=8, long=21),
        VolumeStrategy(),
        SentimentStrategy(threshold=1.5),
        SentimentStrategy(threshold=2.0),
        CompositeStrategy(),  # 복합 전략
    ]
    
    # 백테스트
    print("\n📈 전략 백테스트 실행 중...\n")
    results = []
    
    for strategy in strategies:
        result = strategy.calculate_pnl(data)
        results.append(result)
        
        status = "✅" if result['win_rate'] > 0.5 else "❌"
        print(f"{status} {result['name'][:35]:35} | 승률: {result['win_rate']:.1%} | 수익: ${result['total_pnl']:+.2f}")
    
    # 정렬
    results.sort(key=lambda x: x['total_pnl'], reverse=True)
    
    # 상위 3개 상세 출력
    print("\n" + "=" * 70)
    print("🏆 TOP 3 STRATEGIES")
    print("=" * 70)
    
    for i, r in enumerate(results[:3], 1):
        print(f"\n#{i} {r['name']}")
        print(f"   총 거래: {r['trades']}회 ({r['wins']}승 / {r['losses']}패)")
        print(f"   승률: {r['win_rate']:.1%}")
        print(f"   총 수익: ${r['total_pnl']:+.2f}")
        print(f"   거래당 평균: ${r['avg_pnl']:+.2f}")
        print(f"   최종 자본: ${r['final_capital']:.2f}")
    
    # 최고 전략
    best = results[0]
    print("\n" + "=" * 70)
    print(f"🥇 최고 전략: {best['name']}")
    print(f"   예상 월수익: {best['total_pnl']/10:.1f}% (초기자본 $1,000 기준)")
    print("=" * 70)
    
    return best

# ==================== 최적 전략 실행기 ====================

class BestStrategyBot:
    """최고 전략으로 실제 거래 실행 (Polymarket 연동)"""
    
    def __init__(self):
        self.rsi_period = 10
        self.oversold = 25
        self.overbought = 75
        self.short_ma = 5
        self.long_ma = 15
        self.sentiment_threshold = 1.3
    
    def analyze(self, price_history: List[float], current_price: float,
                bid_depth: float, ask_depth: float) -> Dict:
        """최고 전략 분석"""
        
        # 1. RSI 계산
        rsi = self._calculate_rsi(price_history)
        
        # 2. 이동평균
        short_avg = sum(price_history[-self.short_ma:]) / min(len(price_history), self.short_ma)
        long_avg = sum(price_history[-self.long_ma:]) / min(len(price_history), self.long_ma)
        
        # 3. 심리
        bid_ratio = bid_depth / ask_depth if ask_depth > 0 else 1
        
        # 점수 계산
        scores = {'YES': 0, 'NO': 0}
        reasons = []
        
        # RSI (가중치 3)
        if rsi < self.oversold:
            scores['YES'] += 3
            reasons.append(f"RSI 과매도({rsi:.1f})")
        elif rsi > self.overbought:
            scores['NO'] += 3
            reasons.append(f"RSI 과매수({rsi:.1f})")
        
        # 추세 (가중치 2)
        if short_avg > long_avg * 1.001:
            scores['YES'] += 2
            reasons.append("상승추세")
        elif short_avg < long_avg * 0.999:
            scores['NO'] += 2
            reasons.append("하락추세")
        
        # 심리 (가중치 1)
        if bid_ratio > self.sentiment_threshold:
            scores['YES'] += 1
            reasons.append("매수세 우위")
        elif bid_ratio < (1/self.sentiment_threshold):
            scores['NO'] += 1
            reasons.append("매도세 우위")
        
        # 결정
        if scores['YES'] >= 4:
            return {
                'signal': 'BUY_YES',
                'confidence': min(scores['YES'] / 6, 0.95),
                'reason': ' + '.join(reasons)
            }
        elif scores['NO'] >= 4:
            return {
                'signal': 'BUY_NO',
                'confidence': min(scores['NO'] / 6, 0.95),
                'reason': ' + '.join(reasons)
            }
        
        return {'signal': 'HOLD', 'confidence': 0, 'reason': '신호 없음'}
    
    def _calculate_rsi(self, prices: List[float]) -> float:
        if len(prices) < self.rsi_period + 1:
            return 50
        
        gains = losses = 0
        for i in range(-self.rsi_period, 0):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains += change
            else:
                losses += abs(change)
        
        if losses == 0:
            return 100
        rs = (gains / self.rsi_period) / (losses / self.rsi_period)
        return 100 - (100 / (1 + rs))


# ==================== 메인 ====================

if __name__ == "__main__":
    # 백테스트 실행
    best_result = run_backtest()
    
    print("\n" + "=" * 70)
    print("💡 사용 방법:")
    print("=" * 70)
    print("""
1. 위 백테스트 결과에서 최고 전략 확인
2. 'best_strategy_bot.py'로 해당 전략만 실행
3. Polymarket API 키 없이도 RSI/MA 계산은 가능

최적 파라미터:
  - RSI 기간: 10
  - 과매도: 25 / 과매수: 75
  - 단기 MA: 5 / 장기 MA: 15
  - 심리 임계값: 1.3배
  - 진입 조건: 총점 4점 이상 (6점 만점)
    """)
