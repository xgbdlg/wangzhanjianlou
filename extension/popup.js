// popup.js
// 弹出面板交互逻辑

const API = "http://127.0.0.1:8080";

// ────────────────────────── 初始化 ──────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await checkBackend();
  await loadAccounts();
  await loadBalance();
  await loadEngineStatus();
  await loadStats();
  await restoreMode();

  // 事件绑定
  document.getElementById("accountSelect").addEventListener("change", onAccountChange);
  document.getElementById("btnManual").addEventListener("click", () => setMode("manual"));
  document.getElementById("btnAuto").addEventListener("click", () => setMode("auto"));
  document.getElementById("btnToggleMarket").addEventListener("click", toggleMarket);
  document.getElementById("btnToggleAuction").addEventListener("click", toggleAuction);
  document.getElementById("btnOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());
  document.getElementById("btnMonitor").addEventListener("click", () => {
    chrome.tabs.create({ url: "monitor.html" });
  });
});

// ────────────────────────── API 调用 ──────────────────────────

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

// ────────────────────────── 后端检测 ──────────────────────────

async function checkBackend() {
  const { status } = await api("/api/health");
  const banner = document.getElementById("errorBanner");
  const wsStatus = document.getElementById("wsStatus");
  const wsDot = document.getElementById("wsIndicator");

  if (status === 200) {
    banner.classList.add("hidden");
    wsStatus.textContent = "后端: 🟢 已连接";
    wsDot.textContent = "🟢";
    wsDot.title = "后端已连接";
  } else {
    banner.classList.remove("hidden");
    wsStatus.textContent = "后端: 🔴 未连接";
    wsDot.textContent = "🔴";
    wsDot.title = "后端未连接，请启动服务";
  }
}

// ────────────────────────── 账号管理 ──────────────────────────

async function loadAccounts() {
  const { status, data } = await api("/api/accounts");
  const select = document.getElementById("accountSelect");

  if (status !== 200 || !data.accounts) return;

  select.innerHTML = '<option value="">-- 选择账号 --</option>';
  data.accounts.forEach((acc) => {
    const opt = document.createElement("option");
    opt.value = acc.name;
    opt.textContent = `${acc.name} (汇率 ${acc.empire_rate})`;
    select.appendChild(opt);
  });

  // 恢复上次选择
  const { lastAccount } = await chrome.storage.local.get("lastAccount");
  if (lastAccount) {
    select.value = lastAccount;
    // 尝试切换到该账号
    await api(`/api/accounts/${encodeURIComponent(lastAccount)}/switch`, "POST", {});
  }
}

async function onAccountChange(e) {
  const name = e.target.value;
  if (!name) return;
  await api(`/api/accounts/${encodeURIComponent(name)}/switch`, "POST", {});
  await chrome.storage.local.set({ lastAccount: name });
  await loadBalance();
  await loadStats();
}

// ────────────────────────── 余额 ──────────────────────────

async function loadBalance() {
  const { status, data } = await api("/api/balance");
  const el = document.getElementById("balanceDisplay");
  if (status === 200 && data.balance) {
    const b = data.balance;
    el.innerHTML = `💰 余额: ${b.balance || "?"} Coins | $${(b.balance_usd || 0).toFixed(2)} USD`;
  } else {
    el.textContent = "💰 余额: 未连接 Empire";
  }
}

// ────────────────────────── 引擎状态 ──────────────────────────

async function loadEngineStatus() {
  const [{ data: m }, { data: a }] = await Promise.all([
    api("/api/market/status"),
    api("/api/auction/status"),
  ]);

  updateStatusEl("marketStatus", "市场监控", m?.status === "running");
  updateStatusEl("auctionStatus", "拍卖监控", a?.status === "running");

  // 更新按钮文字
  document.getElementById("btnToggleMarket").textContent =
    m?.status === "running" ? "⏹️ 停止市场监控" : "▶️ 启动市场监控";
  document.getElementById("btnToggleMarket").className =
    m?.status === "running" ? "btn btn-stop" : "btn btn-start";

  document.getElementById("btnToggleAuction").textContent =
    a?.status === "running" ? "⏹️ 停止拍卖监控" : "▶️ 启动拍卖监控";
  document.getElementById("btnToggleAuction").className =
    a?.status === "running" ? "btn btn-stop" : "btn btn-start";
}

function updateStatusEl(id, label, running) {
  const el = document.getElementById(id);
  if (running) {
    el.innerHTML = `<span class="status-dot running">🟢 运行中</span>`;
  } else {
    el.innerHTML = `<span class="status-dot stopped">🔴 已停止</span>`;
  }
}

// ────────────────────────── 统计 ──────────────────────────

async function loadStats() {
  // 市场统计
  const { data: m } = await api("/api/market/deals?limit=1000");
  if (m) {
    const detected = m.deals?.filter((d) => d.status === "detected").length || 0;
    const bought = m.deals?.filter((d) => d.status === "bought").length || 0;
    document.getElementById("marketStats").textContent = `检测 ${detected} | 购买 ${bought}`;
  }

  // 拍卖统计
  const { data: a } = await api("/api/auction/history?limit=1000");
  if (a) {
    const total = a.total || 0;
    const won = a.auctions?.filter((d) => d.won_by_me).length || 0;
    document.getElementById("auctionStats").textContent = `出价 ${total} | 拍得 ${won}`;
  }
}

// ────────────────────────── 模式切换 ──────────────────────────

async function setMode(mode) {
  document.getElementById("btnManual").className =
    `mode-btn ${mode === "manual" ? "active" : ""}`;
  document.getElementById("btnAuto").className =
    `mode-btn ${mode === "auto" ? "active" : ""}`;
  await chrome.storage.local.set({ mode });
}

async function restoreMode() {
  const { mode } = await chrome.storage.local.get("mode");
  await setMode(mode || "manual");
}

// ────────────────────────── 启停控制 ──────────────────────────

async function toggleMarket() {
  const btn = document.getElementById("btnToggleMarket");
  if (btn.textContent.includes("启动")) {
    const { status, data } = await api("/api/market/start", "POST", {});
    if (status !== 200) alert("启动失败: " + (data?.detail || "未知错误"));
  } else {
    await api("/api/market/stop", "POST", {});
  }
  await loadEngineStatus();
  await loadStats();
}

async function toggleAuction() {
  const btn = document.getElementById("btnToggleAuction");
  if (btn.textContent.includes("启动")) {
    const { status, data } = await api("/api/auction/start", "POST", {});
    if (status !== 200) alert("启动失败: " + (data?.detail || "未知错误"));
  } else {
    await api("/api/auction/stop", "POST", {});
  }
  await loadEngineStatus();
}
