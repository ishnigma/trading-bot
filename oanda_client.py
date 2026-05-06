# oanda_client.py
# Handles OANDA API requests for candles and market orders

import requests
from config import OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_BASE_URL


class OandaClient:
    def __init__(self):
        # Save OANDA account settings
        self.base_url = OANDA_BASE_URL
        self.account_id = OANDA_ACCOUNT_ID

        # Authorization headers for OANDA
        self.headers = {
            "Authorization": f"Bearer {OANDA_API_KEY}",
            "Content-Type": "application/json",
        }

    def get_candles(self, instrument="EUR_USD", count=50, granularity="M5"):
        # Get recent price candles from OANDA
        url = f"{self.base_url}/instruments/{instrument}/candles"

        params = {
            "count": count,
            "granularity": granularity,
            "price": "M",
        }

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=15)
            data = response.json()

            # Print OANDA error if candles fail
            if response.status_code >= 400:
                print(f"[OANDA CANDLES FAILED] {data}")
                return []

            # Return only completed candle close prices
            return [
                float(candle["mid"]["c"])
                for candle in data.get("candles", [])
                if candle.get("complete")
            ]

        except Exception as error:
            print(f"[OANDA CANDLES ERROR] {error}")
            return []

    def place_order(self, instrument="EUR_USD", units=1000, side="buy"):
        # Place a buy or sell market order
        url = f"{self.base_url}/accounts/{self.account_id}/orders"

        # Buy uses positive units, sell uses negative units
        final_units = abs(int(units)) if side.lower() == "buy" else -abs(int(units))

        payload = {
            "order": {
                "units": str(final_units),
                "instrument": instrument,
                "timeInForce": "FOK",
                "type": "MARKET",
                "positionFill": "DEFAULT",
            }
        }

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=15)
            data = response.json()

            # Only count it as success if OANDA confirms fill
            if "orderFillTransaction" in data:
                order_id = data["orderFillTransaction"]["id"]
                print(f"[OANDA ORDER SUCCESS] {order_id}")
                return order_id

            print(f"[OANDA ORDER FAILED] {data}")
            return None

        except Exception as error:
            print(f"[OANDA ORDER ERROR] {error}")
            return None
