# oanda_client.py
# Handles all communication with OANDA API

import requests
from config import OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_BASE_URL


class OandaClient:
    def __init__(self):
        # Base API settings
        self.base_url = OANDA_BASE_URL
        self.account_id = OANDA_ACCOUNT_ID

        # Auth headers required by OANDA
        self.headers = {
            "Authorization": f"Bearer {OANDA_API_KEY}",
            "Content-Type": "application/json"
        }

    def get_candles(self, instrument="EUR_USD", count=50, granularity="M5"):
        """
        Fetch recent candle data
        Used by your strategy to calculate EMA
        """
        url = f"{self.base_url}/instruments/{instrument}/candles"

        params = {
            "count": count,
            "granularity": granularity,
            "price": "M"
        }

        try:
            response = requests.get(url, headers=self.headers, params=params)
            data = response.json()

            # Extract closing prices
            candles = [
                float(c["mid"]["c"])
                for c in data.get("candles", [])
                if c["complete"]
            ]

            return candles

        except Exception as e:
            print(f"[OANDA ERROR] get_candles: {e}")
            return []

    def place_order(self, instrument="EUR_USD", units=1000, side="buy"):
        """
        Place a market order
        """

        url = f"{self.base_url}/accounts/{self.account_id}/orders"

        # Buy = positive units, Sell = negative units
        units = abs(units) if side == "buy" else -abs(units)

        order_data = {
            "order": {
                "units": str(units),
                "instrument": instrument,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT"
            }
        }

        try:
            response = requests.post(url, headers=self.headers, json=order_data)
            data = response.json()

            # Check if order was actually filled
            if "orderFillTransaction" in data:
                order_id = data["orderFillTransaction"]["id"]
                print(f"[ORDER SUCCESS] ID: {order_id}")
                return order_id

            else:
                print(f"[ORDER FAILED] {data}")
                return None

        except Exception as e:
            print(f"[OANDA ERROR] place_order: {e}")
            return None