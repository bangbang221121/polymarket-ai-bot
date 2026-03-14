#!/usr/bin/env python3
"""
Polymarket BTC/ETH 5분 Up/Down - 최고 전략 자동 베팅 봇
백테스트 기반 최적화된 복합 전략

설치:
  pip install py-clob-client requests

실행:
  python final_bot.py

환경 변수 (.env 파일):
  POLYMARKET_API_KEY=...
  POLYMARKET_API_SECRET=...
  POLYMARKET_PASSPHRASE=...
  POLYMARKET_PRIVATE_KEY=...
  BTC_MARKET_ID=0x...
  ETH_MARKET_ID=0x...
"""

import os
import time
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
from decimal import Decimal

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

# ==================== 설정 ====================

CONFIG = {
    "api_key": os.getenv("POLYMARKET_API_KEY", ""),
    "api_secret": os.getenv("POLYMARKET_API_SECRET", ""),
    "passphrase": os.getenv("POLYMARKET_PASSPHRASE", ""),
    "private_key": os.getenv("POLYMARKET_PRIVATE_KEY", ""),
    
    # 최적화된 전략 파라미터
    "rsi_period": 10,
    "oversold": 25,
    "overbought": 75,
    "short_ma": 5,
    "long_ma": 15,
    "sentiment_threshold": 1.3,
    "min_score": 4,  # 4점 이상일 때 거래
    
    # 베팅 설정
    "bet_amount": 10,  # USDT
    "max_daily_loss": 100,
    "min_confidence": 0.65,
    
    # 마켓
    "markets": {
        "BTC": os.getenv("BTC_MARKET_ID", ""),
        "ETH": os.getenv("ETH_MARKET_ID", "")
    }
}

# ==================== 전략 엔진 ====================

class BestStrategyEngine:
    """최적화된 복합 전략 엔진"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.price_history: Dict[str, List[float]] = {}
        self.daily_pnl = 0
        self.trade_count = 0
    
    def analyze(self, market_id: str, current_price: float, 
                bid_depth: float, ask_depth: float) -> Dict:
        """
        최고 전략 분석
        Returns: {'signal': 'BUY_YES'/'BUY_NO'/'HOLD', 'confidence': 0.0-1.0, 'reason': str}
        """
        
        # 가격 히스토리 업데이트
        if market_id not in self.price_history:
            self.price_history[market_id] = []
        self.price_history[market_id].append(current_price)
        
        prices = self.price_history[market_id]
        
        # 충분한 데이터 체크
        if len(prices) < self.config["long_ma"] + 1:
            return {'signal': 'HOLD', 'confidence': 0, 'reason': '데이터 충분하지 않음'}
        
        # 점수 계산
        scores = {'YES': 0, 'NO': 0}
        reasons = []
        
        # 1. RSI (가중치 3)
        rsi = self._calculate_rsi(prices)
        if rsi < self.config["oversold"]:
            scores['YES'] += 3
            reasons.append(f"RSI과매도({rsi:.0f})")
        elif rsi > self.config["overbought"]:
            scores['NO'] += 3
            reasons.append(f"RSI과매수({rsi:.0f})")
        
        # 2. 이동평균 (가중치 2)
        short_avg = sum(prices[-self.config["short_ma"]:]) / self.config["short_ma"]
        long_avg = sum(prices[-self.config["long_ma"]:]) / self.config["long_ma"]
        
        if short_avg > long_avg * 1.001:
            scores['YES'] += 2
            reasons.append("상승추세")
        elif short_avg < long_avg * 0.999:
            scores['NO'] += 2
            reasons.append("하락추세")
        
        # 3. 오더북 심리 (가중치 1)
        if ask_depth > 0:
            bid_ratio = bid_depth / ask_depth
            if bid_ratio > self.config["sentiment_threshold"]:
                scores['YES'] += 1
                reasons.append("매수세우위")
            elif bid_ratio < (1 / self.config["sentiment_threshold"]):
                scores['NO'] += 1
                reasons.append("매도세우위")
        
        # 결정
        total_score = 6  # 최대 점수
        
        if scores['YES'] >= self.config["min_score"]:
            confidence = scores['YES'] / total_score
            return {
                'signal': 'BUY_YES',
                'confidence': min(confidence, 0.95),
                'reason': ' + '.join(reasons),
                'rsi': rsi,
                'scores': scores
            }
        elif scores['NO'] >= self.config["min_score"]:
            confidence = scores['NO'] / total_score
            return {
                'signal': 'BUY_NO',
                'confidence': min(confidence, 0.95),
                'reason': ' + '.join(reasons),
                'rsi': rsi,
                'scores': scores
            }
        
        return {
            'signal': 'HOLD',
            'confidence': 0,
            'reason': f"점수부족(YES:{scores['YES']}, NO:{scores['NO']})",
            'rsi': rsi,
            'scores': scores
        }
    
    def _calculate_rsi(self, prices: List[float]) -> float:
        """RSI 계산"""
        period = self.config["rsi_period"]
        if len(prices) < period + 1:
            return 50
        
        gains = losses = 0
        for i in range(-period, 0):
            change = prices[i] - prices[i-1]
            if change > 0:
                gains += change
            else:
                losses += abs(change)
        
        if losses == 0:
            return 100
        
        avg_gain = gains / period
        avg_loss = losses / period
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))


# ==================== 메인 봇 ====================

class PolymarketBestBot:
    """Polymarket 최적 전략 자동 베팅 봇"""
    
    def __init__(self, config: Dict):
        self.config = config
        
        # Polymarket 클라이언트
        creds = ApiCreds(
            api_key=config["api_key"],
            api_secret=config["api_secret"],
            passphrase=config["passphrase"]
        )
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=config["private_key"],
            chain_id=137,
            creds=creds
        )
        
        # 전략 엔진
        self.strategy = BestStrategyEngine(config)
        
        # 상태
        self.running = False
        self.trades_today = 0
        self.pnl_today = 0
    
    def run_single_analysis(self, market_name: str, market_id: str):
        """단일 마켓 분석 및 거래"""
        
        print(f"\n🔍 [{market_name}] 분석 중...")
        
        try:
            # 1. 시장 데이터 수집
            orderbook = self.client.get_order_book(market_id)
            market = self.client.get_market(market_id)
            
            best_bid = float(orderbook.bids[0].price) if orderbook.bids else 0
            best_ask = float(orderbook.asks[0].price) if orderbook.asks else 1
            current_price = (best_bid + best_ask) / 2
            
            bid_depth = sum(float(b.size) for b in orderbook.bids[:10])
            ask_depth = sum(float(a.size) for a in orderbook.asks[:10])
            
            print(f"   현재가: {current_price:.4f} | 매수깊이: {bid_depth:.0f} | 매도깊이: {ask_depth:.0f}")
            
            # 2. 전략 분석
            result = self.strategy.analyze(market_id, current_price, bid_depth, ask_depth)
            
            print(f"   신호: {result['signal']} | 신뢰도: {result['confidence']:.1%}")
            print(f"   사유: {result['reason']}")
            
            # 3. 거래 실행
            if result['signal'] in ['BUY_YES', 'BUY_NO'] and result['confidence'] >= self.config["min_confidence"]:
                if self.pnl_today > -self.config["max_daily_loss"]:
                    self._execute_trade(market_id, result)
                else:
                    print("   ⚠️ 일일 손실 한도 도달 - 거래 중단")
            else:
                print("   ⏸️ 거래 없음 (신뢰도 부족 또는 HOLD)")
                
        except Exception as e:
            print(f"   ❌ 오류: {e}")
    
    def _execute_trade(self, market_id: str, signal: Dict):
        """거래 실행"""
        try:
            side = BUY
            token_id = market_id if signal['signal'] == 'BUY_YES' else f"{market_id}-NO"
            size = self.config["bet_amount"]
            
            order_args = OrderArgs(
                price=0.5,
                size=size,
                side=side,
                token_id=token_id
            )
            
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)
            
            order_id = response.get('orderID', 'unknown')
            
            print(f"   ✅ 거래 실행 완료!")
            print(f"      주문 ID: {order_id}")
            print(f"      방향: {signal['signal']}")
            print(f"      금액: {size} USDT")
            
            self.trades_today += 1
            
        except Exception as e:
            print(f"   ❌ 거래 실패: {e}")
    
    def run_continuous(self, interval: int = 300):
        """지속 실행 (기본 5분 간격)"""
        
        print("=" * 60)
        print("🚀 POLYMARKET 최적 전략 자동 베팅 봇 시작")
        print("=" * 60)
        print(f"전략: 복합 전략 (RSI+MA+심리)")
        print(f"진입 조건: {self.config['min_score']}점 이상 (6점 만점)")
        print(f"베팅 금액: {self.config['bet_amount']} USDT")
        print(f"최소 신뢰도: {self.config['min_confidence']:.0%}")
        print(f"분석 간격: {interval}초 ({interval//60}분)")
        print("=" * 60)
        
        # 필수 설정 확인
        if not all([self.config["api_key"], self.config["private_key"]]):
            print("\n❌ 오류: API 키가 설정되지 않았습니다.")
            print("   .env 파일을 확인하세요.")
            return
        
        self.running = True
        
        while self.running:
            try:
                print(f"\n{'='*60}")
                print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"📊 오늘 거래: {self.trades_today}회 | 누적 PnL: {self.pnl_today:+.2f} USDT")
                print('='*60)
                
                # 각 마켓 분석
                for name, market_id in self.config["markets"].items():
                    if market_id:
                        self.run_single_analysis(name, market_id)
                
                print(f"\n⏳ 다음 분석까지 {interval}초 대기...")
                print("(중지: Ctrl+C)")
                
                time.sleep(interval)
                
            except KeyboardInterrupt:
                print("\n\n👋 사용자에 의해 중단되었습니다.")
                break
            except Exception as e:
                print(f"\n❌ 오류 발생: {e}")
                time.sleep(10)
        
        print("\n📊 최종 결과:")
        print(f"   총 거래: {self.trades_today}회")
        print(f"   누적 PnL: {self.pnl_today:+.2f} USDT")


# ==================== 실행 ====================

if __name__ == "__main__":
    # 환경 변수 확인
    required = ['POLYMARKET_API_KEY', 'POLYMARKET_PRIVATE_KEY']
    missing = [r for r in required if not os.getenv(r)]
    
    if missing:
        print("❌ 필수 환경 변수가 설정되지 않았습니다:")
        for var in missing:
            print(f"   - {var}")
        print("\n.env 파일을 생성하고 API 키를 설정하세요.")
        print("\n예시 .env 파일:")
        print("""
POLYMARKET_API_KEY=your_api_key
POLYMARKET_API_SECRET=your_secret
POLYMARKET_PASSPHRASE=your_passphrase
POLYMARKET_PRIVATE_KEY=your_private_key
BTC_MARKET_ID=0x...
ETH_MARKET_ID=0x...
        """)
        exit(1)
    
    # 봇 실행
    bot = PolymarketBestBot(CONFIG)
    bot.run_continuous(interval=300)  # 5분마다 실행
