# US daily run (Tuesday to Saturday mornings, Sydney time, after the US close)

The US book is a separate US$50,000 paper account in `us/`. Follow `DAILY_RUN.md` step by step, with these differences. Paper money only.

## 0. Setup
- Same repo and skill as the ASX run. Fetch the latest `origin/main` first: the ASX evening run also pushes to it.
- Read `RULES.md` (including the "US book" section), `us/lessons.md` and `lessons.md`.
- Add `--market us` to **every** engine command. Without it you are changing the ASX book.

## 1. Update
`python engine.py --market us update`. If `processed` is empty (US holiday, e.g. Thanksgiving, or a weekend), skip to step 5 and report "market closed".

## 2. Mistake review
`python engine.py --market us review`. Write entries in `us/lessons.md`, tag with `python engine.py --market us tag ...`. Tuning follows rule 19 using the US book's own closed trades, logged in `us/changelog.md`, and edits `us/params.json` only.

## 3. New trades
`python engine.py --market us candidates --top 5` (run it once per day). If `gates.open` is false, place nothing and note why.

For each candidate, the review is the same as the ASX one, except:
1. **News and calendar:** `news.py` reads only the ASX feed, so use web search: "<CODE> earnings date", "<CODE> ex-dividend date", "<CODE> stock news", plus trading halts, offerings (secondary or ATM raisings) and insider selling. US companies report quarterly and most pay quarterly dividends, so check both every time. Rule 14 failure → skip with `--category calendar` or `news`.
2. **Sector (rule 6):** use the GICS sector and sub-industry from `us/universe.csv` to choose the industry group. Count only US holdings and orders.
3. **Debate:** as in `references/debate.md`; macro means the Fed, US yields, the dollar and the sector's drivers.
4. Place with `python engine.py --market us place ...`. Never work around a refusal.
5. Log every skip with `python engine.py --market us skip ...`.

## 4. Report
`python engine.py --market us report` writes `us/reports/<date>.md`. The date is the US session date.

## 5. Save and notify
- Commit with the message `US daily run <date>: equity US$X, day ±Y` and push to `main`.
- Send TWO push notifications, every run, starting with "US paper":
  a) summary: value, today's P&L, total P&L, vs SPY, trades today, open positions, breaches, go-live x/7; with HALTED, FROZEN, GATES SHUT (reason), MARKET CLOSED or 30-TRADE REVIEW DUE first when they apply.
  b) next session's paper orders from `us/state.json` "orders": CODE BUY qty @ limit, stop, target, risk $; or "no orders" and the reason.
