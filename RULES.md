# Paper-trading rulebook v1 (agreed 11 Nov 2026)

Account: $50,000 paper money · brokerage $5.50 each way ($11 round trip) · paper only, nothing reaches a broker.

## Loss limits (fixed)
1. Daily loss max $250. If hit, no new orders that day.
2. Weekly loss max $500. If hit, no new orders for the rest of the week.
3. Drawdown: $1,500 below the peak halts all trading until Prabhakar approves a restart (`"halted": false` in state.json).

## Position limits (fixed)
4. Max 3 trades at a time (open positions + pending orders).
5. Max $100 loss per stock incl. brokerage. Total open + pending risk never above $250 (so tomorrow's worst case fits rule 1). A gap through a stop is logged as a breach.
6. One stock per GICS industry group, from the fixed list of 25 in engine.py (e.g. one bank, one insurer, one Materials stock; all miners count as Materials). The engine refuses any label not on the list.
7. After 3 losses in a row, risk is $50 per trade until the next winner.

## Stops
8. Stop at least 1.5 ATR below entry and below the last swing low. Size shrinks to fit rule 5; the stop is never tightened to fit.
9. Stops only move up: to breakeven (covering brokerage) once the trade is +1.5R. Never average down.
10. Time stop: exit at the close after 15 trading days.

## Entries
11. Market filter: new trades only when the ASX 200 closes above its 50-day EMA.
12. Stocks: 20-day turnover ≥ $2m/day, ATR ≤ 2.5% of price, price ≥ $0.50, position ≥ $2,200 (brokerage ≤ 0.5%), ≤ 20% of equity, ≤ 1% of average daily volume.
13a. No entries in the first hour: an order can only fill from 11:00am Sydney time. A dip to the limit between 10:00 and 11:00 doesn't count, and the order stays live. Fills are checked on 5-minute prices; if those aren't available (more than 5 days old), the daily price is used and the fill is marked "unverified".
13. Limit orders only. Cancel after 3 days unfilled, or if the stock opens > 0.5 ATR above the limit, or opens at/below the stop.
14. No entry within 5 trading days of results; no ex-dividend date inside the holding window; no halted stocks; no pending capital raising.
15. Cooldown: no re-entry for 5 trading days after a stop-out.
16. Target ≥ 2R, with the nearest swing-high resistance at least 2R above entry.
17. Every trade first passes the asx-trader review: scan, entry plan, news, calendar, bull/bear debate (must be TRADE PLAN OK), risk gate and 10,000 replays (expectancy ≥ 0.10R).

## Learning and reporting
18. Daily review tags each closed trade as process error (a rule or check failed) or bad luck (rules followed). Lessons go in `lessons.md`, read first every run.
19. Settings in `params.json` may change only after ≥ 8 closed trades show a pattern, within the bounds in `engine.py`, at most one change per week, each recorded in `changelog.md`. Rules 1–17 above are never changed by the system.
20. Daily report after the close: value, today's P&L, total P&L, trades, open positions, breaches.
21. Benchmark: a $50k line in the IOZ ASX 200 ETF (price only, so dividends of about 3.5% a year are left out) is tracked daily alongside the account.
21b. Shadow journal: rejected candidates (by filter, gate, debate, calendar, sector or no slot) are tracked as if taken, under the same fill and exit rules, and measured in R. This shows whether each filter protects the account or just costs trades. It informs the 30-trade review only; it never changes rules automatically.
22. Judge after ≥ 30 closed trades, against holding an ASX 200 ETF over the same period.

## Go-live criteria (agreed in advance; all must be met before real money)
1. At least 30 closed trades.
2. Net P&L after brokerage is positive.
3. Average R is positive.
4. The account beats the $50k ASX 200 ETF line (IOZ, price only, started 11 Nov 2026 at $36.69).
5. The drawdown halt never triggered.
6. No rule breaches in the last 10 trades.
7. No process errors in the last 10 trades.

Meeting them doesn't mean "go big". It means a sit-down review with Prabhakar, then real money at the same $100-per-trade risk. `python engine.py golive` shows the checklist; the daily report shows the score. At 30 closed trades the report flags **30-TRADE REVIEW DUE** until `"review30_done": true` is set in state.json.

## Corporate actions
If a held stock opens more than 40% away from its previous close, or Yahoo rewrites its price history (a split or consolidation), the position is **frozen**: no exits are processed and the report flags it for manual review. Pending orders on such a stock are cancelled.

## How fills and exits are decided (no "stop first" assumption)
- The run happens after the close, but it replays the day minute by minute using 1-minute prices (last 7 trading days) or 5-minute prices (up to 60 days). Whichever of the stop or the target was actually reached first is the exit, with its time recorded.
- An open below the stop sells at the open (a gap). An open above the target sells at the open.
- Only when a single minute bar touches both levels is the order estimated, using whichever level is nearer that bar's open. Such exits are tagged "estimate" in the trade record and the daily review.
- With no intraday data at all (run missed for more than 60 days), daily prices are used and the result is tagged "daily data only".
- **Realistic fills:** a buy limit fills only if the price trades at least one tick *below* it (a touch isn't enough, because of the queue). A target sell needs a trade one tick *above* it. Fills are at the limit, or the bar's open if that's better.
- **Stop slippage:** stops fill one tick below the stop price (or at the open on a gap). Position sizing includes this tick, so the $100 cap holds.
