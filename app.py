"""
ORBIT → Telegram relay server
-----------------------------
Receives webhook alerts from TradingView (sent by the ORBIT indicator),
formats them into a clean Telegram message, and forwards them to your chat.

HONEST BY DESIGN: this server forwards FACTS only (level, EMA state,
volatility, distance). It never adds a price prediction. If you later bolt
on an LLM, keep it to phrasing the facts — do not let it invent targets.

Environment variables (set these in Render, NOT in the code):
  TELEGRAM_TOKEN   - from @BotFather
  TELEGRAM_CHAT_ID - your chat id (from @userinfobot)
  ORBIT_SECRET     - optional shared secret; if set, alerts must include it
"""

import os
import logging
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SECRET = os.environ.get("ORBIT_SECRET", "")  # optional

# Map ORBIT event codes -> emoji + plain-English line (facts only, no predictions)
EVENT_STYLE = {
    "support_touch": ("🟦", "Price touched nearest support — watch for a hold or a break."),
    "resistance_touch": ("🟥", "Price touched nearest resistance — watch for a reject or a reclaim."),
    "reclaim": ("🟢", "Closed back above nearest resistance — bias improving."),
    "breakdown": ("🔴", "Closed below nearest support — protect yourself."),
    "trend_up": ("📈", "EMA 9 crossed above 21 — momentum turned up."),
    "trend_down": ("📉", "EMA 9 crossed below 21 — momentum turned down."),
    "tp1": ("💰", "TP1 reached — trim and lock gains."),
    "tp2": ("💰", "TP2 reached — trim again."),
    "runner": ("🚀", "TP3 reached — runner only, trail your stop."),
    "stop": ("⛔", "Stop / invalidation hit — plan invalidated."),
    "bull_confirmed": ("🟢", "Bull thesis strengthening — reclaim held, momentum up."),
    "profit_protection": ("🟡", "Extended into resistance — consider reducing exposure."),
    "thesis_weakening": ("🔴", "Key support lost — bull thesis weakening."),
    "reversal_confirmed": ("🚨", "Trend reversal confirmed — original thesis invalidated."),
}


def fmt(data: dict) -> str:
    """Build the Telegram text from the ORBIT JSON payload. Facts only."""
    code = str(data.get("orbit", "alert")).lower()
    emoji, line = EVENT_STYLE.get(code, ("🛰", "ORBIT alert."))
    sym = data.get("symbol", "?")
    price = data.get("price")
    level = data.get("level")
    ema = data.get("ema")
    vol = data.get("vol")
    nxt = data.get("next")
    move = data.get("move_pct")

    parts = [f"{emoji} ORBIT · {sym}", line]
    facts = []
    if price is not None:
        facts.append(f"Price: {price}")
    if level is not None:
        facts.append(f"Level: {level}")
    if ema is not None:
        facts.append(f"EMA: {ema}")
    if vol is not None:
        facts.append(f"Vol: {vol}")
    if nxt is not None:
        facts.append(f"Next: {nxt}")
    if move is not None:
        facts.append(f"Move: {move}%")
    if facts:
        parts.append(" | ".join(facts))
    parts.append("— facts, not a forecast")
    return "\n".join(parts)


def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
        if r.status_code != 200:
            logging.error("Telegram error %s: %s", r.status_code, r.text)
            return False
        return True
    except requests.RequestException as e:
        logging.error("Telegram request failed: %s", e)
        return False


@app.route("/")
def health():
    # Render pings this; also lets you confirm the server is alive in a browser.
    return "ORBIT relay is running.", 200


@app.route("/orbit", methods=["POST"])
def orbit():
    if not TOKEN or not CHAT_ID:
        logging.error("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID env vars.")
        return jsonify(error="server not configured"), 500

    # TradingView sometimes sends text/plain; force JSON parse and fall back gracefully.
    data = request.get_json(force=True, silent=True)
    if data is None:
        raw = request.get_data(as_text=True).strip()
        # allow a plain string alert too
        data = {"orbit": "alert", "symbol": "?", "note": raw}

    # optional shared-secret check
    if SECRET and str(data.get("secret", "")) != SECRET:
        logging.warning("Rejected webhook: bad secret.")
        return jsonify(error="unauthorized"), 401

    text = fmt(data)
    ok = send_telegram(text)
    return jsonify(ok=ok), (200 if ok else 502)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
