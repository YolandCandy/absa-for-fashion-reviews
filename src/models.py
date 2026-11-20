"""
Mô hình dữ liệu (Pydantic Models) cho Sản phẩm và Bình luận phục vụ bài toán ABSA.
Hỗ trợ:
- Chuẩn hóa thời gian (timestamp) về định dạng ngày tháng năm dễ đọc & unix timestamp.
- Ưu tiên các bình luận mới hơn (trong vòng 1 năm trở lại đây trước, không bắt buộc cắt cứng).
- Xuất đúng định dạng JSON lồng nhau tiếng Việt theo yêu cầu đề bài và dạng phẳng (flat) cho gán nhãn ABSA.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


VN_TZ = timezone(timedelta(hours=7))


def parse_timestamp_to_unix_and_str(raw_ts: Any) -> tuple[int, str]:
    """
    Chuyển đổi nhiều định dạng thời gian từ Shopee / TikTok Shop (unix seconds, unix ms, ISO string,
    hoặc chuỗi ngày tháng) về (unix_timestamp_seconds, 'YYYY-MM-DD HH:MM:SS').
    """
    now_dt = datetime.now(VN_TZ)
    if raw_ts is None or raw_ts == "":
        return int(now_dt.timestamp()), now_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Nếu là số nguyên hoặc chuỗi số (Unix seconds hoặc milliseconds)
    if isinstance(raw_ts, (int, float)) or (isinstance(raw_ts, str) and raw_ts.strip().isdigit()):
        val = int(float(raw_ts))
        # Nếu tính bằng mili-giây (13 chữ số)
        if val > 10_000_000_000:
            val = val // 1000
        if val <= 0:
            return int(now_dt.timestamp()), now_dt.strftime("%Y-%m-%d %H:%M:%S")
        dt = datetime.fromtimestamp(val, tz=VN_TZ)
        return val, dt.strftime("%Y-%m-%d %H:%M:%S")

    raw_str = str(raw_ts).strip()

    # Thử parse một số định dạng chuỗi phổ biến trên Shopee / TikTok Shop DOM
    # Ví dụ Shopee DOM: "2026-08-15 14:22 | Phân loại hàng: ..."
    match_iso = re.search(r"(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)", raw_str)
    if match_iso:
        date_part = match_iso.group(1).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_part, fmt).replace(tzinfo=VN_TZ)
                return int(dt.timestamp()), dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

    # Định dạng DD/MM/YYYY hoặc DD-MM-YYYY
    match_dmy = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})", raw_str)
    if match_dmy:
        date_part = match_dmy.group(1).replace("-", "/")
        try:
            dt = datetime.strptime(date_part, "%d/%m/%Y").replace(tzinfo=VN_TZ)
            return int(dt.timestamp()), dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    return int(now_dt.timestamp()), raw_str


def clean_review_text(text: Optional[str]) -> str:
    """Làm sạch khoảng trắng thừa nhưng giữ nguyên dấu câu, biểu cảm tiếng Việt cho ABSA."""
    if not text:
        return ""
    cleaned = re.sub(r"\r\n|\r", "\n", str(text))
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def is_noise_or_spam_review(text: Optional[str]) -> bool:
    """
    Bộ lọc Heuristic loại bỏ các bình luận nhiễu trước khi lưu vào dataset ABSA:
    - Chứa đường dẫn URL quảng cáo (http://, https://, www.)
    - Văn mẫu AI / ChatGPT copy-paste ("~200 ký tự", "nếu bạn cần", "dưới đây là đoạn")
    - Tin nhắn hệ thống / thông báo giả mạo ("[TB]", "tgdv.vtscloud.vn")
    - Bình luận quá ngắn (< 15 ký tự) chỉ mang tính nhận xu mà không chứa nhận xét thực tế
    - Chuỗi ký tự lặp vô nghĩa (ví dụ: "aaaaaaaa", "jizio.fhhb")
    """
    if not text:
        return True
    t = text.strip()
    low = t.lower()

    # 1. Chứa URL hoặc tin nhắn thông báo spam
    if re.search(r"https?://|www\.", low):
        return True
    if low.startswith("[tb]") or "@huyền" in low:
        return True

    # 2. Văn mẫu AI / hướng dẫn viết review
    ai_markers = (
        "ký tự",
        "nếu bạn cần",
        "dưới đây là đoạn",
        "giọng văn học sinh",
        "nhấn vào đây",
    )
    if any(m in low for m in ai_markers):
        return True

    # 3. Quá ngắn chỉ để nhận xu (không có nhận xét về chất lượng/vải/form/size/giao hàng)
    if len(t) < 15:
        generic_only = (
            "nhận xu",
            "hình ảnh mang tính chất",
            "tạm ổn",
            "ok",
            "oke",
            "tốt",
            "đẹp",
        )
        if any(low == g or low.startswith(g + " ") for g in generic_only) and len(t.split()) <= 4:
            return True

    # 4. Chuỗi ký tự lặp vô nghĩa kéo dài (vd: jjjjjjjj)
    if re.search(r"(.)\1{5,}", low):
        return True

    return False


class CommentItem(BaseModel):
    """Mô hình một bình luận (đánh giá) của khách hàng."""

    cmt_id: str = Field(..., description="ID duy nhất của bình luận")
    product_id: str = Field(..., description="ID sản phẩm")
    platform: str = Field(..., description="shopee hoặc tiktok")
    review_text: str = Field(..., description="Nội dung văn bản của bình luận")
    rating_star: int = Field(..., ge=1, le=5, description="Số sao đánh giá (1 - 5)")
    variation: str = Field(default="", description="Phân loại hàng (màu sắc, kích cỡ...)")
    timestamp: str = Field(..., description="Thời gian định dạng YYYY-MM-DD HH:MM:SS")
    unix_timestamp: int = Field(..., description="Unix timestamp (giây) để sắp xếp & lọc độ mới")
    like_count: int = Field(default=0, ge=0, description="Số lượt thích / hữu ích")

    @field_validator("review_text", mode="before")
    @classmethod
    def _clean_text(cls, v: Any) -> str:
        return clean_review_text(str(v) if v is not None else "")

    @field_validator("like_count", mode="before")
    @classmethod
    def _parse_like(cls, v: Any) -> int:
        if v is None or v == "":
            return 0
        if isinstance(v, (int, float)):
            return max(0, int(v))
        digits = re.sub(r"[^\d]", "", str(v))
        return int(digits) if digits else 0

    def is_within_days(self, days: int = 365, reference_unix: Optional[int] = None) -> bool:
        """Kiểm tra bình luận có nằm trong khoảng `days` ngày trở lại đây không."""
        ref = reference_unix or int(datetime.now(VN_TZ).timestamp())
        cutoff = ref - (days * 86400)
        return self.unix_timestamp >= cutoff

    def to_nested_cmt_entry(self) -> Dict[str, Any]:
        """Xuất ra cấu trúc con bên trong trường 'cmt' của JSON sản phẩm."""
        return {
            "text": self.review_text,
            "rating_star": self.rating_star,
            "variation": self.variation,
            "thời gian": self.timestamp,
            "số like": self.like_count,
        }

    def to_flat_dict(
        self,
        shop_name: str = "",
        product_name: str = "",
    ) -> Dict[str, Any]:
        """Xuất ra dòng dữ liệu phẳng (phục vụ CSV / JSONL cho gán nhãn & huấn luyện ABSA)."""
        return {
            "cmt_id": self.cmt_id,
            "product_id": self.product_id,
            "platform": self.platform,
            "shop_name": shop_name,
            "product_name": product_name,
            "review_text": self.review_text,
            "rating_star": self.rating_star,
            "variation": self.variation,
            "timestamp": self.timestamp,
            "like_count": self.like_count,
        }


class ProductRecord(BaseModel):
    """Mô hình dữ liệu tổng hợp của một sản phẩm và danh sách bình luận đi kèm."""

    product_id: str = Field(..., description="ID sản phẩm")
    platform: str = Field(..., description="shopee hoặc tiktok")
    shop_id: str = Field(default="", description="ID cửa hàng (nếu có)")
    shop_name: str = Field(default="", description="Tên cửa hàng")
    product_name: str = Field(default="", description="Tên sản phẩm")
    product_description: str = Field(default="", description="Thông tin thuộc tính và mô tả sản phẩm")
    product_url: str = Field(default="", description="URL sản phẩm")
    comments: Dict[str, CommentItem] = Field(
        default_factory=dict,
        description="Dictionary ánh xạ từ cmt_id -> CommentItem",
    )

    def select_and_sort_comments(
        self,
        max_comments: int = 100,
        prefer_recent_days: int = 365,
        strict_max_age: bool = False,
        reference_unix: Optional[int] = None,
        max_per_star: Optional[int] = None,
    ) -> List[CommentItem]:
        """
        Sắp xếp và chọn tối đa `max_comments` bình luận cho sản phẩm theo nguyên tắc:
        1. Ưu tiên các bình luận mới hơn (trong vòng `prefer_recent_days` ngày trở lại đây) lên đầu.
        2. Nếu `strict_max_age=False` (mặc định theo yêu cầu), khi bình luận trong 1 năm chưa đủ
           `max_comments`, vẫn giữ các bình luận cũ hơn (sắp xếp giảm dần theo thời gian mới -> cũ).
        3. Nếu `strict_max_age=True`, chỉ lấy bình luận trong vòng `prefer_recent_days`.
        4. Nếu `max_per_star` được chỉ định (ví dụ 20), giới hạn tối đa `max_per_star` bình luận
           cho mỗi mức sao (1★..5★) trên sản phẩm này để tránh 1 mức sao chiếm toàn bộ sản phẩm.
        """
        all_cmts = list(self.comments.values())
        ref = reference_unix or int(datetime.now(VN_TZ).timestamp())

        recent_cmts = [c for c in all_cmts if c.is_within_days(prefer_recent_days, ref)]
        older_cmts = [c for c in all_cmts if not c.is_within_days(prefer_recent_days, ref)]

        # Sắp xếp mới nhất lên trước trong từng nhóm
        recent_cmts.sort(key=lambda c: c.unix_timestamp, reverse=True)
        older_cmts.sort(key=lambda c: c.unix_timestamp, reverse=True)

        ordered = recent_cmts if strict_max_age else (recent_cmts + older_cmts)

        if max_per_star is not None and max_per_star > 0:
            star_buckets: Dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
            balanced: List[CommentItem] = []
            for c in ordered:
                if star_buckets.get(c.rating_star, 0) < max_per_star:
                    balanced.append(c)
                    star_buckets[c.rating_star] = star_buckets.get(c.rating_star, 0) + 1
                    if len(balanced) >= max_comments:
                        break
            return balanced

        return ordered[:max_comments]

    def to_vietnamese_json(
        self,
        max_comments: int = 100,
        prefer_recent_days: int = 365,
        strict_max_age: bool = False,
        reference_unix: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Xuất ra đúng định dạng JSON tiếng Việt theo mẫu của người dùng:
        {
            "product_id": ...,
            "tên cửa hàng": ...,
            "tên sản phẩm": ...,
            "thông tin và mô tả sp": ...,
            "số cmt": ...,
            "cmt": {
                "cmt_id": {
                    "text": ...,
                    "rating_star": ...,
                    "variation": ...,
                    "thời gian": ...,
                    "số like": ...
                }
            }
        }
        """
        selected_list = self.select_and_sort_comments(
            max_comments=max_comments,
            prefer_recent_days=prefer_recent_days,
            strict_max_age=strict_max_age,
            reference_unix=reference_unix,
        )
        cmt_dict = {item.cmt_id: item.to_nested_cmt_entry() for item in selected_list}
        return {
            "product_id": self.product_id,
            "platform": self.platform,
            "tên cửa hàng": self.shop_name,
            "tên sản phẩm": self.product_name,
            "thông tin và mô tả sp": self.product_description,
            "số cmt": len(cmt_dict),
            "cmt": cmt_dict,
        }
