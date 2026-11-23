// Background Service Worker điều phối hàng đợi sản phẩm Bán chạy, xoay vòng từ khóa & cảnh báo CAPTCHA

const LOCAL_SERVER = "http://127.0.0.1:8765";

const DEFAULT_STATE = {
  running: false,
  targetStars: [1, 2, 3, 4, 5],
  crawlMode: "keyword_sales",
  minSoldCount: 5000,
  maxProducts: 30,
  batchPauseEveryN: 10,
  keywordConfigs: [],
  keywordIndex: 0,
  crawledForCurrentKw: 0,
  searchPageOffset: 0,
  crawledInRun: 0,
  productsSinceCaptcha: 0,
  requestsSinceCaptcha: 0,
  activeTabId: null,
  visitedUrls: [],
  pendingUrls: [],
  lastStatus: "Sẵn sàng"
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set(DEFAULT_STATE);
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "START_CRAWL") {
    chrome.action.setBadgeText({ text: "" });
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0];
      if (!tab || !tab.id) {
        sendResponse({ ok: false, error: "Không tìm thấy tab đang mở" });
        return;
      }
      const nextState = {
        running: true,
        targetStars: msg.targetStars || [1, 2, 3, 4, 5],
        crawlMode: msg.crawlMode || "keyword_sales",
        minSoldCount: Number(msg.minSoldCount ?? 500),
        maxProducts: Number(msg.maxProducts || 30),
        batchPauseEveryN: Number(msg.batchPauseEveryN || 10),
        keywordConfigs: msg.keywordConfigs || [],
        keywordIndex: 0,
        crawledForCurrentKw: 0,
        searchPageOffset: 0,
        crawledInRun: 0,
        productsSinceCaptcha: 0,
        requestsSinceCaptcha: 0,
        activeTabId: tab.id,
        visitedUrls: [],
        pendingUrls: msg.pendingUrls || [],
        lastStatus: "Đang bắt đầu thu thập..."
      };
      chrome.storage.local.set(nextState, () => {
        chrome.tabs.sendMessage(tab.id, { type: "RUN_PRODUCT_CRAWL" }).catch(() => {
          chrome.scripting.executeScript(
            { target: { tabId: tab.id }, files: ["content.js"] },
            () => {
              setTimeout(() => {
                chrome.tabs.sendMessage(tab.id, { type: "RUN_PRODUCT_CRAWL" }).catch(() => { });
              }, 300);
            }
          );
        });
        sendResponse({ ok: true });
      });
    });
    return true;
  }

  if (msg.type === "STOP_CRAWL") {
    chrome.action.setBadgeText({ text: "" });
    chrome.storage.local.set({ running: false, lastStatus: "⏹ Đã dừng bởi người dùng." }, () => {
      sendResponse({ ok: true });
    });
    return true;
  }

  if (msg.type === "NAVIGATE_NEXT") {
    chrome.storage.local.get(["running", "activeTabId"], (state) => {
      if (!state.running || !msg.url) return;
      const tabId = (sender.tab && sender.tab.id) || state.activeTabId;
      if (tabId) {
        chrome.tabs.update(tabId, { url: msg.url });
      }
    });
    return false;
  }

  if (msg.type === "CAPTCHA_DETECTED") {
    const productsCount = Number(msg.productsCount || 0);
    const requestsCount = Number(msg.requestsCount || 0);
    const reason = msg.reason || "Phát hiện CAPTCHA / Yêu cầu xác minh";
    const url = msg.url || "";

    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#dc2626" });

    if (sender.tab && sender.tab.id) {
      chrome.tabs.update(sender.tab.id, { active: true }).catch(() => { });
      if (sender.tab.windowId) {
        chrome.windows.update(sender.tab.windowId, { focused: true }).catch(() => { });
      }
    }

    const iconDataUrl =
      "data:image/svg+xml;base64," +
      btoa('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#dc2626"/><text x="32" y="46" font-size="42" text-anchor="middle" fill="#fff" font-family="Arial">!</text></svg>');
    chrome.notifications.create(`captcha_${Date.now()}`, {
      type: "basic",
      iconUrl: iconDataUrl,
      title: "⚠️ CẢNH BÁO CAPTCHA - ABSA Shopee Collector",
      message: `${reason}\nĐã crawl ${productsCount} SP (${requestsCount} lượt tải) trước khi gặp CAPTCHA.`,
      priority: 2,
      requireInteraction: true
    });

    fetch(`${LOCAL_SERVER}/api/report-captcha`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        platform: "shopee",
        url,
        reason,
        products_before_captcha: productsCount,
        requests_before_captcha: requestsCount
      })
    }).catch(() => { });

    return false;
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete") return;
  chrome.storage.local.get(["running", "activeTabId"], (state) => {
    if (!state.running || tabId !== state.activeTabId) return;
    if (tab.url && tab.url.includes("shopee.vn")) {
      setTimeout(() => {
        chrome.tabs.sendMessage(tabId, { type: "RUN_PRODUCT_CRAWL" }).catch(() => { });
      }, 1500);
    }
  });
});
