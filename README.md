# paper-trader

A $50,000 **paper** account for ASX swing trades. No real orders are ever placed.

- `RULES.md`: the agreed rulebook
- `DAILY_RUN.md`: what the scheduled run does each weekday after the close
- `engine.py`: fills, stops, rule gates, sizing, scanning and reports (uses the asx-trader skill's scripts)
- `state.json`: account, positions, orders, closed trades, equity history
- `params.json`: tunable settings (bounded; changes are logged in `changelog.md`)
- `lessons.md`: the daily mistake review
- `reports/YYYY-MM-DD.md`: the daily report

To restart after a drawdown halt, set `"halted": false` in `state.json`.
