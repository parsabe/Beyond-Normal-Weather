"""Generate the appendix plots from the checked-in benchmark tables."""

import os
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import PercentFormatter
import pandas as pd


PAPER = Path(__file__).resolve().parent
ROOT = PAPER.parent
sys.path.insert(0, str(ROOT))

from beyond_normal.run import plot_results


def plot_flash_droughts(data, output):
    styles = {
        "gcm_chronology": ("Climate-model order", "#46556b", "o"),
        "storage_calibrated": ("Storage-calibrated", "#11785e", "s"),
    }
    fig, axes = plt.subplots(1, 2, figsize=(10.6, 2.55), layout="constrained")
    for axis, resource in zip(axes, ("wind", "solar")):
        rows = data.loc[data.resource == resource].set_index("method")
        published = rows.published_nearby_change_percent.iloc[0]
        axis.axvline(0, color="#c0c8ce", linewidth=0.8, zorder=0)
        axis.axvline(published, color="#396eac", linestyle="--", linewidth=1.4, zorder=1)
        for position, (method, (_, color, marker)) in enumerate(reversed(styles.items())):
            row = rows.loc[method]
            mean = row.study_mean_change_percent
            low = mean - row.study_min_change_percent
            high = row.study_max_change_percent - mean
            axis.errorbar(mean, position, xerr=[[low], [high]], fmt=marker, color=color,
                          linewidth=1.8, markersize=5.5, capsize=4, zorder=2)
        axis.set_title(resource.capitalize(), fontweight="bold", color="#1f303b")
        axis.set_ylim(-0.5, 1.55)
        axis.set_yticks([0, 1], ["Storage-calibrated", "Climate-model order"] if axis is axes[0] else ["", ""])
        axis.tick_params(axis="y", length=0)
        axis.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
        axis.spines[["top", "left", "right"]].set_visible(False)
        axis.grid(axis="x", alpha=0.16)
        axis.set_xlabel("Change in flash-drought frequency")
        if axis is axes[1]:
            axis.legend(handles=[Line2D([0], [0], color="#396eac", linestyle="--", label="Published grid cell")],
                        frameon=False, loc="upper right", fontsize=9)
    fig.savefig(output)
    plt.close(fig)


def main():
    os.environ.setdefault("SOURCE_DATE_EPOCH", "1794960000")
    plt.rcParams.update({"font.family": "STIXGeneral", "font.size": 12, "pdf.fonttype": 42})
    reports = ROOT / "reports"
    figures = PAPER / "figures"
    figures.mkdir(exist_ok=True)
    plot_results(pd.read_csv(reports / "historical_scores.csv"), pd.read_csv(reports / "future_summary.csv"),
                 figures / "hydrogen.pdf")
    plot_flash_droughts(pd.read_csv(reports / "flash_drought_summary.csv"), figures / "flash-droughts.pdf")


if __name__ == "__main__":
    main()
