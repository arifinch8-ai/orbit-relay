# ════════════════════════════════════════════════════════════════════
#  ORBIT × Ari Logic → Telegram relay  (Python / Flask, for Render)
#
#  Drop-in replacement for app.py. Turns the JSON the merged indicator
#  sends (TradingView webhooks) into clean Telegram messages:
#    • ORBIT events  (momentum / support / resistance / VWAP / TP narrator)
#    • Ari TICK TARGETS ladder  (TP1…TP5 + Swing, with points & R)
#    • nearest RESISTANCE / SUPPORT
#    • HITS and REJECTIONS ("closed back — watch retest / reversal")
#
#  ENVIRONMENT VARIABLES (set these in Render → your service → Environment):
#    TELEGRAM_TOKEN    = bot token from @BotFather
#    TELEGRAM_CHAT_ID  = chat / channel id to post to
#    ORBIT_SECRET      = same string as the indicator's "Webhook secret"
#    PORT              = provided by Render automatically
#
#  Keep your existing render.yaml and requirements.txt (flask). This file
#  exposes `app`, so it runs under gunicorn (gunicorn app:app) or directly
#  (python app.py). No third-party HTTP library needed — uses urllib.
#
#  FONTS: Telegram bots can't change the font family. The clean look is
#  bold headlines + <code> monospace numbers + emoji (HTML parse mode).
# ════════════════════════════════════════════════════════════════════

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from flask import Flask, request

app = Flask(__name__)

TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SECRET  = os.environ.get("ORBIT_SECRET", "")


# ── helpers ─────────────────────────────────────────────────────────
def numf(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def n2(v):
    f = numf(v)
    return f"{f:.2f}" if f is not None else "—"


def px(v):
    f = numf(v)
    return f"<code>{f:.2f}</code>" if f is not None else "—"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sr_line(d):
    r = px(d.get("near_res")) if numf(d.get("near_res")) is not None else None
    s = px(d.get("near_sup")) if numf(d.get("near_sup")) is not None else None
    parts = []
    if r:
        parts.append(f"R {r}")
    if s:
        parts.append(f"S {s}")
    return f"🧭 <b>Levels</b>  " + "  ·  ".join(parts) if parts else ""


# ── ARI: tick-target ladder ─────────────────────────────────────────
ARI_TIERS = [
    ("tp1",   "TP1",   "🏆", ""),
    ("tp2",   "TP2",   "🏆", ""),
    ("tp3",   "TP3",   "💎", ""),
    ("tp4",   "TP4",   "💰", " IN PROFIT"),
    ("tp5",   "TP5",   "🚀", ""),
    ("swing", "Swing", "🌙", ""),
]


def tier_row(d, key, name, emoji, tail):
    if numf(d.get(key)) is None:
        return None
    pts = d.get(key + "_pts")
    rr = d.get(key + "_rr")
    pts_s = f"+{round(float(pts))}pt" if numf(pts) is not None else ""
    rr_s = f" · {float(rr):.1f}R" if (numf(rr) is not None and float(rr) > 0) else ""
    return f"<b>{emoji} {name} {px(d.get(key))}  {pts_s}{rr_s}{tail}</b>"


def tick_ladder(d, with_stop=True):
    rows = [tier_row(d, *t) for t in ARI_TIERS]
    rows = [r for r in rows if r]
    if not rows:
        return ""
    head = "🎯 <b>Tick targets</b>"
    if with_stop and numf(d.get("stop")) is not None:
        sp = f" ({round(float(d['stop_pts']))}pt)" if numf(d.get("stop_pts")) is not None else ""
        head += f"   ⛔ Stop {px(d.get('stop'))}{sp}"
    return head + "\n" + "\n".join(rows)


def ari_stats(d):
    bits = []
    if d.get("grade"):
        bits.append(f"Grade {esc(d['grade'])}")
    if d.get("conf"):
        bits.append(esc(d["conf"]))
    if numf(d.get("winrate")) is not None:
        bits.append(f"Win {round(float(d['winrate']))}%")
    return "📊 " + " · ".join(bits) if bits else ""


def tier_from_code(code):
    code = str(code)
    rejected = code.endswith("_reject")
    base = code[:-7] if rejected else code
    for key, name, emoji, tail in ARI_TIERS:
        if base == "ari_" + key:
            return (key, name, emoji, tail, rejected)
    return None


def ari_message(d):
    sym = esc(d.get("symbol", "ALERT"))
    side = esc(str(d.get("side", "")).upper())
    arrow = "📈" if side == "LONG" else "📉"
    code = d.get("orbit", "")
    lines = []

    if code in ("ari_long", "ari_short"):
        colour = "🟢" if side == "LONG" else "🔴"
        lines.append(f"{colour}{arrow} <b>{sym} — NEW {side}</b>")
        if numf(d.get("entry")) is not None:
            plan = f"🎬 Entry {px(d.get('entry'))}"
            if numf(d.get("stop")) is not None:
                sp = f" ({round(float(d['stop_pts']))}pt)" if numf(d.get("stop_pts")) is not None else ""
                plan += f"   ⛔ Stop {px(d.get('stop'))}{sp}"
            lines.append(plan)
        lad = tick_ladder(d, with_stop=False)
        if lad:
            lines.append(lad)
        sr = sr_line(d)
        if sr:
            lines.append(sr)
        st = ari_stats(d)
        if st:
            lines.append(st)

    elif tier_from_code(code):
        key, name, emoji, tail, rejected = tier_from_code(code)
        price = px(d.get(key))
        pts = f" (+{round(float(d[key + '_pts']))}pt)" if numf(d.get(key + "_pts")) is not None else ""
        if rejected:
            lines.append(f"🧱 <b>{sym} REJECTED at {name}</b> {price}{pts} — closed back. Watch retest / reversal 👀")
        else:
            lines.append(f"{emoji}💥 <b>{sym} {name} HIT</b> {price}{pts} — bank it 🎉")
        sr = sr_line(d)
        if sr:
            lines.append(sr)
        lad = tick_ladder(d)
        if lad:
            lines.append(lad)

    elif code == "ari_stop_hit":
        lines.append(f"⛔ <b>{sym} STOPPED</b> {px(d.get('stop'))} (−1R) — plan's done, live to trade again 🛟")
        sr = sr_line(d)
        if sr:
            lines.append(sr)
    elif code == "ari_exit":
        lines.append(f"🟠 <b>{sym} EXIT</b> — momentum / flow turned against the {side or 'trade'} 👋 lock profits")
        sr = sr_line(d)
        if sr:
            lines.append(sr)
    elif code == "ari_reversal":
        lines.append(f"🔁 <b>{sym} reversal brewing</b> — the opposite side is gaining confluence")
        sr = sr_line(d)
        if sr:
            lines.append(sr)
    else:
        lines.append(f"🛰 <b>{sym}</b> {esc(code or 'alert')}")

    lines.append("— facts, not a forecast 🤖")
    return "\n".join(lines)


# ── ORBIT: structural-level + TP narrator events (pic-1 style) ──────
ORBIT_TEXT = {
    "trend_up":         ("📈", "EMA 9 crossed above 21 — momentum turned up."),
    "trend_down":       ("📉", "EMA 9 crossed below 21 — momentum turned down."),
    "support_touch":    ("🟢", "Tapped support — hold or fold? 👀"),
    "resistance_touch": ("🔴", "Tapped resistance — reject or reclaim? 👀"),
    "reclaim":          ("✅", "Reclaimed the level (closed above) — bulls poking back 🐂"),
    "breakdown":        ("🔻", "Lost the level (closed below) — mind the downside 🛡"),
    "vwap_reclaim":     ("🔵", "Reclaimed VWAP — back above the line."),
    "vwap_lost":        ("🔵", "Lost VWAP — slipped below the line."),
}


def orbit_narrator(d):
    sym = esc(d.get("symbol", "ALERT"))
    t = px(d.get("target"))
    nm = esc(d.get("target_name", "target"))
    dist = ""
    if numf(d.get("dist_pct")) is not None:
        dist = f" — only {float(d['dist_pct']):.2f}% away 🏃"
    code = d.get("orbit")
    headers = {
        "approach":        f"📡 <b>{sym}</b> closing in on {nm} {t}{dist}",
        "target_met":      f"🎯💥 <b>{sym} TARGET SMASHED</b> — {nm} {t} cleared on the close!",
        "target_rejected": f"🧱 <b>{sym} REJECTED</b> at {nm} {t} — closed back. Watch retest / reversal 👀",
        "target_retest":   f"🔁 <b>{sym}</b> back for a RETEST at {t} — does it hold?",
        "tp1":             f"💰 <b>{sym} TP1 tagged</b> {t} — bank a little 🎉",
        "tp2":             f"💰💰 <b>{sym} TP2 tagged</b> {t} — trim again",
        "runner":          f"🚀 <b>{sym} TP3 hit</b> {t} — runner mode, trail your stop",
        "stop":            f"⛔ <b>{sym} STOP</b> {t} — plan's done 🛟",
    }
    return headers.get(code)


def orbit_message(d):
    sym = esc(d.get("symbol", "ALERT"))
    code = d.get("orbit", "alert")
    lines = []

    narr = orbit_narrator(d)
    if narr:
        # TP ladder / narrator event
        lines.append(narr)
        ladder = []
        for k, label in (("tp1", "TP1"), ("tp2", "TP2"), ("tp3", "TP3")):
            if numf(d.get(k)) is not None:
                ladder.append(f"{label} {n2(d.get(k))}")
        lad = "🪜 " + " · ".join(ladder) if ladder else ""
        if lad and numf(d.get("stop")) is not None:
            lad += f"   ⛔ Stop {n2(d.get('stop'))}"
        if lad:
            lines.append(lad)
    else:
        emoji, sentence = ORBIT_TEXT.get(code, ("🛰", "ORBIT alert."))
        lines.append(f"{emoji} <b>ORBIT · {sym}</b>")
        lines.append(f"<b>{sentence}</b>")

    # stats line (Price | Level | EMA | Vol) — matches the familiar look
    stats = []
    if numf(d.get("price")) is not None:
        stats.append(f"Price: {n2(d.get('price'))}")
    if numf(d.get("level")) is not None:
        stats.append(f"Level: {n2(d.get('level'))}")
    if d.get("ema"):
        stats.append(f"EMA: {esc(d['ema'])}")
    if d.get("vol"):
        stats.append(f"Vol: {esc(d['vol'])}")
    if stats:
        lines.append("<i>" + " | ".join(stats) + "</i>")

    sr = sr_line(d)
    if sr:
        lines.append(sr)

    lines.append("— facts, not a forecast 🤖")
    return "\n".join(lines)


# ── router ──────────────────────────────────────────────────────────
IDA_TP = [
    ("tp1", "TP1", "🎯"),
    ("tp2", "TP2", "🎯"),
    ("tp3", "TP3", "💎"),
    ("tp4", "TP4", "💰"),
    ("tp5", "TP5", "🚀"),
    ("tp6", "TP6", "🌙"),
]

IDA_HIT = {
    "ida_tp1": ("TP1", "🎯"),
    "ida_tp2": ("TP2", "🎯"),
    "ida_tp3": ("TP3", "💎"),
    "ida_tp4": ("TP4", "💰"),
    "ida_tp5": ("TP5", "🚀"),
    "ida_tp6": ("TP6", "🌙"),
}


def ida_message(d):
    sym = esc(d.get("symbol", "ALERT"))
    code = str(d.get("orbit", ""))
    direction = esc(str(d.get("dir", "")))
    if code in ("ida_long", "ida_short"):
        is_long = code == "ida_long"
        arrow = "🟢📈" if is_long else "🔴📉"
        side = "LONG" if is_long else "SHORT"
        lines = [f"{arrow} <b>{sym} — IDA {side}</b>"]
        entry = []
        if numf(d.get("price")) is not None:
            entry.append(f"Entry {px(d.get('price'))}")
        if numf(d.get("stop")) is not None:
            entry.append(f"⛔ Stop {px(d.get('stop'))}")
        if entry:
            lines.append("  ·  ".join(entry))
        rows = [f"{em} <b>{nm}</b> {px(d.get(k))}" for k, nm, em in IDA_TP if numf(d.get(k)) is not None]
        if rows:
            lines.append("🎯 <b>Price targets</b>")
            lines.extend(rows)
        ctx = []
        if direction and direction != "WAIT":
            ctx.append(direction)
        if d.get("vwap"):
            ctx.append(esc(d["vwap"]))
        if d.get("bos") and str(d.get("bos")) != "NONE":
            ctx.append("BOS " + esc(d["bos"]))
        if ctx:
            lines.append("🧭 " + " · ".join(ctx))
        lines.append("— facts, not a forecast 🤖")
        return "\n".join(lines)
    if code in IDA_HIT:
        nm, em = IDA_HIT[code]
        key = code.replace("ida_", "")
        val = px(d.get(key)) if numf(d.get(key)) is not None else px(d.get("price"))
        return f"{em}💥 <b>{sym} {nm} HIT</b> {val} — bank it 🎉\n— facts, not a forecast 🤖"
    if code == "ida_stop":
        sp = px(d.get("stop")) if numf(d.get("stop")) is not None else px(d.get("price"))
        return f"🛑 <b>{sym} STOPPED / REJECTED</b> {sp} — plan's done 🛟\n— facts, not a forecast 🤖"
    lines = [f"🛰 <b>IDA · {sym}</b>"]
    if numf(d.get("price")) is not None:
        lines.append(f"<i>Price: {n2(d.get('price'))}</i>")
    lines.append("— facts, not a forecast 🤖")
    return "\n".join(lines)


import datetime as _dt


def _today_iso():
    return _dt.datetime.utcnow().date().isoformat()


def _expiry_target(mode):
    today = _dt.datetime.utcnow().date()
    mode = (mode or "weekly").lower()
    if mode == "0dte":
        return today.isoformat()
    if mode == "monthly":
        # third Friday of this month (roll to next month if already past)
        first = today.replace(day=1)
        fridays = [first + _dt.timedelta(days=i) for i in range(31)
                   if (first + _dt.timedelta(days=i)).month == first.month
                   and (first + _dt.timedelta(days=i)).weekday() == 4]
        third = fridays[2] if len(fridays) >= 3 else fridays[-1]
        if third < today:
            nm = (first + _dt.timedelta(days=32)).replace(day=1)
            fridays = [nm + _dt.timedelta(days=i) for i in range(31)
                       if (nm + _dt.timedelta(days=i)).month == nm.month
                       and (nm + _dt.timedelta(days=i)).weekday() == 4]
            third = fridays[2] if len(fridays) >= 3 else fridays[-1]
        return third.isoformat()
    # weekly: next Friday (today if Friday)
    ahead = (4 - today.weekday()) % 7
    return (today + _dt.timedelta(days=ahead)).isoformat()


def _abs_delta(r):
    g = r.get("greeks") or {}
    de = g.get("delta")
    return abs(de) if de is not None else None


def _liquidity_score(r):
    # Liquidity = traded volume + open interest, penalized by a wide bid/ask spread.
    day = r.get("day") or {}
    vol = day.get("volume") or 0
    oi = r.get("open_interest") or 0
    q = r.get("last_quote") or {}
    bid = q.get("bid")
    ask = q.get("ask")
    spread_factor = 1.0
    try:
        if bid is not None and ask is not None:
            mid = (float(bid) + float(ask)) / 2.0
            if mid > 0:
                spread_pct = (float(ask) - float(bid)) / mid
                spread_factor = 1.0 / (1.0 + max(0.0, spread_pct) * 3.0)
    except (TypeError, ValueError):
        pass
    return (float(vol) + float(oi)) * spread_factor


def _tradeable(r):
    # Drop contracts with junk data or no real market so they can't be selected.
    iv = r.get("implied_volatility")
    try:
        if iv is not None and float(iv) > 3.0:        # >300% IV = stale / garbage
            return False
    except (TypeError, ValueError):
        pass
    q = r.get("last_quote") or {}
    bid = q.get("bid")
    try:
        if bid is None or float(bid) <= 0:            # no bid = no way to exit
            return False
    except (TypeError, ValueError):
        return False
    day = r.get("day") or {}
    vol = day.get("volume") or 0
    oi = r.get("open_interest") or 0
    try:
        if (float(vol) + float(oi)) <= 0:             # totally dead contract
            return False
    except (TypeError, ValueError):
        pass
    return True


def choose_contract(results, side, target_delta, target_date, delta_window=0.12):
    want = "call" if side == "call" else "put"
    cands = []
    for r in results:
        det = r.get("details", {}) or {}
        if det.get("contract_type") != want:
            continue
        exp = det.get("expiration_date")
        if not exp:
            continue
        if not _tradeable(r):          # skip junk before picking expiration/strike
            continue
        cands.append((exp, r))
    if not cands:
        return None

    # 1) expiration: nearest one at/after the target date for the chosen style
    exps = sorted({e for e, _ in cands})
    chosen = next((e for e in exps if e >= target_date), exps[-1])
    pool = [r for e, r in cands if e == chosen]
    if not pool:
        return None

    # 2) keep strikes whose delta is near the target (right "moneyness")
    near = [r for r in pool
            if _abs_delta(r) is not None and abs(_abs_delta(r) - target_delta) <= delta_window]

    if near:
        # 3) among those, the most tradeable wins; break ties by delta closeness
        near.sort(key=lambda r: (-_liquidity_score(r), abs((_abs_delta(r) or 9.0) - target_delta)))
        return near[0]

    # fallback: nothing in the delta window -> closest delta overall
    pool.sort(key=lambda r: (999 if _abs_delta(r) is None else abs(_abs_delta(r) - target_delta)))
    return pool[0]


def polygon_pick_contract(underlying, side, target_delta, mode, price):
    if not POLYGON_API_KEY or not underlying:
        return None
    try:
        tgt = _expiry_target(mode)
        qs = [
            ("contract_type", "call" if side == "call" else "put"),
            ("expiration_date.gte", _today_iso()),
            ("limit", "250"),
            ("sort", "expiration_date"),
            ("apiKey", POLYGON_API_KEY),
        ]
        if numf(price) is not None:
            lo = float(price) * 0.80
            hi = float(price) * 1.20
            qs.insert(1, ("strike_price.gte", f"{lo:.2f}"))
            qs.insert(2, ("strike_price.lte", f"{hi:.2f}"))
        query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in qs)
        url = f"https://api.polygon.io/v3/snapshot/options/{urllib.parse.quote(underlying)}?{query}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        r = choose_contract(data.get("results", []) or [], side, target_delta, tgt)
        if not r:
            return None
        det = r.get("details", {}) or {}
        g = r.get("greeks") or {}
        q = r.get("last_quote") or {}
        t = r.get("last_trade") or {}
        return {
            "ticker": det.get("ticker"),
            "strike": det.get("strike_price"),
            "expiry": det.get("expiration_date"),
            "type": det.get("contract_type"),
            "delta": g.get("delta"),
            "theta": g.get("theta"),
            "gamma": g.get("gamma"),
            "vega": g.get("vega"),
            "iv": r.get("implied_volatility"),
            "oi": r.get("open_interest"),
            "vol": (r.get("day") or {}).get("volume"),
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "last": t.get("price"),
        }
    except Exception as e:
        print("Polygon lookup failed:", e)
        return None


OPT_HIT = {
    "options_tp1": ("TP1", "\U0001F3AF"),
    "options_tp2": ("TP2", "\U0001F3AF"),
    "options_tp3": ("TP3", "\U0001F48E"),
    "options_tp4": ("TP4", "\U0001F4B0"),
    "options_tp5": ("TP5", "\U0001F680"),
    "options_tp6": ("TP6", "\U0001F319"),
}


def is_optionable(sym):
    # Polygon options cover US equities / ETFs / indices only — not crypto or futures.
    s = str(sym or "").upper()
    if not s:
        return False
    if s.endswith("!"):       # futures (e.g. MNQ1!, NQ1!)
        return False
    if s.endswith(".P"):      # perpetuals
        return False
    for q in ("USDT", "USDC", "BUSD", "DAI", "USD"):   # crypto / forex quote currencies
        if s.endswith(q) and len(s) > len(q):
            return False
    return True


def options_message(d):
    sym = esc(d.get("symbol", "ALERT"))
    code = str(d.get("orbit", ""))
    side_lbl = str(d.get("side", "")).upper()
    tag = f" {side_lbl}" if side_lbl in ("CALL", "PUT") else ""

    if code in ("options_call", "options_put"):
        is_call = code == "options_call"
        side = "call" if is_call else "put"
        cp = "C" if is_call else "P"
        arrow = "\U0001F7E2\U0001F4C8" if is_call else "\U0001F534\U0001F4C9"
        target_delta = numf(d.get("opt_delta")) or 0.60
        mode = str(d.get("opt_expiry", "weekly")).lower()
        lines = [f"{arrow} <b>{sym} \u2014 {'CALL' if is_call else 'PUT'}</b>"]

        if not is_optionable(sym):
            lines.append("ℹ️ <i>No listed options for this symbol (crypto / futures) — underlying levels only</i>")
        else:
            c = polygon_pick_contract(sym, side, target_delta, mode, numf(d.get("price")))
            if c and c.get("strike") is not None:
                strike = f"{float(c['strike']):g}"
                exp = c.get("expiry") or ""
                dte_s = ""
                try:
                    _days = (_dt.date.fromisoformat(exp) - _dt.datetime.utcnow().date()).days
                    dte_s = f" ({_days} DTE)"
                except Exception:
                    dte_s = ""
                lines.append(f"📄 <b>{sym} {strike}{cp}</b> · exp <code>{esc(exp)}</code>{dte_s}")
                met = []
                if c.get("delta") is not None:
                    met.append(f"Δ {float(c['delta']):.2f}")
                if c.get("theta") is not None:
                    met.append(f"θ {float(c['theta']):.2f}")
                if c.get("iv") is not None:
                    met.append(f"IV {round(float(c['iv']) * 100)}%")
                if c.get("oi") is not None:
                    met.append(f"OI {int(c['oi'])}")
                if c.get("vol") is not None:
                    met.append(f"Vol {int(c['vol'])}")
                prem = None
                if numf(c.get("bid")) is not None and numf(c.get("ask")) is not None:
                    prem = f"{float(c['bid']):.2f}/{float(c['ask']):.2f}"
                elif numf(c.get("last")) is not None:
                    prem = f"{float(c['last']):.2f}"
                if prem:
                    met.append(f"${prem}")
                if met:
                    lines.append("💵 " + " · ".join(met))
            else:
                lines.append("ℹ️ <i>Live contract unavailable right now — underlying levels only</i>")

        und = []
        if numf(d.get("price")) is not None:
            und.append(f"Underlying {px(d.get('price'))}")
        if numf(d.get("stop")) is not None:
            und.append(f"\u26D4 {px(d.get('stop'))}")
        if und:
            lines.append("  \u00B7  ".join(und))
        rows = [f"{em} <b>{nm}</b> {px(d.get(k))}" for k, nm, em in IDA_TP if numf(d.get(k)) is not None]
        if rows:
            lines.append("\U0001F3AF <b>Targets (underlying)</b>")
            lines.extend(rows)
        lines.append("\u2014 facts, not a forecast \U0001F916")
        return "\n".join(lines)

    if code in OPT_HIT:
        nm, em = OPT_HIT[code]
        key = code.replace("options_", "")
        val = px(d.get(key)) if numf(d.get(key)) is not None else px(d.get("price"))
        return f"{em}\U0001F4A5 <b>{sym}{tag} {nm} HIT</b> {val} \u2014 take profit \U0001F389\n\u2014 facts, not a forecast \U0001F916"

    if code == "options_stop":
        sp = px(d.get("stop")) if numf(d.get("stop")) is not None else px(d.get("price"))
        return f"\u26D4 <b>{sym}{tag} STOPPED</b> {sp} \u2014 close it \U0001F6DF\n\u2014 facts, not a forecast \U0001F916"

    if code == "options_reject":
        rp = px(d.get("price"))
        return f"\U0001F9F1 <b>{sym}{tag} REJECTED</b> {rp} \u2014 setup failed, stand down \U0001F440\n\u2014 facts, not a forecast \U0001F916"

    lines = [f"\U0001F6F0 <b>OPTIONS \u00B7 {sym}</b>"]
    if numf(d.get("price")) is not None:
        lines.append(f"<i>Price: {n2(d.get('price'))}</i>")
    lines.append("\u2014 facts, not a forecast \U0001F916")
    return "\n".join(lines)


def build_message(d):
    if d.get("engine") == "ari" or str(d.get("orbit", "")).startswith("ari_"):
        return ari_message(d)
    if d.get("engine") == "ida" or str(d.get("orbit", "")).startswith("ida_"):
        return ida_message(d)
    if d.get("engine") == "options" or str(d.get("orbit", "")).startswith("options_"):
        return options_message(d)
    return orbit_message(d)


# ── Telegram send (stdlib only) ─────────────────────────────────────
def _id_list(*names):
    out = []
    for n in names:
        for part in os.environ.get(n, "").split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _env(*names):
    # first non-empty match wins; lets you use _IDA-suffixed names to avoid
    # duplicate-key clashes in Render, with a fallback to the plain name.
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


# Flat feed = personal chat (+ optional broadcast channel). Posts everywhere here.
CHAT_IDS = _id_list("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_CHANNEL_ID", "TELEGRAM_CHAT_CHANNEL_ID_IDA")

# Forum / topics group + per-tab thread ids (all optional; leave unset to skip).
FORUM_ID      = _env("TELEGRAM_FORUM_ID_IDA", "TELEGRAM_FORUM_ID")
TOPIC_IDA     = _env("TELEGRAM_TOPIC_IDA")
TOPIC_ENTRIES = _env("TELEGRAM_TOPIC_ENTRIES")
TOPIC_TARGETS = _env("TELEGRAM_TOPIC_TARGETS")
TOPIC_CONTEXT = _env("TELEGRAM_TOPIC_CONTEXT")

# Options tabs (forum thread ids) + Polygon key for real contract lookup.
TOPIC_OPT_CALLS    = _env("TELEGRAM_TOPIC_OPT_CALLS")
TOPIC_OPT_PUTS     = _env("TELEGRAM_TOPIC_OPT_PUTS")
TOPIC_OPT_HIT      = _env("TELEGRAM_TOPIC_OPT_HIT")
TOPIC_OPT_STOPPED  = _env("TELEGRAM_TOPIC_OPT_STOPPED")
TOPIC_OPT_REJECTED = _env("TELEGRAM_TOPIC_OPT_REJECTED")
POLYGON_API_KEY    = _env("POLYGON_API_KEY")


def topic_for(code, side=""):
    code = str(code or "")
    side = str(side or "").lower()
    if code.startswith("options_"):
        # Two tabs only: every options event (entry, hit, stop, reject) lands
        # in the Calls or Puts tab based on the trade's side.
        if code == "options_call":
            return TOPIC_OPT_CALLS
        if code == "options_put":
            return TOPIC_OPT_PUTS
        return TOPIC_OPT_PUTS if side == "put" else TOPIC_OPT_CALLS
    if code.startswith("ida_"):
        return TOPIC_IDA
    if code in ("ari_long", "ari_short"):
        return TOPIC_ENTRIES
    if code.endswith("_reject") or code in ("ari_stop_hit", "stop", "target_rejected"):
        return TOPIC_REJECTED or TOPIC_TARGETS
    if code.startswith("ari_tp") or code in ("ari_swing", "tp1", "tp2", "runner", "target_met", "target_retest", "approach"):
        return TOPIC_TARGETS
    return TOPIC_CONTEXT


def _post(chat_id, text, thread_id=None):
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if thread_id:
        try:
            body["message_thread_id"] = int(thread_id)
        except (TypeError, ValueError):
            pass
    payload = json.dumps(body).encode("utf-8")
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        print(f"Telegram error to {chat_id} (thread {thread_id}):", e.code, e.read().decode("utf-8", "ignore"))
    except Exception as e:
        print(f"Telegram send failed to {chat_id}:", e)


def send_telegram(text, code="", side=""):
    if not TOKEN:
        print("Missing TELEGRAM_TOKEN")
        return
    # 1) flat feed: personal chat + optional broadcast channel
    for cid in CHAT_IDS:
        _post(cid, text)
    # 2) group tab routing: drop into the matching topic if the forum is configured
    if FORUM_ID:
        thread = topic_for(code, side)
        if thread:
            _post(FORUM_ID, text, thread)


# ════════════════════════════════════════════════════════════════════
#  ORBIT ANALYTICS ENGINE  (stats, leaderboards, slash-commands)
#
#  Turns the live alert stream into a stateful desk:
#    • records every entry / TP hit / reject / stop as an event
#    • aggregates per-symbol performance over today / this week
#    • powers digest tabs (IDA command center, Leaderboard, Hot, Scorecard)
#    • answers Telegram slash-commands (/ida /entries /targets /rejected
#      /leaderboard /hot /scorecard /status)
#    • quality gate: optionally suppress low-grade entries
#
#  PERSISTENCE: events are written to ORBIT_STATE_FILE (default /tmp).
#  On Render's FREE tier the filesystem is ephemeral — stats reset on
#  redeploy / spin-down. Point ORBIT_STATE_FILE at a persistent disk
#  (paid) or swap _load/_save for an external store for durable history.
# ════════════════════════════════════════════════════════════════════
import re
import time as _time
import datetime as _dt2

STATE_FILE = os.environ.get("ORBIT_STATE_FILE", "/tmp/orbit_state.json")
CRON_KEY   = _env("ORBIT_CRON_KEY") or SECRET or "orbit"
TZ_OFFSET  = numf(os.environ.get("ORBIT_TZ_OFFSET", "-4")) or -4.0   # ET default
MIN_GRADE  = _env("ORBIT_MIN_GRADE").upper()                          # "" = allow all
GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}

# extra digest-tab thread ids (all optional)
TOPIC_REJECTED    = _env("TELEGRAM_TOPIC_REJECTED", "TELEGRAM_TOPIC_OPT_REJECTED")
TOPIC_LEADERBOARD = _env("TELEGRAM_TOPIC_LEADERBOARD")
TOPIC_HOT         = _env("TELEGRAM_TOPIC_HOT")
TOPIC_SCORECARD   = _env("TELEGRAM_TOPIC_SCORECARD", "TELEGRAM_TOPIC_DAILY_RECAP")

_BOOT = _time.time()


# ── time / persistence ──────────────────────────────────────────────
def _now():
    return _time.time()


def _day_str(ts=None):
    t = (ts if ts is not None else _now()) + TZ_OFFSET * 3600.0
    return _dt2.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")


def _clock(ts):
    t = ts + TZ_OFFSET * 3600.0
    return _dt2.datetime.utcfromtimestamp(t).strftime("%H:%M")


_STATE = None


def _load():
    global _STATE
    if _STATE is None:
        try:
            with open(STATE_FILE) as f:
                _STATE = json.load(f)
        except Exception:
            _STATE = {"events": []}
        if not isinstance(_STATE, dict) or "events" not in _STATE:
            _STATE = {"events": []}
    return _STATE


def _save():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(_STATE, f)
    except Exception as e:
        print("state save failed:", e, flush=True)


# ── classification ──────────────────────────────────────────────────
def classify(code, engine=""):
    """Return alert_type: entry | tp | reject | stop | context."""
    c = str(code or "").lower()
    if c in ("ida_long", "ida_short", "options_call", "options_put", "ari_long", "ari_short"):
        return "entry"
    if c in ("ida_stop", "options_stop", "ari_stop_hit", "stop"):
        return "stop"
    if c == "options_reject" or c.endswith("_reject") or c == "target_rejected":
        return "reject"
    if re.search(r"tp(\d)", c) or c in ("ari_swing", "runner", "target_met"):
        return "tp"
    return "context"


def _tp_level(code, d):
    for src in (str(code or "").lower(), str(d.get("target_name", "")).lower()):
        m = re.search(r"tp(\d)", src)
        if m:
            return int(m.group(1))
    c = str(code or "").lower()
    if "swing" in c or c == "runner":
        return 6
    return None


def record_event(d):
    code = d.get("orbit", "")
    typ = classify(code, d.get("engine", ""))
    if typ == "context":
        return
    ev = {
        "ts": _now(),
        "day": _day_str(),
        "engine": str(d.get("engine", "")).lower(),
        "symbol": (str(d.get("symbol", "")).upper() or "?"),
        "type": typ,
        "tp": _tp_level(code, d),
        "side": str(d.get("side", "")).lower(),
        "grade": (str(d.get("grade", "")).upper() or None),
        "winrate": numf(d.get("winrate")),
    }
    st = _load()
    st["events"].append(ev)
    if len(st["events"]) > 5000:
        st["events"] = st["events"][-5000:]
    _save()


# ── windows + aggregation ───────────────────────────────────────────
def _events(window="today"):
    evs = _load()["events"]
    if window == "today":
        day = _day_str()
        return [e for e in evs if e.get("day") == day]
    if window == "week":
        cut = _now() - 7 * 86400
        return [e for e in evs if e.get("ts", 0) >= cut]
    return list(evs)


def agg(events):
    out = {}
    for e in events:
        s = e.get("symbol", "?")
        a = out.setdefault(s, {"entries": 0, "tp": 0, "tp_by": {}, "tp6": 0,
                               "reject": 0, "stop": 0, "max_tp": 0,
                               "grades": [], "winrates": [], "engines": set()})
        a["engines"].add(e.get("engine", ""))
        if e.get("grade"):
            a["grades"].append(e["grade"])
        if e.get("winrate") is not None:
            a["winrates"].append(e["winrate"])
        t = e.get("type")
        if t == "entry":
            a["entries"] += 1
        elif t == "tp":
            a["tp"] += 1
            lv = e.get("tp") or 0
            if lv:
                a["tp_by"][lv] = a["tp_by"].get(lv, 0) + 1
                a["max_tp"] = max(a["max_tp"], lv)
                if lv >= 6:
                    a["tp6"] += 1
        elif t == "reject":
            a["reject"] += 1
        elif t == "stop":
            a["stop"] += 1
    for s, a in out.items():
        decided = a["tp"] + a["reject"] + a["stop"]
        a["winpct"] = (a["tp"] / decided * 100.0) if decided else None
        a["rejpct"] = ((a["reject"] + a["stop"]) / decided * 100.0) if decided else None
        a["last_grade"] = a["grades"][-1] if a["grades"] else None
    return out


def _wlabel(window):
    return "This Week" if window == "week" else "Today"


def _pct(x):
    return f"{x:.0f}%" if x is not None else "—"


# ── digest templates ────────────────────────────────────────────────
def _scope_filter(events, scope):
    if scope == "calls":
        return [e for e in events if e.get("side") == "call"]
    if scope == "puts":
        return [e for e in events if e.get("side") == "put"]
    if scope == "options":
        return [e for e in events if e.get("engine") == "options"]
    if scope == "futures":
        return [e for e in events if e.get("engine") in ("ari", "ida", "orbit")]
    return events


def _scope_label(scope):
    return {"calls": " · Calls", "puts": " · Puts",
            "options": " · Options", "futures": " · Futures"}.get(scope, "")


def ida_overview(window="today", scope="all"):
    evs = _scope_filter(_events(window), scope)
    a = agg(evs)
    tot_tp = sum(v["tp"] for v in a.values())
    tot_rej = sum(v["reject"] for v in a.values())
    tot_stop = sum(v["stop"] for v in a.values())
    tot_tp6 = sum(v["tp6"] for v in a.values())
    decided = tot_tp + tot_rej + tot_stop
    winrate = (tot_tp / decided * 100.0) if decided else None
    ranked = sorted(a.items(), key=lambda kv: (kv[1]["tp"], -(kv[1]["reject"] + kv[1]["stop"])), reverse=True)
    lines = [f"📊 <b>ORBIT — IDA COMMAND CENTER · {_wlabel(window)}{_scope_label(scope)}</b>"]
    lines.append(f"Win rate <b>{_pct(winrate)}</b>  ·  TP hits <b>{tot_tp}</b>  ·  TP6 <b>{tot_tp6}</b>")
    lines.append(f"Rejections <b>{tot_rej}</b>  ·  Stop-outs <b>{tot_stop}</b>")
    if ranked:
        lines.append("")
        lines.append("<b>Top symbols</b>")
        for i, (s, v) in enumerate(ranked[:6], 1):
            lines.append(f"{i}. <code>{esc(s)}</code> — {v['tp']} TP · {_pct(v['winpct'])} · maxTP{v['max_tp'] or '-'}")
        graded = [(s, v) for s, v in a.items() if v["winpct"] is not None]
        if graded:
            best = max(graded, key=lambda kv: kv[1]["winpct"])
            worst = min(graded, key=lambda kv: kv[1]["winpct"])
            lines.append("")
            lines.append(f"Best <code>{esc(best[0])}</code> ({_pct(best[1]['winpct'])})  ·  Worst <code>{esc(worst[0])}</code> ({_pct(worst[1]['winpct'])})")
    else:
        lines.append("\n<i>No graded activity yet.</i>")
    lines.append("\n— stats, not advice 🤖")
    return "\n".join(lines)


def leaderboard(window="today", scope="all"):
    a = agg(_scope_filter(_events(window), scope))
    lines = [f"🏆 <b>ORBIT LEADERBOARD · {_wlabel(window)}{_scope_label(scope)}</b>"]
    if not a:
        return lines[0] + "\n\n<i>No activity yet.</i>"
    decided = {s: v for s, v in a.items() if v["winpct"] is not None}
    if decided:
        bw = max(decided.items(), key=lambda kv: kv[1]["winpct"])
        lines.append(f"Best win rate — <code>{esc(bw[0])}</code> {_pct(bw[1]['winpct'])}")
    mt = max(a.items(), key=lambda kv: kv[1]["tp"])
    lines.append(f"Most TP hits — <code>{esc(mt[0])}</code> {mt[1]['tp']}")
    m6 = max(a.items(), key=lambda kv: kv[1]["tp6"])
    if m6[1]["tp6"] > 0:
        lines.append(f"Most TP6 — <code>{esc(m6[0])}</code> {m6[1]['tp6']}")
    lr = min(a.items(), key=lambda kv: (kv[1]["reject"] + kv[1]["stop"]))
    lines.append(f"Fewest rejects — <code>{esc(lr[0])}</code> {lr[1]['reject'] + lr[1]['stop']}")
    if decided:
        worst = min(decided.items(), key=lambda kv: kv[1]["winpct"])
        lines.append(f"\nBest overall <code>{esc(bw[0])}</code>  ·  Worst <code>{esc(worst[0])}</code>")
    lines.append("\n— stats, not advice 🤖")
    return "\n".join(lines)


def hot_symbols(window="today", scope="all"):
    a = agg(_scope_filter(_events(window), scope))
    hot = []
    for s, v in a.items():
        if v["winpct"] is None:
            continue
        recent_a = v["last_grade"] == "A"
        if v["winpct"] >= 70 and (v["rejpct"] is None or v["rejpct"] < 20) and v["tp"] >= 2 and recent_a:
            hot.append((s, v))
    hot.sort(key=lambda kv: kv[1]["winpct"], reverse=True)
    lines = [f"🔥 <b>HOT SYMBOLS · {_wlabel(window)}{_scope_label(scope)}</b>",
             "<i>Win&gt;70% · rejects&lt;20% · recent Grade A · ≥2 TP</i>"]
    if not hot:
        lines.append("\n<i>None clear the bar right now. Stay patient.</i>")
    else:
        for s, v in hot:
            lines.append(f"• <code>{esc(s)}</code>  {_pct(v['winpct'])} · rej {_pct(v['rejpct'])} · {v['last_grade']} · maxTP{v['max_tp']}")
    lines.append("\n— stats, not advice 🤖")
    return "\n".join(lines)


def scorecard(window="today", scope="all"):
    a = agg(_scope_filter(_events(window), scope))
    lines = [f"📅 <b>ORBIT SCORECARD · {_wlabel(window)}{_scope_label(scope)}</b>"]
    if not a:
        return lines[0] + "\n\n<i>No activity yet.</i>"
    decided = {s: v for s, v in a.items() if v["winpct"] is not None}
    if decided:
        best = max(decided.items(), key=lambda kv: (kv[1]["winpct"], kv[1]["tp"]))
        lines.append(f"Best symbol — <code>{esc(best[0])}</code> ({_pct(best[1]['winpct'])}, {best[1]['tp']} TP)")
    clean = [(s, v) for s, v in a.items() if v["tp"] >= 1]
    if clean:
        cleanest = min(clean, key=lambda kv: (kv[1]["reject"] + kv[1]["stop"]))
        lines.append(f"Cleanest — <code>{esc(cleanest[0])}</code> ({cleanest[1]['reject'] + cleanest[1]['stop']} rejects)")
    noisiest = max(a.items(), key=lambda kv: (kv[1]["reject"] + kv[1]["stop"]))
    if (noisiest[1]["reject"] + noisiest[1]["stop"]) > 0:
        lines.append(f"Noisiest — <code>{esc(noisiest[0])}</code> ({noisiest[1]['reject'] + noisiest[1]['stop']} rejects/stops)")
    tot_tp = sum(v["tp"] for v in a.values())
    tot_rej = sum(v["reject"] for v in a.values())
    tot_stop = sum(v["stop"] for v in a.values())
    lines.append(f"\nTotals — TP <b>{tot_tp}</b> · Rejects <b>{tot_rej}</b> · Stops <b>{tot_stop}</b>")
    lines.append("\n— stats, not advice 🤖")
    return "\n".join(lines)


def _recent(window, types, title, emoji, scope="all"):
    evs = [e for e in _scope_filter(_events(window), scope) if e.get("type") in types]
    evs = sorted(evs, key=lambda e: e.get("ts", 0), reverse=True)[:10]
    lines = [f"{emoji} <b>{title} · {_wlabel(window)}{_scope_label(scope)}</b>"]
    if not evs:
        lines.append("\n<i>Nothing yet.</i>")
        return "\n".join(lines)
    for e in evs:
        sd = (e.get("side") or "").upper()
        tp = f" TP{e['tp']}" if e.get("tp") else ""
        gr = f" · {e['grade']}" if e.get("grade") else ""
        lines.append(f"• <code>{esc(e['symbol'])}</code> {sd}{tp}{gr}  <i>{_clock(e.get('ts', _now()))}</i>")
    return "\n".join(lines)


def status_msg():
    evs = _load()["events"]
    today = _events("today")
    up = int(_now() - _BOOT)
    h, m = up // 3600, (up % 3600) // 60
    fid = "set" if FORUM_ID else "—"
    return ("🛰 <b>ORBIT RELAY STATUS</b>\n"
            f"Uptime <b>{h}h {m}m</b>  ·  events stored <b>{len(evs)}</b> (today {len(today)})\n"
            f"Forum {fid}  ·  min grade {MIN_GRADE or 'all'}\n"
            f"Tabs: IDA {'✓' if TOPIC_IDA else '—'} · Entries {'✓' if TOPIC_ENTRIES else '—'} · "
            f"Targets {'✓' if TOPIC_TARGETS else '—'} · Rejected {'✓' if TOPIC_REJECTED else '—'} · "
            f"Leaderboard {'✓' if TOPIC_LEADERBOARD else '—'} · Hot {'✓' if TOPIC_HOT else '—'} · "
            f"Scorecard {'✓' if TOPIC_SCORECARD else '—'}")


# ── command dispatch ────────────────────────────────────────────────
def cmd_response(text):
    parts = str(text or "").strip().split()
    if not parts:
        return None
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    low = text.lower()
    window = "week" if "week" in low else "today"
    scope = "all"
    for sc in ("calls", "puts", "options", "futures"):
        if sc in low:
            scope = sc
            break
    if cmd == "ida":
        return ida_overview(window, scope)
    if cmd == "entries":
        return _recent(window, ("entry",), "RECENT ENTRIES", "📥", scope)
    if cmd == "targets":
        return _recent(window, ("tp",), "RECENT TP HITS", "🎯", scope)
    if cmd == "rejected":
        return _recent(window, ("reject", "stop"), "RECENT REJECTS / STOPS", "⚠️", scope)
    if cmd == "leaderboard":
        return leaderboard(window, scope)
    if cmd == "hot":
        return hot_symbols(window, scope)
    if cmd == "scorecard":
        return scorecard(window, scope)
    if cmd == "status":
        return status_msg()
    if cmd in ("help", "start"):
        return ("🛰 <b>ORBIT commands</b>\n"
                "/ida — accuracy command center\n"
                "/entries — recent entries\n"
                "/targets — recent TP hits\n"
                "/rejected — recent rejects/stops\n"
                "/leaderboard — best symbols\n"
                "/hot — only the cleanest setups\n"
                "/scorecard — day recap\n"
                "/status — relay health\n"
                "<i>add 'week' for 7-day · add 'calls' or 'puts' to filter</i>\n"
                "<i>e.g. /hot calls · /leaderboard puts week</i>")
    return None


def passes_quality(d):
    """Entry quality gate. Empty MIN_GRADE = allow all; missing grade = allow."""
    if not MIN_GRADE:
        return True
    g = str(d.get("grade", "")).upper()
    if not g:
        return True
    return GRADE_RANK.get(g, 0) >= GRADE_RANK.get(MIN_GRADE, 0)


# ── webhook ─────────────────────────────────────────────────────────
@app.route("/orbit", methods=["POST"])
def orbit():
    d = request.get_json(silent=True)
    if d is None:
        try:
            d = json.loads(request.get_data(as_text=True))
        except Exception:
            d = {}
    print(f"[orbit] received code={d.get('orbit')} engine={d.get('engine')}", flush=True)
    if SECRET and d.get("secret") != SECRET:
        print("Rejected alert: bad/missing secret")
        return "bad secret", 401
    try:
        code = d.get("orbit", "")
        side = d.get("side", "")
        record_event(d)
        if classify(code, d.get("engine", "")) == "entry" and not passes_quality(d):
            print(f"[orbit] filtered low-grade entry {d.get('symbol')} grade={d.get('grade')}", flush=True)
            return "ok (filtered)"
        send_telegram(build_message(d), code, side)
        return "ok"
    except Exception as e:
        print("Relay error:", e)
        return "error", 500


@app.route("/")
def home():
    return "ORBIT × Ari relay is up. POST alerts to /orbit"


@app.route("/telegram", methods=["POST"])
def telegram_hook():
    # Receives bot updates (slash-commands) after setWebhook. Replies in-thread.
    u = request.get_json(silent=True) or {}
    msg = u.get("message") or u.get("channel_post") or u.get("edited_message") or {}
    text = msg.get("text", "") or ""
    chat = (msg.get("chat") or {}).get("id")
    thread = msg.get("message_thread_id")
    if text.startswith("/") and chat is not None:
        cmd_word = text.strip().split()[0].lstrip("/").split("@")[0].lower()
        if cmd_word in ("id", "whereami", "thread"):
            tid = thread if thread is not None else "General (no topic)"
            _post(chat, f"🪪 This tab's <b>thread id</b>: <code>{tid}</code>\nchat id: <code>{chat}</code>", thread)
            return "ok"
        resp = cmd_response(text)
        if resp:
            _post(chat, resp, thread)
    return "ok"


@app.route("/cron", methods=["GET", "POST"])
def cron():
    # Hit this from a free external scheduler to post digests on a timer.
    #   /cron?key=YOURKEY&what=scorecard&window=today
    if request.args.get("key") != CRON_KEY:
        return "no", 401
    what = request.args.get("what", "scorecard")
    window = request.args.get("window", "today")
    scope = request.args.get("scope", "all")
    if not FORUM_ID:
        return "no forum", 200
    routing = {
        "scorecard":   (TOPIC_SCORECARD,   scorecard),
        "leaderboard": (TOPIC_LEADERBOARD, leaderboard),
        "hot":         (TOPIC_HOT,         hot_symbols),
        "ida":         (TOPIC_IDA,         ida_overview),
    }
    if what in routing:
        thread, fn = routing[what]
        if thread:
            _post(FORUM_ID, fn(window, scope), thread)
            return "posted"
    return "skipped"


@app.route("/set_command_webhook")
def set_command_webhook():
    # One-time: registers {host}/telegram with Telegram so slash-commands work.
    # NOTE: this disables getUpdates for the bot (you already have your tab ids).
    if request.args.get("key") != CRON_KEY:
        return "no", 401
    url = request.host_url.rstrip("/") + "/telegram"
    api = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
    body = json.dumps({"url": url, "allowed_updates": ["message", "channel_post"]}).encode("utf-8")
    req = urllib.request.Request(api, data=body, headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.read().decode("utf-8", "ignore")
    except Exception as e:
        return f"error: {e}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
