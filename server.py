"""
Local Python Receiver Server (FastAPI) kết nối với Chrome Extension (Manifest V3).
Chạy tại http://127.0.0.1:8765 để:
1. Trả về danh sách mức sao đang thiếu Quota & kiểm tra giới hạn SP/Shop (`GET /api/needed-stars`).
2. Nhận dữ liệu sản phẩm + bình luận từ Chrome Extension (`POST /api/ingest`),
   áp dụng bộ lọc nhiễu ABSA, trần bình luận/mức sao/SP, lưu SQLite và xuất JSON/CSV.
3. Cung cấp số liệu thống kê cho Popup của Extension (`GET /api/stats`).
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field

from src.models import (
    CommentItem,
    ProductRecord,
    clean_review_text,
    is_noise_or_spam_review,
    parse_timestamp_to_unix_and_str,
)
from src.storage import StorageManager


UI_SHOP_BLACKLIST = {
    "chat now",
    "chat ngay",
    "shop vouchers",
    "xem shop",
    "view shop",
    "theo dõi",
    "follow",
    "đang hoạt động",
    "online",
    "tỉ lệ phản hồi",
    "tỷ lệ phản hồi",
    "response rate",
}


def sanitize_shop_name(raw_name: str) -> str:
    """Loại bỏ các nhãn nút bấm UI bị bắt nhầm làm tên cửa hàng (vd: 'Chat Now')."""
    cleaned = clean_review_text(raw_name)
    if not cleaned:
        return ""
    for line in cleaned.splitlines():
        cand = line.strip()
        if cand and cand.lower() not in UI_SHOP_BLACKLIST:
            return cand
    return ""


class RawExtensionComment(BaseModel):
    cmt_id: str
    text: str
    rating_star: int = Field(..., ge=1, le=5)
    variation: str = ""
    ctime: Any = None
    like_count: int = 0


class IngestProductPayload(BaseModel):
    product_id: str
    platform: str = "shopee"
    shop_id: str = ""
    shop_name: str = ""
    product_name: str = ""
    product_description: str = ""
    product_url: str = ""
    keyword: str = ""
    comments: List[RawExtensionComment] = Field(default_factory=list)


class CaptchaReportPayload(BaseModel):
    platform: str = "shopee"
    url: str = ""
    reason: str = "Phát hiện CAPTCHA / Yêu cầu xác minh"
    products_before_captcha: int = 0
    requests_before_captcha: int = 0


class FilterUrlsItem(BaseModel):
    product_id: str
    url: str


class FilterUrlsPayload(BaseModel):
    items: List[FilterUrlsItem] = Field(default_factory=list)


class MarkSkippedPayload(BaseModel):
    product_id: str
    url: str = ""
    platform: str = "shopee"
    reason: str = "skipped_low_reviews"


def _trigger_windows_captcha_alert(payload: CaptchaReportPayload) -> None:
    """Phát chuỗi âm thanh Beep & bật hộp thoại MessageBox nổi trên cùng màn hình Windows (luồng nền)."""
    def _worker() -> None:
        try:
            if sys.platform == "win32":
                import winsound

                for freq, dur in [(950, 260), (1300, 260), (950, 260), (1450, 400)]:
                    winsound.Beep(freq, dur)
                    time.sleep(0.07)

                MB_OK = 0x00000000
                MB_ICONWARNING = 0x00000030
                MB_TOPMOST = 0x00040000
                MB_SETFOREGROUND = 0x00010000
                msg = (
                    f"Extension phát hiện CAPTCHA / Xác minh trên {payload.platform.upper()}!\n\n"
                    f"Lý do: {payload.reason}\n"
                    f"Đã chạy trước khi gặp CAPTCHA: {payload.products_before_captcha} SP "
                    f"({payload.requests_before_captcha} requests).\n\n"
                    "Extension đã TỰ ĐỘNG TẠM DỪNG.\n"
                    "Hãy giải CAPTCHA trên cửa sổ Chrome rồi bấm Bắt đầu lại!"
                )
                ctypes.windll.user32.MessageBoxW(
                    0,
                    msg,
                    f"⚠️ CẢNH BÁO CAPTCHA - {payload.platform.upper()}",
                    MB_OK | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND,
                )
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def load_yaml_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def create_app(
    config: Optional[Dict[str, Any]] = None,
    storage: Optional[StorageManager] = None,
    enable_gui_alerts: bool = True,
) -> FastAPI:
    cfg = config if config is not None else load_yaml_config("config.yaml")
    db_path = cfg.get("project", {}).get("checkpoint_db", "data/checkpoint.db")
    store = storage if storage is not None else StorageManager(db_path=db_path)

    app = FastAPI(title="ABSA Local Data Receiver", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/needed-stars")
    def needed_stars(
        product_id: str = "",
        shop_id: str = "",
        stars: str = "",
    ) -> Dict[str, Any]:
        rb_cfg = cfg.get("rating_balance", {})
        lim_cfg = cfg.get("product_review_limits", {})

        target_stars: List[int] = rb_cfg.get("target_stars", [1, 2, 3, 4, 5])
        if stars.strip():
            parsed = [int(x.strip()) for x in stars.split(",") if x.strip().isdigit() and 1 <= int(x.strip()) <= 5]
            if parsed:
                target_stars = parsed

        quota_per_star = rb_cfg.get("quota_per_star", {1: 3000, 2: 3000, 3: 3000, 4: 3000, 5: 3000})
        priority_order = rb_cfg.get("star_priority_order", [3, 2, 1, 4, 5])

        max_per_product = int(lim_cfg.get("max_comments_per_product", 100))
        max_per_star = int(lim_cfg.get("max_per_star_per_product", 20))
        max_products_per_shop = int(lim_cfg.get("max_products_per_shop", 2))

        already_crawled = (
            (store.is_product_crawled(product_id) or store.is_product_skipped(product_id))
            if product_id
            else False
        )
        shop_count = store.count_products_by_shop(shop_id) if shop_id else 0
        shop_limit_reached = bool(shop_id and max_products_per_shop > 0 and shop_count >= max_products_per_shop)

        needed = store.get_needed_stars(
            target_stars=target_stars,
            quota_per_star=quota_per_star,
            priority_order=priority_order,
        )
        return {
            "already_crawled": already_crawled,
            "shop_limit_reached": shop_limit_reached,
            "shop_product_count": shop_count,
            "max_products_per_shop": max_products_per_shop,
            "needed_stars": needed,
            "max_comments_per_product": max_per_product,
            "max_per_star_per_product": max_per_star,
            "star_counts": store.get_star_counts(),
        }

    @app.post("/api/ingest")
    def ingest_product(payload: IngestProductPayload) -> Dict[str, Any]:
        lim_cfg = cfg.get("product_review_limits", {})
        time_cfg = cfg.get("time_filter", {})
        proj_cfg = cfg.get("project", {})

        min_text_len = int(lim_cfg.get("min_text_length", 5))
        enable_noise_filter = bool(lim_cfg.get("enable_noise_filter", True))
        max_per_product = int(lim_cfg.get("max_comments_per_product", 100))
        max_per_star = int(lim_cfg.get("max_per_star_per_product", 20))
        prefer_recent_days = int(time_cfg.get("prefer_recent_days", 365))
        strict_max_age = bool(time_cfg.get("strict_max_age", False))

        out_dir = proj_cfg.get("output_dir", "data/output")
        by_product_dir = proj_cfg.get("by_product_dir", "data/output/by_product")

        filtered_noise = 0
        comments_dict: Dict[str, CommentItem] = {}

        for raw in payload.comments:
            txt = clean_review_text(raw.text)
            if len(txt) < min_text_len:
                continue
            if enable_noise_filter and is_noise_or_spam_review(txt):
                filtered_noise += 1
                continue
            if raw.cmt_id in comments_dict or store.is_comment_exists(raw.cmt_id):
                continue

            unix_ts, fmt_ts = parse_timestamp_to_unix_and_str(raw.ctime)
            comments_dict[raw.cmt_id] = CommentItem(
                cmt_id=raw.cmt_id,
                product_id=payload.product_id,
                platform=payload.platform,
                review_text=txt,
                rating_star=raw.rating_star,
                variation=raw.variation,
                timestamp=fmt_ts,
                unix_timestamp=unix_ts,
                like_count=raw.like_count,
            )

        record = ProductRecord(
            product_id=payload.product_id,
            platform=payload.platform,
            shop_id=payload.shop_id,
            shop_name=sanitize_shop_name(payload.shop_name),
            product_name=clean_review_text(payload.product_name),
            product_description=clean_review_text(payload.product_description),
            product_url=payload.product_url,
            comments=comments_dict,
        )

        saved_count = 0
        if len(record.comments) > 0:
            saved_count = store.save_product_record(
                record=record,
                by_product_dir=by_product_dir,
                max_comments=max_per_product,
                prefer_recent_days=prefer_recent_days,
                strict_max_age=strict_max_age,
                max_per_star=max_per_star if max_per_star > 0 else None,
            )
            if payload.product_url:
                store.enqueue_urls(
                    [payload.product_url],
                    platform=payload.platform,
                    keyword=payload.keyword or "extension",
                    product_ids={payload.product_url: payload.product_id},
                )
                store.mark_url_status(
                    payload.product_url,
                    status="crawled",
                    product_id=payload.product_id,
                )
            store.export_datasets(
                output_dir=out_dir,
                max_comments_per_product=max_per_product,
                prefer_recent_days=prefer_recent_days,
                strict_max_age=strict_max_age,
                platform_filter=payload.platform,
            )

        logger.success(
            f"[SERVER] Đã nhận SP '{record.product_name[:40]}' ({record.product_id}): "
            f"+{saved_count} cmt hợp lệ (đã lọc bỏ {filtered_noise} cmt spam/rác)."
        )
        return {
            "status": "ok",
            "product_id": record.product_id,
            "saved_comments": saved_count,
            "filtered_noise": filtered_noise,
            "stats": store.get_summary_stats(),
        }

    @app.post("/api/filter-urls")
    def filter_urls(payload: FilterUrlsPayload) -> Dict[str, Any]:
        raw_items = [{"product_id": i.product_id, "url": i.url} for i in payload.items]
        filtered = store.filter_unvisited_products(raw_items)
        return {
            "total_input": len(raw_items),
            "total_valid": len(filtered),
            "items": filtered,
        }

    @app.post("/api/mark-skipped")
    def mark_skipped(payload: MarkSkippedPayload) -> Dict[str, Any]:
        store.mark_product_skipped(
            product_id=payload.product_id,
            url=payload.url,
            platform=payload.platform,
            reason=payload.reason,
        )
        return {"status": "skipped_recorded", "product_id": payload.product_id}

    @app.post("/api/report-captcha")
    def report_captcha(payload: CaptchaReportPayload) -> Dict[str, Any]:
        store.log_captcha_event(
            platform=payload.platform,
            url=payload.url,
            reason=payload.reason,
            products_before_captcha=payload.products_before_captcha,
            requests_before_captcha=payload.requests_before_captcha,
        )
        logger.error(
            f"\n{'=' * 72}\n"
            f"[CAPTCHA DETECTED - {payload.platform.upper()}] {payload.reason}\n"
            f"Ngưỡng đạt được trước CAPTCHA: {payload.products_before_captcha} SP / "
            f"{payload.requests_before_captcha} requests\n"
            f"URL: {payload.url}\n"
            f"{'=' * 72}"
        )
        if enable_gui_alerts:
            _trigger_windows_captcha_alert(payload)

        return {
            "status": "recorded",
            "captcha_stats": store.get_captcha_stats(),
        }

    @app.get("/api/stats")
    def get_stats() -> Dict[str, Any]:
        stats = store.get_summary_stats()
        stats["quota_per_star"] = cfg.get("rating_balance", {}).get(
            "quota_per_star", {1: 3000, 2: 3000, 3: 3000, 4: 3000, 5: 3000}
        )
        stats["captcha_stats"] = store.get_captcha_stats()
        return stats

    return app


app = create_app()


if __name__ == "__main__":
    cfg = load_yaml_config("config.yaml")
    srv_cfg = cfg.get("server", {})
    host = str(srv_cfg.get("host", "127.0.0.1"))
    port = int(srv_cfg.get("port", 8765))
    logger.info(f"Khởi chạy Local Receiver Server tại http://{host}:{port} ...")
    uvicorn.run(app, host=host, port=port, log_level="info")
