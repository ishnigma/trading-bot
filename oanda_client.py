# oanda_client.py - OANDA API wrapper
import requests
from typing import Dict, Any, Optional, List

class OandaClient:
    def __init__(self, api_key: str, account_id: str, is_demo: bool = True):
        self.api_key = api_key
        self.account_id = account_id
        
        if is_demo:
            self.base_url = "https://api-fxpractice.oanda.com/v3"
        else:
            self.base_url = "https://api-fxtrade.oanda.com/v3"
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def _make_request(self, method: str, endpoint: str, data: Dict = None, params: Dict = None) -> Dict:
        url = f"{self.base_url}{endpoint}"
        try:
            if method == "GET":
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
            elif method == "POST":
                response = requests.post(url, headers=self.headers, json=data, timeout=30)
            elif method == "PUT":
                response = requests.put(url, headers=self.headers, json=data, timeout=30)
            elif method == "DELETE":
                response = requests.delete(url, headers=self.headers, timeout=30)
            else:
                return {"error": f"Unsupported method: {method}"}
            
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "status_code": getattr(e.response, 'status_code', None)}
    
    def get_account_summary(self) -> Dict:
        return self._make_request("GET", f"/accounts/{self.account_id}/summary")
    
    def get_account_balance(self) -> float:
        summary = self.get_account_summary()
        try:
            return float(summary.get('account', {}).get('balance', 0))
        except:
            return 0.0
    
    def get_buying_power(self) -> float:
        summary = self.get_account_summary()
        try:
            return float(summary.get('account', {}).get('nav', 0))
        except:
            return 0.0
    
    def get_daily_pnl(self) -> float:
        summary = self.get_account_summary()
        try:
            return float(summary.get('account', {}).get('unrealizedPL', 0))
        except:
            return 0.0
    
    def get_open_positions(self) -> List[Dict]:
        response = self._make_request("GET", f"/accounts/{self.account_id}/positions")
        positions = []
        
        for position in response.get('positions', []):
            long_units = float(position.get('long', {}).get('units', '0'))
            if long_units != 0:
                positions.append({
                    'symbol': position['instrument'],
                    'qty': abs(long_units),
                    'avg_entry_price': float(position['long']['averagePrice']),
                    'unrealized_pl': float(position['long']['unrealizedPL']),
                    'side': 'long'
                })
            
            short_units = float(position.get('short', {}).get('units', '0'))
            if short_units != 0:
                positions.append({
                    'symbol': position['instrument'],
                    'qty': abs(short_units),
                    'avg_entry_price': float(position['short']['averagePrice']),
                    'unrealized_pl': float(position['short']['unrealizedPL']),
                    'side': 'short'
                })
        
        return positions
    
    def place_market_order(self, symbol: str, units: int, stop_loss: Optional[float] = None, take_profit: Optional[float] = None) -> Dict:
        if "_" not in symbol and len(symbol) == 6:
            symbol = f"{symbol[:3]}_{symbol[3:]}"
        
        order = {
            "order": {
                "type": "MARKET",
                "instrument": symbol,
                "units": str(units),
                "timeInForce": "FOK"
            }
        }
        
        if stop_loss is not None:
            order["order"]["stopLossOnFill"] = {
                "price": str(round(stop_loss, 5)),
                "timeInForce": "GTC"
            }
        
        if take_profit is not None:
            order["order"]["takeProfitOnFill"] = {
                "price": str(round(take_profit, 5)),
                "timeInForce": "GTC"
            }
        
        response = self._make_request("POST", f"/accounts/{self.account_id}/orders", data=order)
        order_id = response.get("orderCreateTransaction", {}).get("id", "unknown")
        
        return {"id": order_id, "result": response}
    
    def close_position(self, symbol: str) -> Dict:
        if "_" not in symbol and len(symbol) == 6:
            symbol = f"{symbol[:3]}_{symbol[3:]}"
        return self._make_request("PUT", f"/accounts/{self.account_id}/positions/{symbol}/close", 
                                   data={"longUnits": "ALL", "shortUnits": "ALL"})
    
    def close_all_positions(self) -> Dict:
        positions = self.get_open_positions()
        results = []
        for position in positions:
            result = self.close_position(position['symbol'])
            results.append(result)
        return {"closed_positions": len(results), "results": results}
    
    def get_order_status(self, order_id: str) -> Dict:
        return self._make_request("GET", f"/accounts/{self.account_id}/orders/{order_id}")
    
    def get_candle_data(self, symbol: str, count: int = 100, granularity: str = "M5") -> List[Dict]:
        if "_" not in symbol and len(symbol) == 6:
            symbol = f"{symbol[:3]}_{symbol[3:]}"
        
        params = {"count": count, "granularity": granularity, "price": "M"}
        response = self._make_request("GET", f"/instruments/{symbol}/candles", params=params)
        
        candles = []
        for candle in response.get('candles', []):
            if candle.get('complete', False):
                candles.append({
                    'time': candle['time'],
                    'open': float(candle['mid']['o']),
                    'high': float(candle['mid']['h']),
                    'low': float(candle['mid']['l']),
                    'close': float(candle['mid']['c']),
                    'volume': candle.get('volume', 0)
                })
        return candles
    
    def is_market_open(self) -> bool:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        now_et = datetime.now(ZoneInfo("America/New_York"))
        
        if now_et.weekday() == 4 and now_et.hour >= 17:
            return False
        if now_et.weekday() == 5:
            return False
        if now_et.weekday() == 6 and now_et.hour < 17:
            return False
        return True
