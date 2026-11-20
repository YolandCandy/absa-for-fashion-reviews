"""
Script Thực nghiệm So sánh 3 Phiên bản Tiền xử lý (A: Minimal, B: Rule-based, C: Hybrid)
tuân thủ quy trình chống Data Leakage:
  Khử trùng lặp -> Chia Train/Valid (85/15, random_state=42) -> Fit chỉ trên Train -> Transform Valid & Test.

Chạy lệnh:
    python scripts/run_preprocessing.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from sklearn.model_selection import train_test_split

from src.preprocessing import SAFE_NORMALIZATION, TextPreprocessor


ASPECT_COLS = ["Chất liệu", "Kiểu dáng", "Kích cỡ", "Giá cả", "Dịch vụ"]
TEENCODE_CHECK_RE = re.compile(
    r"(?<![^\W_])(?:" + "|".join(re.escape(k) for k in SAFE_NORMALIZATION.keys() if k not in {"shop", "ok", "thank", "thanks"}) + r")(?![^\W_])",
    re.IGNORECASE | re.UNICODE,
)


def compute_version_metrics(raw_series: pd.Series, proc_series: pd.Series) -> Dict[str, float]:
    raw_list = raw_series.fillna("").astype(str).tolist()
    proc_list = proc_series.fillna("").astype(str).tolist()

    changed = sum(1 for r, p in zip(raw_list, proc_list) if r.strip() != p.strip())
    vocab = set()
    teencode_hits = 0
    for p in proc_list:
        for tok in p.lower().split():
            vocab.add(tok)
        if TEENCODE_CHECK_RE.search(p):
            teencode_hits += 1

    total = max(1, len(proc_list))
    return {
        "changed_pct": round(changed / total * 100, 1),
        "vocab_size": len(vocab),
        "remaining_teencode_pct": round(teencode_hits / total * 100, 2),
    }


def main() -> None:
    hf_path = Path("data/external/FashionReviews.csv")
    sp_path = Path("data/output/shopee_absa_reviews_flat.csv")
    out_dir = Path("data/processed")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Nạp FashionReviews, xử lý NaN & khử trùng lặp TRƯỚC khi chia Train/Valid
    df_hf = pd.read_csv(hf_path)
    for col in ASPECT_COLS:
        df_hf[col] = df_hf[col].fillna(0).astype(int)
    df_hf["Nội dung review"] = df_hf["Nội dung review"].fillna("").astype(str)
    df_hf = df_hf.drop_duplicates(subset=["Nội dung review"]).reset_index(drop=True)

    # 2. Chia Train / Valid (85% / 15%) với random_state=42 cố định TRƯỚC khi chạy Fit/Transform
    train_df, valid_df = train_test_split(
        df_hf,
        test_size=0.15,
        random_state=42,
        shuffle=True,
    )
    train_df = train_df.reset_index(drop=True)
    valid_df = valid_df.reset_index(drop=True)

    test_df = pd.read_csv(sp_path)
    test_df["review_text"] = test_df["review_text"].fillna("").astype(str)

    # 3. Fit chỉ trên Train -> Transform độc lập lên Train, Valid, Test cho cả 3 phiên bản A, B, C
    modes = [
        ("minimal", "text_A_minimal"),
        ("rule_based", "text_B_rule"),
        ("hybrid", "text_C_hybrid"),
    ]
    summary_rows: List[Dict[str, object]] = []

    for mode, col_name in modes:
        prep = TextPreprocessor(mode=mode)
        prep.fit(train_df["Nội dung review"].tolist())

        train_df[col_name] = prep.transform(train_df["Nội dung review"].tolist())
        valid_df[col_name] = prep.transform(valid_df["Nội dung review"].tolist())
        test_df[col_name] = prep.transform(test_df["review_text"].tolist())

        m_val = compute_version_metrics(valid_df["Nội dung review"], valid_df[col_name])
        m_test = compute_version_metrics(test_df["review_text"], test_df[col_name])

        summary_rows.append(
            {
                "Phiên bản": col_name,
                "Valid Vocab": m_val["vocab_size"],
                "Valid Changed (%)": m_val["changed_pct"],
                "Valid Teencode sót (%)": m_val["remaining_teencode_pct"],
                "Test Vocab": m_test["vocab_size"],
                "Test Changed (%)": m_test["changed_pct"],
                "Test Teencode sót (%)": m_test["remaining_teencode_pct"],
            }
        )

    # 4. Xuất dữ liệu đã tiền xử lý phục vụ thí nghiệm A/B Testing
    train_out = out_dir / "hf_train.csv"
    valid_out = out_dir / "hf_valid.csv"
    test_out = out_dir / "shopee_test_preprocessed.csv"

    train_df.to_csv(train_out, index=False, encoding="utf-8-sig")
    valid_df.to_csv(valid_out, index=False, encoding="utf-8-sig")
    test_df.to_csv(test_out, index=False, encoding="utf-8-sig")

    print("\n=== SO SÁNH 3 PHIÊN BẢN TIỀN XỬ LÝ (CHỐNG DATA LEAKAGE) ===")
    print(f"Train: {len(train_df):,} dòng | Valid: {len(valid_df):,} dòng | Shopee Test: {len(test_df):,} dòng\n")
    comp_df = pd.DataFrame(summary_rows)
    print(comp_df.to_string(index=False))

    print("\n=== VÍ DỤ ĐỐI CHIẾU TRÊN TẬP TEST SHOPEE ===")
    for idx in [0, 1, 8, 14]:
        if idx < len(test_df):
            r = test_df.iloc[idx]
            print(f"\n[Mẫu #{idx}]")
            print(f"  Raw : {r['review_text'][:110]}")
            print(f"  A   : {r['text_A_minimal'][:110]}")
            print(f"  B   : {r['text_B_rule'][:110]}")
            print(f"  C   : {r['text_C_hybrid'][:110]}")

    print(f"\nDa luu tai: {out_dir}")


if __name__ == "__main__":
    main()
