"""
Script Tải & Phân tích Khám phá Dữ liệu (EDA) cho tập `vinhplaykennen/FashionReviews`
trên HuggingFace, đồng thời đối chiếu Domain Shift với tập Test Shopee hiện có.

Chạy lệnh:
    python scripts/eda_fashion_reviews_hf.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

import httpx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


plt.rcParams["font.sans-serif"] = ["Segoe UI", "DejaVu Sans", "Arial"]
plt.rcParams["axes.unicode_minus"] = False
sns.set_theme(style="whitegrid", font="Segoe UI")

HF_CSV_URL = "https://huggingface.co/datasets/vinhplaykennen/FashionReviews/resolve/main/FashionReviews.csv"
ASPECT_COLS: List[str] = ["Chất liệu", "Kiểu dáng", "Kích cỡ", "Giá cả", "Dịch vụ"]
LABEL_NAMES: Dict[int, str] = {
    0: "0: Không đề cập",
    1: "1: Tiêu cực",
    2: "2: Trung tính",
    3: "3: Tích cực",
}
TEENCODE_PATTERN = r"\b(?:ko|k|hok|sz|from|mk|mn|đc|dc|sp|siu|qá|cx|vs|nt|t|m)\b"


def download_dataset_if_needed(dest_path: Path) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 10_000_000:
        print(f"Da co san file: {dest_path} ({dest_path.stat().st_size / 1e6:.2f} MB)")
        return dest_path

    print(f"Dang tai FashionReviews.csv tu HuggingFace...")
    with httpx.stream("GET", HF_CSV_URL, follow_redirects=True, timeout=120.0) as resp:
        resp.raise_for_status()
        with dest_path.open("wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                f.write(chunk)
    print(f"Tai thanh cong: {dest_path} ({dest_path.stat().st_size / 1e6:.2f} MB)")
    return dest_path


def normalize_text_for_dup(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def plot_01_aspect_label_dist(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """Đồ thị 1: Phân bố 4 nhãn (0, 1, 2, 3) trên 5 khía cạnh & số khía cạnh/câu."""
    rows = []
    for col in ASPECT_COLS:
        vc = df[col].dropna().astype(int).value_counts()
        row = {"Khía cạnh": col}
        for lbl in [0, 1, 2, 3]:
            row[lbl] = int(vc.get(lbl, 0))
        rows.append(row)

    dist_df = pd.DataFrame(rows).set_index("Khía cạnh")
    pct_df = dist_df.div(dist_df.sum(axis=1), axis=0) * 100

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2))

    # Trái: Stacked bar 100%
    colors = ["#cbd5e1", "#dc2626", "#f59e0b", "#16a34a"]
    pct_df.plot(kind="barh", stacked=True, color=colors, ax=axes[0], width=0.62)
    axes[0].set_title("Tỷ lệ (%) 4 Nhãn trên từng Khía cạnh (gồm Nhãn 0)", fontsize=12, fontweight="bold")
    axes[0].set_xlabel("Tỷ lệ (%)")
    axes[0].set_ylabel("")
    axes[0].legend(
        labels=[LABEL_NAMES[i] for i in [0, 1, 2, 3]],
        loc="lower right",
        fontsize=8.5,
        framealpha=0.95,
    )

    # Phải: Số khía cạnh được đề cập trên mỗi review (khác 0)
    mentioned_cnt = (df[ASPECT_COLS].fillna(0) > 0).sum(axis=1).value_counts().sort_index()
    sns.barplot(x=mentioned_cnt.index.astype(str), y=mentioned_cnt.values, hue=mentioned_cnt.index.astype(str), palette="Blues_d", legend=False, ax=axes[1])
    for idx, val in enumerate(mentioned_cnt.values):
        axes[1].text(idx, val + 350, f"{val:,}\n({val/len(df)*100:.1f}%)", ha="center", fontsize=8.5)
    axes[1].set_title("Số lượng Khía cạnh được nhắc tới trên mỗi Review", fontsize=12, fontweight="bold")
    axes[1].set_xlabel("Số khía cạnh đề cập (0 đến 5)")
    axes[1].set_ylabel("Số dòng review")
    axes[1].set_ylim(0, mentioned_cnt.max() * 1.18)

    plt.tight_layout()
    fig.savefig(out_dir / "hf_01_aspect_label_distribution.png", dpi=300)
    plt.close(fig)
    return dist_df


def plot_02_sentiment_only_balance(dist_df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """Đồ thị 2: Cân bằng 3 nhãn cảm xúc (1: Tiêu cực, 2: Trung tính, 3: Tích cực) khi bỏ nhãn 0."""
    sent_df = dist_df[[1, 2, 3]].copy()
    sent_df.columns = ["1: Tiêu cực", "2: Trung tính", "3: Tích cực"]
    sent_pct = sent_df.div(sent_df.sum(axis=1), axis=0) * 100

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    sent_df.plot(kind="bar", color=["#dc2626", "#f59e0b", "#16a34a"], ax=axes[0], width=0.72)
    axes[0].set_title("Số lượng tuyệt đối các nhãn Cảm xúc {1, 2, 3}", fontsize=12, fontweight="bold")
    axes[0].set_ylabel("Số lượng mẫu")
    axes[0].set_xlabel("")
    axes[0].tick_params(axis="x", rotation=15)

    sent_pct.plot(kind="bar", stacked=True, color=["#dc2626", "#f59e0b", "#16a34a"], ax=axes[1], width=0.65)
    axes[1].set_title("Tỷ trọng (%) giữa 3 nhãn Cảm xúc {1, 2, 3}", fontsize=12, fontweight="bold")
    axes[1].set_ylabel("Tỷ lệ (%)")
    axes[1].set_xlabel("")
    axes[1].tick_params(axis="x", rotation=15)

    plt.tight_layout()
    fig.savefig(out_dir / "hf_02_sentiment_only_balance.png", dpi=300)
    plt.close(fig)
    return sent_df


def plot_03_domain_shift_comparison(df_hf: pd.DataFrame, shopee_csv: Path, out_dir: Path) -> Dict[str, float]:
    """Đồ thị 3: So sánh Domain Shift (độ dài câu & tỷ lệ teencode) giữa FashionReviews và Tập Test Shopee."""
    hf_len = df_hf["Nội dung review"].fillna("").astype(str).str.len()
    hf_teen = df_hf["Nội dung review"].fillna("").astype(str).str.lower().str.contains(TEENCODE_PATTERN, regex=True).mean() * 100

    stats = {
        "hf_median_len": float(hf_len.median()),
        "hf_mean_len": float(hf_len.mean()),
        "hf_teencode_pct": float(hf_teen),
    }

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5))

    if shopee_csv.exists():
        df_sp = pd.read_csv(shopee_csv)
        sp_len = df_sp["review_text"].fillna("").astype(str).str.len()
        sp_teen = df_sp["review_text"].fillna("").astype(str).str.lower().str.contains(TEENCODE_PATTERN, regex=True).mean() * 100
        stats["sp_median_len"] = float(sp_len.median())
        stats["sp_mean_len"] = float(sp_len.mean())
        stats["sp_teencode_pct"] = float(sp_teen)

        sns.kdeplot(hf_len.clip(upper=500), label=f"FashionReviews HF (Median: {hf_len.median():.0f})", fill=True, alpha=0.35, ax=axes[0], color="#2563eb")
        sns.kdeplot(sp_len.clip(upper=500), label=f"Shopee Test Set (Median: {sp_len.median():.0f})", fill=True, alpha=0.35, ax=axes[0], color="#ee4d2d")
        axes[0].set_title("Phân bố Độ dài Ký tự (cắt ngưỡng 500 ký tự)", fontsize=12, fontweight="bold")
        axes[0].set_xlabel("Số ký tự / review")
        axes[0].legend()

        comp_df = pd.DataFrame({
            "Tập dữ liệu": ["FashionReviews (HF)", "Shopee Test Set"],
            "Tỷ lệ chứa Teencode/Viết tắt (%)": [hf_teen, sp_teen],
        })
        sns.barplot(data=comp_df, x="Tập dữ liệu", y="Tỷ lệ chứa Teencode/Viết tắt (%)", hue="Tập dữ liệu", palette=["#2563eb", "#ee4d2d"], legend=False, ax=axes[1])
        for idx, val in enumerate([hf_teen, sp_teen]):
            axes[1].text(idx, val + 1.0, f"{val:.1f}%", ha="center", fontweight="bold", fontsize=11)
        axes[1].set_title("So sánh Mức độ Teencode / Viết tắt (Domain Shift)", fontsize=12, fontweight="bold")
        axes[1].set_ylim(0, max(hf_teen, sp_teen) * 1.25)

    plt.tight_layout()
    fig.savefig(out_dir / "hf_03_text_length_domain_shift.png", dpi=300)
    plt.close(fig)
    return stats


def plot_04_aspect_cooccurrence(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """Đồ thị 4: Ma trận đồng xuất hiện (%) giữa 5 khía cạnh."""
    bin_df = (df[ASPECT_COLS].fillna(0) > 0).astype(int)
    cooc = (bin_df.T @ bin_df) / len(df) * 100
    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    sns.heatmap(cooc, annot=True, fmt=".1f", cmap="YlGnBu", linewidths=0.5, ax=ax, cbar_kws={"label": "% Review đồng xuất hiện"})
    ax.set_title("Ma trận Đồng xuất hiện (%) giữa 5 Khía cạnh ABSA", fontsize=12.5, fontweight="bold", pad=10)
    plt.tight_layout()
    fig.savefig(out_dir / "hf_04_aspect_cooccurrence.png", dpi=300)
    plt.close(fig)
    return cooc


def main() -> None:
    csv_path = download_dataset_if_needed(Path("data/external/FashionReviews.csv"))
    df = pd.read_csv(csv_path)

    figures_dir = Path("reports/figures")
    figures_dir.mkdir(parents=True, exist_ok=True)
    report_path = Path("reports/eda_fashion_reviews_hf.md")

    total_rows = len(df)
    null_counts = df.isna().sum().to_dict()

    # Kiểm tra các giá trị nhãn duy nhất
    unique_vals = {col: sorted(df[col].dropna().unique().tolist()) for col in ASPECT_COLS}

    # Kiểm tra trùng lặp văn bản (Exact & Normalized)
    texts = df["Nội dung review"].fillna("").astype(str)
    exact_dups = int(texts.duplicated().sum())
    norm_texts = texts.apply(normalize_text_for_dup)
    norm_dups = int(norm_texts.duplicated().sum())
    unique_reviews = int(norm_texts.nunique())
    short_lt_10 = int((texts.str.len() < 10).sum())

    dist_df = plot_01_aspect_label_dist(df, figures_dir)
    sent_df = plot_02_sentiment_only_balance(dist_df, figures_dir)
    domain_stats = plot_03_domain_shift_comparison(df, Path("data/output/shopee_absa_reviews_flat.csv"), figures_dir)
    plot_04_aspect_cooccurrence(df, figures_dir)

    zero_all = int(((df[ASPECT_COLS].fillna(0) > 0).sum(axis=1) == 0).sum())

    lines = [
        "# Báo cáo EDA: Tập dữ liệu `vinhplaykennen/FashionReviews` (HuggingFace)",
        "",
        "## 1. Chỉ số Tổng quan & Sức khỏe Dữ liệu (Data Integrity)",
        f"- **Tổng số dòng**: `{total_rows:,}` dòng | **Số cột**: `{df.shape[1]}`",
        f"- **Giá trị khuyết (`NaN`)**: `Nội dung review`: `{null_counts.get('Nội dung review', 0)}` | "
        + ", ".join(f"`{c}`: `{null_counts.get(c, 0)}`" for c in ASPECT_COLS),
        f"- **Số dòng trùng lặp nguyên văn (`Exact Duplicates`)**: **`{exact_dups:,}`** ({exact_dups/total_rows*100:.2f}%)",
        f"- **Số dòng trùng lặp sau chuẩn hóa (`Normalized Duplicates`)**: **`{norm_dups:,}`** ({norm_dups/total_rows*100:.2f}%) $\\rightarrow$ Chỉ còn **`{unique_reviews:,}`** câu review duy nhất!",
        f"- **Số dòng quá ngắn (< 10 ký tự)**: `{short_lt_10:,}`",
        f"- **Số dòng không đề cập khía cạnh nào (cả 5 cột = `0`)**: `{zero_all:,}` ({zero_all/total_rows*100:.2f}%)",
        f"- **Tập giá trị nhãn kiểm tra được**: " + "; ".join(f"`{c}`: `{v}`" for c, v in unique_vals.items()),
        "",
        "---",
        "",
        "## 2. Phân bố 4 Nhãn (`0, 1, 2, 3`) trên 5 Khía cạnh ABSA",
        "",
        "| Khía cạnh | `0: Không đề cập` | `1: Tiêu cực` | `2: Trung tính` | `3: Tích cực` | Tỷ lệ có đề cập (`1,2,3`) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]

    for col in ASPECT_COLS:
        r = dist_df.loc[col]
        mentioned = int(r[1] + r[2] + r[3])
        lines.append(
            f"| **{col}** | {int(r[0]):,} ({r[0]/total_rows*100:.1f}%) | {int(r[1]):,} ({r[1]/total_rows*100:.1f}%) | "
            f"{int(r[2]):,} ({r[2]/total_rows*100:.1f}%) | {int(r[3]):,} ({r[3]/total_rows*100:.1f}%) | **{mentioned:,} ({mentioned/total_rows*100:.1f}%)** |"
        )

    lines.extend(
        [
            "",
            "![Phân bố 4 nhãn trên 5 khía cạnh](figures/hf_01_aspect_label_distribution.png)",
            "",
            "---",
            "",
            "## 3. Cân bằng Nội tại giữa 3 Nhãn Cảm xúc (`1: Tiêu cực`, `2: Trung tính`, `3: Tích cực`)",
            "",
            "| Khía cạnh | `1: Tiêu cực` (%) | `2: Trung tính` (%) | `3: Tích cực` (%) |",
            "| :--- | :---: | :---: | :---: |",
        ]
    )

    for col in ASPECT_COLS:
        s1, s2, s3 = int(sent_df.loc[col, "1: Tiêu cực"]), int(sent_df.loc[col, "2: Trung tính"]), int(sent_df.loc[col, "3: Tích cực"])
        tot_s = max(1, s1 + s2 + s3)
        lines.append(f"| **{col}** | {s1:,} ({s1/tot_s*100:.1f}%) | {s2:,} ({s2/tot_s*100:.1f}%) | {s3:,} ({s3/tot_s*100:.1f}%) |")

    lines.extend(
        [
            "",
            "![Cân bằng 3 nhãn cảm xúc](figures/hf_02_sentiment_only_balance.png)",
            "",
            "---",
            "",
            "## 4. So sánh Domain Shift với Tập Test Shopee của Bạn",
            f"- **Độ dài văn bản `FashionReviews` (HF)**: Trung vị = `{domain_stats['hf_median_len']:.1f}` ký tự | Trung bình = `{domain_stats['hf_mean_len']:.1f}` ký tự | Tỷ lệ chứa Teencode/Viết tắt: **`{domain_stats['hf_teencode_pct']:.1f}%`**.",
        ]
    )
    if "sp_median_len" in domain_stats:
        lines.append(
            f"- **Độ dài văn bản `Shopee Test Set` của bạn**: Trung vị = `{domain_stats['sp_median_len']:.1f}` ký tự | Trung bình = `{domain_stats['sp_mean_len']:.1f}` ký tự | Tỷ lệ chứa Teencode/Viết tắt: **`{domain_stats['sp_teencode_pct']:.1f}%`**."
        )

    lines.extend(
        [
            "",
            "![So sánh Domain Shift](figures/hf_03_text_length_domain_shift.png)",
            "",
            "![Ma trận đồng xuất hiện khía cạnh](figures/hf_04_aspect_cooccurrence.png)",
        ]
    )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Da xuat bao cao EDA HuggingFace tai: {report_path}")


if __name__ == "__main__":
    main()
