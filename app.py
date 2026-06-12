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


def choose_contract(results, side, target_delta, target_date):
    want = "call" if side == "call" else "put"
    cands = []
    for r in results:
        det = r.get("details", {}) or {}
        if det.get("contract_type") != want:
            continue
        exp = det.get("expiration_date")
        if exp:
            cands.append((exp, r))
    if not cands:
        return None
    exps = sorted({e for e, _ in cands})
    chosen = next((e for e in exps if e >= target_date), exps[-1])
    pool = [r for e, r in cands if e == chosen]

    def dscore(r):
        g = r.get("greeks") or {}
        de = g.get("delta")
        return 999 if de is None else abs(abs(de) - target_delta)

    pool.sort(key=dscore)
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
            "iv": r.get("implied_volatility"),
            "oi": r.get("open_interest"),
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


def options_message(d):
    sym = esc(d.get("symbol", "ALERT"))
    code = str(d.get("orbit", ""))

    if code in ("options_call", "options_put"):
        is_call = code == "options_call"
        side = "call" if is_call else "put"
        cp = "C" if is_call else "P"
        arrow = "\U0001F7E2\U0001F4C8" if is_call else "\U0001F534\U0001F4C9"
        target_delta = numf(d.get("opt_delta")) or 0.60
        mode = str(d.get("opt_expiry", "weekly")).lower()
        lines = [f"{arrow} <b>{sym} \u2014 {'CALL' if is_call else 'PUT'}</b>"]

        c = polygon_pick_contract(sym, side, target_delta, mode, numf(d.get("price")))
        if c and c.get("strike") is not None:
            strike = f"{float(c['strike']):g}"
            lines.append(f"\U0001F4C4 <b>{sym} {strike}{cp}</b> \u00B7 exp <code>{esc(c.get('expiry') or '')}</code>")
            met = []
            if c.get("delta") is not None:
                met.append(f"\u0394 {float(c['delta']):.2f}")
            if c.get("iv") is not None:
                met.append(f"IV {round(float(c['iv']) * 100)}%")
            if c.get("oi") is not None:
                met.append(f"OI {int(c['oi'])}")
            prem = None
            if numf(c.get("bid")) is not None and numf(c.get("ask")) is not None:
                prem = f"{float(c['bid']):.2f}/{float(c['ask']):.2f}"
            elif numf(c.get("last")) is not None:
                prem = f"{float(c['last']):.2f}"
            if prem:
                met.append(f"${prem}")
            if met:
                lines.append("\U0001F4B5 " + " \u00B7 ".join(met))
        else:
            px_ = numf(d.get("price"))
            step = numf(d.get("opt_strike_step")) or 1.0
            if px_ is not None and step:
                strike = round(px_ / step) * step
                lines.append(f"\U0001F4C4 <b>{sym} {strike:g}{cp}</b> \u00B7 exp <code>{_expiry_target(str(d.get('opt_expiry', 'weekly')).lower())}</code> <i>(suggested)</i>")
            lines.append("<i>live contract unavailable \u2014 suggestion only</i>")

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
        return f"{em}\U0001F4A5 <b>{sym} {nm} HIT</b> {val} \u2014 take profit \U0001F389\n\u2014 facts, not a forecast \U0001F916"

    if code == "options_stop":
        sp = px(d.get("stop")) if numf(d.get("stop")) is not None else px(d.get("price"))
        return f"\u26D4 <b>{sym} STOPPED</b> {sp} \u2014 close it \U0001F6DF\n\u2014 facts, not a forecast \U0001F916"

    if code == "options_reject":
        rp = px(d.get("price"))
        return f"\U0001F9F1 <b>{sym} REJECTED</b> {rp} \u2014 setup failed, stand down \U0001F440\n\u2014 facts, not a forecast \U0001F916"

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


def topic_for(code):
    code = str(code or "")
    if code in ("options_call",):
        return TOPIC_OPT_CALLS
    if code in ("options_put",):
        return TOPIC_OPT_PUTS
    if code.startswith("options_tp"):
        return TOPIC_OPT_HIT
    if code == "options_stop":
        return TOPIC_OPT_STOPPED
    if code == "options_reject":
        return TOPIC_OPT_REJECTED
    if code.startswith("ida_"):
        return TOPIC_IDA
    if code in ("ari_long", "ari_short"):
        return TOPIC_ENTRIES
    if code.startswith("ari_tp") or code in ("ari_swing", "ari_stop_hit", "tp1", "tp2", "runner", "stop", "target_met", "target_rejected", "target_retest", "approach"):
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


def send_telegram(text, code=""):
    if not TOKEN:
        print("Missing TELEGRAM_TOKEN")
        return
    # 1) flat feed: personal chat + optional broadcast channel
    for cid in CHAT_IDS:
        _post(cid, text)
    # 2) group tab routing: drop into the matching topic if the forum is configured
    if FORUM_ID:
        thread = topic_for(code)
        if thread:
            _post(FORUM_ID, text, thread)


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
        send_telegram(build_message(d), d.get("orbit", ""))
        return "ok"
    except Exception as e:
        print("Relay error:", e)
        return "error", 500


@app.route("/")
def home():
    return "ORBIT × Ari relay is up. POST alerts to /orbit"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
