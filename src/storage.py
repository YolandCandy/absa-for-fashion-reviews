"""
Quản lý lưu trữ dữ liệu, SQLite Checkpoint, Hàng đợi sản phẩm (Product Queue),
Quản lý Quota cân bằng theo rating_star (1★..5★) và Xuất file JSON / CSV.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from loguru import logger
from src.models import CommentItem, ProductRecord


VN_TZ = timezone(timedelta(hours=7))


class StorageManager:
    """Quản lý SQLite checkpoint, bộ đếm Quota từng mức sao và xuất dữ liệu đầu ra."""

    def __init__(self, db_path: str = "data/checkpoint.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS product_queue (
                    url TEXT PRIMARY KEY,
                    product_id TEXT,
                    platform TEXT NOT NULL,
                    keyword TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    discovered_at TEXT,
                    crawled_at TEXT
                );

                CREATE TABLE IF NOT EXISTS products (
                    product_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    shop_id TEXT DEFAULT '',
                    shop_name TEXT DEFAULT '',
                    product_name TEXT DEFAULT '',
                    product_description TEXT DEFAULT '',
                    product_url TEXT DEFAULT '',
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS comments (
                    cmt_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    review_text TEXT NOT NULL,
                    rating_star INTEGER NOT NULL,
                    variation TEXT DEFAULT '',
                    timestamp TEXT NOT NULL,
                    unix_timestamp INTEGER NOT NULL,
                    like_count INTEGER DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY (product_id) REFERENCES products (product_id)
                );

                CREATE INDEX IF NOT EXISTS idx_comments_star ON comments(rating_star);
                CREATE INDEX IF NOT EXISTS idx_comments_product ON comments(product_id);
                CREATE INDEX IF NOT EXISTS idx_queue_platform_status ON product_queue(platform, status);

                CREATE TABLE IF NOT EXISTS captcha_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    url TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    products_before_captcha INTEGER DEFAULT 0,
                    requests_before_captcha INTEGER DEFAULT 0,
                    occurred_at TEXT NOT NULL
                );
                """
            )

    # ------------------------------------------------------------------
    # Quản lý Hàng đợi Sản phẩm (Product Queue)
    # ------------------------------------------------------------------
    def enqueue_urls(
        self,
        urls: Iterable[str],
        platform: str,
        keyword: str = "",
        product_ids: Optional[Dict[str, str]] = None,
    ) -> int:
        """Thêm danh sách URL sản phẩm vào hàng đợi (bỏ qua nếu đã tồn tại). Trả về số URL mới."""
        now_str = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        added = 0
        with self._get_conn() as conn:
            for raw_url in urls:
                url = raw_url.strip()
                if not url or url.startswith("#"):
                    continue
                pid = (product_ids or {}).get(url, "")
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO product_queue (url, product_id, platform, keyword, status, discovered_at)
                    VALUES (?, ?, ?, ?, 'pending', ?)
                    """,
                    (url, pid, platform, keyword, now_str),
                )
                if cur.rowcount > 0:
                    added += 1
        return added

    def load_urls_from_file(self, file_path: str) -> Tuple[int, int]:
        """
        Đọc file danh sách URL đầu vào (tự động nhận diện Shopee / TikTok Shop) và đưa vào queue.
        Trả về (số URL Shopee mới, số URL TikTok mới).
        """
        path = Path(file_path)
        if not path.exists():
            return 0, 0

        shopee_urls: List[str] = []
        tiktok_urls: List[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            if "shopee.vn" in clean.lower():
                shopee_urls.append(clean)
            elif "tiktok.com" in clean.lower():
                tiktok_urls.append(clean)

        s_added = self.enqueue_urls(shopee_urls, platform="shopee", keyword="manual_file")
        t_added = self.enqueue_urls(tiktok_urls, platform="tiktok", keyword="manual_file")
        return s_added, t_added

    def get_pending_urls(self, platform: str, limit: int = 200) -> List[Dict[str, str]]:
        """Lấy danh sách URL sản phẩm đang chờ crawl (`status = 'pending'`)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT url, product_id, platform, keyword
                FROM product_queue
                WHERE platform = ? AND status = 'pending'
                ORDER BY discovered_at ASC
                LIMIT ?
                """,
                (platform, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_url_status(self, url: str, status: str, product_id: str = "") -> None:
        """Cập nhật trạng thái của URL trong hàng đợi ('crawled', 'skipped', 'failed')."""
        now_str = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            if product_id:
                conn.execute(
                    """
                    UPDATE product_queue
                    SET status = ?, product_id = ?, crawled_at = ?
                    WHERE url = ?
                    """,
                    (status, product_id, now_str, url),
                )
            else:
                conn.execute(
                    """
                    UPDATE product_queue
                    SET status = ?, crawled_at = ?
                    WHERE url = ?
                    """,
                    (status, now_str, url),
                )

    def is_product_crawled(self, product_id: str) -> bool:
        """Kiểm tra sản phẩm đã có trong bảng products và có bình luận chưa."""
        if not product_id:
            return False
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS cnt FROM comments WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            return bool(row and row["cnt"] > 0)

    def is_product_skipped(self, product_id: str) -> bool:
        """Kiểm tra sản phẩm đã từng bị đánh dấu bỏ qua (ví dụ ít bình luận) chưa."""
        if not product_id:
            return False
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM product_queue WHERE product_id = ? AND status LIKE 'skipped%' LIMIT 1",
                (product_id,),
            ).fetchone()
            return row is not None

    def mark_product_skipped(
        self,
        product_id: str,
        url: str,
        platform: str = "shopee",
        reason: str = "skipped_low_reviews",
    ) -> None:
        """Lưu sản phẩm bị bỏ qua vào SQLite để các lần quét sau không mở lại."""
        now_str = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        clean_url = url.split("?")[0].strip() or f"https://shopee.vn/product/{product_id}"
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO product_queue (url, product_id, platform, keyword, status, discovered_at, crawled_at)
                VALUES (?, ?, ?, 'extension', ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    product_id = excluded.product_id,
                    status = excluded.status,
                    crawled_at = excluded.crawled_at
                """,
                (clean_url, product_id, platform, reason, now_str, now_str),
            )

    def filter_unvisited_products(self, items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Lọc bỏ các sản phẩm đã crawl hoặc đã bị đánh dấu skipped khỏi danh sách ứng viên."""
        result: List[Dict[str, str]] = []
        for item in items:
            pid = str(item.get("product_id") or "").strip()
            if not pid:
                continue
            if self.is_product_crawled(pid) or self.is_product_skipped(pid):
                continue
            result.append(item)
        return result

    def count_products_by_shop(self, shop_id: str) -> int:
        """Đếm số sản phẩm đã thu thập thuộc về cùng một `shop_id` để tránh lệch nguồn về 1 shop."""
        if not shop_id:
            return 0
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS cnt FROM products WHERE shop_id = ?",
                (str(shop_id),),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def is_comment_exists(self, cmt_id: str) -> bool:
        """Kiểm tra bình luận đã tồn tại trong DB chưa."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM comments WHERE cmt_id = ? LIMIT 1",
                (cmt_id,),
            ).fetchone()
            return row is not None

    # ------------------------------------------------------------------
    # Quản lý Quota Cân bằng Dữ liệu theo Mức Sao (1★ .. 5★)
    # ------------------------------------------------------------------
    def get_star_counts(self, platform: Optional[str] = None) -> Dict[int, int]:
        """Lấy số lượng bình luận đã thu thập cho từng mức sao 1..5."""
        counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        with self._get_conn() as conn:
            if platform and platform != "all":
                rows = conn.execute(
                    """
                    SELECT rating_star, COUNT(1) AS cnt
                    FROM comments
                    WHERE platform = ?
                    GROUP BY rating_star
                    """,
                    (platform,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT rating_star, COUNT(1) AS cnt
                    FROM comments
                    GROUP BY rating_star
                    """
                ).fetchall()
            for r in rows:
                star = int(r["rating_star"])
                if star in counts:
                    counts[star] = int(r["cnt"])
        return counts

    def get_needed_stars(
        self,
        target_stars: List[int],
        quota_per_star: Dict[int, int],
        priority_order: Optional[List[int]] = None,
    ) -> List[int]:
        """
        Trả về danh sách các mức sao (trong `target_stars`) CHƯA đạt đủ hạn ngạch (quota),
        sắp xếp theo thứ tự ưu tiên `priority_order` (ví dụ: [1, 2, 3, 4, 5]).
        """
        counts = self.get_star_counts()
        order = priority_order or [1, 2, 3, 4, 5]
        # Đảm bảo mọi sao trong target_stars đều có mặt trong order
        for s in target_stars:
            if s not in order:
                order.append(s)

        needed: List[int] = []
        for star in order:
            if star not in target_stars:
                continue
            limit = int(quota_per_star.get(star, quota_per_star.get(str(star), 3000)))
            if counts.get(star, 0) < limit:
                needed.append(star)
        return needed

    def remaining_quota_for_star(self, star: int, quota_per_star: Dict[int, int]) -> int:
        """Số lượng bình luận còn thiếu của mức sao `star` trên toàn bộ dataset."""
        counts = self.get_star_counts()
        limit = int(quota_per_star.get(star, quota_per_star.get(str(star), 3000)))
        return max(0, limit - counts.get(star, 0))

    def is_all_quotas_met(
        self,
        target_stars: List[int],
        quota_per_star: Dict[int, int],
    ) -> bool:
        """Kiểm tra toàn bộ các mức sao mục tiêu đã đạt đủ quota chưa."""
        return len(self.get_needed_stars(target_stars, quota_per_star)) == 0

    # ------------------------------------------------------------------
    # Lưu trữ Sản phẩm & Bình luận
    # ------------------------------------------------------------------
    def save_product_record(
        self,
        record: ProductRecord,
        by_product_dir: str = "data/output/by_product",
        max_comments: int = 100,
        prefer_recent_days: int = 365,
        strict_max_age: bool = False,
        max_per_star: Optional[int] = None,
    ) -> int:
        """
        Lưu thông tin sản phẩm và danh sách bình luận đã chọn vào SQLite,
        đồng thời ghi ngay file JSON riêng của sản phẩm xuống đĩa.
        Trả về số lượng bình luận mới được thêm vào DB.
        """
        selected_comments = record.select_and_sort_comments(
            max_comments=max_comments,
            prefer_recent_days=prefer_recent_days,
            strict_max_age=strict_max_age,
            max_per_star=max_per_star,
        )
        if not selected_comments:
            return 0

        now_str = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        inserted_count = 0

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO products (
                    product_id, platform, shop_id, shop_name,
                    product_name, product_description, product_url, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    shop_name = excluded.shop_name,
                    product_name = excluded.product_name,
                    product_description = excluded.product_description,
                    product_url = excluded.product_url,
                    updated_at = excluded.updated_at
                """,
                (
                    record.product_id,
                    record.platform,
                    record.shop_id,
                    record.shop_name,
                    record.product_name,
                    record.product_description,
                    record.product_url,
                    now_str,
                ),
            )

            for cmt in selected_comments:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO comments (
                        cmt_id, product_id, platform, review_text,
                        rating_star, variation, timestamp, unix_timestamp,
                        like_count, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cmt.cmt_id,
                        record.product_id,
                        record.platform,
                        cmt.review_text,
                        cmt.rating_star,
                        cmt.variation,
                        cmt.timestamp,
                        cmt.unix_timestamp,
                        cmt.like_count,
                        now_str,
                    ),
                )
                if cur.rowcount > 0:
                    inserted_count += 1

        # Xuất file JSON riêng cho sản phẩm này
        out_dir = Path(by_product_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in record.product_id)
        product_json_path = out_dir / f"{record.platform}_{safe_id}.json"
        product_payload = record.to_vietnamese_json(
            max_comments=max_comments,
            prefer_recent_days=prefer_recent_days,
            strict_max_age=strict_max_age,
        )
        product_json_path.write_text(
            json.dumps(product_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return inserted_count

    # ------------------------------------------------------------------
    # Xuất toàn bộ Dataset ra JSON chuẩn đề bài & CSV phẳng cho ABSA
    # ------------------------------------------------------------------
    def export_datasets(
        self,
        output_dir: str = "data/output",
        max_comments_per_product: int = 100,
        prefer_recent_days: int = 365,
        strict_max_age: bool = False,
        stars_filter: Optional[List[int]] = None,
        platform_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Kết xuất toàn bộ dữ liệu từ SQLite ra:
        1. `[<platform>_]products_reviews.json`: Cấu trúc JSON lồng theo sản phẩm đúng mẫu yêu cầu.
        2. `[<platform>_]absa_reviews_flat.csv` & `.jsonl`: Bảng phẳng phục vụ gán nhãn ABSA.
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        nested_products: List[Dict[str, Any]] = []
        flat_rows: List[Dict[str, Any]] = []

        with self._get_conn() as conn:
            if platform_filter and platform_filter != "all":
                prod_rows = conn.execute(
                    "SELECT * FROM products WHERE platform = ? ORDER BY updated_at ASC",
                    (platform_filter,),
                ).fetchall()
            else:
                prod_rows = conn.execute("SELECT * FROM products ORDER BY updated_at ASC").fetchall()

            for p in prod_rows:
                pid = p["product_id"]
                if stars_filter:
                    placeholders = ",".join("?" for _ in stars_filter)
                    cmt_rows = conn.execute(
                        f"""
                        SELECT * FROM comments
                        WHERE product_id = ? AND rating_star IN ({placeholders})
                        ORDER BY unix_timestamp DESC
                        """,
                        (pid, *stars_filter),
                    ).fetchall()
                else:
                    cmt_rows = conn.execute(
                        """
                        SELECT * FROM comments
                        WHERE product_id = ?
                        ORDER BY unix_timestamp DESC
                        """,
                        (pid,),
                    ).fetchall()

                if not cmt_rows:
                    continue

                comments_dict: Dict[str, CommentItem] = {}
                for c in cmt_rows:
                    item = CommentItem(
                        cmt_id=c["cmt_id"],
                        product_id=c["product_id"],
                        platform=c["platform"],
                        review_text=c["review_text"],
                        rating_star=int(c["rating_star"]),
                        variation=c["variation"] or "",
                        timestamp=c["timestamp"],
                        unix_timestamp=int(c["unix_timestamp"]),
                        like_count=int(c["like_count"] or 0),
                    )
                    comments_dict[item.cmt_id] = item

                record = ProductRecord(
                    product_id=pid,
                    platform=p["platform"],
                    shop_id=p["shop_id"] or "",
                    shop_name=p["shop_name"] or "",
                    product_name=p["product_name"] or "",
                    product_description=p["product_description"] or "",
                    product_url=p["product_url"] or "",
                    comments=comments_dict,
                )

                vn_json = record.to_vietnamese_json(
                    max_comments=max_comments_per_product,
                    prefer_recent_days=prefer_recent_days,
                    strict_max_age=strict_max_age,
                )
                if vn_json["số cmt"] > 0:
                    nested_products.append(vn_json)

                selected_items = record.select_and_sort_comments(
                    max_comments=max_comments_per_product,
                    prefer_recent_days=prefer_recent_days,
                    strict_max_age=strict_max_age,
                )
                for item in selected_items:
                    flat_rows.append(
                        item.to_flat_dict(
                            shop_name=record.shop_name,
                            product_name=record.product_name,
                        )
                    )

        prefix = f"{platform_filter}_" if (platform_filter and platform_filter != "all") else ""
        json_file = out_path / f"{prefix}products_reviews.json"
        csv_file = out_path / f"{prefix}absa_reviews_flat.csv"
        jsonl_file = out_path / f"{prefix}absa_reviews_flat.jsonl"

        json_file.write_text(
            json.dumps(nested_products, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        fieldnames = [
            "cmt_id",
            "product_id",
            "platform",
            "shop_name",
            "product_name",
            "review_text",
            "rating_star",
            "variation",
            "timestamp",
            "like_count",
        ]
        with csv_file.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_rows)

        with jsonl_file.open("w", encoding="utf-8") as f:
            for r in flat_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        logger.info(
            f"Đã xuất {len(nested_products)} sản phẩm ({len(flat_rows)} bình luận) ra:\n"
            f"  - Nested JSON: {json_file}\n"
            f"  - Flat CSV   : {csv_file}\n"
            f"  - Flat JSONL : {jsonl_file}"
        )
        return {
            "total_products": len(nested_products),
            "total_comments": len(flat_rows),
            "json_file": str(json_file),
            "csv_file": str(csv_file),
            "jsonl_file": str(jsonl_file),
        }

    def log_captcha_event(
        self,
        platform: str,
        url: str,
        reason: str,
        products_before_captcha: int,
        requests_before_captcha: int,
    ) -> None:
        """Ghi lại mốc số sản phẩm & số request chạy được trước khi gặp CAPTCHA."""
        now_str = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO captcha_events (
                    platform, url, reason, products_before_captcha, requests_before_captcha, occurred_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    platform,
                    url,
                    reason,
                    max(0, int(products_before_captcha)),
                    max(0, int(requests_before_captcha)),
                    now_str,
                ),
            )

    def get_captcha_stats(self) -> Dict[str, Any]:
        """Thống kê số lần gặp CAPTCHA và ngưỡng trung bình số sản phẩm trước khi bị hỏi xác minh."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT platform, url, reason, products_before_captcha, requests_before_captcha, occurred_at
                FROM captcha_events
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
            agg = conn.execute(
                """
                SELECT
                    COUNT(1) AS total_events,
                    AVG(products_before_captcha) AS avg_products,
                    AVG(requests_before_captcha) AS avg_requests
                FROM captcha_events
                """
            ).fetchone()

        return {
            "total_events": int(agg["total_events"] or 0) if agg else 0,
            "avg_products_before_captcha": round(float(agg["avg_products"]), 1) if agg and agg["avg_products"] is not None else None,
            "avg_requests_before_captcha": round(float(agg["avg_requests"]), 1) if agg and agg["avg_requests"] is not None else None,
            "recent_events": [dict(r) for r in rows],
        }

    def get_summary_stats(self) -> Dict[str, Any]:
        """Thống kê tổng quan số sản phẩm, hàng đợi và phân bố từng mức sao."""
        with self._get_conn() as conn:
            total_products = conn.execute("SELECT COUNT(1) FROM products").fetchone()[0]
            total_comments = conn.execute("SELECT COUNT(1) FROM comments").fetchone()[0]
            platform_rows = conn.execute(
                "SELECT platform, COUNT(1) AS cnt FROM comments GROUP BY platform"
            ).fetchall()
            queue_rows = conn.execute(
                "SELECT platform, status, COUNT(1) AS cnt FROM product_queue GROUP BY platform, status"
            ).fetchall()

        return {
            "total_products": total_products,
            "total_comments": total_comments,
            "star_counts": self.get_star_counts(),
            "shopee_star_counts": self.get_star_counts("shopee"),
            "tiktok_star_counts": self.get_star_counts("tiktok"),
            "by_platform": {r["platform"]: r["cnt"] for r in platform_rows},
            "queue": [dict(r) for r in queue_rows],
        }
