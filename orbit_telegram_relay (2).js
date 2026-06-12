// ════════════════════════════════════════════════════════════════════
//  ORBIT × Ari Logic → Telegram relay   (Node.js 18+, one dep: express)
//
//  Turns the JSON the merged indicator sends (TradingView webhooks) into
//  clean, emoji-clear Telegram messages. It spells out:
//    • the TICK TARGETS ladder  (TP1…TP5 + Swing, with points & R)
//    • nearest RESISTANCE / SUPPORT levels
//    • HITS and REJECTIONS ("closed back — watch retest / reversal")
//    • stop, exit and reversal warnings
//
//  SETUP
//  ─────
//  1) npm install express        (or use the bundled package.json)
//  2) Environment variables:
//        TELEGRAM_TOKEN    = bot token from @BotFather
//        TELEGRAM_CHAT_ID  = chat / channel id to post to
//        ORBIT_SECRET      = same string as the indicator's "Webhook secret"
//        PORT              = optional (default 3000)
//  3) node orbit_telegram_relay.js
//  4) TradingView alert:  Condition = "Any alert() function call"
//                         Webhook URL = https://YOUR_SERVER/orbit
//
//  FONTS: Telegram bots can't change the font family. The "nice" look is
//  bold headlines + <code> monospace numbers + emoji (HTML parse mode).
// ════════════════════════════════════════════════════════════════════

const express = require("express");
const app = express();
app.use(express.json({ limit: "32kb" }));

const TOKEN   = process.env.TELEGRAM_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;
const SECRET  = process.env.ORBIT_SECRET || "";
const PORT    = process.env.PORT || 3000;

// ── helpers ─────────────────────────────────────────────────────────
const has = (v) => v !== null && v !== undefined && v !== "" && !Number.isNaN(Number(v));
const n2  = (v) => (has(v) ? Number(v).toFixed(2) : "—");
const px  = (v) => (has(v) ? `<code>${Number(v).toFixed(2)}</code>` : "—");
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const sideArrow = (side) => (String(side).toUpperCase() === "LONG" ? "📈" : "📉");

// Nearest resistance / support, always shown when present.
function srLine(d) {
  if (!has(d.near_res) && !has(d.near_sup)) return "";
  const r = has(d.near_res) ? `R ${px(d.near_res)}` : "";
  const s = has(d.near_sup) ? `S ${px(d.near_sup)}` : "";
  return `🧭 <b>Levels</b>  ${[r, s].filter(Boolean).join("  ·  ")}`;
}

// ── ARI: tick-target ladder ─────────────────────────────────────────
const ARI_TIERS = [
  { key: "tp1",   name: "TP1",   emoji: "🏆", tail: "" },
  { key: "tp2",   name: "TP2",   emoji: "🏆", tail: "" },
  { key: "tp3",   name: "TP3",   emoji: "💎", tail: "" },
  { key: "tp4",   name: "TP4",   emoji: "💰", tail: " IN PROFIT" },
  { key: "tp5",   name: "TP5",   emoji: "🚀", tail: "" },
  { key: "swing", name: "Swing", emoji: "🌙", tail: "" },
];

function tierRow(d, t) {
  const price = d[t.key];
  if (!has(price)) return null;
  const pts = has(d[`${t.key}_pts`]) ? `+${Math.round(d[`${t.key}_pts`])}pt` : "";
  const rr  = has(d[`${t.key}_rr`]) && Number(d[`${t.key}_rr`]) > 0 ? ` · ${Number(d[`${t.key}_rr`]).toFixed(1)}R` : "";
  return `${t.emoji} <b>${t.name}</b> ${px(price)}  ${pts}${rr}${t.tail}`;
}

function tickLadder(d) {
  const rows = ARI_TIERS.map((t) => tierRow(d, t)).filter(Boolean);
  if (!rows.length) return "";
  let head = `🎯 <b>Tick targets</b>`;
  if (has(d.stop)) head += `   ⛔ Stop ${px(d.stop)}${has(d.stop_pts) ? ` (${Math.round(d.stop_pts)}pt)` : ""}`;
  return head + "\n" + rows.join("\n");
}

function ariStats(d) {
  const bits = [];
  if (d.grade)        bits.push(`Grade ${esc(d.grade)}`);
  if (d.conf)         bits.push(esc(d.conf));
  if (has(d.winrate)) bits.push(`Win ${Number(d.winrate).toFixed(0)}%`);
  return bits.length ? `📊 ${bits.join(" · ")}` : "";
}

function tierFromCode(code) {
  const m = String(code).match(/^ari_(tp[1-5]|swing)(_reject)?$/);
  if (!m) return null;
  const t = ARI_TIERS.find((x) => x.key === m[1]);
  return t ? { tier: t, rejected: !!m[2] } : null;
}

function ariMessage(d) {
  const sym  = esc(d.symbol || "ALERT");
  const side = esc((d.side || "").toUpperCase());
  const arrow = sideArrow(d.side);
  const lines = [];

  if (d.orbit === "ari_long" || d.orbit === "ari_short") {
    const colour = side === "LONG" ? "🟢" : "🔴";
    lines.push(`${colour}${arrow} <b>${sym} — NEW ${side}</b>`);
    if (has(d.entry)) {
      let plan = `🎬 Entry ${px(d.entry)}`;
      if (has(d.stop)) plan += `   ⛔ Stop ${px(d.stop)}${has(d.stop_pts) ? ` (${Math.round(d.stop_pts)}pt)` : ""}`;
      lines.push(plan);
    }
    const lad = tickLadder({ ...d, stop: undefined });
    if (lad) lines.push(lad);
    const sr = srLine(d); if (sr) lines.push(sr);
    const st = ariStats(d); if (st) lines.push(st);
  } else if (tierFromCode(d.orbit)) {
    const { tier, rejected } = tierFromCode(d.orbit);
    const price = px(d[tier.key]);
    const pts = has(d[`${tier.key}_pts`]) ? ` (+${Math.round(d[`${tier.key}_pts`])}pt)` : "";
    if (rejected) {
      lines.push(`🧱 <b>${sym} REJECTED at ${tier.name}</b> ${price}${pts} — closed back. Watch retest / reversal 👀`);
    } else {
      lines.push(`${tier.emoji}💥 <b>${sym} ${tier.name} HIT</b> ${price}${pts} — bank it 🎉`);
    }
    const sr = srLine(d); if (sr) lines.push(sr);
    const lad = tickLadder(d); if (lad) lines.push(lad);
  } else if (d.orbit === "ari_stop_hit") {
    lines.push(`⛔ <b>${sym} STOPPED</b> ${px(d.stop)} (−1R) — plan's done, live to trade again 🛟`);
    const sr = srLine(d); if (sr) lines.push(sr);
  } else if (d.orbit === "ari_exit") {
    lines.push(`🟠 <b>${sym} EXIT</b> — momentum / flow turned against the ${side || "trade"} 👋 lock profits`);
    const sr = srLine(d); if (sr) lines.push(sr);
  } else if (d.orbit === "ari_reversal") {
    lines.push(`🔁 <b>${sym} reversal brewing</b> — the opposite side is gaining confluence`);
    const sr = srLine(d); if (sr) lines.push(sr);
  } else {
    lines.push(`🛰 <b>${sym}</b> ${esc(d.orbit || "alert")}`);
  }

  lines.push("— facts, not a forecast 🤖");
  return lines.join("\n");
}

// ── ORBIT: structural-level events ──────────────────────────────────
function headingLine(d) {
  const toSupport = (d.heading || "").toLowerCase().includes("support");
  const target = toSupport ? d.near_sup : d.near_res;
  if (!has(target) || !has(d.price)) return "";
  const dist = Math.abs(((target - d.price) / d.price) * 100).toFixed(2);
  const word = toSupport ? "down toward support" : "up toward resistance";
  return `🧭 Heading ${word} — ${px(target)} (${dist}% away)`;
}

function ladderLine(d) {
  if (!has(d.tp1) && !has(d.tp2) && !has(d.tp3)) return "";
  const parts = [];
  if (has(d.tp1)) parts.push(`TP1 ${n2(d.tp1)}`);
  if (has(d.tp2)) parts.push(`TP2 ${n2(d.tp2)}`);
  if (has(d.tp3)) parts.push(`TP3 ${n2(d.tp3)}`);
  let line = `🪜 ${parts.join(" · ")}`;
  if (has(d.stop)) line += `   ⛔ Stop ${n2(d.stop)}`;
  return line;
}

function orbitHeadline(d) {
  const s = esc(d.symbol || "ALERT");
  const t = px(d.target);
  const nm = esc(d.target_name || "target");
  const lv = px(d.level);
  const dist = has(d.dist_pct) ? ` — only ${Number(d.dist_pct).toFixed(2)}% away 🏃` : "";
  switch (d.orbit) {
    case "approach":         return `📡 <b>${s}</b> closing in on ${nm} ${t}${dist}`;
    case "target_met":       return `🎯💥 <b>${s} TARGET SMASHED</b> — ${nm} ${t} cleared on the close!`;
    case "target_rejected":  return `🧱 <b>${s} REJECTED</b> at ${nm} ${t} — closed back. Watch retest / reversal 👀`;
    case "target_retest":    return `🔁 <b>${s}</b> back for a RETEST at ${t} — does it hold?`;
    case "tp1":              return `💰 <b>${s} TP1 tagged</b> ${t} — bank a little 🎉`;
    case "tp2":              return `💰💰 <b>${s} TP2 tagged</b> ${t} — trim again`;
    case "runner":           return `🚀 <b>${s} TP3 hit</b> ${t} — runner mode, trail your stop`;
    case "stop":             return `⛔ <b>${s} STOP</b> ${t} — plan's done 🛟`;
    case "resistance_touch": return `🔴 <b>${s}</b> tapped resistance ${lv} — reject or reclaim? 👀`;
    case "support_touch":    return `🟢 <b>${s}</b> tapped support ${lv} — hold or fold? 👀`;
    case "reclaim":          return `✅ <b>${s}</b> reclaimed ${lv} (closed above) — bulls poking back 🐂`;
    case "breakdown":        return `🔻 <b>${s}</b> lost ${lv} (closed below) — mind the downside 🛡`;
    case "trend_up":         return `📈 <b>${s}</b> momentum flipped UP — EMA 9 over 21 🐂`;
    case "trend_down":       return `📉 <b>${s}</b> momentum flipped DOWN — EMA 9 under 21 🐻`;
    case "vwap_reclaim":     return `🔵 <b>${s}</b> reclaimed VWAP ${lv} — back above the line`;
    case "vwap_lost":        return `🔵 <b>${s}</b> lost VWAP ${lv} — slipped below the line`;
    default:                 return `🛰 <b>${s}</b> ${esc(d.orbit || "alert")}`;
  }
}

function orbitMessage(d) {
  const lines = [orbitHeadline(d)];
  const h = headingLine(d); if (h) lines.push(h);
  const sr = srLine(d);     if (sr && !h) lines.push(sr);
  const l = ladderLine(d);  if (l) lines.push(l);
  if (d.ema || d.vol) lines.push(`<i>Price ${n2(d.price)} · EMA ${esc(d.ema || "—")} · Vol ${esc(d.vol || "—")}</i>`);
  lines.push("— facts, not a forecast 🤖");
  return lines.join("\n");
}

// ── router ──────────────────────────────────────────────────────────
function buildMessage(d) {
  if (d.engine === "ari" || String(d.orbit || "").startsWith("ari_")) return ariMessage(d);
  return orbitMessage(d);
}

// ── Telegram send ───────────────────────────────────────────────────
async function sendTelegram(text) {
  if (!TOKEN || !CHAT_ID) {
    console.error("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID");
    return;
  }
  const url = `https://api.telegram.org/bot${TOKEN}/sendMessage`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: CHAT_ID,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
  if (!res.ok) console.error("Telegram error:", res.status, await res.text());
}

// ── webhook ─────────────────────────────────────────────────────────
app.post("/orbit", async (req, res) => {
  let d = req.body;
  if (typeof d === "string") { try { d = JSON.parse(d); } catch { d = {}; } }
  if (SECRET && d.secret !== SECRET) {
    console.warn("Rejected alert: bad/missing secret");
    return res.status(401).send("bad secret");
  }
  try {
    await sendTelegram(buildMessage(d));
    res.send("ok");
  } catch (e) {
    console.error("Relay error:", e);
    res.status(500).send("error");
  }
});

app.get("/", (_req, res) => res.send("ORBIT × Ari relay is up. POST alerts to /orbit"));
app.listen(PORT, () => console.log(`ORBIT × Ari relay listening on :${PORT}`));
