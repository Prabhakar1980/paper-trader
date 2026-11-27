# US lessons log

Read this before every US run, together with ../lessons.md (the ASX lessons apply here too). Newest at the bottom. Process errors carry a fix; bad luck is a single line.

## 2026-11-25 (US day 1, setup on 27 Nov Sydney time)
- US book opened at US$50,000 on the 25 Nov US close, at Prabhakar's request: separate account, same rulebook, SPY benchmark from $716.70.
- Universe: S&P 500 constituents (us/universe.csv, 503 names, with GICS sector and sub-industry for the rule 6 check).
- Market filter (rule 11) shut from the first day: S&P 500 closed 7183 below its 50-day EMA 7521. No candidates reviewed, no orders placed; 8 gate-shut names (CB, KO, AEE, MCK, PG, CAH, HLT, WRB) went to the shadow journal.
- Watch: US companies report quarterly and most pay quarterly dividends, so rule 14 (results and ex-dividend dates) will block far more US candidates than ASX ones. That is the rule working, not an error.

## 2026-11-26 (Thanksgiving, US market closed)
- `update` processed no bar (last processed 25 Nov). No positions, no orders, nothing to review; candidates not run. Next session is Friday 27 Nov (half day, 1pm New York close).

## 2026-11-27 (half day, 1pm New York close)
- No closed trades; nothing to tag. No positions, no pending orders.
- Market filter (rule 11) shut for a second session: S&P 500 closed 7297.5 below its 50-day EMA 7511.9 (7183 → 7298; gap to EMA narrowed from 338 to 214 points). No candidates reviewed, no orders placed; 8 gate-shut names (COST, BDX, MCK, EG, CAH, BRK.B, RSG, MSCI) went to the shadow journal. `candidates` run once only.
- Shadows from 25 Nov: KO and PG no fill (traded through only before 10:30); CB, AEE, MCK, CAH, HLT cancelled on the open filter after a gap-up. No shadow closes yet.
