// Content script chạy trực tiếp trong ngữ cảnh Tab Chrome cá nhân (không có CDP/WebDriver)
// Hỗ trợ kiểm soát chỉ tiêu Min - Max số sản phẩm hợp lệ cho từng từ khóa (min <= x <= max):
// - Đạt Max của từ khóa hiện tại -> chuyển ngay sang từ khóa tiếp theo.
// - Hết sản phẩm trên trang hiện tại mà chưa đạt Min -> tự động lật trang kết quả tìm kiếm tiếp theo.
// - Đã đạt >= Min mà hết sản phẩm -> chuyển sang từ khóa tiếp theo.

const LOCAL_SERVER = "http://127.0.0.1:8765";
const MIN_WITH_COMMENTS = 30;
const MAX_SEARCH_PAGES_PER_KW = 3; // Tối đa lật đến page=3 (4 trang) để tìm đủ Min cho 1 từ khóa
let isProcessingTab = false;
let stopRequested = false;

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.running && changes.running.newValue === false) {
    stopRequested = true;
  }
});

const FASHION_KEYWORDS = [
  "áo", "váy", "đầm", "quần", "chân váy", "cardigan", "blazer",
  "set", "đồ bộ", "sơ mi", "khoác", "jeans", "jean", "thun", "len", "nữ"
];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomDelay(minMs, maxMs) {
  const ms = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  return sleep(ms);
}

function playAlertBeep() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const notes = [880, 1174, 880, 1318];
    notes.forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.25, ctx.currentTime + idx * 0.22);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + idx * 0.22 + 0.18);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(ctx.currentTime + idx * 0.22);
      osc.stop(ctx.currentTime + idx * 0.22 + 0.19);
    });
  } catch (e) { }
}

function extractShopeeIds(url) {
  const clean = (url || "").split("?")[0];
  let m = clean.match(/-i\.(\d+)\.(\d+)/);
  if (m) return { shopId: m[1], itemId: m[2] };
  m = clean.match(/\/product\/(\d+)\/(\d+)/);
  if (m) return { shopId: m[1], itemId: m[2] };
  return null;
}

function detectSecurityIssue() {
  const u = (window.location.href || "").toLowerCase();
  if (u.includes("/verify/captcha")) return "Trang xác minh CAPTCHA (/verify/captcha)";
  if (u.includes("/verify/traffic")) return "Trang kiểm tra lưu lượng (/verify/traffic)";
  if (u.includes("/buyer/login")) return "Yêu cầu đăng nhập lại Shopee (/buyer/login)";

  const captchaSelectors = [
    "#secsdk-captcha-drag-wrapper",
    ".captcha_verify_container",
    "iframe[src*='captcha']"
  ];
  for (const sel of captchaSelectors) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity || "1") < 0.1) {
      continue;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width >= 180 && rect.height >= 120) {
      return `Popup CAPTCHA hiển thị trên màn hình (${sel})`;
    }
  }
  return null;
}

async function updateStatus(statusText, extra = {}) {
  return new Promise((resolve) => {
    chrome.storage.local.set({ lastStatus: statusText, ...extra }, resolve);
  });
}

async function triggerCaptchaAlert(reason) {
  playAlertBeep();
  const st = await chrome.storage.local.get(["productsSinceCaptcha", "requestsSinceCaptcha"]);
  const productsCount = Number(st.productsSinceCaptcha || 0);
  const requestsCount = Number(st.requestsSinceCaptcha || 0);

  await updateStatus(
    `⚠️ ${reason} (Sau ${productsCount} SP / ${requestsCount} lượt tải). Đã tạm dừng!`,
    { running: false }
  );

  chrome.runtime.sendMessage({
    type: "CAPTCHA_DETECTED",
    reason,
    url: window.location.href,
    productsCount,
    requestsCount
  });
}

async function incrementActionCounter() {
  const st = await chrome.storage.local.get(["requestsSinceCaptcha"]);
  const nextReq = Number(st.requestsSinceCaptcha || 0) + 1;
  await new Promise((resolve) => chrome.storage.local.set({ requestsSinceCaptcha: nextReq }, resolve));
  return nextReq;
}

function extractDomMetadata() {
  let productName = "";
  const h1 = document.querySelector("h1");
  if (h1 && h1.innerText.trim()) {
    productName = h1.innerText.trim();
  } else {
    productName = (document.title || "").split("|")[0].trim();
  }

  let shopName = "";
  const shopBlacklist = [
    "chat now", "chat ngay", "shop vouchers", "xem shop", "view shop",
    "online", "tỉ lệ phản hồi", "tỷ lệ phản hồi", "response rate"
  ];
  const shopEl =
    document.querySelector("div.fV3TIn") ||
    document.querySelector("section.page-product__shop") ||
    document.querySelector("div.page-product__shop");
  if (shopEl && shopEl.innerText) {
    const lines = shopEl.innerText.split("\n").map((s) => s.trim()).filter(Boolean);
    for (const ln of lines) {
      if (!shopBlacklist.includes(ln.toLowerCase())) {
        shopName = ln;
        break;
      }
    }
  }

  let productDesc = "";
  const descEl =
    document.querySelector("div.f7AU53") ||
    document.querySelector("section.I_DV_3") ||
    document.querySelector("div.product-detail");
  if (descEl && descEl.innerText) {
    productDesc = descEl.innerText.trim();
  }

  return { productName, shopName, productDesc };
}

function parseSoldCountFromCard(cardText) {
  if (!cardText) return null;
  const m = cardText.match(
    /(?:(?:đã\s*)?bán\s*([\d.,]+)\s*(k|tr|m)?\+?|\b([\d.,]+)\s*(k|tr|m)?\+?\s*(?:đã\s*bán|sold)\b)/i
  );
  if (!m) return null;
  const raw = (m[1] || m[3] || "").trim();
  const unit = (m[2] || m[4] || "").toLowerCase();
  if (!raw) return null;

  let normalized = raw;
  if (unit) {
    // Co don vi (1,2k / 1.5tr): coi dau phay hoac cham la thap phan
    normalized = raw.replace(",", ".");
  } else if (/^\d{1,3}([.,]\d{3})+$/.test(raw)) {
    // Khong co don vi va theo dang 1.200 / 1,200 -> tach hang nghin
    normalized = raw.replace(/[.,]/g, "");
  } else {
    normalized = raw.replace(",", ".");
  }

  let val = parseFloat(normalized);
  if (isNaN(val)) return null;
  if (unit === "k") val *= 1000;
  else if (unit === "tr" || unit === "m") val *= 1000000;
  return Math.round(val);
}

function extractSoldCountFromDetailPage() {
  const scope =
    document.querySelector("main") ||
    document.querySelector('[role="main"]') ||
    document.body;
  const topText = (scope && scope.innerText ? scope.innerText : "").slice(0, 2500);
  return parseSoldCountFromCard(topText);
}

function collectProductCandidatesFromDom(excludeProductId = "", visitedUrls = [], minSoldCount = 5000) {
  const visitedSet = new Set(visitedUrls.map((u) => u.split("?")[0]));
  const anchors = Array.from(document.querySelectorAll("a[href*='-i.'], a[href*='/product/']"));
  const candidates = [];
  const seenPids = new Set();

  for (const a of anchors) {
    const href = a.getAttribute("href");
    if (!href) continue;
    const fullUrl = new URL(href, "https://shopee.vn").href.split("?")[0];
    const ids = extractShopeeIds(fullUrl);
    if (!ids) continue;

    const pid = `shopee_${ids.shopId}_${ids.itemId}`;
    if (pid === excludeProductId || seenPids.has(pid) || visitedSet.has(fullUrl)) continue;

    const cardText = a.innerText || "";
    const containerText =
      (a.closest("[data-sqe='item']") && a.closest("[data-sqe='item']").innerText) ||
      (a.parentElement && a.parentElement.innerText) ||
      "";
    const soldCount =
      parseSoldCountFromCard(cardText) ?? parseSoldCountFromCard(containerText);

    // Fail-closed: khi bat loc minSoldCount > 0, neu khong doc duoc luot ban hoac < nguong thi bo qua
    if (minSoldCount > 0 && (soldCount === null || soldCount < minSoldCount)) {
      continue;
    }

    const title = cardText.toLowerCase();
    const combined = `${title} ${fullUrl.toLowerCase()}`;
    const isFashion = FASHION_KEYWORDS.some((kw) => combined.includes(kw));

    seenPids.add(pid);
    candidates.push({
      product_id: pid,
      url: fullUrl,
      isFashion,
      soldCount: soldCount !== null ? soldCount : 0
    });
  }

  candidates.sort((a, b) => {
    const fashionDiff = (b.isFashion ? 1 : 0) - (a.isFashion ? 1 : 0);
    if (fashionDiff !== 0) return fashionDiff;
    return b.soldCount - a.soldCount;
  });
  return candidates;
}

async function filterUnvisitedViaServer(candidates) {
  if (!candidates || candidates.length === 0) return [];
  try {
    const resp = await fetch(`${LOCAL_SERVER}/api/filter-urls`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: candidates.map((c) => ({ product_id: c.product_id, url: c.url }))
      })
    });
    if (resp.ok) {
      const data = await resp.json();
      return (data.items || []).map((i) => i.url);
    }
  } catch (e) { }
  return candidates.map((c) => c.url);
}

async function waitForSelector(selectors, timeoutMs = 6000, intervalMs = 300) {
  const list = Array.isArray(selectors) ? selectors : [selectors];
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (stopRequested) return null;
    for (const sel of list) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    await sleep(intervalMs);
  }
  return null;
}

async function scrollToRatingsSection() {
  for (let step = 0; step < 16; step++) {
    if (stopRequested) return false;
    const sec =
      document.querySelector("div.product-rating-overview") ||
      document.querySelector("div.product-ratings");
    if (sec) {
      sec.scrollIntoView({ behavior: "smooth", block: "center" });
      await waitForSelector(
        [
          "div.product-rating-overview__filter",
          "div[data-cmtid]",
          "div.shopee-product-rating"
        ],
        5000,
        300
      );
      await sleep(600);
      return true;
    }
    window.scrollBy({ top: 850, behavior: "smooth" });
    await sleep(700);
  }
  return false;
}

function extractVisibleReviewsFromDom(fallbackStar) {
  const items = document.querySelectorAll("div[data-cmtid], div.shopee-product-rating");
  const list = [];

  for (const item of items) {
    const cmtId = (item.getAttribute("data-cmtid") || "").trim();
    if (!cmtId) continue;

    const solidStars = item.querySelectorAll("svg.icon-rating-solid, svg[class*='icon-rating-solid']").length;
    const ratingStar = solidStars >= 1 && solidStars <= 5 ? solidStars : fallbackStar;

    const metaEl =
      item.querySelector("div.XYk98l") ||
      item.querySelector("div.j5ucs4") ||
      item.querySelector("div.shopee-product-rating__time");
    const metaText = metaEl ? (metaEl.innerText || "").trim() : "";

    let tsPart = metaText;
    let variation = "";
    if (metaText.includes("|")) {
      const parts = metaText.split("|");
      tsPart = parts[0].trim();
      variation = parts.slice(1).join("|").replace(/^(Phân loại hàng|Variation)\s*:\s*/i, "").trim();
    }

    const bodyEl = item.querySelector("div.YNedDV") || item.querySelector("div.meQyXP");
    const reviewText = bodyEl ? (bodyEl.innerText || "").trim() : "";
    if (!reviewText) continue;

    const likeEl = item.querySelector("div.shopee-product-rating__like-count");
    const likeText = likeEl ? (likeEl.innerText || "").replace(/[^\d]/g, "") : "0";
    const likeCount = likeText ? Number(likeText) : 0;

    list.push({
      cmt_id: cmtId,
      text: reviewText,
      rating_star: ratingStar,
      variation,
      ctime: tsPart,
      like_count: likeCount
    });
  }
  return list;
}

function getWithCommentsCount() {
  const filters = Array.from(document.querySelectorAll("div.product-rating-overview__filter"));
  for (const el of filters) {
    const label = (el.innerText || "").trim();
    if (/^(with\s*comments|có\s*bình\s*luận)/i.test(label)) {
      const m = label.match(/\(([\d.,]+)\s*(k|tr)?\)/i);
      if (!m) return null;
      let val = parseFloat(m[1].replace(",", "."));
      if (isNaN(val)) return null;
      const unit = (m[2] || "").toLowerCase();
      if (unit === "k") val *= 1000;
      else if (unit === "tr") val *= 1000000;
      return Math.round(val);
    }
  }
  return null;
}

async function clickStarFilterTab(star) {
  const filters = Array.from(document.querySelectorAll("div.product-rating-overview__filter"));
  const regex = new RegExp(`^${star}\\s*(sao|star)`, "i");
  for (const el of filters) {
    const label = (el.innerText || "").trim();
    if (regex.test(label)) {
      if (label.replace(/\s+/g, "").includes("(0)")) {
        return false;
      }
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      await sleep(300);
      el.click();
      await incrementActionCounter();
      return true;
    }
  }
  return false;
}

async function clickNextReviewPage() {
  const btn =
    document.querySelector("div.product-ratings button.shopee-icon-button--right") ||
    document.querySelector("nav.shopee-page-controller button.shopee-icon-button--right") ||
    document.querySelector("button.shopee-icon-button--right");
  if (!btn) return false;
  if (btn.disabled || (btn.className || "").toLowerCase().includes("disabled")) {
    return false;
  }
  btn.scrollIntoView({ behavior: "smooth", block: "center" });
  await sleep(300);
  btn.click();
  await incrementActionCounter();
  return true;
}

async function collectReviewsByUiForStar(star, maxForStar, seenIds) {
  const starComments = [];
  const clickedTab = await clickStarFilterTab(star);
  if (!clickedTab) {
    return starComments;
  }

  await randomDelay(2000, 3200);
  const secAfterTab = detectSecurityIssue();
  if (secAfterTab) {
    throw new Error(`CAPTCHA_DETECTED::${secAfterTab}`);
  }

  const maxPages = 6;
  for (let p = 0; p < maxPages; p++) {
    if (stopRequested) break;

    const visible = extractVisibleReviewsFromDom(star);
    let addedOnPage = 0;
    for (const c of visible) {
      if (c.rating_star !== star) continue;
      if (seenIds.has(c.cmt_id)) continue;
      seenIds.add(c.cmt_id);
      starComments.push(c);
      addedOnPage++;
      if (starComments.length >= maxForStar) break;
    }

    if (starComments.length >= maxForStar || addedOnPage === 0) {
      break;
    }

    const moved = await clickNextReviewPage();
    if (!moved) break;

    await randomDelay(2000, 3200);
    const secAfterPage = detectSecurityIssue();
    if (secAfterPage) {
      throw new Error(`CAPTCHA_DETECTED::${secAfterPage}`);
    }
  }

  return starComments;
}

// Chuyển sang trang tìm kiếm của từ khóa (hoặc sang từ khóa tiếp theo trong danh sách)
async function advanceToNextKeywordOrSearchPage(state, reasonPrefix = "") {
  const kwConfigs = state.keywordConfigs || [];
  let kwIdx = Number(state.keywordIndex || 0);
  const crawledForKw = Number(state.crawledForCurrentKw || 0);
  const pageOffset = Number(state.searchPageOffset || 0);
  const curKw = kwConfigs[kwIdx] || null;

  // Nếu vẫn ở trong từ khóa hiện tại và chưa đạt Min -> thử lật trang tìm kiếm tiếp theo (&page=1, 2, 3)
  if (curKw && crawledForKw < curKw.min && pageOffset < MAX_SEARCH_PAGES_PER_KW) {
    const nextPage = pageOffset + 1;
    const searchUrl = `https://shopee.vn/search?keyword=${encodeURIComponent(curKw.keyword)}&sortBy=sales&page=${nextPage}`;
    await updateStatus(
      `${reasonPrefix}Chưa đạt Min (${crawledForKw}/${curKw.min}). Đang mở trang ${nextPage + 1} của "${curKw.keyword}"...`,
      { searchPageOffset: nextPage, pendingUrls: [] }
    );
    await randomDelay(2000, 3200);
    chrome.runtime.sendMessage({ type: "NAVIGATE_NEXT", url: searchUrl });
    return true;
  }

  // Ngược lại (đã đạt >= Min hoặc đã đạt Max hoặc đã hết số trang thử) -> chuyển sang từ khóa kế tiếp
  const nextIdx = kwIdx + 1;
  if (nextIdx < kwConfigs.length) {
    const nextKw = kwConfigs[nextIdx];
    const searchUrl = `https://shopee.vn/search?keyword=${encodeURIComponent(nextKw.keyword)}&sortBy=sales`;
    await updateStatus(
      `${reasonPrefix}Chuyển sang từ khóa ${nextIdx + 1}/${kwConfigs.length}: "${nextKw.keyword}" (Min:${nextKw.min}, Max:${nextKw.max})...`,
      {
        keywordIndex: nextIdx,
        crawledForCurrentKw: 0,
        searchPageOffset: 0,
        pendingUrls: []
      }
    );
    await randomDelay(2200, 3500);
    chrome.runtime.sendMessage({ type: "NAVIGATE_NEXT", url: searchUrl });
    return true;
  }

  await updateStatus(`🎉 Đã hoàn thành toàn bộ ${kwConfigs.length} từ khóa trong danh sách!`, { running: false });
  return false;
}

async function runProductCrawl() {
  if (isProcessingTab) return;
  isProcessingTab = true;
  stopRequested = false;

  try {
    const state = await chrome.storage.local.get([
      "running",
      "targetStars",
      "crawlMode",
      "minSoldCount",
      "maxProducts",
      "batchPauseEveryN",
      "keywordConfigs",
      "keywordIndex",
      "crawledForCurrentKw",
      "searchPageOffset",
      "crawledInRun",
      "productsSinceCaptcha",
      "visitedUrls",
      "pendingUrls"
    ]);

    if (!state.running) return;

    const secIssue = detectSecurityIssue();
    if (secIssue) {
      await triggerCaptchaAlert(secIssue);
      return;
    }

    if ((state.crawledInRun || 0) >= (state.maxProducts || 30)) {
      await updateStatus(`✅ Đã hoàn thành mục tiêu ${state.maxProducts} sản phẩm!`, { running: false });
      return;
    }

    const minSoldCount = Number(state.minSoldCount ?? 5000);
    const crawlMode = state.crawlMode || "keyword_sales";
    const kwConfigs = state.keywordConfigs || [];
    const kwIdx = Number(state.keywordIndex || 0);
    const currentKwObj = kwConfigs[kwIdx] || null;
    const currentUrl = window.location.href.split("?")[0];
    const ids = extractShopeeIds(currentUrl);

    // 1. Đang ở trang Tìm kiếm / Danh mục Bán chạy
    if (!ids) {
      await updateStatus(`🔍 Đang chờ tải & quét trang Bán chạy (lọc >= ${minSoldCount} lượt bán)...`);
      await waitForSelector(["a[href*='-i.']", "a[href*='/product/']"], 6000, 300);
      for (let i = 0; i < 4; i++) {
        if (stopRequested) return;
        window.scrollBy({ top: 900, behavior: "smooth" });
        await sleep(900);
      }
      await sleep(600);

      const rawCandidates = collectProductCandidatesFromDom("", state.visitedUrls || [], minSoldCount);
      const filteredLinks = await filterUnvisitedViaServer(rawCandidates);

      if (filteredLinks.length === 0) {
        if (crawlMode === "keyword_sales" && kwConfigs.length > 0) {
          // Nếu đang đứng ở trang danh mục ban đầu (chưa vào search của từ khóa 0) -> mở từ khóa 0
          if (!window.location.href.includes("/search?") && kwIdx === 0 && Number(state.searchPageOffset || 0) === 0) {
            const firstKw = kwConfigs[0];
            const searchUrl = `https://shopee.vn/search?keyword=${encodeURIComponent(firstKw.keyword)}&sortBy=sales`;
            await updateStatus(`🔎 Đang mở tìm kiếm từ khóa 1/${kwConfigs.length}: "${firstKw.keyword}"...`);
            await randomDelay(1500, 2500);
            chrome.runtime.sendMessage({ type: "NAVIGATE_NEXT", url: searchUrl });
            return;
          }
          await advanceToNextKeywordOrSearchPage(state, "Trang hiện tại không có SP mới. ");
          return;
        }
        await updateStatus(`⚠️ Không tìm thấy sản phẩm mới (>= ${minSoldCount} lượt bán) trên trang này.`, {
          running: false
        });
        return;
      }

      const nextUrl = filteredLinks[0];
      const remaining = filteredLinks.slice(1);
      await updateStatus(`📋 Đã gom ${filteredLinks.length} SP bán chạy. Đang mở SP đầu tiên...`, {
        pendingUrls: remaining
      });
      chrome.runtime.sendMessage({ type: "NAVIGATE_NEXT", url: nextUrl });
      return;
    }

    // 2. Đang ở trang Chi tiết Sản phẩm
    const productId = `shopee_${ids.shopId}_${ids.itemId}`;
    const visitedUrls = Array.from(new Set([...(state.visitedUrls || []), currentUrl]));
    await updateStatus(`Đang kiểm tra Quota cho ${productId}...`, { visitedUrls });

    const starsParam = (state.targetStars || [1, 2, 3, 4, 5]).join(",");
    let quotaInfo;
    try {
      const qResp = await fetch(
        `${LOCAL_SERVER}/api/needed-stars?product_id=${encodeURIComponent(productId)}&shop_id=${encodeURIComponent(
          ids.shopId
        )}&stars=${encodeURIComponent(starsParam)}`
      );
      quotaInfo = await qResp.json();
    } catch (err) {
      await updateStatus("❌ Không kết nối được Local Server (127.0.0.1:8765). Hãy chạy: python server.py", {
        running: false
      });
      return;
    }

    let shouldSkip = quotaInfo.already_crawled || quotaInfo.shop_limit_reached;
    const neededStars = quotaInfo.needed_stars || [];
    let newCrawledForKw = Number(state.crawledForCurrentKw || 0);

    if (!shouldSkip && neededStars.length > 0) {
      await waitForSelector(["h1"], 6000, 300);
      await sleep(500);
      const detailSoldCount = extractSoldCountFromDetailPage();
      if (minSoldCount > 0 && detailSoldCount !== null && detailSoldCount < minSoldCount) {
        shouldSkip = true;
        await updateStatus(
          `⏭️ Bỏ qua SP (${detailSoldCount} < ${minSoldCount} lượt bán). Chuyển SP kế tiếp...`
        );
        fetch(`${LOCAL_SERVER}/api/mark-skipped`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            product_id: productId,
            url: currentUrl,
            platform: "shopee",
            reason: "skipped_low_sold_count"
          })
        }).catch(() => { });
      }
    }

    if (!shouldSkip && neededStars.length > 0) {
      const meta = extractDomMetadata();
      const foundRatings = await scrollToRatingsSection();

      if (foundRatings && !stopRequested) {
        const withCommentsCount = getWithCommentsCount();
        if (withCommentsCount !== null && withCommentsCount < MIN_WITH_COMMENTS) {
          shouldSkip = true;
          await updateStatus(
            `⏭️ Bỏ qua & lưu cache (${withCommentsCount} < ${MIN_WITH_COMMENTS} cmt). Chuyển SP kế tiếp...`
          );
          fetch(`${LOCAL_SERVER}/api/mark-skipped`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              product_id: productId,
              url: currentUrl,
              platform: "shopee",
              reason: "skipped_low_reviews"
            })
          }).catch(() => { });
        } else {
          const maxPerStar = quotaInfo.max_per_star_per_product || 20;
          const seenIds = new Set();
          const allComments = [];

          for (const star of neededStars) {
            if (stopRequested) break;
            await updateStatus(`Đang lấy bình luận ${star}★ của ${meta.productName.slice(0, 35)}...`);
            const starCmts = await collectReviewsByUiForStar(star, maxPerStar, seenIds);
            allComments.push(...starCmts);
            if (stopRequested) break;
            await randomDelay(600, 1200);
          }

          if (!stopRequested && allComments.length > 0) {
            const ingestResp = await fetch(`${LOCAL_SERVER}/api/ingest`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                product_id: productId,
                platform: "shopee",
                shop_id: ids.shopId,
                shop_name: meta.shopName,
                product_name: meta.productName,
                product_description: meta.productDesc,
                product_url: currentUrl,
                keyword: currentKwObj ? currentKwObj.keyword : "extension",
                comments: allComments
              })
            });
            const ingestData = await ingestResp.json();
            const inc = ingestData.saved_comments > 0 ? 1 : 0;
            const newCrawledCount = (state.crawledInRun || 0) + inc;
            newCrawledForKw += inc;
            const newSinceCaptcha = (state.productsSinceCaptcha || 0) + inc;
            await updateStatus(
              `✅ Đã lưu +${ingestData.saved_comments} cmt (${newCrawledCount}/${state.maxProducts} SP).`,
              {
                crawledInRun: newCrawledCount,
                crawledForCurrentKw: newCrawledForKw,
                productsSinceCaptcha: newSinceCaptcha
              }
            );
          }
        }
      }
    } else {
      await updateStatus(`⏭️ Sản phẩm ${productId} đã xử lý trước đó. Chuyển nhanh SP tiếp theo...`);
    }

    // 3. Kiểm tra chỉ tiêu Min/Max của từ khóa & chuyển sang SP / từ khóa tiếp theo
    const latestState = await chrome.storage.local.get([
      "running",
      "crawlMode",
      "minSoldCount",
      "keywordConfigs",
      "keywordIndex",
      "crawledForCurrentKw",
      "searchPageOffset",
      "crawledInRun",
      "batchPauseEveryN",
      "maxProducts",
      "visitedUrls",
      "pendingUrls"
    ]);
    if (!latestState.running || stopRequested) return;
    if ((latestState.crawledInRun || 0) >= (latestState.maxProducts || 30)) {
      await updateStatus(`🎉 Hoàn thành đủ ${latestState.maxProducts} sản phẩm!`, { running: false });
      return;
    }

    const pauseEvery = Number(latestState.batchPauseEveryN || 10);
    const crawledNow = Number(latestState.crawledInRun || 0);
    if (!shouldSkip && pauseEvery > 0 && crawledNow > 0 && crawledNow % pauseEvery === 0) {
      await updateStatus(`☕ Đã crawl ${crawledNow} SP liên tiếp. Nghỉ giải lao 60s để tránh CAPTCHA...`);
      await sleep(60000);
      if (stopRequested) return;
    }

    const activeKwCfg = (latestState.keywordConfigs || [])[Number(latestState.keywordIndex || 0)] || null;
    const kwDoneNow = Number(latestState.crawledForCurrentKw || 0);

    // Nếu ở chế độ keyword_sales và từ khóa hiện tại đã chạm trần Max -> chuyển ngay sang từ khóa tiếp theo!
    if ((latestState.crawlMode || "keyword_sales") === "keyword_sales" && activeKwCfg && kwDoneNow >= activeKwCfg.max) {
      await advanceToNextKeywordOrSearchPage(
        latestState,
        `✅ Đã đạt trần Max (${kwDoneNow}/${activeKwCfg.max} SP) cho "${activeKwCfg.keyword}". `
      );
      return;
    }

    let queue = (latestState.pendingUrls || []).filter((u) => !(latestState.visitedUrls || []).includes(u));

    if ((latestState.crawlMode || "keyword_sales") === "related_hop") {
      window.scrollTo({ top: document.body.scrollHeight * 0.75, behavior: "auto" });
      await sleep(1200);
      const rawRelated = collectProductCandidatesFromDom(
        productId,
        latestState.visitedUrls || [],
        Number(latestState.minSoldCount ?? 5000)
      );
      const relatedLinks = await filterUnvisitedViaServer(rawRelated);
      queue = Array.from(new Set([...queue, ...relatedLinks]));
    }

    if (queue.length === 0) {
      if ((latestState.crawlMode || "keyword_sales") === "keyword_sales" && activeKwCfg) {
        await advanceToNextKeywordOrSearchPage(latestState, "Hết sản phẩm trên trang hiện tại. ");
        return;
      }
      await updateStatus("⚠️ Đã hết sản phẩm trong hàng đợi.", { running: false });
      return;
    }

    const nextUrl = queue[0];
    const nextQueue = queue.slice(1);
    if (shouldSkip) {
      await updateStatus(`⏭️ Chuyển nhanh sang SP tiếp theo (còn ${nextQueue.length} SP)...`, {
        pendingUrls: nextQueue
      });
      await randomDelay(1000, 1500);
    } else {
      await updateStatus(`⏳ Nghỉ 3.5–5.5s trước khi sang SP tiếp theo (còn ${nextQueue.length} SP)...`, {
        pendingUrls: nextQueue
      });
      await randomDelay(6000, 8000);
    }

    const stillRunning = await chrome.storage.local.get(["running"]);
    if (stillRunning.running && !stopRequested) {
      chrome.runtime.sendMessage({ type: "NAVIGATE_NEXT", url: nextUrl });
    }
  } catch (err) {
    const msg = err ? String(err.message || err) : "Lỗi không xác định";
    if (msg.startsWith("CAPTCHA_DETECTED::")) {
      const reason = msg.replace("CAPTCHA_DETECTED::", "");
      await triggerCaptchaAlert(reason);
    } else {
      await updateStatus(`❌ Lỗi: ${msg}`, { running: false });
    }
  } finally {
    isProcessingTab = false;
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "RUN_PRODUCT_CRAWL") {
    runProductCrawl();
  }
});
