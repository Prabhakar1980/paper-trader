#!/usr/bin/env python3
"""Paper-trading engine for a $50k ASX paper account.

Mechanical side only: fills, stops, targets, rule gates, sizing, scanning and reports.
Judgement (news, results dates, sector, bull/bear debate, lessons) is done by Claude
in the daily run, using the asx-trader skill, before `place` is called.

Commands:
  update       process every new daily bar since the last run (fills, exits, limits)
  candidates   if all gates are open, scan the ASX and print sized trade candidates (JSON)
  place        add a reviewed candidate as a pending limit order (enforces every rule again)
  review       mistake-review data for recently closed trades (JSON)
  report       write reports/<date>.md and print it
  status       short JSON status (gates, budgets, positions)

Nothing here can place a real order. Paper money only.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import glob
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state.json")
PARAMS = os.path.join(ROOT, "params.json")


def _skill_scripts():
    env = os.environ.get("ASX_TRADER_SCRIPTS")
    if env and os.path.isdir(env):
        return env
    hits = glob.glob(os.path.expanduser("~/.claude/skills/**/asx-trader/scripts"), recursive=True)
    if not hits:
        sys.exit("asx-trader skill scripts not found; set ASX_TRADER_SCRIPTS")
    return hits[0]


sys.path.insert(0, _skill_scripts())
import common as C  # noqa: E402
import entry as E  # noqa: E402
import scan as SC  # noqa: E402
import timemachine as TM  # noqa: E402

# ---------- fixed rules (user's rulebook v1; never auto-tuned) ----------
RULES = {
    "start_equity": 50000.0,
    "brokerage_per_side": 5.50,
    "daily_loss_limit": 250.0,      # rule 1
    "weekly_loss_limit": 500.0,     # rule 2
    "max_drawdown": 1500.0,         # rule 3
    "max_slots": 3,                 # rule 4 (open + pending)
    "max_risk_per_stock": 100.0,    # rule 5 (incl. round-trip brokerage)
    "max_total_open_risk": 250.0,   # rule 5
    "streak_losses": 3,             # rule 7
    "streak_risk": 50.0,            # rule 7
    "min_trade_risk": 40.0,
    "min_position_value": 2200.0,   # rule 12 (brokerage drag <= 0.5%)
    "max_position_pct": 20.0,
    "max_pct_avg_volume": 1.0,
    "max_order_gap_atr": 0.5,       # rule 13
    "cooldown_days": 5,             # rule 15
    "entry_not_before": "11:00",    # rule 13a: no entries in the first hour (Sydney time)
    "index": "^AXJO",
    "benchmark": "IOZ",             # rule 22: iShares ASX 200 ETF, price only
    "corp_action_jump": 0.40,       # overnight move that flags a possible split/consolidation
    "index_ema": 50,                # rule 11
}

GICS_GROUPS = [
    "Energy", "Materials", "Capital Goods", "Commercial & Professional Services", "Transportation",
    "Automobiles & Components", "Consumer Durables & Apparel", "Consumer Services",
    "Consumer Discretionary Distribution & Retail", "Consumer Staples Distribution & Retail",
    "Food, Beverage & Tobacco", "Household & Personal Products", "Health Care Equipment & Services",
    "Pharmaceuticals, Biotechnology & Life Sciences", "Banks", "Financial Services", "Insurance",
    "Software & Services", "Technology Hardware & Equipment", "Semiconductors & Semiconductor Equipment",
    "Telecommunication Services", "Media & Entertainment", "Utilities", "Equity REITs",
    "Real Estate Management & Development", "ETF / LIC (index exposure)",
]  # rule 6: one stock per GICS industry group

# ---------- tunable settings with hard bounds (rule 17) ----------
PARAM_BOUNDS = {
    "stop_atr_mult": (1.5, 2.5),
    "target_R": (2.0, 3.0),
    "breakeven_at_R": (1.5, 2.0),
    "time_stop_days": (10, 20),
    "order_expiry_days": (2, 5),
    "max_atr_pct": (1.5, 3.0),
    "min_turnover": (2_000_000, 10_000_000),
    "min_price": (0.50, 2.00),
    "min_score": (60, 90),
    "min_expected_R": (0.0, 0.5),
}
PARAM_DEFAULTS = {
    "stop_atr_mult": 1.5, "target_R": 2.0, "breakeven_at_R": 1.5, "time_stop_days": 15,
    "order_expiry_days": 3, "max_atr_pct": 2.5, "min_turnover": 2_000_000, "min_price": 0.50,
    "min_score": 70, "min_expected_R": 0.10,
}


def load_params():
    p = dict(PARAM_DEFAULTS)
    if os.path.isfile(PARAMS):
        p.update(json.load(open(PARAMS)))
    for k, (lo, hi) in PARAM_BOUNDS.items():
        p[k] = min(max(p[k], lo), hi)
    return p


def new_state():
    return {"cash": RULES["start_equity"], "peak_equity": RULES["start_equity"],
            "equity_history": [], "positions": [], "orders": [], "closed": [], "cancelled": [],
            "last_processed": None, "halted": False, "halt_reason": None,
            "breaches": [], "next_id": 1, "created": dt.date.today().isoformat()}


def load_state():
    return json.load(open(STATE)) if os.path.isfile(STATE) else new_state()


def save_state(s):
    json.dump(s, open(STATE, "w"), indent=1)


def brk():
    return RULES["brokerage_per_side"]


def bars_for(code):
    return C.get_bars(code, "1d", "1y")


def trading_dates(bars, after):
    return [b for b in bars if after is None or b["date"] > after]


def week_key(d):
    y, w, _ = dt.date.fromisoformat(d).isocalendar()
    return "%d-W%02d" % (y, w)


# ---------- accounting ----------
def realized_on(s, date):
    return sum(t["pnl"] for t in s["closed"] if t["exit_date"] == date)


def realized_week(s, date):
    wk = week_key(date)
    return sum(t["pnl"] for t in s["closed"] if week_key(t["exit_date"]) == wk)


def loss_streak(s):
    n = 0
    for t in sorted(s["closed"], key=lambda t: (t["exit_date"], t["id"]), reverse=True):
        if t["pnl"] < 0:
            n += 1
        else:
            break
    return n


def trade_risk(qty, entry, stop):
    """Dollars lost if the stop is hit, incl. both brokerage legs (0 if stop at/above entry+costs)."""
    return max(0.0, (entry - stop + C.tick_size(stop)) * qty + 2 * brk())  # incl. 1 tick stop slippage


def open_risk(s):
    r = sum(max(0.0, (p["entry"] - p["stop"] + C.tick_size(p["stop"])) * p["qty"] + brk()) for p in s["positions"])  # entry leg paid
    r += sum(trade_risk(o["qty"], o["limit"], o["stop"]) for o in s["orders"])
    return r


def last_equity(s):
    return s["equity_history"][-1]["equity"] if s["equity_history"] else RULES["start_equity"]


def budgets(s, date):
    """Risk dollars available for one new order tonight, and why gates are shut."""
    reasons = []
    eq = last_equity(s)
    if s["halted"]:
        reasons.append("HALTED: " + (s["halt_reason"] or "drawdown"))
    if s["peak_equity"] - eq >= RULES["max_drawdown"]:
        reasons.append("drawdown limit reached")
    day = realized_on(s, date)
    if day <= -RULES["daily_loss_limit"]:
        reasons.append("daily loss limit reached today (%.2f)" % day)
    wk = realized_week(s, date)
    if wk <= -RULES["weekly_loss_limit"]:
        reasons.append("weekly loss limit reached (%.2f)" % wk)
    slots = RULES["max_slots"] - len(s["positions"]) - len(s["orders"])
    if slots <= 0:
        reasons.append("all %d slots used" % RULES["max_slots"])
    orisk = open_risk(s)
    per_trade = RULES["streak_risk"] if loss_streak(s) >= RULES["streak_losses"] else RULES["max_risk_per_stock"]
    # tomorrow's worst case must stay inside the daily cap and what is left of the weekly cap
    room_day = RULES["daily_loss_limit"] - orisk
    room_week = RULES["weekly_loss_limit"] + min(0.0, wk) - orisk
    avail = max(0.0, min(per_trade, room_day, room_week))
    if avail < RULES["min_trade_risk"] and not reasons:
        reasons.append("risk budget left (%.2f) is below the $%.0f minimum" % (avail, RULES["min_trade_risk"]))
    return {"open": not reasons, "reasons": reasons, "slots": max(0, slots), "risk_per_trade": round(avail, 2),
            "open_risk": round(orisk, 2), "loss_streak": loss_streak(s), "day_pnl_realized": round(day, 2),
            "week_pnl_realized": round(wk, 2), "equity": round(eq, 2), "peak_equity": round(s["peak_equity"], 2)}


def market_filter():
    bars = bars_for(RULES["index"])
    c = [b["c"] for b in bars]
    e = C.last_val(C.ema(c, RULES["index_ema"]))
    return {"index_close": round(c[-1], 1), "ema50": round(e, 1), "date": bars[-1]["date"], "ok": c[-1] > e}


# ---------- daily processing ----------
def load_intraday(code):
    """date -> (bars, resolution). 1-minute bars cover ~7 trading days, 5-minute ~60 days."""
    out = {}
    for interval, rng in (("5m", "60d"), ("1m", "7d")):  # 1m overwrites 5m where both exist
        try:
            days = {}
            for x in C.get_bars(code, interval, rng):
                days.setdefault(x["date"], []).append(x)
            for d, seq in days.items():
                out[d] = (seq, interval)
        except Exception:
            pass
    return out



def close_pos(s, p, date, price, reason, when=None, how=None):
    gross = (price - p["entry"]) * p["qty"]
    pnl = gross - 2 * brk()
    s["cash"] += price * p["qty"] - brk()
    r_unit = p["init_risk_per_share"] * p["qty"]
    t = dict(p)
    t.update(exit_date=date, exit=round(price, 4), reason=reason, pnl=round(pnl, 2), exit_time=when,
             exit_resolution=how, R=round(gross / r_unit, 2) if r_unit else None)
    if pnl < -RULES["max_risk_per_stock"] - 0.01:
        b = {"date": date, "id": p["id"], "code": p["code"], "rule": "5 per-stock loss",
             "detail": "lost %.2f (limit 100) via %s" % (-pnl, reason)}
        s["breaches"].append(b)
        t["breach"] = b["detail"]
    s["closed"].append(t)
    s["positions"].remove(p)


def tk(p):
    return C.tick_size(p)


def walk_exits(seq, stop, target, res):
    """Walk intraday bars in time order and return the level actually reached first.
    Only when one bar (1 minute, or 5 if that is all there is) touches both is the
    order estimated, by which level is nearer that bar's open; that case is flagged."""
    for x in seq:
        hs, ht = x["l"] <= stop, x["h"] >= round(target + tk(target), 4)  # a resting sell needs a trade through it
        if hs and ht:
            first = "stop" if (x["o"] - stop) <= (target - x["o"]) else "target"
            return first, x, "both in one %s bar at %s: %s nearer the open (estimate)" % (res, x["time"], first)
        if hs:
            return "stop", x, "%s bars: stop reached first at %s" % (res, x["time"])
        if ht:
            return "target", x, "%s bars: target reached first at %s" % (res, x["time"])
    return None, None, None


def exit_price(kind, x, stop, target):
    if kind == "stop":
        if x["o"] <= stop:
            return x["o"]           # opened below the stop: sells at the open
        return round(stop - tk(stop), 4)  # one tick of slippage on a stop
    return max(x["o"], target)


def apply_exit(s, p, date, kind, x, how, events, tag=""):
    px = exit_price(kind, x, p["stop"], p["target"])
    if kind == "stop":
        reason = ("breakeven stop" if p["be_moved"] else "stop") + tag
    else:
        reason = "target" + tag
    close_pos(s, p, date, px, reason, x.get("time"), how)
    events.append("%s %s @ %.3f at %s (%s)" % (reason.upper(), p["code"], px, x.get("time", "?"), how))


def process_bar(s, code_bars, date, P, events, intraday=None):
    intraday = intraday or {}
    # pending orders first (orders placed before `date` can fill on `date`)
    for o in list(s["orders"]):
        b = code_bars.get(o["code"], {}).get(date)
        if not b or o["placed"] >= date:
            continue
        o["days_live"] += 1
        seq, res = intraday.get(o["code"], {}).get(date, (None, None))
        fill, ftime, after = None, None, []
        if b["o"] > o["limit"] + RULES["max_order_gap_atr"] * o["atr"]:
            o["cancel_reason"] = "opened %.3f, more than 0.5 ATR above the limit" % b["o"]
        elif b["o"] <= o["stop"]:
            o["cancel_reason"] = "opened at/below the stop, setup void"
        elif b["l"] <= round(o["limit"] - tk(o["limit"]), 4):  # a resting buy needs a trade through it
            if seq:
                late = [x for x in seq if x["time"] >= RULES["entry_not_before"]]
                for i, x in enumerate(late):
                    if x["l"] <= round(o["limit"] - tk(o["limit"]), 4):
                        fill, ftime, after = min(x["o"], o["limit"]), x["time"], late[i:]
                        break
                if fill is None:
                    events.append("NO FILL %s: limit only traded through before %s" % (o["code"], RULES["entry_not_before"]))
            else:  # no intraday data at all: daily fallback, flagged
                fill, ftime = min(b["o"], o["limit"]), "unverified (daily data only)"
        if fill is not None:
            s["cash"] -= fill * o["qty"] + brk()
            p = {k: o[k] for k in ("id", "code", "qty", "stop", "target", "atr", "sector", "thesis", "review")}
            p.update(entry=round(fill, 4), entry_date=date, entry_time=ftime, entry_resolution=res or "daily",
                     init_stop=o["stop"], init_risk_per_share=round(fill - o["stop"], 4), bars_held=0,
                     mfe=fill, mae=fill, be_moved=False)
            s["positions"].append(p)
            s["orders"].remove(o)
            events.append("FILLED %s %d @ %.3f at %s" % (o["code"], o["qty"], fill, ftime))
            if after:
                # the fill bar itself can only stop out (its high may predate the fill); later bars walk normally
                fb = after[0]
                if fb["l"] <= p["stop"]:
                    apply_exit(s, p, date, "stop", fb, "%s bars: fell to the stop in the fill bar %s" % (res, fb["time"]), events, " (fill day)")
                else:
                    kind, x, how = walk_exits(after[1:], p["stop"], p["target"], res)
                    if kind:
                        apply_exit(s, p, date, kind, x, how, events, " (fill day)")
                for x in after[1:]:
                    if p in s["positions"]:
                        p["mfe"], p["mae"] = max(p["mfe"], x["h"]), min(p["mae"], x["l"])
                if p in s["positions"]:
                    p["mae"] = min(p["mae"], fb["l"])
            elif b["l"] <= p["stop"]:  # daily-only fallback: can't order the moves, flag it
                apply_exit(s, p, date, "stop", b, "daily data only: stop touched after an unverified fill", events, " (fill day)")
            continue
        elif o["days_live"] >= P["order_expiry_days"]:
            o["cancel_reason"] = "not filled in %d days" % o["days_live"]
        if o.get("cancel_reason"):
            o["cancel_date"] = date
            s["cancelled"].append(o)
            s["orders"].remove(o)
            events.append("CANCELLED %s: %s" % (o["code"], o["cancel_reason"]))

    for p in list(s["positions"]):
        b = code_bars.get(p["code"], {}).get(date)
        if not b or p["entry_date"] >= date or p.get("frozen"):
            continue
        p["bars_held"] += 1
        p["mfe"], p["mae"] = max(p["mfe"], b["h"]), min(p["mae"], b["l"])
        seq, res = intraday.get(p["code"], {}).get(date, (None, None))
        if b["o"] <= p["stop"]:
            close_pos(s, p, date, b["o"], "gap through stop", "open", "opened below the stop")
            events.append("GAP-STOP %s @ %.3f" % (p["code"], b["o"]))
            continue
        if b["o"] >= p["target"]:
            close_pos(s, p, date, b["o"], "target", "open", "opened above the target")
            events.append("TARGET %s @ %.3f at the open" % (p["code"], b["o"]))
            continue
        kind = None
        if b["l"] <= p["stop"] or b["h"] >= round(p["target"] + tk(p["target"]), 4):
            if seq:
                kind, x, how = walk_exits(seq, p["stop"], p["target"], res)
            if not kind:  # no intraday data: use the daily bar, flag any two-sided day as an estimate
                x = b
                if b["l"] <= p["stop"] and b["h"] >= round(p["target"] + tk(p["target"]), 4):
                    kind = "stop" if (b["o"] - p["stop"]) <= (p["target"] - b["o"]) else "target"
                    how = "daily data only, both touched: %s nearer the open (estimate)" % kind
                else:
                    kind = "stop" if b["l"] <= p["stop"] else "target"
                    how = "daily data only"
            apply_exit(s, p, date, kind, x, how, events)
        elif p["bars_held"] >= P["time_stop_days"]:
            close_pos(s, p, date, b["c"], "time stop", "close", "time stop")
            events.append("TIME-STOP %s @ %.3f" % (p["code"], b["c"]))
        else:
            if not p["be_moved"] and b["h"] >= p["entry"] + P["breakeven_at_R"] * p["init_risk_per_share"]:
                be = C.round_tick(p["entry"] + 2 * brk() / p["qty"], "up")
                if be > p["stop"]:
                    p["stop"], p["be_moved"] = be, True
                    events.append("BREAKEVEN %s stop -> %.3f" % (p["code"], be))
            p["last_close"] = b["c"]


def mark_to_market(s, code_bars, date, bench_bars=None):
    mv = 0.0
    for p in s["positions"]:
        b = code_bars.get(p["code"], {}).get(date)
        px = b["c"] if b else p.get("last_close", p["entry"])
        if not p.get("frozen"):
            p["last_close"] = px
            if b:
                p["last_seen"] = date
        mv += p["last_close"] * p["qty"]
    eq = s["cash"] + mv
    row = {"date": date, "equity": round(eq, 2), "cash": round(s["cash"], 2),
           "positions": len(s["positions"]), "orders": len(s["orders"])}
    bb = (bench_bars or {}).get(date)
    if bb:
        bm = s.setdefault("benchmark", {"code": RULES["benchmark"], "start_date": date, "start_price": bb["c"],
                                        "units": RULES["start_equity"] / bb["c"]})
        row["benchmark"] = round(bm["units"] * bb["c"], 2)
    s["equity_history"].append(row)
    s["peak_equity"] = max(s["peak_equity"], eq)
    if s["peak_equity"] - eq >= RULES["max_drawdown"] and not s["halted"]:
        s["halted"], s["halt_reason"] = True, "drawdown %.2f from peak on %s" % (s["peak_equity"] - eq, date)
        s["ever_halted"] = True
    return eq


def corp_action_check(s, code_bars, dates):
    """Flag possible splits/consolidations: an overnight move over 40%, or Yahoo re-writing the
    close we stored for a held stock (history adjusted for a split). Flagged positions are frozen
    (no exits processed) until reviewed; flagged orders are cancelled."""
    alerts = []
    for p in s["positions"]:
        bars = code_bars.get(p["code"], {})
        seen = p.get("last_seen")
        if seen and seen in bars and p.get("last_close"):
            ratio = bars[seen]["c"] / p["last_close"]
            if abs(ratio - 1) > 0.05:
                p["frozen"] = "price history for %s changed by %.0f%% (split/consolidation?)" % (seen, (ratio - 1) * 100)
        for d in dates:
            ks = sorted(k for k in bars if k < d)
            if d in bars and ks:
                jump = bars[d]["o"] / bars[ks[-1]]["c"] - 1
                if abs(jump) > RULES["corp_action_jump"]:
                    p["frozen"] = "opened %+.0f%% on %s (corporate action?)" % (jump * 100, d)
        if p.get("frozen"):
            alerts.append("%s CORPORATE ACTION? %s: %s. Position frozen for manual review." % (dates[0] if dates else "", p["code"], p["frozen"]))
    for o in list(s["orders"]):
        bars = code_bars.get(o["code"], {})
        for d in dates:
            ks = sorted(k for k in bars if k < d)
            if d in bars and ks and abs(bars[d]["o"] / bars[ks[-1]]["c"] - 1) > RULES["corp_action_jump"]:
                o["cancel_reason"], o["cancel_date"] = "opened >40%% away on %s (corporate action?)" % d, d
                s["cancelled"].append(o); s["orders"].remove(o)
                alerts.append("%s CANCELLED %s: %s" % (d, o["code"], o["cancel_reason"]))
                break
    return alerts


def cmd_update(a):
    s, P = load_state(), load_params()
    sh = shadow_book(s)
    codes = sorted({x["code"] for x in s["positions"] + s["orders"] + sh["positions"] + sh["orders"]})
    idx = bars_for(RULES["index"])
    dates = [b["date"] for b in trading_dates(idx, s["last_processed"])]
    if s["last_processed"] is None:  # first run: start from the latest session, no history to replay
        dates = dates[-1:]
    code_bars, intraday = {}, {}
    for c in codes:
        code_bars[c] = {b["date"]: b for b in bars_for(c)}
    for c in codes:
        intraday[c] = load_intraday(c)
    try:
        bench = {b["date"]: b for b in bars_for(RULES["benchmark"])}
    except Exception:
        bench = {}
    alerts = corp_action_check(s, code_bars, dates)
    out = []
    for d in dates:
        events = [a for a in alerts if a.startswith(d)]
        process_bar(s, code_bars, d, P, events, intraday)
        sbook = {"cash": 0.0, "orders": sh["orders"], "positions": sh["positions"], "closed": sh["closed"],
                 "cancelled": sh["cancelled"], "breaches": []}
        sev = []
        process_bar(sbook, code_bars, d, P, sev, intraday)
        events += ["shadow: " + e for e in sev]
        eq = mark_to_market(s, code_bars, d, bench)
        s["last_processed"] = d
        out.append({"date": d, "equity": round(eq, 2), "events": events})
    save_state(s)
    print(json.dumps({"processed": out, "budgets": budgets(s, s["last_processed"])}, indent=1))


# ---------- shadow journal ----------
LAST_CANDS = os.path.join(ROOT, "last_candidates.json")
SHADOW_CATEGORIES = ["gate: market filter", "gate: limits/slots", "resistance", "size/brokerage", "replay odds",
                     "debate", "calendar", "sector", "news", "no slot", "other"]


def shadow_book(s):
    return s.setdefault("shadow", {"orders": [], "positions": [], "closed": [], "cancelled": [], "next_id": 1})


def shadow_category(why):
    w = why.lower()
    if w.startswith("gate"):
        return "gate: market filter" if "market filter" in w else "gate: limits/slots"
    for key, cat in (("resistance", "resistance"), ("position $", "size/brokerage"), ("size rounds", "size/brokerage"),
                     ("replay", "replay odds")):
        if key in w:
            return cat
    return "other"


def shadow_add(s, plan, why, date, category=None):
    """Track a rejected candidate as if it had been ordered. Returns False if already tracked."""
    sh = shadow_book(s)
    live = {x["code"] for x in sh["orders"] + sh["positions"]}
    if plan["code"] in live:
        return False
    rps = plan["entry_limit"] - plan["stop"]
    qty = plan["qty"] or max(1, int((RULES["max_risk_per_stock"] - 2 * brk()) // rps))
    sid = "S%04d" % sh["next_id"]; sh["next_id"] += 1
    sh["orders"].append({"id": sid, "code": plan["code"], "qty": qty, "limit": plan["entry_limit"],
                         "stop": plan["stop"], "target": plan["target"], "atr": plan["atr"],
                         "sector": "", "placed": date, "days_live": 0, "thesis": why,
                         "review": {"category": category or shadow_category(why), "why": why,
                                    "expected_R": plan["odds"]["expected_R"], "score": plan["scan"]["score"]}})
    return True


def shadow_stats(s):
    sh = shadow_book(s)
    real = s["closed"]
    rows = {}
    for t in sh["closed"]:
        c = t["review"]["category"]
        r = rows.setdefault(c, {"closed": 0, "wins": 0, "sum_R": 0.0})
        r["closed"] += 1; r["wins"] += t["pnl"] > 0; r["sum_R"] += t["R"] or 0
    for c, r in rows.items():
        r["win_rate"] = round(r["wins"] / r["closed"], 2)
        r["avg_R"] = round(r["sum_R"] / r["closed"], 2)
        del r["sum_R"]
    for o in sh["orders"] + sh["positions"] + sh["cancelled"]:
        c = o["review"]["category"]
        r = rows.setdefault(c, {"closed": 0, "wins": 0})
        key = "never_filled" if o in sh["cancelled"] else "live"
        r[key] = r.get(key, 0) + 1
    return {"by_reason": rows, "real_trades": {"closed": len(real),
            "avg_R": round(sum(t["R"] or 0 for t in real) / len(real), 2) if real else None},
            "note": "A filter earns its keep when the trades it blocked average worse than the trades taken. "
                    "Need ~10+ closed shadows in a category before reading anything into it."}


def cmd_skip(a):
    s = load_state()
    last = json.load(open(LAST_CANDS)) if os.path.isfile(LAST_CANDS) else {"candidates": []}
    plan = next((c for c in last["candidates"] if c["code"] == a.code), None)
    if not plan:
        sys.exit("%s is not in last_candidates.json; run candidates first" % a.code)
    ok = shadow_add(s, plan, a.reason, s["last_processed"], a.category)
    save_state(s)
    print(json.dumps({"shadowed": ok, "code": a.code, "category": a.category}, indent=1))


def cmd_shadow(a):
    print(json.dumps(shadow_stats(load_state()), indent=1))


# ---------- candidates ----------
def _score(code, idx_roc60, cfg):
    try:
        bars = bars_for(code)
    except Exception as e:
        return None
    try:
        res, why = SC.score_ticker(bars, idx_roc60, cfg)
    except Exception:
        return None
    if not res:
        return None
    res["ticker"] = code
    return res, bars


def build_plan(code, bars, info, P, risk_dollars, equity):
    plan = E.plan_entry(bars, "breakout" if info["setup"] == "breakout" else "pullback")
    st = plan.get("status", "")
    if not (st.startswith("wait") or st.startswith("active")):
        return None, "entry: " + (plan.get("reason") or st)
    a = plan["atr14"]
    entry = plan["entry_limit"]
    _, lows = C.swing_points(bars, 3, 3)
    below = [p for (_, p) in lows if p < entry]
    swing_low = max(below) if below else None
    stop = min(plan["stop"], entry - P["stop_atr_mult"] * a)
    if swing_low:
        stop = min(stop, swing_low - 0.1 * a)
    stop = C.round_tick(stop, "down")
    rps = entry - stop
    if rps <= 0:
        return None, "invalid stop"
    target = C.round_tick(entry + P["target_R"] * rps, "down")
    res = plan.get("nearest_resistance")
    why = None
    if res and (res - entry) / rps < P["target_R"]:
        why = "resistance %.3f leaves %.1fR (< %.1fR)" % (res, (res - entry) / rps, P["target_R"])
    qty = int((risk_dollars - 2 * brk()) // (rps + C.tick_size(stop)))
    avg_vol = sum(b["v"] for b in bars[-20:]) / 20.0
    qty = max(0, min(qty, int(equity * RULES["max_position_pct"] / 100 // entry), int(avg_vol * RULES["max_pct_avg_volume"] / 100)))
    value = qty * entry
    if qty <= 0:
        why = why or "size rounds to zero"
    elif value < RULES["min_position_value"]:
        why = why or "position $%.0f below $%.0f (brokerage drag)" % (value, RULES["min_position_value"])
    tpl = TM.swing_templates(bars[-250:])
    sim = TM.sim_swing(entry, stop, target, tpl, P["time_stop_days"], n=10000)
    simn = TM.sim_swing(entry, stop, target, TM.swing_templates(bars[-250:], neutral=True), P["time_stop_days"], n=10000)
    if sim["expected_R"] < P["min_expected_R"]:
        why = why or "replay expectancy %.2fR below %.2fR" % (sim["expected_R"], P["min_expected_R"])
    return {"code": code, "status": st, "last_close": plan["last_close"], "entry_limit": entry, "stop": stop,
            "target": target, "risk_per_share": round(rps, 4), "stop_atr": round(rps / a, 2), "atr": round(a, 4),
            "qty": qty, "value": round(value, 2), "risk_dollars": round(trade_risk(qty, entry, stop), 2),
            "drag_pct": round(2 * brk() / value * 100, 2) if value else None, "zone": plan["entry_zone"], "resistance": res,
            "swing_low": swing_low, "odds": {k: sim[k] for k in ("p_target_first", "p_stop_first", "expected_R")},
            "odds_neutral": {k: simn[k] for k in ("p_target_first", "p_stop_first", "expected_R")},
            "scan": info, "notes": plan.get("notes", [])}, why


def cmd_candidates(a):
    s, P = load_state(), load_params()
    date = s["last_processed"] or dt.date.today().isoformat()
    g = budgets(s, date)
    mf = market_filter()
    if not mf["ok"]:
        g["reasons"].append("market filter: ASX 200 %.1f below its 50-day EMA %.1f" % (mf["index_close"], mf["ema50"]))
        g["open"] = False
    out = {"gates": g, "market_filter": mf, "candidates": [], "rejected": [], "shadowed": []}
    gate_why = None if g["open"] else "gate: " + "; ".join(g["reasons"])
    cfg = C.load_config()
    cfg.update(min_turnover_aud=P["min_turnover"], min_price=P["min_price"], min_reward_risk=P["target_R"])
    idx = bars_for(RULES["index"])
    ic = [b["c"] for b in idx]
    roc = ic[-1] / ic[-61] - 1
    universe = SC.listed_universe() if not a.tickers else a.tickers.split(",")
    if a.max:
        universe = universe[:a.max]
    held = {x["code"] for x in s["positions"] + s["orders"]}
    cool = {t["code"] for t in s["closed"] if t["reason"].startswith(("stop", "gap"))
            and (dt.date.fromisoformat(date) - dt.date.fromisoformat(t["exit_date"])).days < RULES["cooldown_days"] * 7 / 5}
    scored = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(lambda c: _score(c, roc, cfg), universe):
            if r:
                scored.append(r)
    out["scanned"], out["passed_gates"] = len(universe), len(scored)
    pool = []
    for info, bars in scored:
        if info["score"] < P["min_score"] or info["atr_pct"] is None or info["atr_pct"] > P["max_atr_pct"]:
            continue
        if info["setup"].startswith(("extended", "weak")) or info["trend"] != "bullish":
            continue
        if info["ticker"] in held or info["ticker"] in cool:
            out["rejected"].append({"code": info["ticker"], "why": "held or in cooldown"}); continue
        pool.append((info, bars))
    pool.sort(key=lambda x: (-x[0]["score"], -(x[0]["rs60_vs_index_pct"] or 0)))
    out["shortlist_size"] = len(pool)
    budget = g["risk_per_trade"] if g["open"] else RULES["max_risk_per_stock"]
    for info, bars in pool[:max(a.top * 3, a.shadow_max * 2)]:
        plan, why = build_plan(info["ticker"], bars, info, P, budget, g["equity"])
        why = gate_why or why
        if plan and not why and len(out["candidates"]) < a.top:
            out["candidates"].append(plan)
        elif plan and why:
            out["rejected"].append({"code": info["ticker"], "why": why})
            if len(out["shadowed"]) < a.shadow_max and shadow_add(s, plan, why, date):
                out["shadowed"].append(info["ticker"])
        elif not plan:
            out["rejected"].append({"code": info["ticker"], "why": why})
    save_state(s)
    json.dump({"date": date, "candidates": out["candidates"]}, open(LAST_CANDS, "w"), indent=1)
    print(json.dumps(out, indent=1))


def cmd_place(a):
    s = load_state()
    date = s["last_processed"]
    g = budgets(s, date)
    mf = market_filter()
    errs = list(g["reasons"])
    if not mf["ok"]:
        errs.append("market filter closed")
    if a.code in {x["code"] for x in s["positions"] + s["orders"]}:
        errs.append("already held or pending")
    sectors = {x.get("sector") for x in s["positions"] + s["orders"]}
    if a.sector in sectors:
        errs.append("sector %s already held (rule 6)" % a.sector)
    risk = trade_risk(a.qty, a.limit, a.stop)
    if risk > g["risk_per_trade"] + 0.01:
        errs.append("risk %.2f exceeds tonight's budget %.2f" % (risk, g["risk_per_trade"]))
    if a.qty * a.limit < RULES["min_position_value"]:
        errs.append("position below $%.0f" % RULES["min_position_value"])
    if a.debate != "BULL":
        errs.append("debate verdict must be BULL (got %s)" % a.debate)
    if a.results_clear != "yes" or a.exdiv_clear != "yes":
        errs.append("calendar checks not confirmed clear (rule 14)")
    if errs:
        print(json.dumps({"placed": False, "errors": errs}, indent=1)); sys.exit(1)
    oid = "T%04d" % s["next_id"]; s["next_id"] += 1
    o = {"id": oid, "code": a.code, "qty": a.qty, "limit": a.limit, "stop": a.stop, "target": a.target,
         "atr": a.atr, "sector": a.sector, "placed": date, "days_live": 0, "thesis": a.thesis,
         "review": {"debate": a.debate, "debate_net": a.debate_net, "news": a.news, "expected_R": a.expected_R}}
    s["orders"].append(o)
    save_state(s)
    print(json.dumps({"placed": True, "order": o, "risk_dollars": round(risk, 2)}, indent=1))


# ---------- review & report ----------
def cmd_review(a):
    s = load_state()
    since = (dt.date.fromisoformat(s["last_processed"]) - dt.timedelta(days=a.days)).isoformat()
    rows = []
    for t in s["closed"]:
        if t["exit_date"] < since:
            continue
        tags = []
        if t["reason"] == "gap through stop":
            tags.append("gap-through")
        if t.get("breach"):
            tags.append("RULE BREACH")
        if t["reason"].startswith(("stop", "breakeven")):
            try:
                after = [b for b in bars_for(t["code"]) if b["date"] > t["exit_date"]][:10]
                if any(b["h"] >= t["target"] for b in after):
                    tags.append("shakeout: hit the original target within 10 days after the stop")
            except Exception:
                pass
        if "estimate" in (t.get("exit_resolution") or "") or "unverified" in str(t.get("entry_time")):
            tags.append("ESTIMATED timing: check the price data for this trade")
        if t["reason"] == "time stop":
            tags.append("time stop: went nowhere")
        if t["reason"] == "breakeven stop":
            tags.append("breakeven stop: check if the move-up was too early")
        rows.append({"id": t["id"], "code": t["code"], "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                     "reason": t["reason"], "pnl": t["pnl"], "R": t["R"],
                     "mfe_R": round((t["mfe"] - t["entry"]) / t["init_risk_per_share"], 2),
                     "mae_R": round((t["mae"] - t["entry"]) / t["init_risk_per_share"], 2),
                     "stop_atr": round(t["init_risk_per_share"] / t["atr"], 2), "tags": tags,
                     "review": t.get("review"), "thesis": t.get("thesis")})
    allc = s["closed"]
    stops = [t for t in allc if t["reason"].startswith("stop")]
    print(json.dumps({"recent_closed": rows, "totals": {
        "closed": len(allc), "wins": sum(t["pnl"] > 0 for t in allc),
        "stop_outs": len(stops), "time_stops": sum(t["reason"] == "time stop" for t in allc),
        "gap_throughs": sum(t["reason"] == "gap through stop" for t in allc),
        "avg_R": round(sum(t["R"] or 0 for t in allc) / len(allc), 2) if allc else None,
        "tuning_allowed": len(allc) >= 8}, "params": load_params(), "bounds": PARAM_BOUNDS}, indent=1))


def cmd_report(a):
    s = load_state()
    d = s["last_processed"]
    g = budgets(s, d)
    eh = s["equity_history"]
    eq = eh[-1]["equity"] if eh else RULES["start_equity"]
    prev = eh[-2]["equity"] if len(eh) > 1 else RULES["start_equity"]
    tot = eq - RULES["start_equity"]
    closed_today = [t for t in s["closed"] if t["exit_date"] == d]
    filled_today = [p for p in s["positions"] if p["entry_date"] == d] + [t for t in closed_today if t["entry_date"] == d]
    placed_today = [o for o in s["orders"] if o["placed"] == d]
    allc = s["closed"]
    wins = sum(t["pnl"] > 0 for t in allc)
    gl = go_live_status(s)
    L = ["# Paper account: %s" % d, ""]
    if gl["review30_due"]:
        L += ["**30-TRADE REVIEW DUE**: time to sit down with Prabhakar (see RULES.md, go-live criteria).", ""]
    if any(p.get("frozen") for p in s["positions"]):
        L += ["**FROZEN POSITION**: possible corporate action, needs manual review.", ""]
    L += [
         "**Value $%s** · today %+.2f · total %+.2f (%+.2f%%)" % (format(round(eq, 2), ","), eq - prev, tot, tot / RULES["start_equity"] * 100),
         "Trades today: %d filled, %d closed, %d new orders" % (len(filled_today), len(closed_today), len(placed_today)),
         "Closed so far: %d (wins %d, avg %sR) · loss streak %d" % (len(allc), wins,
             round(sum(t["R"] or 0 for t in allc) / len(allc), 2) if allc else "–", g["loss_streak"]),
         "Gates: %s" % ("OPEN" if g["open"] else "SHUT – " + "; ".join(g["reasons"])),
         ("Benchmark %s (price only): $%s · account %+.2f vs benchmark" % (s["benchmark"]["code"], format(eh[-1]["benchmark"], ","),
             eq - eh[-1]["benchmark"])) if eh and "benchmark" in eh[-1] else "Benchmark: not started",
         "Go-live checklist: %d/%d met" % (sum(c["met"] for c in gl["checks"]), len(gl["checks"])),
         "Open risk $%.2f / $250 · week realised %+.2f / -500 · drawdown %.2f / 1500" % (
             g["open_risk"], g["week_pnl_realized"], s["peak_equity"] - eq), ""]
    if s["positions"]:
        L.append("## Open positions")
        for p in s["positions"]:
            L.append("- %s %d @ %.3f, last %.3f, stop %.3f, target %.3f, P&L %+.2f, day %d" % (
                p["code"], p["qty"], p["entry"], p["last_close"], p["stop"], p["target"],
                (p["last_close"] - p["entry"]) * p["qty"] - brk(), p["bars_held"]))
    if s["orders"]:
        L.append("## Pending orders")
        for o in s["orders"]:
            L.append("- %s BUY %d limit %.3f, stop %.3f, target %.3f (placed %s)" % (
                o["code"], o["qty"], o["limit"], o["stop"], o["target"], o["placed"]))
    if closed_today:
        L.append("## Closed today")
        for t in closed_today:
            L.append("- %s %s: %+.2f (%sR)%s" % (t["code"], t["reason"], t["pnl"], t["R"],
                                               " – BREACH" if t.get("breach") else ""))
    st = shadow_stats(s)
    if st["by_reason"]:
        L.append("## Shadow journal (rejected trades tracked as if taken)")
        for c, r in sorted(st["by_reason"].items()):
            L.append("- %s: %d closed%s, %d live" % (c, r["closed"],
                     (", win %d%%, avg %sR" % (r["win_rate"] * 100, r["avg_R"])) if r["closed"] else "", r.get("live", 0)))
        L.append("- real trades: %d closed, avg %sR" % (st["real_trades"]["closed"], st["real_trades"]["avg_R"] or "–"))
    br = [b for b in s["breaches"] if b["date"] == d]
    L.append("## Rule breaches today")
    L += ["- %s: %s" % (b["rule"], b["detail"]) for b in br] or ["- none"]
    txt = "\n".join(L) + "\n"
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    open(os.path.join(ROOT, "reports", d + ".md"), "w").write(txt)
    print(txt)


GO_LIVE = [
    ("at least 30 closed trades", lambda s, x: x["closed"] >= 30),
    ("net P&L after brokerage is positive", lambda s, x: x["net_pnl"] > 0),
    ("average R is positive", lambda s, x: (x["avg_R"] or 0) > 0),
    ("account beats the ASX 200 ETF line", lambda s, x: x["vs_benchmark"] is not None and x["vs_benchmark"] > 0),
    ("drawdown halt never triggered", lambda s, x: not s.get("ever_halted")),
    ("no rule breaches in the last 10 trades", lambda s, x: x["breaches_last10"] == 0),
    ("no process errors in the last 10 trades", lambda s, x: x["process_errors_last10"] == 0),
]


def go_live_status(s):
    allc = s["closed"]
    last10 = sorted(allc, key=lambda t: (t["exit_date"], t["id"]))[-10:]
    eh = s["equity_history"]
    vs = (eh[-1]["equity"] - eh[-1]["benchmark"]) if eh and "benchmark" in eh[-1] else None
    x = {"closed": len(allc), "net_pnl": round(sum(t["pnl"] for t in allc), 2),
         "avg_R": round(sum(t["R"] or 0 for t in allc) / len(allc), 2) if allc else None,
         "vs_benchmark": round(vs, 2) if vs is not None else None,
         "breaches_last10": sum(bool(t.get("breach")) for t in last10),
         "process_errors_last10": sum(bool(t.get("process_error")) for t in last10)}
    x["checks"] = [{"criterion": n, "met": bool(f(s, x))} for n, f in GO_LIVE]
    x["all_met"] = all(c["met"] for c in x["checks"])
    x["review30_due"] = len(allc) >= 30 and not s.get("review30_done")
    return x


def cmd_tag(a):
    s = load_state()
    t = next((t for t in s["closed"] if t["id"] == a.id), None)
    if not t:
        sys.exit("no closed trade %s" % a.id)
    if a.process_error:
        t["process_error"] = a.process_error
    if a.bad_luck:
        t["bad_luck"] = a.bad_luck
    save_state(s)
    print(json.dumps({"id": a.id, "process_error": t.get("process_error"), "bad_luck": t.get("bad_luck")}, indent=1))


def cmd_golive(a):
    print(json.dumps(go_live_status(load_state()), indent=1))


def cmd_status(a):
    s = load_state()
    print(json.dumps({"budgets": budgets(s, s["last_processed"] or dt.date.today().isoformat()),
                      "positions": s["positions"], "orders": s["orders"], "halted": s["halted"]}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("update")
    c = sp.add_parser("candidates")
    c.add_argument("--top", type=int, default=5)
    c.add_argument("--workers", type=int, default=6)
    c.add_argument("--max", type=int, default=0)
    c.add_argument("--tickers")
    c.add_argument("--shadow-max", type=int, default=8, help="max new shadow entries per run")
    k = sp.add_parser("skip", help="shadow a candidate skipped during review")
    k.add_argument("--code", required=True)
    k.add_argument("--category", required=True, choices=SHADOW_CATEGORIES)
    k.add_argument("--reason", required=True)
    sp.add_parser("shadow")
    tg = sp.add_parser("tag", help="record the daily review verdict on a closed trade")
    tg.add_argument("--id", required=True)
    tg.add_argument("--process-error", dest="process_error")
    tg.add_argument("--bad-luck", dest="bad_luck")
    sp.add_parser("golive")
    p = sp.add_parser("place")
    p.add_argument("--sector", required=True, choices=GICS_GROUPS)
    for k in ("code", "thesis", "debate", "news", "results_clear", "exdiv_clear"):
        p.add_argument("--" + k.replace("_", "-"), dest=k, required=True)
    p.add_argument("--qty", type=int, required=True)
    for k in ("limit", "stop", "target", "atr", "debate_net", "expected_R"):
        p.add_argument("--" + k.replace("_", "-"), dest=k, type=float, required=True)
    r = sp.add_parser("review"); r.add_argument("--days", type=int, default=21)
    sp.add_parser("report"); sp.add_parser("status")
    a = ap.parse_args()
    {"update": cmd_update, "candidates": cmd_candidates, "place": cmd_place, "review": cmd_review,
     "report": cmd_report, "status": cmd_status, "skip": cmd_skip, "shadow": cmd_shadow,
     "tag": cmd_tag, "golive": cmd_golive}[a.cmd](a)


if __name__ == "__main__":
    main()
