# Hệ thống Thu thập, EDA & Tiền xử lý Dữ liệu ABSA Ngành Thời trang nữ

Dự án xây dựng pipeline thu thập bình luận đánh giá sản phẩm ngành **Thời trang nữ** trên Shopee (qua Chrome Extension Manifest V3 + Local FastAPI Server), phân tích khám phá dữ liệu (EDA) và tiền xử lý văn bản với 3 phiên bản thí nghiệm (**A: Minimal**, **B: Rule-based**, **C: Hybrid**) phục vụ bài toán **ABSA (Aspect-Based Sentiment Analysis)**.

---

## 1. Cấu trúc Thư mục Chính

- `extension/`: Chrome Extension Manifest V3 (`manifest.json`, `background.js`, `content.js`, `popup.html`, `popup.js`) thu thập dữ liệu trực tiếp trên trình duyệt cá nhân, hỗ trợ chỉ tiêu `Min–Max` sản phẩm/từ khóa, cảnh báo CAPTCHA đa kênh và tự động chuyển trang.
- `server.py`: Local FastAPI Receiver Server (`http://127.0.0.1:8765`) tiếp nhận dữ liệu từ Extension, lọc nhiễu ABSA, cân bằng `rating_star` (`1★..5★`), lưu SQLite và xuất JSON/CSV.
- `src/`:
  - `models.py`: Mô hình dữ liệu `ProductRecord`, `CommentItem` và bộ lọc nhiễu `is_noise_or_spam_review`.
  - `storage.py`: Quản lý `SQLite` checkpoint, đếm Quota từng mức sao và xuất dataset.
  - `preprocessing.py`: Module tiền xử lý văn bản tiếng Việt 3 phiên bản (`minimal`, `rule_based`, `hybrid`) kèm `offset_map` chống lệch span.
- `scripts/`:
  - `eda_report.py`: Tạo báo cáo & 5 đồ thị EDA cho tập dữ liệu Shopee đã crawl.
  - `eda_fashion_reviews_hf.py`: Tải và EDA tập `vinhplaykennen/FashionReviews` trên HuggingFace.
  - `run_preprocessing.py`: Chia `Train/Valid` chống Data Leakage và xuất 3 phiên bản tiền xử lý cho Train, Valid, Test.
- `reports/`: Lưu các báo cáo Markdown và đồ thị trực quan hóa (`reports/figures/*.png`).

---

## 2. Hướng dẫn Chạy Nhanh

### A. Cài đặt thư viện
```powershell
pip install -r requirements.txt
```

### B. Thu thập dữ liệu (Chrome Extension + Local Server)
1. Chạy Local Server:
   ```powershell
   python server.py
   ```
2. Mở `chrome://extensions/` $\rightarrow$ bật **Developer mode** $\rightarrow$ **Load unpacked** thư mục `extension/`.
3. Mở trang **Thời trang nữ** trên Shopee $\rightarrow$ mở popup **ABSA Shopee Collector** $\rightarrow$ cấu hình chỉ tiêu `Min–Max` từ khóa và bấm **▶ Bắt đầu Crawl**.

### C. Phân tích EDA & Tiền xử lý dữ liệu
```powershell
# Chạy EDA dữ liệu Shopee đã thu thập
python scripts/eda_report.py

# Tải & EDA tập vinhplaykennen/FashionReviews từ HuggingFace
python scripts/eda_fashion_reviews_hf.py

# Chạy tiền xử lý 3 phiên bản (A, B, C) chống Data Leakage
python scripts/run_preprocessing.py
```
