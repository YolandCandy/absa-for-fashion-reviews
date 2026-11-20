const LOCAL_SERVER = "http://127.0.0.1:8765";
let cachedCaptchaStats = null;

function parseKeywordConfigs(rawText, defaultMin, defaultMax) {
  return rawText
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((entry) => {
      const parts = entry.split(":").map((p) => p.trim());
      const keyword = parts[0];
      let min = defaultMin;
      let max = defaultMax;
      if (parts.length >= 2 && /^\d+$/.test(parts[1])) {
        min = Number(parts[1]);
      }
      if (parts.length >= 3 && /^\d+$/.test(parts[2])) {
        max = Number(parts[2]);
      }
      return { keyword, min, max };
    })
    .filter((item) => item.keyword.length > 0);
}

async function refreshServerStats() {
  const badge = document.getElementById("serverBadge");
  try {
    const resp = await fetch(`${LOCAL_SERVER}/api/stats`);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    badge.textContent = "Server: Online";
    badge.className = "badge-ok";

    document.getElementById("totalInfo").textContent = `${data.total_products || 0} SP / ${data.total_comments || 0} cmt`;
    const stars = data.star_counts || {};
    for (let i = 1; i <= 5; i++) {
      document.getElementById(`s${i}`).textContent = stars[i] || 0;
    }
    cachedCaptchaStats = data.captcha_stats || null;
  } catch (e) {
    badge.textContent = "Server: Offline";
    badge.className = "badge-err";
  }
}

function refreshLocalState() {
  chrome.storage.local.get(
    [
      "running",
      "lastStatus",
      "productsSinceCaptcha",
      "requestsSinceCaptcha",
      "pendingUrls",
      "keywordConfigs",
      "keywordIndex",
      "crawledForCurrentKw",
      "crawlMode"
    ],
    (st) => {
      const qLen = Array.isArray(st.pendingUrls) ? st.pendingUrls.length : 0;
      let kwPrefix = "";
      if ((st.crawlMode || "keyword_sales") === "keyword_sales" && Array.isArray(st.keywordConfigs) && st.keywordConfigs.length > 0) {
        const idx = Math.min(Number(st.keywordIndex || 0), st.keywordConfigs.length - 1);
        const cur = st.keywordConfigs[idx];
        const done = Number(st.crawledForCurrentKw || 0);
        if (cur) {
          kwPrefix = `[TK ${idx + 1}/${st.keywordConfigs.length}: "${cur.keyword}" ${done}/${cur.max} (Min:${cur.min})] `;
        }
      }
      const qPrefix = qLen > 0 ? `[Chờ: ${qLen}] ` : "";
      document.getElementById("statusBox").textContent = kwPrefix + qPrefix + (st.lastStatus || "Sẵn sàng.");
      document.getElementById("btnStart").disabled = Boolean(st.running);

      const pCnt = Number(st.productsSinceCaptcha || 0);
      const rCnt = Number(st.requestsSinceCaptcha || 0);
      let capText = "Chưa ghi nhận";
      if (cachedCaptchaStats && cachedCaptchaStats.total_events > 0) {
        capText = `TB ${cachedCaptchaStats.avg_products_before_captcha} SP (${cachedCaptchaStats.avg_requests_before_captcha} req) [${cachedCaptchaStats.total_events} lần]`;
      }
      document.getElementById("captchaMetrics").textContent =
        `Phiên hiện tại: ${pCnt} SP / ${rCnt} req | Ngưỡng CAPTCHA: ${capText}`;
    }
  );
}

document.getElementById("crawlMode").addEventListener("change", (e) => {
  document.getElementById("keywordsBox").style.display =
    e.target.value === "keyword_sales" ? "block" : "none";
});

document.getElementById("btnStart").addEventListener("click", () => {
  const selectedStars = Array.from(document.querySelectorAll(".star-cb:checked")).map((cb) => Number(cb.value));
  if (selectedStars.length === 0) {
    alert("Vui lòng chọn ít nhất 1 mức sao!");
    return;
  }
  const crawlMode = document.getElementById("crawlMode").value;
  const minSoldCount = Number(document.getElementById("minSoldCount").value) || 0;
  const maxProducts = Number(document.getElementById("maxProducts").value) || 30;
  const batchPauseEveryN = Number(document.getElementById("batchPauseEveryN").value) || 0;
  const minPerKw = Number(document.getElementById("minPerKw").value) || 1;
  const maxPerKw = Number(document.getElementById("maxPerKw").value) || 25;

  if (minPerKw > maxPerKw) {
    alert("Chỉ tiêu Min SP / từ khóa phải nhỏ hơn hoặc bằng Max SP / từ khóa!");
    return;
  }

  const keywordConfigs = parseKeywordConfigs(
    document.getElementById("keywordsInput").value,
    minPerKw,
    maxPerKw
  );
  for (const cfg of keywordConfigs) {
    if (cfg.min > cfg.max) {
      alert(`Từ khóa "${cfg.keyword}" có Min (${cfg.min}) > Max (${cfg.max}). Vui lòng kiểm tra lại!`);
      return;
    }
  }

  chrome.runtime.sendMessage(
    {
      type: "START_CRAWL",
      targetStars: selectedStars,
      crawlMode,
      minSoldCount,
      maxProducts,
      batchPauseEveryN,
      keywordConfigs
    },
    () => {
      refreshLocalState();
    }
  );
});

document.getElementById("btnStop").addEventListener("click", () => {
  chrome.storage.local.set({ running: false, lastStatus: "⏹ Đã dừng bởi người dùng." }, () => {
    chrome.runtime.sendMessage({ type: "STOP_CRAWL" }).catch(() => {});
    refreshLocalState();
  });
});

refreshServerStats().then(refreshLocalState);
setInterval(() => {
  refreshServerStats().then(refreshLocalState);
}, 2000);
