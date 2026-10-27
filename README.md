# Kraken and Crypto.com Exchange API field notes

Practical notes from running a production trading integration against the Kraken Spot REST API and the Crypto.com Exchange API (v1). These are the things that broke for us or surprised us with real orders in real accounts, why, and what fixed them. Each one cost us time or a member's trade, and none of them is spelled out clearly in the official docs.

Maintained by the team behind [JorgAI](https://jorgai.com), an auto-trading platform that places trades inside members' own brokerage and exchange accounts. The trader-facing setup guides are here: [How to automate crypto trading on Kraken](https://jorgai.com/blog/how-to-automate-crypto-trading-on-kraken) and [How to automate crypto trading on the Crypto.com Exchange](https://jorgai.com/blog/how-to-automate-crypto-trading-on-crypto-com-exchange). The permission-by-permission guide to the Kraken key form is [here](https://jorgai.com/blog/kraken-api-key-permissions-for-a-trading-bot-2026-which-toggles-to-turn-on-which-to-leave-off-and-why).

Nothing here is affiliated with or endorsed by Kraken or Crypto.com. Verify against the current docs at [docs.kraken.com](https://docs.kraken.com/api/) and [exchange-docs.crypto.com](https://exchange-docs.crypto.com/) before relying on any detail; both exchanges change things. Dates below are when we verified each item.

## Quick reference

| Item | Kraken (Spot REST v0) | Crypto.com Exchange (v1) |
|------|------------------------|---------------------------|
| Auth | API key + private key; `API-Sign` = base64(HMAC-SHA512(base64decode(secret), path + SHA256(nonce + postdata))) | API key + secret; `sig` = hex(HMAC-SHA256(secret, method + id + api_key + flattened params + nonce)) |
| Nonce | Always-increasing integer per key (we use microseconds) | Epoch milliseconds, must be within 60 s of their clock |
| Permissions a bot needs | Query Funds, Query Open Orders & Trades, Query Closed Orders & Trades, Modify Orders, Cancel/Close Orders, Query Ledger Entries. Never Withdraw Funds | Trading on, Withdrawal off |
| IP allow-list | Optional | Effectively required: the key only accepts calls from whitelisted addresses |
| Symbols | Three names per pair: legacy key `XXBTZUSD`, altname `XBTUSD`, wsname `XBT/USD`. Bitcoin is `XBT`, Dogecoin is `XDG` | `BTC_USD` style, case-sensitive, underscore |
| Market buy sizing | `volume` in base units; `oflags=viqc` sizes a market buy in quote currency | `notional` (quote amount) for a market buy, `quantity` for a market sell; numeric fields are strings |
| Minimums | `AssetPairs`: `ordermin` (base), `costmin` (quote), `lot_decimals`, `tick_size` | `public/get-instruments`: `qty_tick_size`, `price_tick_size`; the tick doubles as the minimum |
| Stops | `stop-loss`, `stop-loss-limit`, `take-profit`, `take-profit-limit`, `trailing-stop` | `STOP_LOSS`, `STOP_LIMIT`, `TAKE_PROFIT` variants; stops carry `ref_price` |
| Bracket / OCO | Not available (one conditional close per order at most) | Not available |
| Errors | HTTP 200 with `{"error": ["EAPI:Invalid key", ...]}`; read the array | JSON `code` field; `40101`, `40103`, `40104` are key or signature problems; HTTP 401/403 also appear |
| Rate limits | Private call counter: 15 (starter), 20 (intermediate, pro); most calls cost 1, history calls 2; decays 1 point every 3 s (starter), 2 s (intermediate), 1 s (pro) | Order create/cancel 15 per 100 ms; order detail 30 per 100 ms; trade history 1 per second; other private calls 3 per 100 ms |
| Sandbox | None for spot | None for retail (UAT is institutional, invitation only) |

Verified October 15 to 21, 2026 against the live APIs and our own accounts.

## Trap 1 (Kraken): a three-coin account polled every two minutes burns the starter-tier call counter

Kraken meters private REST calls with a decaying counter: 15 points at the starter verification tier, most calls costing 1, ledger and trade-history calls costing 2, decaying one point every three seconds. That is generous for a human and tight for software that reconciles positions.

The first member to connect a Kraken account held three coins. Our reconciler read balances and trade history for each coin every couple of minutes, plus the scanner's own reads. Within an hour the key had tripped `EAPI:Rate limit exceeded` sixty times. Nothing was wrong with the account; the integration was simply spending the budget on reads that returned the same answer every time.

**What fixed it:** a short cache in front of the read calls, keyed by API key: balances for 15 seconds, trade history for 60 seconds, and the cache is dropped the moment an order is placed or cancelled so a fill read-back or a sell-size check never sees stale numbers. Per-pair order placement has its own, separate counter (60, 125 or 180 by tier), which a low-frequency bot never approaches. See [`examples/kraken_private_call.py`](examples/kraken_private_call.py).

**The user-facing rule that follows:** a "rate limit exceeded" message to the user is useless; the cause is your polling, not their account. Log it as your own error, not theirs.

## Trap 2 (Kraken): the nonce is per key, so two programs on one key fight each other

Every private request carries a nonce, and Kraken rejects a request whose nonce is lower than the highest it has seen for that key (`EAPI:Invalid nonce`). Two things cause it in practice: a user pasting the same key into a second tool, and your own workers racing each other. Too many invalid nonces earn a temporary ban.

**What fixed it:** microsecond nonces (so parallel workers almost never collide), one retry with a fresh nonce on `EAPI:Invalid nonce` before treating it as an error, and never treating a nonce error as an authentication failure. `EAPI:Invalid key`, `EAPI:Invalid signature` and `EGeneral:Permission denied` are the member's key; `EAPI:Invalid nonce` is ordering. Kraken also offers a "nonce window" on the key form that tolerates small out-of-order arrivals; it helps, it does not fix a shared key.

## Trap 3 (both): there is no bracket order, so every exit lives on your side

Neither exchange links a take-profit to a stop-loss. Kraken accepts a single conditional close on an order; Crypto.com has stop and take-profit order types but no one-cancels-the-other pair. If you place both a stop and a target as resting orders, both are live, and when one fills the other is still there, now an unhedged order on a position you no longer hold.

**What we do:** place the entry, record the stop and target in our own system, and run the exits from a monitor that evaluates every open position on a short loop, around the clock, because the market does not close. We mark every crypto entry `bracket_failed=True` internally so the rest of the platform knows the broker holds no exits. The consequence to be honest about: if your connection to the exchange drops, your exits stop with it, unlike a bracket resting at a stock broker. Design the connection monitoring and the position sizing with that in mind.

## Trap 4 (Crypto.com): the key sees the Exchange balance, not the App balance

Crypto.com has two products behind one login: the App wallet and the Exchange. An Exchange API key reads the Exchange balance only. The most common support question we get on this connection is "it shows zero balance", and the answer is always the same: the coins are in the App. The user transfers them to the Exchange (free, instant, inside the Crypto.com app) and the balance appears.

**What we do:** on connect, if the balance read succeeds but every balance is zero, say exactly that on the screen ("your key is working; your Exchange balance is empty; transfer from the App to the Exchange") rather than a generic "no funds".

## Trap 5 (Crypto.com): a dollar-sized market buy fills slightly under the dollar amount

A market buy takes `notional` (a quote-currency amount, "10" for ten dollars) and fills to the instrument's quantity increment, so a ten-dollar order can fill at $9.97 worth. A market sell takes `quantity`, not `notional`. Stops carry a `ref_price`. Every numeric field is a string. Send a float where a string is expected and the error message does not say so.

**What we do:** round quantities down to `qty_tick_size` before an order leaves, treat the tick as the minimum order size, and read `cumulative_quantity` from the order after the fill rather than trusting the requested amount. Leave a small cushion when sizing a sell from a prior buy's notional.

## Trap 6 (Crypto.com): the IP whitelist is not optional in practice

The key form offers an IP whitelist. Without the caller's address on it, private calls fail in ways that look like a bad key. If you run a hosted service, publish your outbound addresses and tell users to add them when they create the key; if you run from a laptop, your home IP changes and the key stops working one morning for no visible reason.

## Trap 7 (Kraken): three names for every pair and two odd asset codes

`XXBTZUSD`, `XBTUSD` and `XBT/USD` are the same pair, and responses can use any of them (`AddOrder` accepts the altname; `Balance` keys assets as `XXBT`, `ZUSD`, `XETH`, `SOL`). Bitcoin is `XBT` and Dogecoin is `XDG`. Build one pair table from `AssetPairs` and `Assets` at startup and map every response through it; do not string-match.

## Smaller things worth knowing

- **Kraken errors arrive with HTTP 200.** Parse the `error` array on every response; a status check alone passes failures through.
- **Kraken fees on buys come out of the quote currency by default** (`fciq`), so the whole bought amount is sellable. Keep a sell-size clamp anyway.
- **Kraken has no spot sandbox and Crypto.com's is institutional.** We proved both integrations with a tiny live order in our own account. Budget for it.
- **Key expiration on Kraken** is a free rotation reminder. Rotate by creating the new key first, confirming a balance read, then deleting the old one, so open positions never sit without their exits.
- **Key-level 2FA on Kraken** requires a one-time code on every private call. Leave it off for a bot and keep 2FA on the login, which Kraken requires before it issues API keys at all.
- **Market data for scanning** does not have to come from the exchange that executes. We size and price orders on the executing exchange's public ticker and scan on a separate feed, which keeps the private-call budget for orders and reconciliation.

## Who we are

JorgAI runs rules-based automated trading in members' own brokerage and exchange accounts (Schwab, Alpaca, Tradier, tastytrade, Kraken and the Crypto.com Exchange). The Schwab equivalent of these notes is [schwab-trader-api-field-notes](https://github.com/Jorg-AI-JorgAI/schwab-trader-api-field-notes). Corrections welcome as issues or pull requests.

## License

MIT. See [LICENSE](LICENSE).
