"""
Script Phân tích Khám phá Dữ liệu (EDA) & Trực quan hóa Sự mất cân bằng
giữa các Sản phẩm và giữa các Keyword / Nhóm loại hàng Thời trang nữ.

Chạy lệnh:
    python scripts/eda_report.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


# Cấu hình font hiển thị tiếng Việt chuẩn trên Windows
plt.rcParams["font.sans-serif"] = ["Segoe UI", "DejaVu Sans", "Arial"]
plt.rcParams["axes.unicode_minus"] = False
sns.set_theme(style="whitegrid", font="Segoe UI")


ASPECT_PATTERNS: Dict[str, str] = {
    "Chất liệu / Vải": r"\b(?:vải|chất|cotton|lụa|len|mỏng|dày|mát|nóng|co giãn|nhăn|xù)\b",
    "Form dáng / Size": r"\b(?:form|from|dáng|size|sz|rộng|chật|ôm|vừa|ngắn|dài|kg|m\d)\b",
    "Màu sắc": r"\b(?:màu|trắng|đen|đỏ|xanh|be|kem|hồng|nâu|ghi|sáng|tối|y hình)\b",
    "Đường may": r"\b(?:may|đường chỉ|chỉ thừa|vắt sổ|cúc|khóa|rách|lỗi)\b",
    "Giá cả": r"\b(?:giá|rẻ|đắt|tiền|sale|seo)\b",
    "Giao hàng / Shop": r"\b(?:giao|ship|vận chuyển|đóng gói|gói|nhận hàng|shop|sốp|tư vấn|đổi trả)\b",
}


def classify_keyword_category(product_name: str) -> str:
    """Phân loại sản phẩm vào nhóm Keyword loại hàng Thời trang nữ."""
    n = str(product_name or "").lower()
    if any(k in n for k in ["váy", "đầm"]):
        return "Váy / Đầm / Chân váy"
    if any(k in n for k in ["quần", "jean", "short", "legging"]):
        return "Quần nữ"
    if any(k in n for k in ["khoác", "blazer", "cardigan", "hoodie", "vest"]):
        return "Áo khoác / Blazer / Cardigan"
    if any(k in n for k in ["đồ bộ", "pijama", "mặc nhà", "set"]):
        return "Đồ bộ / Set"
    if any(k in n for k in ["áo"]):
        return "Áo (Thun / Sơ mi / Kiểu)"
    return "Khác"


def shorten_label(text: str, max_len: int = 36) -> str:
    t = str(text or "").strip()
    return t if len(t) <= max_len else t[: max_len - 3] + "..."


def plot_01_comments_per_product(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """Đồ thị 1: Phân bố số lượng bình luận trên từng sản phẩm & ngưỡng mục tiêu 60 cmt/SP."""
    prod_df = (
        df.groupby(["product_id", "product_name", "sub_cat"], as_index=False)
        .agg(cmt_count=("cmt_id", "count"))
        .sort_values("cmt_count", ascending=True)
    )

    colors = [
        "#16a34a" if c >= 60 else ("#f59e0b" if c >= 30 else "#ef4444")
        for c in prod_df["cmt_count"]
    ]
    labels = [
        f"{shorten_label(n, 34)} ({pid.split('_')[-1][-4:]})"
        for pid, n in zip(prod_df["product_id"], prod_df["product_name"])
    ]

    fig_h = max(8.5, len(prod_df) * 0.27)
    fig, ax = plt.subplots(figsize=(12, fig_h))
    bars = ax.barh(labels, prod_df["cmt_count"], color=colors, edgecolor="none", height=0.72)

    ax.axvline(60, color="#15803d", linestyle="--", linewidth=1.6, label="Ngưỡng mục tiêu (60 cmt/SP)")
    ax.axvline(30, color="#d97706", linestyle=":", linewidth=1.5, label="Ngưỡng tối thiểu (30 cmt/SP)")

    for bar, val in zip(bars, prod_df["cmt_count"]):
        ax.text(val + 1.0, bar.get_y() + bar.get_height() / 2, str(val), va="center", fontsize=8.5)

    ax.set_title(
        f"Mất cân bằng Số lượng Bình luận giữa {len(prod_df)} Sản phẩm (Tổng: {len(df):,} cmt)",
        fontsize=13,
        fontweight="bold",
        pad=12,
    )
    ax.set_xlabel("Số bình luận thu thập được", fontsize=11)
    ax.set_xlim(0, max(prod_df["cmt_count"].max() + 12, 105))
    ax.legend(loc="lower right", frameon=True)
    ax.tick_params(axis="y", labelsize=8.5)

    plt.tight_layout()
    fig.savefig(out_dir / "01_comments_per_product_imbalance.png", dpi=300)
    plt.close(fig)
    return prod_df


def plot_02_category_imbalance(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """Đồ thị 2: So sánh Số lượng Sản phẩm và Tổng số Bình luận theo Nhóm Keyword Loại hàng."""
    cat_df = (
        df.groupby("sub_cat", as_index=False)
        .agg(
            products=("product_id", "nunique"),
            comments=("cmt_id", "count"),
        )
        .sort_values("comments", ascending=False)
    )
    cat_df["pct_comments"] = cat_df["comments"] / cat_df["comments"].sum() * 100
    cat_df["avg_cmt_per_prod"] = cat_df["comments"] / cat_df["products"]

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.5))

    # Trái: Số sản phẩm theo loại hàng
    sns.barplot(
        data=cat_df,
        y="sub_cat",
        x="products",
        hue="sub_cat",
        palette="viridis",
        legend=False,
        ax=axes[0],
    )
    for idx, row in enumerate(cat_df.itertuples()):
        axes[0].text(row.products + 0.3, idx, f"{row.products} SP", va="center", fontsize=10, fontweight="bold")
    axes[0].set_title("Số lượng Sản phẩm theo Nhóm Loại hàng", fontsize=12, fontweight="bold")
    axes[0].set_xlabel("Số sản phẩm")
    axes[0].set_ylabel("")
    axes[0].set_xlim(0, cat_df["products"].max() + 5)

    # Phải: Tổng số bình luận & % tỷ trọng
    sns.barplot(
        data=cat_df,
        y="sub_cat",
        x="comments",
        hue="sub_cat",
        palette="magma",
        legend=False,
        ax=axes[1],
    )
    for idx, row in enumerate(cat_df.itertuples()):
        axes[1].text(
            row.comments + 15,
            idx,
            f"{row.comments:,} ({row.pct_comments:.1f}%)",
            va="center",
            fontsize=10,
            fontweight="bold",
        )
    axes[1].set_title("Tổng số Bình luận & Tỷ trọng theo Nhóm Loại hàng", fontsize=12, fontweight="bold")
    axes[1].set_xlabel("Số bình luận")
    axes[1].set_ylabel("")
    axes[1].set_xlim(0, cat_df["comments"].max() + 260)

    fig.suptitle(
        "Mất cân bằng Dữ liệu giữa các Nhóm Keyword Loại hàng Thời trang nữ",
        fontsize=14,
        fontweight="bold",
        y=1.02,
    )
    plt.tight_layout()
    fig.savefig(out_dir / "02_category_imbalance.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    return cat_df


def plot_03_rating_star_by_category(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """Đồ thị 3: Phân bố Rating Star (1★..5★) theo từng Nhóm Loại hàng."""
    ct = pd.crosstab(df["sub_cat"], df["rating_star"])
    for s in [1, 2, 3, 4, 5]:
        if s not in ct.columns:
            ct[s] = 0
    ct = ct[[1, 2, 3, 4, 5]]
    ct_pct = ct.div(ct.sum(axis=1), axis=0) * 100

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

    # Trái: Stacked bar 100%
    star_colors = ["#dc2626", "#f97316", "#facc15", "#84cc16", "#16a34a"]
    ct_pct.plot(kind="barh", stacked=True, color=star_colors, ax=axes[0], width=0.65)
    axes[0].set_title("Tỷ lệ (%) Mức sao (1★–5★) theo Nhóm Loại hàng", fontsize=12, fontweight="bold")
    axes[0].set_xlabel("Tỷ lệ (%)")
    axes[0].set_ylabel("")
    axes[0].legend(title="Sao", labels=["1★", "2★", "3★", "4★", "5★"], loc="lower right", ncol=5, fontsize=8.5)

    # Phải: Heatmap số lượng tuyệt đối
    sns.heatmap(
        ct,
        annot=True,
        fmt="d",
        cmap="YlOrRd",
        linewidths=0.5,
        ax=axes[1],
        cbar_kws={"label": "Số bình luận"},
    )
    axes[1].set_title("Số lượng Bình luận (1★–5★) theo Nhóm Loại hàng", fontsize=12, fontweight="bold")
    axes[1].set_xlabel("Mức sao (rating_star)")
    axes[1].set_ylabel("")

    plt.tight_layout()
    fig.savefig(out_dir / "03_rating_star_by_category_heatmap.png", dpi=300)
    plt.close(fig)
    return ct


def plot_04_shop_and_recency(df: pd.DataFrame, out_dir: Path) -> None:
    """Đồ thị 4: Top 12 Shop nhiều bình luận nhất & Tỷ lệ bình luận mới <= 365 ngày theo Loại hàng."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))

    # Trái: Top 12 Shop
    top_shops = (
        df.groupby("shop_name", dropna=False)
        .agg(comments=("cmt_id", "count"), products=("product_id", "nunique"))
        .sort_values("comments", ascending=False)
        .head(12)
        .reset_index()
    )
    top_shops["label"] = [f"{shorten_label(s, 22)} ({p} SP)" for s, p in zip(top_shops["shop_name"], top_shops["products"])]
    sns.barplot(data=top_shops, y="label", x="comments", hue="label", palette="crest", legend=False, ax=axes[0])
    for idx, val in enumerate(top_shops["comments"]):
        axes[0].text(val + 4, idx, str(val), va="center", fontsize=9)
    axes[0].set_title("Top 12 Shop Đóng góp nhiều Bình luận nhất", fontsize=12, fontweight="bold")
    axes[0].set_xlabel("Số bình luận")
    axes[0].set_ylabel("")
    axes[0].set_xlim(0, top_shops["comments"].max() + 35)

    # Phải: Tỷ lệ mới (<= 365 ngày) vs cũ (> 365 ngày) theo sub_cat
    df["recency_label"] = df["is_recent_1y"].map(
        {True: "Trong 1 năm (<=365 ngày)", False: "Cũ hơn 1 năm (>365 ngày)"}
    )
    rec_ct = pd.crosstab(df["sub_cat"], df["recency_label"])
    cols = ["Trong 1 năm (<=365 ngày)", "Cũ hơn 1 năm (>365 ngày)"]
    for col in cols:
        if col not in rec_ct.columns:
            rec_ct[col] = 0
    rec_pct = rec_ct[cols].div(rec_ct.sum(axis=1), axis=0) * 100
    rec_pct.plot(kind="barh", stacked=True, color=["#2563eb", "#94a3b8"], ax=axes[1], width=0.6)
    axes[1].set_title("Tỷ lệ Độ mới Bình luận theo Nhóm Loại hàng", fontsize=12, fontweight="bold")
    axes[1].set_xlabel("Tỷ lệ (%)")
    axes[1].set_ylabel("")
    axes[1].legend(loc="lower right", fontsize=9)

    plt.tight_layout()
    fig.savefig(out_dir / "04_shop_and_recency_distribution.png", dpi=300)
    plt.close(fig)


def plot_05_absa_aspects_by_category(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """Đồ thị 5: Heatmap tỷ lệ (%) xuất hiện các Khía cạnh ABSA theo Nhóm Loại hàng."""
    rows: List[Dict[str, float]] = []
    cats = sorted(df["sub_cat"].unique())
    for cat in cats:
        sub = df[df["sub_cat"] == cat]
        row: Dict[str, float] = {"sub_cat": cat}
        texts = sub["review_text"].astype(str).str.lower()
        for asp_name, pat in ASPECT_PATTERNS.items():
            pct = texts.str.contains(pat, regex=True).mean() * 100
            row[asp_name] = round(pct, 1)
        rows.append(row)

    asp_df = pd.DataFrame(rows).set_index("sub_cat")
    fig, ax = plt.subplots(figsize=(11, 5))
    sns.heatmap(
        asp_df,
        annot=True,
        fmt=".1f",
        cmap="Blues",
        linewidths=0.5,
        ax=ax,
        cbar_kws={"label": "Tỷ lệ xuất hiện (%)"},
    )
    ax.set_title("Tỷ lệ (%) Đề cập Các Khía cạnh ABSA theo Nhóm Loại hàng", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Khía cạnh (Aspect)")
    ax.set_ylabel("")
    plt.tight_layout()
    fig.savefig(out_dir / "05_absa_aspects_by_category.png", dpi=300)
    plt.close(fig)
    return asp_df


def write_markdown_report(
    df: pd.DataFrame,
    prod_df: pd.DataFrame,
    cat_df: pd.DataFrame,
    ct_star: pd.DataFrame,
    asp_df: pd.DataFrame,
    report_path: Path,
) -> None:
    """Xuất báo cáo phân tích EDA chi tiết ra file Markdown."""
    total_prods = len(prod_df)
    total_cmts = len(df)
    ge_60 = int((prod_df["cmt_count"] >= 60).sum())
    mid_30_59 = int(((prod_df["cmt_count"] >= 30) & (prod_df["cmt_count"] < 60)).sum())
    lt_30 = int((prod_df["cmt_count"] < 30).sum())

    top5_prods = prod_df.sort_values("cmt_count", ascending=False).head(5)
    bot5_prods = prod_df.sort_values("cmt_count", ascending=True).head(5)

    lines = [
        "# Báo cáo EDA: Phân tích Mất cân bằng giữa Sản phẩm & Các Keyword Loại hàng",
        "",
        f"- **Tổng số sản phẩm**: `{total_prods}`",
        f"- **Tổng số bình luận hợp lệ**: `{total_cmts:,}`",
        f"- **Số bình luận/sản phẩm**: Min = `{prod_df['cmt_count'].min()}`, Trung vị = `{prod_df['cmt_count'].median():.1f}`, Trung bình = `{prod_df['cmt_count'].mean():.1f}`, Max = `{prod_df['cmt_count'].max()}`",
        f"- **Tỷ lệ đạt chuẩn mật độ**: `>= 60 cmt`: **{ge_60} SP** ({ge_60/total_prods*100:.1f}%) | `30–59 cmt`: **{mid_30_59} SP** ({mid_30_59/total_prods*100:.1f}%) | `< 30 cmt`: **{lt_30} SP** ({lt_30/total_prods*100:.1f}%)",
        "",
        "---",
        "",
        "## 1. Mất cân bằng giữa các Nhóm Keyword Loại hàng Thời trang nữ",
        "",
        "| Nhóm Keyword / Loại hàng | Số SP | Số Bình luận | Tỷ trọng (%) | TB cmt/SP |",
        "| :--- | :---: | :---: | :---: | :---: |",
    ]

    for row in cat_df.itertuples():
        lines.append(
            f"| {row.sub_cat} | {row.products} | {row.comments:,} | {row.pct_comments:.1f}% | {row.avg_cmt_per_prod:.1f} |"
        )

    lines.extend(
        [
            "",
            "![Mất cân bằng theo Nhóm Loại hàng](figures/02_category_imbalance.png)",
            "",
            "---",
            "",
            "## 2. Phân bố Mức sao (`rating_star`) theo từng Nhóm Loại hàng",
            "",
            "| Nhóm Keyword / Loại hàng | 1★ | 2★ | 3★ | 4★ | 5★ | Tổng |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
    )

    for cat_name, row in ct_star.iterrows():
        tot = int(row.sum())
        lines.append(
            f"| {cat_name} | {int(row[1])} | {int(row[2])} | {int(row[3])} | {int(row[4])} | {int(row[5])} | **{tot}** |"
        )

    lines.extend(
        [
            "",
            "![Phân bố Rating Star theo Loại hàng](figures/03_rating_star_by_category_heatmap.png)",
            "",
            "---",
            "",
            "## 3. Mất cân bằng giữa các Sản phẩm (Top 5 cao nhất vs Top 5 thấp nhất)",
            "",
            "### Top 5 Sản phẩm nhiều bình luận nhất:",
            "| Product ID | Tên sản phẩm | Nhóm hàng | Số cmt |",
            "| :--- | :--- | :--- | :---: |",
        ]
    )
    for row in top5_prods.itertuples():
        lines.append(f"| `{row.product_id}` | {shorten_label(row.product_name, 55)} | {row.sub_cat} | **{row.cmt_count}** |")

    lines.extend(
        [
            "",
            "### Top 5 Sản phẩm ít bình luận nhất:",
            "| Product ID | Tên sản phẩm | Nhóm hàng | Số cmt |",
            "| :--- | :--- | :--- | :---: |",
        ]
    )
    for row in bot5_prods.itertuples():
        lines.append(f"| `{row.product_id}` | {shorten_label(row.product_name, 55)} | {row.sub_cat} | **{row.cmt_count}** |")

    lines.extend(
        [
            "",
            "![Mất cân bằng giữa các Sản phẩm](figures/01_comments_per_product_imbalance.png)",
            "",
            "---",
            "",
            "## 4. Độ phủ Shop, Độ mới Dữ liệu & Khía cạnh ABSA",
            "",
            "![Top Shop và Độ mới](figures/04_shop_and_recency_distribution.png)",
            "",
            "![Khía cạnh ABSA theo Loại hàng](figures/05_absa_aspects_by_category.png)",
        ]
    )

    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    csv_path = Path("data/output/shopee_absa_reviews_flat.csv")
    if not csv_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file dữ liệu: {csv_path}")

    df = pd.read_csv(csv_path)
    df["sub_cat"] = df["product_name"].apply(classify_keyword_category)
    dt = pd.to_datetime(df["timestamp"], errors="coerce")
    df["is_recent_1y"] = dt >= pd.Timestamp("2025-11-19")

    figures_dir = Path("reports/figures")
    figures_dir.mkdir(parents=True, exist_ok=True)
    report_path = Path("reports/eda_summary.md")

    prod_df = plot_01_comments_per_product(df, figures_dir)
    cat_df = plot_02_category_imbalance(df, figures_dir)
    ct_star = plot_03_rating_star_by_category(df, figures_dir)
    plot_04_shop_and_recency(df, figures_dir)
    asp_df = plot_05_absa_aspects_by_category(df, figures_dir)

    write_markdown_report(df, prod_df, cat_df, ct_star, asp_df, report_path)
    print(f"Da xuat 5 do thi tai: {figures_dir}")
    print(f"Da xuat bao cao EDA tai: {report_path}")


if __name__ == "__main__":
    main()
