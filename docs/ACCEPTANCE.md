# Acceptance checklist (v0.2)

## Market data

- [ ] AKShare sync for A-share 601118 returns real rows and records source, fetched_at, adjustment and volume units.
- [ ] Optional RU0 futures series is separately stored as continuous reference data; it is not treated as the company's realized price.
- [ ] ETF daily data uses a separate asset_type and cache file.
- [ ] Empty provider results, network failures, invalid OHLC, duplicate dates and future bars fail clearly; no synthetic fallback.
- [ ] Data files persist under ./data and are not committed to Git.
- [ ] qfq/none/hfq adjustment labels are visible in every report; hfq non-positive prices are rejected.

## Research and evidence

- [ ] OTW context and thesis are read through the configured MCP otw_read transport.
- [ ] Research report distinguishes confirmed facts, market expectations and inferences.
- [ ] Every eligible evidence record has HTTPS source, human review, published_at, effective_at and retrieved_at <= as_of.
- [ ] Inferences never count as confirmed facts or market expectations.
- [ ] Stock OHLCV / RU0 price series do not prove physical supply, inventory, company profit pass-through or what the market priced in.
- [ ] Missing fundamental evidence remains unknown / insufficient_evidence.

## Backtest

- [ ] Buy-and-hold and SMA trend reports include dates, source, adjustment and bar count.
- [ ] Signal uses completed close and is executed no earlier than next session open.
- [ ] Fee and slippage are applied on position changes.
- [ ] Report includes total return, CAGR, annualized volatility, Sharpe, max drawdown, positive-day ratio, exposure and turnover.
- [ ] Insufficient or invalid history is rejected.
- [ ] All results include eligible_for_trading=false and a research-only disclaimer.

## Safety

- [ ] OTW MCP client only invokes otw_read.
- [ ] No OTW runs/orders/documents are created or modified.
- [ ] No real brokerage API, scheduled jobs or autonomous execution.
- [ ] Engine remains bound to 127.0.0.1 and authenticated.
- [ ] No secrets or data cache committed to Git.

## Before any future paper-trading write access

- [ ] Resolve the historic experiment audit gap.
- [ ] Implement and verify a server-side freeze switch in OTW.
- [ ] Complete exchange rules (T+1, lot size, limit up/down, suspensions, fees, quote freshness).
- [ ] Validate point-in-time fundamentals, adjusted price semantics and out-of-sample backtests.
- [ ] Implement idempotent run writes and read-back checks before any paper order.
- [ ] User explicitly approves the paper-trading restoration.
