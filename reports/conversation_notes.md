# Nhật ký Kỹ thuật, Quyết định Kiến trúc & Ghi chú Dự án ABSA

Tài liệu này tổng hợp lại toàn bộ quá trình phân tích, các thay đổi kỹ thuật, lý do đằng sau từng quyết định thiết kế và bài học thực tế từ phiên làm việc, giúp bạn tra cứu nhanh mỗi khi cần.

---

## 1. Mục tiêu Dự án & Yêu cầu Dữ liệu
- **Bài toán**: Phân tích cảm xúc theo khía cạnh (**ABSA**) ngành **Thời trang nữ** trên Shopee & TikTok Shop.
- **Trường dữ liệu thu thập**: `product_id`, `tên cửa hàng`, `tên sản phẩm`, `thông tin và mô tả sp`, `cmt_id`, `review_text`, `rating_star` (`1★..5★`), `variation`, `timestamp`, `like_count`.
- **Chiến lược lấy mẫu**:
  - Ưu tiên bình luận mới trong vòng 1 năm trở lại đây (không cắt cứng để vẫn giữ các đánh giá `1★–3★` có giá trị).
  - Cân bằng `rating_star` toàn cục bằng **QuotaManager** + hỗ trợ lọc riêng từng mức sao (`--stars`).
  - Giới hạn trần **`20` bình luận / mức sao / sản phẩm** để tránh một mức sao (như `1★` hoặc `5★`) chiếm trọn dung lượng của một sản phẩm.

---

## 2. Quá trình Tiến hóa Kiến trúc Thu thập Dữ liệu (Từ Vấn đề đến Giải pháp)

### Giai đoạn 1: Playwright Persistent Context (`main.py`)
- **Ý tưởng**: Dùng Chrome thật (`channel="chrome"`) lưu profile đăng nhập vào `browser_profiles/` để tránh bị lệch TLS/JA3 fingerprint như khi copy cookie rời sang `requests`.
- **Vấn đề gặp phải**:
  - Bị báo động giả CAPTCHA do thẻ `div[class*='captcha']` ẩn luôn có sẵn trong DOM $\rightarrow$ Đã sửa chỉ bắt khi khung CAPTCHA thực sự hiển thị (`>= 180x120px`).
  - Shopee chặn điều hướng trực tiếp `page.goto()` sang trang sản phẩm bằng `/verify/traffic/error` và `/verify/captcha?...&scene=crawler_item` (kể cả khi đã đăng nhập).

### Giai đoạn 2: Local Browser Automation qua CDP (`run_shopee.py:9222`, `run_tiktok.py:9223`)
- **Ý tưởng**: Tách riêng crawler Shopee & TikTok, kết nối vào Chrome đang mở bằng `connect_over_cdp`, bổ sung tự động đăng nhập Google OAuth qua `.env`.
- **Các lỗi kỹ thuật đã phát hiện & sửa**:
  - `time.sleep()` làm đóng băng vòng lặp Greenlet của Playwright Sync API khiến không bắt được gói tin mạng $\rightarrow$ Phải dùng `page.wait_for_timeout()`.
  - Thẻ sản phẩm Shopee dùng CSS `display: contents` (`<a class="contents">`) nên `bounding_box()` trả về `None` $\rightarrow$ Phải lấy kích thước & click ở thẻ con đầu tiên.
  - Bổ sung fallback đọc DOM mới của Shopee (`div[data-cmtid]`, `div.YNedDV`).
- **Giới hạn cuối cùng**: Dù đã đăng nhập, mọi thao tác tự động chuyển trang thông qua kết nối CDP vẫn có nguy cơ bị Shopee gắn cờ `/verify/traffic/error`.

### Giai đoạn 3: Kiến trúc Hybrid Hiện tại — Chrome Extension (Manifest V3) + Local FastAPI Server (`extension/` + `server.py`)
- **Lý do lựa chọn**:
  - Extension chạy trực tiếp bên trong **trình duyệt Chrome cá nhân bình thường** (không có tiến trình CDP/WebDriver từ bên ngoài) $\rightarrow$ Chỉ cần đăng nhập Shopee **1 lần**, tự động chuyển trang qua danh sách sản phẩm bán chạy mà không dính dấu vết điều khiển ngoài.
  - Local Python Server (`server.py` tại `http://127.0.0.1:8765`) đóng vai trò hậu cần: quản lý SQLite Checkpoint, cân bằng Quota, lọc nhiễu ABSA, và xuất JSON/CSV.
- **Bài học thực tế từ lúc vận hành Extension**:
  1. **Không gọi `fetch('/api/v2/item/get_ratings')` rời**: Thiếu header bảo mật động (`x-sap-ri`, `x-sap-sec`) của frontend Shopee sẽ bị trả về `HTTP 403` ngay ở request đầu tiên $\rightarrow$ Chuyển sang để Extension **click trực tiếp tab lọc sao & nút Next trên DOM** rồi đọc dữ liệu hiển thị.
  2. **Tối ưu nguồn sản phẩm & tốc độ**:
     - Thay vì nhảy ngẫu nhiên vào mục *"Sản phẩm tương tự"* (nhiều shop nhỏ ít review), thêm chế độ **`Bán chạy & Xoay từ khóa`** (`sortBy=sales`), lọc sẵn `lượt bán >= 500`, bỏ qua sản phẩm `With Comments < 30`, và lưu cache sản phẩm bị skip (`/api/mark-skipped`) để không mở lại.
     - Thêm tùy chọn **chỉ tiêu `Min – Max` SP cho mỗi từ khóa** (`min <= x <= max`), tự động lật trang tìm kiếm nếu chưa đủ `Min` và chuyển từ khóa khi chạm `Max`.
  3. **Ngưỡng tốc độ & CAPTCHA đo được thực tế**:
     - Chạy quá nhanh (`~53s/SP`, `246` lượt bấm trong `14` phút) $\rightarrow$ Gặp `/verify/traffic` sau **`16` sản phẩm**.
     - Giữ nhịp vừa phải (`~70–73s/SP`) + nghỉ giải lao `60–120s` mỗi `8–10` SP $\rightarrow$ Chạy ổn định **`30–44` sản phẩm liên tiếp** không bị chặn.
     - Đã tích hợp **cảnh báo đa kênh** khi gặp CAPTCHA: Web Audio Beep + Chrome Desktop Notification + Focus tab + `winsound.Beep` & Windows `MessageBoxW` từ `server.py`.

---

## 3. Ghi chú Kết quả EDA & Phân tích Dữ liệu

### A. Tập Test Shopee tự thu thập (`data/output/shopee_absa_reviews_flat.csv`)
- **Quy mô ghi nhận gần nhất**: **74 sản phẩm / 3,276 bình luận / 60 shop khác nhau** (tất cả từ Shopee).
- **Cân bằng sao**: `1★`: `480` (`14.7%`), `2★`: `369` (`11.3%`), `3★`: `429` (`13.1%`), `4★`: `658` (`20.1%`), `5★`: `1,340` (`40.9%`).
- **Cân bằng nhóm loại hàng**: Áo khoác/Blazer (`32.1%`), Quần nữ (`18.3%`), Áo thun/sơ mi (`17.0%`), Váy/Đầm (`16.1%`), Đồ bộ (`14.3%`).
- **Đánh giá làm Tập Test**: Rất hợp lý nhờ độ phủ shop rộng (`1.23` SP/shop) và nhóm tiêu cực/trung tính (`1★–3★`) chiếm `39.0%`. Nếu lọc bỏ các sản phẩm `< 30` cmt sẽ còn **44 SP / 2,654 bình luận** với phân bố sao đẹp hơn nữa (`462 / 333 / 402 / 610 / 847`).
- *Lưu ý khi chia Train/Test*: Luôn chia theo `product_id` (Group Split), không để review cùng 1 sản phẩm nằm ở cả Train và Test.

### B. Tập Train/Valid HuggingFace (`vinhplaykennen/FashionReviews`)
- **Quy mô**: **`89,145` dòng** (`89,010` câu duy nhất sau khi khử `135` dòng trùng).
- **Schema**: `Nội dung review` + 5 cột khía cạnh (`Chất liệu`, `Kiểu dáng`, `Kích cỡ`, `Giá cả`, `Dịch vụ`) với 4 nhãn `0: Không đề cập, 1: Tiêu cực, 2: Trung tính, 3: Tích cực`.
- **Điểm cần chú ý**:
  1. Có rải rác `NaN` ở 5 cột nhãn (`6` đến `21` dòng/cột) $\rightarrow$ Bắt buộc `fillna(0)` trước khi ép kiểu `int`.
  2. **Domain Shift lớn so với Shopee**: `FashionReviews` dài gần gấp đôi (`Median 175` vs `92` ký tự) và tỷ lệ teencode thấp (`8.7%`) so với tập Shopee (`35.1%` — gấp 4 lần). Do đó cần chuẩn hóa teencode và cân nhắc dùng **Macro-F1** thay cho Accuracy vì nhãn `0` chiếm `31.7% – 45.9%`.

---

## 4. Nguyên tắc Thiết kế Tiền xử lý (`src/preprocessing.py`) & Kết quả Kiểm toán

### 5 Nguyên tắc cốt lõi
1. **Unicode NFC (`unicodedata.normalize("NFC")`)**: Tránh trường hợp tổ hợp ký tự rời (`e` + `^`) và dựng sẵn (`ê`) sinh ra 2 token khác nhau.
2. **Không xóa Stopwords ngữ nghĩa**: Giữ nguyên *"không, chưa, chẳng, nhưng, tuy nhiên, rất, hơi, quá, lắm"* vì xóa đi sẽ đảo ngược cực tính cảm xúc trong ABSA.
3. **Nén ký tự lặp có kiểm soát (`>= 3 -> 1`)**: Biến `"đẹpppppp"` $\rightarrow$ `"đẹp"` nhưng vẫn giữ nguyên các từ tiếng Việt có 2 chữ cái giống nhau hợp lệ (như *"xoong"*).
4. **Không thay thế mù quáng từ mơ hồ (`AMBIGUOUS_TOKENS = {"k", "m", "đ", "b", "c", "e", "t", "s", "l"}`)**: Tránh biến `"50k"` thành `"50không"` hoặc `"size m"` thành `"size mình"`.
5. **Tách biệt Normalization và Word Segmentation**: `src/preprocessing.py` chỉ xuất `Normalized Text`, không trộn logic tách từ (`vải_mát`) của tokenizer PhoBERT vào cùng một hàm.

### 4 Điểm đã siết chặt từ Data Pipeline Audit
1. **Chống Offset Drift (cho ATE)**: Cung cấp `preprocess_with_offsets()` trả về `offset_map` ánh xạ vị trí ký tự từ chuỗi đã xử lý về chuỗi gốc (và quy ước gán nhãn span trên `Normalized Text`).
2. **Chống Data Leakage**: Thực thi đúng thứ tự `Khử trùng lặp -> Chia Train/Valid (85/15, random_state=42) -> Fit chỉ trên Train -> Transform độc lập lên Valid & Test`.
3. **Lookaround Regex Unicode**: Dùng `(?<![^\W_])<tok>(?![^\W_])` thay cho `str.replace()` để bắt đúng từ độc lập kể cả khi dính dấu câu (`"ko?"`), nhưng không đụng vào `"100k"`, `"1m65"`, `"sz m"`.
4. **Phân định rạch ròi 3 phiên bản Ablation Study**:
   - **A (`minimal`)**: Unicode NFC + HTML entities + xóa HTML/URL + khoảng trắng.
   - **B (`rule_based`)**: A + nén lặp + xóa toàn bộ dấu câu + `SAFE_NORMALIZATION`.
   - **C (`hybrid`)**: A + nén lặp + giữ dấu câu cảm xúc (`!`, `?`) + chuẩn hóa cụm số đo (`50 k` $\rightarrow$ `50k`) + `SAFE_NORMALIZATION` + `fit()` trên Train + giữ từ mơ hồ cho Transformer tự suy luận theo ngữ cảnh.

---

## 5. Chỉ mục Nhanh Các File & Lệnh Thường Dùng

| Mục đích | File / Lệnh |
| :--- | :--- |
| **Chạy Local Server nhận dữ liệu Extension** | `python server.py` ([server.py](file:///c:/Users/admin/Developer/web-mining/server.py)) |
| **Chạy Extension thu thập Shopee** | Mở `chrome://extensions/` $\rightarrow$ Load/Reload thư mục [extension/](file:///c:/Users/admin/Developer/web-mining/extension/manifest.json) |
| **Chạy EDA & vẽ 5 đồ thị tập Shopee** | `python scripts/eda_report.py` $\rightarrow$ [reports/eda_summary.md](file:///c:/Users/admin/Developer/web-mining/reports/eda_summary.md) |
| **Tải & chạy EDA tập HuggingFace** | `python scripts/eda_fashion_reviews_hf.py` $\rightarrow$ [reports/eda_fashion_reviews_hf.md](file:///c:/Users/admin/Developer/web-mining/reports/eda_fashion_reviews_hf.md) |
| **Chạy tiền xử lý 3 phiên bản (A / B / C)** | `python scripts/run_preprocessing.py` $\rightarrow$ `data/processed/` |
| **Chạy toàn bộ Unit Tests** | `pytest -v` ([tests/test_pipeline.py](file:///c:/Users/admin/Developer/web-mining/tests/test_pipeline.py)) |
