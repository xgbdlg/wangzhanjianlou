// background.js
// Chrome Extension Service Worker
// 职责：WebSocket 连接维护、桌面通知、badge 更新、消息路由

const API_BASE = "http://127.0.0.1:8080";
const WS_URL = "ws://127.0.0.1:8081";

let ws = null;
let reconnectTimer = null;
let reconnectAttempts = 0;
const MAX_RECONNECT = 30;

// ────────────────────────── WebSocket 连接管理 ──────────────────────────

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log("[BG] WebSocket 已连接");
    reconnectAttempts = 0;
    updateBadge("✓", "#4CAF50");
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handlePushMessage(msg);
    } catch (e) {
      console.warn("[BG] WS 消息解析失败:", e);
    }
  };

  ws.onclose = () => {
    console.log("[BG] WebSocket 断开");
    updateBadge("✗", "#F44336");
    ws = null;
    scheduleReconnect();
  };

  ws.onerror = (err) => {
    console.warn("[BG] WebSocket 错误:", err);
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectAttempts++;
  if (reconnectAttempts > MAX_RECONNECT) {
    console.log("[BG] 重连次数过多，停止重连");
    return;
  }
  const delay = Math.min(5000 * reconnectAttempts, 30000);
  console.log(`[BG] ${delay / 1000}s 后重连 (${reconnectAttempts}/${MAX_RECONNECT})`);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS();
  }, delay);
}

// ────────────────────────── 消息处理 ──────────────────────────

function handlePushMessage(msg) {
  const { event, data } = msg;

  switch (event) {
    case "deal_alert":
      handleDealAlert(data);
      break;
    case "stop_loss_triggered":
      handleStopLoss(data);
      break;
    case "auction_update":
      handleAuctionUpdate(data);
      break;
    case "engine_status":
      handleEngineStatus(data);
      break;
    default:
      console.log("[BG] 未知事件:", event, data);
  }
}

function handleDealAlert(data) {
  const discount = data.discount_pct?.toFixed(1) || "?";
  chrome.notifications.create(`deal_${data.item_id || Date.now()}`, {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: `💰 捡漏! ${discount}% 折扣`,
    message: `${data.name || "未知物品"} | Empire: $${data.empire_price?.toFixed(2) || "?"} | Buff: $${data.buff_price?.toFixed(2) || "?"}`,
    priority: 2,
  });

  // 可选声音提示
  chrome.storage.local.get(["sound_enabled"], (result) => {
    if (result.sound_enabled) {
      playBeep();
    }
  });
}

function handleStopLoss(data) {
  chrome.notifications.create(`stop_${Date.now()}`, {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: "🛑 止损触发",
    message: data.message || "拍卖折扣跌破止损线",
    priority: 2,
  });
}

function handleAuctionUpdate(data) {
  const count = data.active_count || 0;
  if (count > 0) {
    updateBadge(String(count), "#FF9800");
  }
}

function handleEngineStatus(data) {
  console.log("[BG] 引擎状态:", data);
}

// ────────────────────────── Badge 更新 ──────────────────────────

function updateBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

// ────────────────────────── 声音 ──────────────────────────

function playBeep() {
  // 使用 Web Audio API 生成简单提示音
  try {
    const ctx = new OfflineAudioContext(1, 44100 * 0.15, 44100);
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, 0);
    osc.frequency.setValueAtTime(1320, 0.08);
    gain.gain.setValueAtTime(0.3, 0);
    gain.gain.exponentialRampToValueAtTime(0.01, 0.15);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(0);
    osc.stop(0.15);
    ctx.startRendering();
  } catch (e) {
    // 静默失败
  }
}

// ────────────────────────── 消息路由（供 popup/monitor 调用） ──────────────────────────

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  const { action, data } = request;

  switch (action) {
    case "get_status":
      sendResponse({
        wsConnected: ws && ws.readyState === WebSocket.OPEN,
        wsUrl: WS_URL,
        reconnectAttempts,
      });
      break;

    case "fetch_backend":
      handleFetch(request, sendResponse);
      return true; // 异步响应

    case "reconnect_ws":
      if (ws) { ws.close(); ws = null; }
      connectWS();
      sendResponse({ ok: true });
      break;

    default:
      sendResponse({ error: "未知操作" });
  }
});

async function handleFetch(request, sendResponse) {
  const { path, method, body } = request;
  try {
    const opts = {
      method: method || "GET",
      headers: { "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);

    const resp = await fetch(API_BASE + path, opts);
    const json = await resp.json();
    sendResponse({ status: resp.status, data: json });
  } catch (err) {
    sendResponse({ status: 0, error: "后端服务未连接" });
  }
}

// ────────────────────────── 启动 ──────────────────────────

connectWS();

// 定期检查后端健康状态
chrome.alarms.create("health_check", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "health_check") {
    try {
      const resp = await fetch(API_BASE + "/api/health");
      if (resp.ok) {
        if (!ws || ws.readyState !== WebSocket.OPEN) connectWS();
      }
    } catch (e) {
      // 后端不可用
    }
  }
});

console.log("[BG] CSGOEmpire 捡漏助手后台已启动");
