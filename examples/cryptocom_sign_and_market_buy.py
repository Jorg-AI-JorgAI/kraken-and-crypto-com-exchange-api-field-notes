"""Crypto.com Exchange v1: request envelope, signature, and a dollar-sized
market buy. Standard library only. Set CRYPTOCOM_API_KEY and
CRYPTOCOM_API_SECRET; the key needs Trading on, Withdrawal off, and the
caller's IP on the key's whitelist.

Numeric fields are strings. A MARKET BUY takes `notional` (quote amount),
a MARKET SELL takes `quantity`. Round to the instrument's qty_tick_size
first; the fill can land a little under the notional you asked for.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.request

API = "https://api.crypto.com/exchange/v1/"
KEY = os.environ["CRYPTOCOM_API_KEY"]
SECRET = os.environ["CRYPTOCOM_API_SECRET"]


def _flatten(params) -> str:
    """Sorted keys, key+value concatenated with no separators, recursing
    into dicts and lists; this is what the signature covers."""
    if isinstance(params, dict):
        return "".join(k + _flatten(params[k]) for k in sorted(params))
    if isinstance(params, list):
        return "".join(_flatten(v) for v in params)
    return "null" if params is None else str(params)


def private(method: str, params: dict | None = None) -> dict:
    params = params or {}
    req_id = int(time.time() * 1000)
    nonce = int(time.time() * 1000)          # milliseconds, within 60 s of their clock
    payload = f"{method}{req_id}{KEY}{_flatten(params)}{nonce}"
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    body = json.dumps({"id": req_id, "method": method, "api_key": KEY, "params": params, "nonce": nonce, "sig": sig}).encode()
    req = urllib.request.Request(API + method, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    code = data.get("code")
    if code not in (0, "0", None):
        if int(code) in (40101, 40103, 40104):
            raise PermissionError(f"key or signature rejected (code {code}); check the key, the secret and the IP whitelist")
        raise RuntimeError(f"{method} failed (code {code}): {data.get('message')}")
    return data.get("result", {})


def market_buy_dollars(instrument: str, dollars: str) -> dict:
    """instrument like 'BTC_USD'; dollars as a STRING, e.g. '10'."""
    return private("private/create-order", {
        "instrument_name": instrument, "side": "BUY", "type": "MARKET",
        "notional": dollars, "spot_margin": "SPOT",
    })


if __name__ == "__main__":
    print(private("private/get-accounts"))
