# Hệ thống Crawl Dữ liệu Shopee & TikTok Shop cho Bài toán ABSA (Ngành Thời trang nữ)

Bộ công cụ thu thập dữ liệu đánh giá sản phẩm ngành **Thời trang nữ** được tách riêng cho **Shopee (`run_shopee.py`)** và **TikTok Shop (`run_tiktok.py`)**, sử dụng cơ chế **Local Browser Automation qua cổng Remote Debugging (`connect_over_cdp`)** kết hợp **Tự động chuyển sang Sản phẩm Liên quan (Auto-Hop)**.

---

## 1. Quy trình Chạy Riêng cho SHOPEE (`run_shopee.py` — Port 9222)

### Bước 1: Mở trình duyệt Google Chrome thật với cổng Debug `9222`
```powershell
python run_shopee.py start-chrome
```
*(Hoặc chạy trực tiếp: `chrome.exe --remote-debugging-port=9222 --user-data-dir="C:/chrome_shopee_profile"`)*

### Bước 2: Mở 1 sản phẩm Thời trang nữ đầu tiên (Seed Product)
Trên cửa sổ Chrome vừa hiện lên:
1. Đăng nhập tài khoản Shopee của bạn (chỉ cần đăng nhập 1 lần đầu, Chrome tự lưu vào `C:/chrome_shopee_profile`).
2. Tự tay mở **1 trang sản phẩm Thời trang nữ bất kỳ** (ví dụ: áo thun nữ, váy đầm nữ...).

### Bước 3: Chạy lệnh Crawl & Tự động Nhảy sang Sản phẩm Liên quan
```powershell
# Tự động crawl sản phẩm đang mở -> tự động click sang sản phẩm Thời trang nữ liên quan tiếp theo (tối đa 20 SP)
python run_shopee.py crawl --max-products 20

# Hoặc chỉ chọn lấy riêng các đánh giá 1★, 2★, 3★
python run_shopee.py crawl --stars 1,2,3 --max-products 30

# Nếu chỉ muốn crawl đúng các tab bạn đang mở sẵn (không tự nhảy sang SP liên quan)
python run_shopee.py crawl --no-hop
```
> Sau khi chạy xong, cửa sổ Chrome của bạn **vẫn được giữ nguyên** (không bị đóng). Dữ liệu được xuất tự động ra `data/output/shopee_products_reviews.json` và `data/output/shopee_absa_reviews_flat.csv`.

---

## 2. Quy trình Chạy Riêng cho TIKTOK SHOP (`run_tiktok.py` — Port 9223)

### Bước 1: Mở trình duyệt Google Chrome thật với cổng Debug `9223`
```powershell
python run_tiktok.py start-chrome
```
*(Sử dụng cổng `9223` và thư mục `C:/chrome_tiktok_profile` hoàn toàn tách biệt với Shopee)*

### Bước 2: Mở 1 sản phẩm Thời trang nữ trên TikTok Shop
Trên cửa sổ Chrome vừa mở, bấm vào **1 trang sản phẩm Thời trang nữ bất kỳ** trên TikTok Shop.

### Bước 3: Chạy lệnh Crawl & Tự động Nhảy sang Sản phẩm Liên quan
```powershell
# Tự động crawl sản phẩm đang mở -> tự động nhảy sang các sản phẩm Thời trang nữ liên quan tiếp theo
python run_tiktok.py crawl --max-products 20

# Chỉ lấy riêng các đánh giá 1★, 2★, 3★
python run_tiktok.py crawl --stars 1,2,3 --max-products 30
```
> Dữ liệu được xuất tự động ra `data/output/tiktok_products_reviews.json` và `data/output/tiktok_absa_reviews_flat.csv`.

---

## 3. Xem Thống kê Tiến độ & Kiểm thử

```powershell
# Xem bảng thống kê tiến độ Quota từng mức sao (1★..5★)
python run_shopee.py stats
python run_tiktok.py stats

# Chạy bộ kiểm thử tự động (Unit & Auto-Hop Tests)
pytest -v
```
# absa-for-fashion-reviews
