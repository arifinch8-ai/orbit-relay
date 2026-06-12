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
def build_message(d):
    if d.get("engine") == "ari" or str(d.get("orbit", "")).startswith("ari_"):
        return ari_message(d)
    return orbit_message(d)


# ── Telegram send (stdlib only) ─────────────────────────────────────
def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        print("Telegram error:", e.code, e.read().decode("utf-8", "ignore"))
    except Exception as e:
        print("Telegram send failed:", e)


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
        send_telegram(build_message(d))
        return "ok"
    except Exception as e:
        print("Relay error:", e)
        return "error", 500


@app.route("/")
def home():
    return "ORBIT × Ari relay is up. POST alerts to /orbit"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
