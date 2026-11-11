# Daily run (weekdays after the ASX close)

Follow every step in order. Paper money only. Read `RULES.md` and `lessons.md` before step 3.

## 0. Setup
- Work in the clone of `Prabhakar1980/paper-trader`.
- Load the asx-trader skill with the Skill tool (`anthropic-skills:asx-trader`). The engine finds its scripts under `~/.claude/skills/**/asx-trader/scripts`.

## 1. Update the account
`python engine.py update`. This processes every new daily bar: fills, stops, targets, breakeven moves, time stops, cancellations, the drawdown halt. If `processed` is empty (holiday or weekend), skip to step 5 and report "market closed".

## 2. Mistake review (rule 18)
`python engine.py review`. For each trade closed since the last run, add a dated entry to `lessons.md`:
- **Process error** if a rule or check failed (gap-through on a stock that had news pending, entry near results, a sector clash, a debate that ignored a known risk). State the fix for next time.
- **Bad luck** if the rules were followed. Write one line and don't over-learn from it.
- Use the tags: `shakeout` (stopped, then hit the original target within 10 days), `breakeven stop`, `time stop`, `gap-through`, `RULE BREACH`.

**Tuning (rule 19).** Only if `tuning_allowed` is true (≥ 8 closed trades), at most one change per week, within the bounds:
- shakeouts ≥ 30% of stop-outs → `stop_atr_mult` +0.25
- breakeven stops that later reached the target ≥ 2 → `breakeven_at_R` +0.25
- time stops ≥ 40% of exits → `time_stop_days` −2, or `min_expected_R` +0.05
- gap-throughs ≥ 2 → `max_atr_pct` −0.25
Write the change to `params.json` and record the date, evidence and old → new value in `changelog.md`. Never edit the fixed `RULES` in engine.py.

## 3. New trades
`python engine.py candidates --top 5` (takes about 2 minutes). If `gates.open` is false, place nothing and note why.

For each candidate in order, while slots and risk budget remain, run the asx-trader review:
1. `news.py CODE`, plus a web search for the results date, ex-dividend date, trading halts, capital raisings and director selling. Rule 14 failure → skip.
2. GICS industry group. A clash with a held or pending stock (rule 6) → skip.
3. Bull/bear debate per the skill's `references/debate.md`, using the candidate's numbers. Run `debate_score.py`. Anything other than TRADE PLAN OK → skip.
   Every candidate you skip at steps 1–3, or because slots or budget ran out, MUST be logged:
   `python engine.py skip --code CODE --category <calendar|sector|debate|news|no slot|other> --reason "..."`
4. `python engine.py place ...` with the candidate's qty, limit, stop, target and atr, plus sector, thesis, debate, debate-net, news, results-clear, exdiv-clear and expected-R. If it is refused, do not work around it.
5. After each placement the risk budget for the next slot changes. Re-run `candidates --tickers <remaining codes>` to resize before placing again.

Mechanical rejections (resistance, size/brokerage, replay odds) and gate-shut days are added to the shadow journal automatically by `candidates` (up to 8 a day).

## 3b. Shadow journal
`python engine.py shadow` shows how rejected trades would have done, by rejection reason, next to the real trades' average R. Mention it in lessons.md only when a category has 10 or more closed shadows. A filter whose blocked trades do **better** than the real trades is a candidate for discussion with Prabhakar at the 30-trade review. Never loosen a fixed rule on your own.

## 4. Report
`python engine.py report` writes `reports/<date>.md`.

## 5. Save and notify
- Commit everything with the message `Daily run <date>: equity $X, day ±Y` and push to `main`.
- Send Prabhakar a short push notification: value, today's P&L, total P&L, trades today (filled, closed, new orders), open positions, breaches, plus one line of lessons if any.
- If the account is halted, say so at the top of the notification.
