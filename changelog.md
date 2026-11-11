# Settings changelog

| Date | Setting | Old → New | Evidence |
|---|---|---|---|
| 2026-11-11 | all | defaults | Rulebook v1 agreed: stop 1.5 ATR, target 2R, breakeven at 1.5R, time stop 15 days, order expiry 3 days, max ATR 2.5%, min turnover $2m, min price $0.50, min score 70, min expectancy 0.10R |
| 2026-11-11 | entry_not_before | none → 11:00 | Rule 13a added by Prabhakar: no entries in the first hour |
| 2026-11-11 | exit resolution | stop-first assumption → minute-by-minute replay | Prabhakar: resolve stop vs target by actual time, not assumption |
| 2026-11-11 | shadow journal | added | Prabhakar approved tracking rejected trades to test each filter |
| 2026-11-11 | fills | touch → trade-through by 1 tick; stops −1 tick slippage; sizing includes the tick | Realism review. IAG order resized 317 → 306 to keep risk ≤ $100 |
| 2026-11-11 | sector | free text → fixed list of 25 GICS industry groups | Rule 6 enforcement |
| 2026-11-11 | benchmark | added IOZ line from $36.69 (11 Nov close) | Rule 21 |
| 2026-11-11 | go-live | 7 criteria + 30-trade review flag + trade tagging | Agreed with Prabhakar |
| 2026-11-11 | corporate actions | freeze on >40% open gap or rewritten price history | Split/consolidation safety |
