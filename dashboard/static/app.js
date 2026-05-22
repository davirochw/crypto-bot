const fmt = (v, d = 2) => v == null ? "—" : Number(v).toFixed(d);
const fmtPrice = (v) => v == null ? "—" : Number(v).toPrecision(6);
const trendClass = (t) => ({BULLISH: "bullish", BEARISH: "bearish"})[t] || "neutral";

async function refresh() {
  try {
    const [status, ctxs, sigs, paper] = await Promise.all([
      fetch("/api/status").then(r => r.json()),
      fetch("/api/contexts").then(r => r.json()),
      fetch("/api/signals").then(r => r.json()),
      fetch("/api/paper").then(r => r.json()),
    ]);
    document.getElementById("status-dot").classList.toggle("live", !!status.running);
    document.getElementById("scan-info").textContent =
      `scan #${status.scan_count} ${status.last_scan_at ? "· " + new Date(status.last_scan_at).toLocaleTimeString() : ""}`;

    const tbody = document.querySelector("#contexts-table tbody");
    tbody.innerHTML = ctxs.map(c => `
      <tr>
        <td><b>${c.symbol}</b></td>
        <td class="${trendClass(c.macro_trend)}">${c.macro_trend}</td>
        <td>${c.regime}</td>
        <td>${fmtPrice(c.close)}</td>
        <td>${fmt(c.rsi_1h, 1)}</td>
        <td>${fmt(c.atr, 4)}</td>
        <td>${fmt(c.funding, 5)}</td>
        <td>${fmt(c.long_short_ratio, 2)}</td>
      </tr>`).join("") || "<tr><td colspan='8' class='neutral'>Aguardando dados…</td></tr>";

    const sigList = document.getElementById("signals-list");
    sigList.innerHTML = sigs.map(s => {
      // Linha de Monte Carlo — só aparece se o MC rodou.
      const mcLine = s.p_tp != null
        ? `<div class="mc"><b>🎲 MC:</b> P(TP)=<b>${(s.p_tp*100).toFixed(0)}%</b> · P(SL)=<b>${((s.p_sl||0)*100).toFixed(0)}%</b> · EV=<b class="${s.ev_pct >= 0 ? "pos" : "neg"}">${s.ev_pct >= 0 ? "+" : ""}${fmt(s.ev_pct, 2)}%</b></div>`
        : "";
      // Linha do Order Book — empurrão direcional do livro.
      const obLine = s.ob_imbalance != null
        ? `<div class="ob"><b>📖 Book:</b> imb=<b class="${s.ob_imbalance > 0.15 ? "pos" : (s.ob_imbalance < -0.15 ? "neg" : "neutral")}">${s.ob_imbalance >= 0 ? "+" : ""}${fmt(s.ob_imbalance, 2)}</b> · spread=<b>${fmt(s.ob_spread_pct, 3)}%</b></div>`
        : "";
      return `
      <div class="card">
        <div class="head">
          <span class="symbol">${s.symbol} · ${s.side} · ${s.strategy}</span>
          <span class="score ${s.score >= 80 ? "high" : ""}">${s.score}/100</span>
        </div>
        <div>
          <b>Entry:</b> ${fmtPrice(s.entry)} ·
          <b>Stop:</b> ${fmtPrice(s.stop)} ·
          <b>Take:</b> ${fmtPrice(s.take_profit)} ·
          <b>R:R:</b> ${fmt(s.risk_reward)}
        </div>
        ${mcLine}
        ${obLine}
        <div class="reasons">${(s.reasons || []).map(r => "• " + r).join("<br>")}</div>
        ${s.ai_commentary ? `<div class="ai">${s.ai_commentary}</div>` : ""}
      </div>`;
    }).join("") || "<div class='neutral'>Nenhum sinal ainda.</div>";

    const kpis = document.getElementById("paper-stats");
    if (paper.enabled) {
      const st = paper.stats;
      const pnlClass = st.net_pnl >= 0 ? "pos" : "neg";
      // `available` e `margin_in_use` foram adicionados no PaperTrader
      // pra refletir o modelo de margem alavancada da Binance Futures.
      const availLine = st.margin_in_use > 0
        ? `<div class="kpi"><div class="label">Disponível / Margem</div><div class="val">$${fmt(st.available)} <span class="neutral">/ $${fmt(st.margin_in_use)}</span></div></div>`
        : "";
      kpis.innerHTML = `
        <div class="kpi"><div class="label">Saldo</div><div class="val">$${fmt(st.balance)}</div></div>
        ${availLine}
        <div class="kpi"><div class="label">PnL líquido</div><div class="val ${pnlClass}">${st.net_pnl >= 0 ? "+" : ""}$${fmt(st.net_pnl)}</div></div>
        <div class="kpi"><div class="label">Winrate</div><div class="val">${fmt(st.winrate_pct, 1)}%</div></div>
        <div class="kpi"><div class="label">Trades</div><div class="val">${st.closed} fechados / ${st.open} abertos</div></div>`;
      const openBody = document.querySelector("#paper-open tbody");
      openBody.innerHTML = paper.open_trades.map(t => `
        <tr>
          <td>${t.symbol}</td>
          <td class="${t.side === "LONG" ? "bullish" : "bearish"}">${t.side}</td>
          <td>${fmtPrice(t.entry)}</td>
          <td>${fmtPrice(t.stop)}</td>
          <td>${fmtPrice(t.take_profit)}</td>
          <td>$${fmt(t.size)}</td>
          <td>$${fmt(t.margin)}</td>
          <td>${t.leverage ? fmt(t.leverage, 0) + "×" : "—"}</td>
        </tr>`).join("") || "<tr><td colspan='8' class='neutral'>Sem posições abertas.</td></tr>";
    } else {
      kpis.innerHTML = "<div class='neutral'>Paper trade desabilitado.</div>";
    }
  } catch (e) {
    console.error(e);
  }
}

document.getElementById("brief-btn").addEventListener("click", async () => {
  const btn = document.getElementById("brief-btn");
  btn.disabled = true; btn.textContent = "Gerando…";
  try { await fetch("/api/market_brief", {method: "POST"}); }
  finally { btn.disabled = false; btn.textContent = "Resumo IA"; }
});

refresh();
setInterval(refresh, 5000);
