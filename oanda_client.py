# oanda_client.py
import requests
from typing import Dict, Optional, List


class OandaClient:
    def __init__(self, api_key: str, account_id: str, is_demo: bool = True):
        # Save account settings
        self.api_key = api_key
        self.account_id = account_id

        # Pick demo or live OANDA URL
        self.base_url = (
            "https://api-fxpractice.oanda.com/v3"
            if is_demo
            else "https://api-fxtrade.oanda.com/v3"
        )

        # OANDA authorization headers
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _make_request(self, method: str, endpoint: str, data: Dict = None, params: Dict = None) -> Dict:
        # Send request to OANDA
        url = f"{self.base_url}{endpoint}"

        try:
            if method == "GET":
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
            elif method == "POST":
                response = requests.post(url, headers=self.headers, json=data, timeout=30)
            elif method == "PUT":
                response = requests.put(url, headers=self.headers, json=data, timeout=30)
            else:
                return {"error": f"Unsupported method: {method}"}

            result = response.json() if response.content else {}

            if response.status_code >= 400:
                print(f"[OANDA ERROR] {result}")
                return {"error": result, "status_code": response.status_code}

            return result

        except Exception as error:
            print(f"[OANDA REQUEST ERROR] {error}")
            return {"error": str(error)}

    def get_account_summary(self) -> Dict:
        # Get OANDA account info
        return self._make_request("GET", f"/accounts/{self.account_id}/summary")

    def get_account_balance(self) -> float:
        # Get account balance
        summary = self.get_account_summary()
        return float(summary.get("account", {}).get("balance", 0) or 0)

    def get_buying_power(self) -> float:
        # Get NAV as buying power
        summary = self.get_account_summary()
        return float(summary.get("account", {}).get("NAV", summary.get("account", {}).get("nav", 0)) or 0)

    def get_daily_pnl(self) -> float:
        # Get unrealized profit/loss
        summary = self.get_account_summary()
        return float(summary.get("account", {}).get("unrealizedPL", 0) or 0)

    def get_open_positions(self) -> List[Dict]:
        # Get open forex positions
        response = self._make_request("GET", f"/accounts/{self.account_id}/positions")
        positions = []

        for position in response.get("positions", []):
            long_units = float(position.get("long", {}).get("units", "0") or 0)
            short_units = float(position.get("short", {}).get("units", "0") or 0)

            if long_units != 0:
                positions.append({
                    "symbol": position["instrument"],
                    "qty": abs(long_units),
                    "side": "long",
                    "avg_entry_price": float(position["long"].get("averagePrice", 0) or 0),
                    "unrealized_pl": float(position["long"].get("unrealizedPL", 0) or 0),
                })

            if short_units != 0:
                positions.append({
                    "symbol": position["instrument"],
                    "qty": abs(short_units),
                    "side": "short",
                    "avg_entry_price": float(position["short"].get("averagePrice", 0) or 0),
                    "unrealized_pl": float(position["short"].get("unrealizedPL", 0) or 0),
                })

        return positions

    def place_market_order(self, symbol: str, units: int, stop_loss: Optional[float] = None, take_profit: Optional[float] = None) -> Dict:
        # Format symbol like EUR_USD
        if "_" not in symbol and len(symbol) == 6:
            symbol = f"{symbol[:3]}_{symbol[3:]}"

        order = {
            "order": {
                "type": "MARKET",
                "instrument": symbol,
                "units": str(int(units)),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }

        # Optional stop loss
        if stop_loss is not None:
            order["order"]["stopLossOnFill"] = {
                "price": str(round(stop_loss, 5)),
                "timeInForce": "GTC",
            }

        # Optional take profit
        if take_profit is not None:
            order["order"]["takeProfitOnFill"] = {
                "price": str(round(take_profit, 5)),
                "timeInForce": "GTC",
            }

        response = self._make_request("POST", f"/accounts/{self.account_id}/orders", data=order)

        if "orderFillTransaction" in response:
            order_id = response["orderFillTransaction"]["id"]
            return {"id": order_id, "result": response}

        print(f"[OANDA ORDER FAILED] {response}")
        return {"id": None, "result": response}

    def get_order_status(self, order_id: str) -> Dict:
        # Check order status
        return self._make_request("GET", f"/accounts/{self.account_id}/orders/{order_id}")

    def get_candle_data(self, symbol: str, count: int = 100, granularity: str = "M5") -> List[Dict]:
        # Get candles for strategy
        if "_" not in symbol and len(symbol) == 6:
            symbol = f"{symbol[:3]}_{symbol[3:]}"

        params = {
            "count": count,
            "granularity": granularity,
            "price": "M",
        }

        response = self._make_request("GET", f"/instruments/{symbol}/candles", params=params)

        candles = []
        for candle in response.get("candles", []):
            if candle.get("complete"):
                candles.append({
                    "time": candle["time"],
                    "open": float(candle["mid"]["o"]),
                    "high": float(candle["mid"]["h"]),
                    "low": float(candle["mid"]["l"]),
                    "close": float(candle["mid"]["c"]),
                    "volume": candle.get("volume", 0),
                })

        return candles

    def close_position(self, symbol: str) -> Dict:
        # Close position
        if "_" not in symbol and len(symbol) == 6:
            symbol = f"{symbol[:3]}_{symbol[3:]}"

        return self._make_request(
            "PUT",
            f"/accounts/{self.account_id}/positions/{symbol}/close",
            data={"longUnits": "ALL", "shortUnits": "ALL"},
        )

    def close_all_positions(self) -> Dict:
        # Close all open positions
        positions = self.get_open_positions()
        results = []

        for position in positions:
            results.append(self.close_position(position["symbol"]))

        return {"closed_positions": len(results), "results": results}

    def is_market_open(self) -> bool:
        # Basic forex market check
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
