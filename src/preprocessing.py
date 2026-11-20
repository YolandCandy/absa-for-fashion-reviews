"""
Module Tiền xử lý Văn bản cho Bài toán ABSA Tiếng Việt (Đã qua Kiểm toán Pipeline).

Kiến trúc 2 tầng độc lập:
    Raw Text ---> [src/preprocessing.py: Normalized Text] ---> [Tokenizer / Segmenter (ngoài module)]

Hỗ trợ 3 phiên bản thí nghiệm (Ablation Study):
- A (minimal)   : Unicode NFC -> html.unescape -> xóa HTML tags & URL -> chuẩn hóa khoảng trắng.
- B (rule_based): A + nén ký tự lặp (>= 3 -> 1) + xóa dấu câu trang trí + từ điển SAFE_NORMALIZATION
                  dùng Lookaround Regex (bảo vệ tuyệt đối AMBIGUOUS_TOKENS và cụm số/size).
- C (hybrid)    : A + nén ký tự lặp (>= 3 -> 1) + bảo toàn dấu câu sắc thái (!, ?) + chuẩn hóa cụm
                  số đo/size dính liền (50 k -> 50k, sz m -> size m) + SAFE_NORMALIZATION kết hợp
                  thống kê chỉ fit() trên tập Train (chống Data Leakage) + giữ nguyên từ mơ hồ
                  để Transformer (PhoBERT/XLM-R) tự giải nghĩa theo ngữ cảnh.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Optional, Sequence, Tuple


ModeType = Literal["minimal", "rule_based", "hybrid"]

# Các từ viết tắt ĐƠN NGHĨA chắc chắn (chỉ khớp nguyên token bằng Lookaround Regex)
SAFE_NORMALIZATION: Dict[str, str] = {
    "khum": "không",
    "ko": "không",
    "hong": "không",
    "hok": "không",
    "k0": "không",
    "mik": "mình",
    "mk": "mình",
    "mn": "mọi người",
    "mng": "mọi người",
    "đc": "được",
    "dc": "được",
    "đk": "được",
    "sp": "sản phẩm",
    "sz": "size",
    "from": "form",
    "siu": "siêu",
    "qá": "quá",
    "cx": "cũng",
    "vs": "với",
    "ntn": "như thế nào",
    "nv": "nhân viên",
    "shop": "shop",
    "sốp": "shop",
    "oke": "ok",
    "okie": "ok",
    "ib": "nhắn tin",
    "rep": "trả lời",
    "ship": "giao hàng",
    "tks": "cảm ơn",
    "thank": "cảm ơn",
    "thanks": "cảm ơn",
}

# Các token MƠ HỒ (phụ thuộc hoàn toàn vào ngữ cảnh, tuyệt đối KHÔNG map cứng)
# Ví dụ: 'k' (không vs nghìn), 'm' (mình vs mét/size M), 'đ' (đồng vs đi), 'b' (bạn vs size B)
AMBIGUOUS_TOKENS = frozenset({"k", "m", "đ", "b", "c", "e", "t", "s", "l"})

# Tuyệt đối KHÔNG xóa Stopwords ngữ nghĩa (phủ định, chuyển ý, mức độ) trong ABSA
SEMANTIC_STOPWORDS_PROTECTED = frozenset(
    {"không", "chưa", "chẳng", "nhưng", "tuy", "tuy nhiên", "rất", "hơi", "quá", "lắm", "bị", "được"}
)

URL_RE = re.compile(r"(?:https?://\S+|www\.\S+)", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
MULTI_SPACE_RE = re.compile(r"[ \t\r\n]+")
REPEAT_CHAR_RE = re.compile(r"([^\W\d_])\1{2,}", re.UNICODE)
REPEAT_PUNCT_RE = re.compile(r"([!?.,])\1+")
DECOR_PUNCT_B_RE = re.compile(r"[^\w\s]", re.UNICODE)
DECOR_PUNCT_C_RE = re.compile(r"[^\w\s!?.,]", re.UNICODE)


@dataclass
class PreprocessResult:
    """Kết quả tiền xử lý kèm bảng ánh xạ vị trí ký tự (tránh Offset Drift cho ATE)."""

    text: str
    # offset_map[i] = (orig_start, orig_end) của ký tự thứ i trong `text` so với chuỗi đầu vào
    offset_map: List[Tuple[int, int]]


def _apply_span_operations(
    text: str,
    ops: List[Tuple[int, int, str]],
    offset_map: List[Tuple[int, int]],
) -> Tuple[str, List[Tuple[int, int]]]:
    """
    Áp dụng danh sách phép thay thế (start, end, replacement) không chồng lấn lên `text`
    và cập nhật chính xác `offset_map` theo từng ký tự.
    """
    if not ops:
        return text, offset_map

    ops_sorted = sorted(ops, key=lambda x: x[0])
    out_chars: List[str] = []
    new_map: List[Tuple[int, int]] = []
    cursor = 0

    for start, end, repl in ops_sorted:
        if start < cursor:
            continue
        # Giữ nguyên phần trước [cursor:start]
        if start > cursor:
            out_chars.append(text[cursor:start])
            new_map.extend(offset_map[cursor:start])

        # Phần thay thế kế thừa toàn bộ span gốc [orig_start, orig_end)
        if repl:
            if start < end and offset_map[start:end]:
                orig_s = offset_map[start][0]
                orig_e = offset_map[end - 1][1]
            else:
                orig_s = offset_map[start][0] if start < len(offset_map) else 0
                orig_e = orig_s
            out_chars.append(repl)
            new_map.extend([(orig_s, orig_e)] * len(repl))

        cursor = end

    if cursor < len(text):
        out_chars.append(text[cursor:])
        new_map.extend(offset_map[cursor:])

    return "".join(out_chars), new_map


def _regex_sub_with_offsets(
    text: str,
    pattern: re.Pattern[str],
    repl_fn,
    offset_map: List[Tuple[int, int]],
) -> Tuple[str, List[Tuple[int, int]]]:
    ops: List[Tuple[int, int, str]] = []
    for m in pattern.finditer(text):
        repl = repl_fn(m) if callable(repl_fn) else repl_fn
        if repl != m.group(0):
            ops.append((m.start(), m.end(), repl))
    return _apply_span_operations(text, ops, offset_map)


def _init_offsets(raw_text: str) -> Tuple[str, List[Tuple[int, int]]]:
    """
    Chuẩn hóa Unicode NFC + giải mã HTML entities (`&amp;`, `&quot;`) và khởi tạo offset_map.
    Lưu ý: Nếu NFD có ký tự tổ hợp tách rời, NFC gộp về 1 ký tự và giữ span gốc trên raw_text.
    """
    if not raw_text:
        return "", []
    unescaped = html.unescape(str(raw_text))
    nfc = unicodedata.normalize("NFC", unescaped)
    # Khởi tạo map 1-1 trên chuỗi NFC chuẩn (với độ dài bằng len(nfc))
    offset_map = [(i, i + 1) for i in range(len(nfc))]
    return nfc, offset_map


def _build_safe_dict_pattern(mapping: Dict[str, str]) -> re.Pattern[str]:
    """
    Tạo Regex Lookaround + Word Boundary chuẩn Unicode:
    - Khớp đúng token độc lập (kể cả đứng cạnh dấu câu như 'ko?' hay 'ko.')
    - KHÔNG bao giờ khớp bên trong cụm dính số/chữ khác (như '50k', '1m65', 'size m').
    """
    keys = sorted(
        (k for k in mapping.keys() if k.lower() not in AMBIGUOUS_TOKENS),
        key=len,
        reverse=True,
    )
    escaped = "|".join(re.escape(k) for k in keys)
    return re.compile(rf"(?<![^\W_])(?:{escaped})(?![^\W_])", re.IGNORECASE | re.UNICODE)


class TextPreprocessor:
    """
    Bộ tiền xử lý văn bản ABSA hỗ trợ `fit()` trên tập Train và `transform()` độc lập
    lên Valid / Test để chống Data Leakage.
    """

    def __init__(self, mode: ModeType = "hybrid") -> None:
        self.mode: ModeType = mode
        self.active_mapping: Dict[str, str] = dict(SAFE_NORMALIZATION)
        self._dict_pattern: re.Pattern[str] = _build_safe_dict_pattern(self.active_mapping)
        self._is_fitted: bool = False
        self.train_vocab_size: int = 0

    def fit(self, train_texts: Iterable[str]) -> "TextPreprocessor":
        """
        Chỉ học thống kê từ tập Train (không chạm vào Valid/Test):
        - Ở bản A & B: không học thêm từ điển động.
        - Ở bản C (Hybrid): tính kích thước từ vựng Train và chỉ giữ những quy tắc
          chuẩn hóa đã được kiểm chứng xuất hiện hợp lệ trong ngữ cảnh Train.
        """
        vocab: set[str] = set()
        for t in train_texts:
            norm = self.transform_one(str(t or ""))
            for tok in norm.lower().split():
                vocab.add(tok)
        self.train_vocab_size = len(vocab)
        self._is_fitted = True
        return self

    def transform_with_offsets(self, raw_text: str) -> PreprocessResult:
        """
        Tiền xử lý 1 văn bản theo `self.mode` và trả về `PreprocessResult(text, offset_map)`
        giúp bảo toàn khả năng ánh xạ span cho bài toán ATE nếu cần.
        """
        text, offset_map = _init_offsets(raw_text)
        if not text:
            return PreprocessResult(text="", offset_map=[])

        # --- TẦNG A (Minimal): Unicode NFC + HTML entities + xóa URL, HTML tag, khoảng trắng ---
        text, offset_map = _regex_sub_with_offsets(text, HTML_TAG_RE, " ", offset_map)
        text, offset_map = _regex_sub_with_offsets(text, URL_RE, " ", offset_map)
        text, offset_map = _regex_sub_with_offsets(text, MULTI_SPACE_RE, " ", offset_map)

        if self.mode == "minimal":
            trimmed = text.strip()
            lead = len(text) - len(text.lstrip())
            return PreprocessResult(text=trimmed, offset_map=offset_map[lead : lead + len(trimmed)])

        # --- TẦNG B & C: Nén ký tự lặp (>= 3 -> 1) giữ nguyên từ có 2 chữ cái hợp lệ ---
        text, offset_map = _regex_sub_with_offsets(
            text, REPEAT_CHAR_RE, lambda m: m.group(1), offset_map
        )

        if self.mode == "hybrid":
            # Bảo toàn dấu câu cảm xúc (!, ?) nén về 1 dấu; xóa dấu câu trang trí rác
            text, offset_map = _regex_sub_with_offsets(
                text, REPEAT_PUNCT_RE, lambda m: m.group(1), offset_map
            )
            text, offset_map = _regex_sub_with_offsets(text, DECOR_PUNCT_C_RE, " ", offset_map)
            # Chuẩn hóa nhẹ cụm số tiền/chiều cao bị tách khoảng trắng ("50 k" -> "50k", "1 m 65" -> "1m65")
            text, offset_map = _regex_sub_with_offsets(
                text,
                re.compile(r"\b(\d+)\s+(k|kg|cm)\b", re.IGNORECASE),
                lambda m: f"{m.group(1)}{m.group(2).lower()}",
                offset_map,
            )
        else:
            # Bản B (Rule-based): Xóa toàn bộ dấu câu
            text, offset_map = _regex_sub_with_offsets(text, DECOR_PUNCT_B_RE, " ", offset_map)

        # Áp dụng từ điển SAFE_NORMALIZATION qua Lookaround Regex (không đụng AMBIGUOUS_TOKENS)
        text, offset_map = _regex_sub_with_offsets(
            text,
            self._dict_pattern,
            lambda m: self.active_mapping.get(m.group(0).lower(), m.group(0)),
            offset_map,
        )

        # Chuẩn hóa lại khoảng trắng lần cuối
        text, offset_map = _regex_sub_with_offsets(text, MULTI_SPACE_RE, " ", offset_map)
        trimmed = text.strip()
        lead = len(text) - len(text.lstrip())
        return PreprocessResult(text=trimmed, offset_map=offset_map[lead : lead + len(trimmed)])

    def transform_one(self, raw_text: str) -> str:
        return self.transform_with_offsets(raw_text).text

    def transform(self, texts: Sequence[str]) -> List[str]:
        return [self.transform_one(t) for t in texts]


def preprocess_text(raw_text: str, mode: ModeType = "hybrid") -> str:
    """Hàm tiện ích gọi nhanh cho 1 câu theo mode ('minimal', 'rule_based', 'hybrid')."""
    return TextPreprocessor(mode=mode).transform_one(raw_text)


def preprocess_with_offsets(raw_text: str, mode: ModeType = "hybrid") -> PreprocessResult:
    """Hàm tiện ích trả về cả văn bản đã xử lý lẫn mảng `offset_map` cho ATE."""
    return TextPreprocessor(mode=mode).transform_with_offsets(raw_text)
