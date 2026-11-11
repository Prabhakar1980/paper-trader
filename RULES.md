# Paper-trading rulebook v1 (agreed 11 Nov 2026)

Account: $50,000 paper money · brokerage $5.50 each way ($11 round trip) · paper only, nothing reaches a broker.

## Loss limits (fixed)
1. Daily loss max $250. If hit, no new orders that day.
2. Weekly loss max $500. If hit, no new orders for the rest of the week.
3. Drawdown: $1,500 below the peak halts all trading until Prabhakar approves a restart (`"halted": false` in state.json).

## Position limits (fixed)
4. Max 3 trades at a time (open positions + pending orders).
5. Max $100 loss per stock incl. brokerage. Total open + pending risk never above $250 (so tomorrow's worst case fits rule 1). A gap through a stop is logged as a breach.
6. One stock per GICS industry group (e.g. one bank, one insurer, one gold miner).
7. After 3 losses in a row, risk is $50 per trade until the next winner.

## Stops
8. Stop at least 1.5 ATR below entry and below the last swing low. Size shrinks to fit rule 5; the stop is never tightened to fit.
9. Stops only move up: to breakeven (covering brokerage) once the trade is +1.5R. Never average down.
10. Time stop: exit at the close after 15 trading days.

## Entries
11. Market filter: new trades only when the ASX 200 closes above its 50-day EMA.
12. Stocks: 20-day turnover ≥ $2m/day, ATR ≤ 2.5% of price, price ≥ $0.50, position ≥ $2,200 (brokerage ≤ 0.5%), ≤ 20% of equity, ≤ 1% of average daily volume.
13. Limit orders only. Cancel after 3 days unfilled, or if the stock opens > 0.5 ATR above the limit, or opens at/below the stop.
14. No entry within 5 trading days of results; no ex-dividend date inside the holding window; no halted stocks; no pending capital raising.
15. Cooldown: no re-entry for 5 trading days after a stop-out.
16. Target ≥ 2R, with the nearest swing-high resistance at least 2R above entry.
17. Every trade first passes the asx-trader review: scan, entry plan, news, calendar, bull/bear debate (must be TRADE PLAN OK), risk gate and 10,000 replays (expectancy ≥ 0.10R).

## Learning and reporting
18. Daily review tags each closed trade as process error (a rule or check failed) or bad luck (rules followed). Lessons go in `lessons.md`, read first every run.
19. Settings in `params.json` may change only after ≥ 8 closed trades show a pattern, within the bounds in `engine.py`, at most one change per week, each recorded in `changelog.md`. Rules 1–17 above are never changed by the system.
20. Daily report after the close: value, today's P&L, total P&L, trades, open positions, breaches.
21. Judge after ≥ 30 closed trades, against holding an ASX 200 ETF over the same period.

Simulation is end-of-day: if a day touches both stop and target, the stop is assumed first. Fills at the limit or the open, whichever is lower.
