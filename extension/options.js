// options.js
// 配置页面交互逻辑

const API = "http://127.0.0.1:8080";

// ────────────────────────── 初始化 ──────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Tab 切换
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  // 账号管理
  document.getElementById("addAccountForm").addEventListener("submit", addAccount);
  loadAccountsTable();

  // 策略设置
  loadStrategy();
  document.getElementById("strategyForm").addEventListener("submit", saveStrategy);

  // 数据管理
  document.getElementById("btnExport").addEventListener("click", exportData);
  document.getElementById("btnImport").addEventListener("click", () => document.getElementById("importFile").click());
  document.getElementById("importFile").addEventListener("change", importData);
  document.getElementById("btnClear").addEventListener("click", clearData);
});

// ────────────────────────── Tab 切换 ──────────────────────────

function switchTab(tabId) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tabId));
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.toggle("active", c.id === `tab-${tabId}`));
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

// ────────────────────────── 账号管理 ──────────────────────────

async function loadAccountsTable() {
  const { data } = await api("/api/accounts");
  const tbody = document.querySelector("#accountsTable tbody");
  tbody.innerHTML = "";

  if (!data?.accounts?.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="color:#666">暂无账号</td></tr>';
    return;
  }

  data.accounts.forEach((acc) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(acc.name)}</td>
      <td>${acc.empire_rate}</td>
      <td>${acc.created_at || "-"}</td>
      <td><button class="delete-btn" data-name="${esc(acc.name)}">🗑️ 删除</button></td>
    `;
    tr.querySelector(".delete-btn").addEventListener("click", () => deleteAccount(acc.name));
    tbody.appendChild(tr);
  });
}

async function addAccount(e) {
  e.preventDefault();
  const name = document.getElementById("accName").value.trim();
  const api_key = document.getElementById("accApiKey").value.trim();
  const empire_rate = parseFloat(document.getElementById("accRate").value);

  if (!name || !api_key) return alert("请填写完整信息");

  const { status, data } = await api("/api/accounts", "POST", { name, api_key, empire_rate });
  if (status === 200) {
    alert("账号添加成功");
    document.getElementById("addAccountForm").reset();
    document.getElementById("accRate").value = "0.65";
    loadAccountsTable();
  } else {
    alert("添加失败: " + (data?.detail || "未知错误"));
  }
}

async function deleteAccount(name) {
  if (!confirm(`确定删除账号 "${name}"？`)) return;
  await api(`/api/accounts/${encodeURIComponent(name)}`, "DELETE", {});
  loadAccountsTable();
}

// ────────────────────────── 策略设置 ──────────────────────────

async function loadStrategy() {
  const { data } = await api("/api/strategy");
  if (!data) return;

  document.getElementById("strBuffRate").value = data.buff_rate ?? 0.138;
  document.getElementById("strMinDeal").value = data.min_deal_pct ?? 15;
  document.getElementById("strMaxLoss").value = data.max_loss_pct ?? -5;
  document.getElementById("strMinPrice").value = data.min_item_price ?? 5;
  document.getElementById("strMaxPrice").value = data.max_item_price ?? 2000;
  document.getElementById("strMaxBid").value = data.max_bid_usd ?? 500;
  document.getElementById("strBidDelay").value = data.bid_delay_ms ?? 500;
  document.getElementById("strAutoBuy").checked = data.auto_bid ?? false;
  document.getElementById("strAutoBid").checked = data.auto_buy ?? false;

  // 白名单/黑名单
  try {
    const wl = JSON.parse(data.whitelist || "[]");
    document.getElementById("strWhitelist").value = wl.join("\n");
  } catch { document.getElementById("strWhitelist").value = ""; }

  try {
    const bl = JSON.parse(data.blacklist || "[]");
    document.getElementById("strBlacklist").value = bl.join("\n");
  } catch { document.getElementById("strBlacklist").value = ""; }

  // 磨损
  try {
    const wears = JSON.parse(data.wear_filter || "[]");
    document.querySelectorAll(".wear-cb").forEach((cb) => {
      cb.checked = wears.includes(cb.value);
    });
  } catch {}
}

async function saveStrategy(e) {
  e.preventDefault();

  const whitelist = document.getElementById("strWhitelist").value
    .split("\n").map((s) => s.trim()).filter(Boolean);
  const blacklist = document.getElementById("strBlacklist").value
    .split("\n").map((s) => s.trim()).filter(Boolean);
  const wearFilter = [];
  document.querySelectorAll(".wear-cb:checked").forEach((cb) => wearFilter.push(cb.value));

  const body = {
    account_name: null,
    buff_rate: parseFloat(document.getElementById("strBuffRate").value),
    min_deal_pct: parseFloat(document.getElementById("strMinDeal").value),
    max_loss_pct: parseFloat(document.getElementById("strMaxLoss").value),
    auto_bid: document.getElementById("strAutoBuy").checked,
    auto_buy: document.getElementById("strAutoBid").checked,
    max_bid_usd: parseFloat(document.getElementById("strMaxBid").value),
    max_buy_usd: parseFloat(document.getElementById("strMaxBid").value),
    min_item_price: parseFloat(document.getElementById("strMinPrice").value),
    max_item_price: parseFloat(document.getElementById("strMaxPrice").value),
    whitelist: JSON.stringify(whitelist),
    blacklist: JSON.stringify(blacklist),
    wear_filter: JSON.stringify(wearFilter),
    bid_delay_ms: parseInt(document.getElementById("strBidDelay").value),
  };

  const { status, data } = await api("/api/strategy", "POST", body);
  if (status === 200) {
    alert("策略保存成功！");
  } else {
    alert("保存失败: " + (data?.detail || "未知错误"));
  }
}

// ────────────────────────── 数据管理 ──────────────────────────

async function exportData() {
  const { status, data } = await api("/api/export", "POST", {});
  if (status !== 200 || !data.data) return alert("导出失败");

  const blob = new Blob([JSON.stringify(data.data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `csgoempire-bot-export-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

async function importData(e) {
  const file = e.target.files[0];
  if (!file) return;

  try {
    const text = await file.text();
    const json = JSON.parse(text);
    const { status, data } = await api("/api/import", "POST", { data: json });
    showImportStatus(status === 200, data?.detail || "导入完成");
  } catch (err) {
    showImportStatus(false, "文件解析失败: " + err.message);
  }
  e.target.value = "";
}

async function clearData() {
  if (!confirm("确定清除所有数据？此操作不可撤销！")) return;
  // 删除所有账号
  const { data } = await api("/api/accounts");
  if (data?.accounts) {
    for (const acc of data.accounts) {
      await api(`/api/accounts/${encodeURIComponent(acc.name)}`, "DELETE", {});
    }
  }
  showImportStatus(true, "所有数据已清除");
  loadAccountsTable();
}

function showImportStatus(success, msg) {
  const el = document.getElementById("importStatus");
  el.classList.remove("hidden", "success", "error");
  el.classList.add(success ? "success" : "error");
  el.textContent = msg;
  setTimeout(() => el.classList.add("hidden"), 5000);
}

// ────────────────────────── 辅助 ──────────────────────────

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}
