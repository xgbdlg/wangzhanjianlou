// monitor.js
// 监控面板：实时市场/拍卖数据 + 日志控制台

const API = "http://127.0.0.1:8080";
const WS_URL = "ws://127.0.0.1:8081";

let ws = null;
let refreshTimer = null;
let logs = [];

// ────────────────────────── 初始化 ──────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  connectWS();
  refreshAll();
  refreshTimer = setInterval(refreshAll, 5000);

  document.getElementById("refreshMarket").addEventListener("click", loadMarketDeals);
  document.getElementById("refreshAuction").addEventListener("click", loadAuctions);
});

// ────────────────────────── WebSocket ──────────────────────────

function connectWS() {
  try { ws = new WebSocket(WS_URL); } catch (e) { return; }

  ws.onopen = () => {
    document.getElementById("connStatus").textContent = "已连接";
    document.getElementById("connStatus").className = "conn-badge connected";
    addLog("info", "WebSocket 已连接");
  };

  ws.onmessage = (event) => {
    try {
      const { event: evt, data } = JSON.parse(event.data);
      handleWSEvent(evt, data);
    } catch {}
  };

  ws.onclose = () => {
    document.getElementById("connStatus").textContent = "已断开";
    document.getElementById("connStatus").className = "conn-badge disconnected";
    setTimeout(connectWS, 5000);
  };
}

function handleWSEvent(event, data) {
  switch (event) {
    case "deal_alert":
      addLog("deal", `捡漏: ${data.name} 折扣 ${data.discount_pct?.toFixed(1)}%`);
      loadMarketDeals();
      break;
    case "stop_loss_triggered":
      addLog("stop", data.message || "止损触发");
      loadAuctions();
      break;
    case "auction_update":
      loadAuctions();
      break;
  }
}

// ────────────────────────── API ──────────────────────────

async function api(path, method = "GET", body = null) {
  try {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(API + path, opts);
    return { status: resp.status, data: await resp.json() };
  } catch {
    return { status: 0, error: "后端未连接" };
  }
}

// ────────────────────────── 刷新 ──────────────────────────

async function refreshAll() {
  await Promise.all([loadMarketDeals(), loadAuctions()]);
}

// ────────────────────────── 市场捡漏表格 ──────────────────────────

async function loadMarketDeals() {
  const { data } = await api("/api/market/deals?limit=50");
  const tbody = document.querySelector("#marketTable tbody");
  if (!data?.deals?.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="color:#666;text-align:center">暂无记录</td></tr>';
    return;
  }

  tbody.innerHTML = data.deals.map((d) => `
    <tr>
      <td>${timeAgo(d.created_at)}</td>
      <td title="${esc(d.market_hash_name)}">${truncate(d.market_hash_name, 28)}</td>
      <td>${d.wear || "-"}</td>
      <td>$${d.empire_price_usd?.toFixed(2)}</td>
      <td>$${d.buff_price_usd?.toFixed(2)}</td>
      <td style="color:${d.discount_pct > 0 ? '#4CAF50' : '#F44336'}">${d.discount_pct?.toFixed(1)}%</td>
      <td class="status-${d.status}">${statusLabel(d.status)}</td>
      <td>
        ${d.status === "detected" ? `<button class="btn-sm btn-buy" onclick="manualBuy('${d.item_id}')">购买</button>` : "-"}
      </td>
    </tr>
  `).join("");
}

// ────────────────────────── 拍卖表格 ──────────────────────────

async function loadAuctions() {
  const [{ data: active }, { data: history }] = await Promise.all([
    api("/api/auction/active"),
    api("/api/auction/history?limit=30"),
  ]);

  const auctions = active?.auctions || [];
  const tbody = document.querySelector("#auctionTable tbody");

  if (!auctions.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="color:#666;text-align:center">无活跃拍卖</td></tr>';
    return;
  }

  tbody.innerHTML = auctions.map((a) => {
    const remaining = getRemaining(a.expires_at);
    const urgent = remaining > 0 && remaining < 30 ? "countdown-urgent" : "";
    return `
    <tr>
      <td title="${esc(a.market_hash_name)}">${truncate(a.market_hash_name, 25)}</td>
      <td>${a.wear || "-"}</td>
      <td>${a.current_bid?.toFixed(1)}</td>
      <td>$${a.buff_price_usd?.toFixed(2)}</td>
      <td style="color:${a.discount_pct > 0 ? '#4CAF50' : '#F44336'}">${a.discount_pct?.toFixed(1)}%</td>
      <td class="status-${a.status}">${statusLabel(a.status)}</td>
      <td class="${urgent}">${remaining > 0 ? remaining + "s" : "过期"}</td>
      <td>
        ${a.status === "bidding" || a.status === "waiting" ? `
          <button class="btn-sm btn-bid" onclick="manualBid('${a.id}')">出价+1%</button>
          <button class="btn-sm btn-abort" onclick="manualAbort('${a.id}')">放弃</button>
        ` : "-"}
      </td>
    </tr>
  `;
  }).join("");
}

// ────────────────────────── 手动操作 ──────────────────────────

async function manualBuy(itemId) {
  if (!confirm("确定购买此物品？")) return;
  const { status, data } = await api("/api/market/buy", "POST", { item_id: itemId });
  if (status === 200) {
    addLog("buy", `手动购买成功: ${itemId}`);
  } else {
    addLog("stop", `购买失败: ${data?.detail || "未知错误"}`);
  }
  loadMarketDeals();
}

async function manualBid(auctionId) {
  const { status, data } = await api(`/api/auction/${auctionId}/bid`, "POST", {});
  if (status === 200) {
    addLog("bid", `手动出价成功: ${auctionId}`);
  } else {
    addLog("stop", `出价失败: ${data?.detail || "未知错误"}`);
  }
  loadAuctions();
}

async function manualAbort(auctionId) {
  if (!confirm("确定放弃此竞拍？")) return;
  await api(`/api/auction/${auctionId}/abort`, "POST", {});
  addLog("stop", `手动放弃竞拍: ${auctionId}`);
  loadAuctions();
}

// ────────────────────────── 日志 ──────────────────────────

function addLog(type, message) {
  const now = new Date().toLocaleTimeString("zh-CN");
  logs.unshift({ time: now, type, message });
  if (logs.length > 50) logs.length = 50;
  renderLogs();
}

function renderLogs() {
  const container = document.getElementById("logContainer");
  container.innerHTML = logs.map((l) => `
    <div class="log-entry">
      <span class="log-time">${l.time}</span>
      <span class="log-${l.type}">${esc(l.message)}</span>
    </div>
  `).join("");
}

// ────────────────────────── 辅助 ──────────────────────────

function timeAgo(iso) {
  if (!iso) return "-";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s 前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m 前`;
  return `${Math.floor(diff / 3600)}h 前`;
}

function getRemaining(iso) {
  if (!iso) return 0;
  return Math.max(0, Math.floor((new Date(iso).getTime() - Date.now()) / 1000));
}

function statusLabel(s) {
  const map = { detected: "待购买", bought: "已购买", missed: "已错过", expired: "已过期", bidding: "竞价中", waiting: "观望", aborted: "已止损", won: "已中标", monitoring: "监控中" };
  return map[s] || s;
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n) + "..." : s;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// 暴露到全局作用域
window.manualBuy = manualBuy;
window.manualBid = manualBid;
window.manualAbort = manualAbort;
