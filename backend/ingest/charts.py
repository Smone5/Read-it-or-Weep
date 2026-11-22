"""Native Office charts (the kind inserted from Excel) are stored as data, not pictures.

This module reads that data out of the chart XML so that (a) the numbers become
searchable text for question writing, and (b) a clean image can be re-drawn for
picture-round questions – no Office or LibreOffice installation needed.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from lxml import etree

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

_PALETTE = ["#2563EB", "#0D9488", "#D97706", "#DC2626", "#7C3AED", "#4F46E5", "#0891B2", "#475569"]


@dataclass
class Series:
    name: str
    categories: list[str]
    values: list[float | None]


@dataclass
class ChartData:
    kind: str                      # bar | line | pie | doughnut | area | scatter
    title: str | None
    series: list[Series] = field(default_factory=list)
    horizontal: bool = False
    stacked: bool = False
    category_axis: str | None = None
    value_axis: str | None = None

    def as_text(self) -> str:
        lines = [f"Chart: {self.title}" if self.title else "Chart", f"Chart type: {self.kind}"]
        if self.category_axis:
            lines.append(f"Category axis: {self.category_axis}")
        if self.value_axis:
            lines.append(f"Value axis: {self.value_axis}")
        for s in self.series:
            points = "; ".join(f"{c} = {_fmt(v)}" for c, v in zip(s.categories, s.values) if v is not None)
            lines.append(f'Series "{s.name}": {points}')
        return "\n".join(lines)

    def render_png(self) -> bytes:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Drawn to stay razor-sharp under mobile pinch-zoom and on 1080p/4K projectors (1452x1012 at 220 dpi).
        plt.rcParams.update({"font.size": 13, "axes.titlesize": 15, "axes.labelsize": 13,
                             "xtick.labelsize": 12, "ytick.labelsize": 12})
        fig, ax = plt.subplots(figsize=(6.6, 4.6), dpi=220)
        try:
            categories = self.series[0].categories if self.series else []
            if self.kind in ("pie", "doughnut"):
                s = self.series[0]
                pairs = [(c, v) for c, v in zip(s.categories, s.values) if v]
                width = 0.42 if self.kind == "doughnut" else 1
                ax.pie([v for _, v in pairs], labels=[f"{c}\n{_fmt(v)}" for c, v in pairs], startangle=90,
                       colors=_PALETTE, wedgeprops={"width": width, "edgecolor": "white"},
                       textprops={"fontsize": 12})
            elif self.kind == "scatter":
                numeric_x = all(_number(c) is not None for s in self.series for c in s.categories if c)
                for i, s in enumerate(self.series):
                    color = _PALETTE[i % len(_PALETTE)]
                    xs = [(_number(c) if numeric_x else float(idx)) for idx, c in enumerate(s.categories)]
                    pts = [(x, y) for x, y in zip(xs, s.values) if x is not None and y is not None]
                    if pts:
                        ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=55, color=color, alpha=0.88, label=s.name)
                if not numeric_x:
                    ax.set_xticks(range(len(categories)), categories, rotation=0 if len(categories) <= 8 else 45,
                                  ha="center" if len(categories) <= 8 else "right")
            elif self.kind in ("line", "area"):
                for i, s in enumerate(self.series):
                    ys = [v if v is not None else float("nan") for v in s.values]
                    color = _PALETTE[i % len(_PALETTE)]
                    if self.kind == "area":
                        ax.fill_between(range(len(ys)), ys, alpha=0.35, color=color, label=s.name)
                    ax.plot(range(len(ys)), ys, marker="o", color=color, linewidth=2,
                            label=None if self.kind == "area" else s.name)
                    if len(self.series) == 1 and len(ys) <= 14:
                        for x, y in enumerate(ys):
                            if y == y:
                                ax.annotate(_fmt(y), (x, y), textcoords="offset points", xytext=(0, 7),
                                            ha="center", fontsize=11)
                ax.set_xticks(range(len(categories)), categories, rotation=0 if len(categories) <= 8 else 45,
                              ha="center" if len(categories) <= 8 else "right")
            else:  # bar / column (grouped or stacked)
                count = max(len(self.series), 1)
                if self.stacked and count > 1:
                    n_cats = max((len(s.values) for s in self.series), default=0)
                    pos_base = [0.0] * n_cats
                    neg_base = [0.0] * n_cats
                    for i, s in enumerate(self.series):
                        values = [float(v or 0.0) for v in s.values]
                        bases = [pos_base[j] if values[j] >= 0 else neg_base[j] for j in range(len(values))]
                        color = _PALETTE[i % len(_PALETTE)]
                        if self.horizontal:
                            ax.barh(range(len(values)), values, left=bases, height=0.65, color=color, label=s.name)
                        else:
                            ax.bar(range(len(values)), values, bottom=bases, width=0.65, color=color, label=s.name)
                        for j, val in enumerate(values):
                            if val >= 0:
                                pos_base[j] += val
                            else:
                                neg_base[j] += val
                else:
                    width = 0.8 / count
                    for i, s in enumerate(self.series):
                        positions = [x - 0.4 + width * (i + 0.5) for x in range(len(s.values))]
                        values = [v or 0 for v in s.values]
                        color = _PALETTE[i % len(_PALETTE)]
                        if self.horizontal:
                            bars = ax.barh(positions, values, height=width, color=color, label=s.name)
                        else:
                            bars = ax.bar(positions, values, width=width, color=color, label=s.name)
                        if len(categories) * count <= 16:
                            ax.bar_label(bars, labels=[_fmt(v) for v in values], fontsize=11, padding=2)
                if self.horizontal:
                    ax.set_yticks(range(len(categories)), categories)
                    ax.invert_yaxis()
                else:
                    ax.set_xticks(range(len(categories)), categories, rotation=0 if len(categories) <= 7 else 45,
                                  ha="center" if len(categories) <= 7 else "right")
            if self.kind not in ("pie", "doughnut"):
                ax.spines[["top", "right"]].set_visible(False)
                ax.set_axisbelow(True)
                ax.grid(axis="x" if self.horizontal else "y", linestyle="--", alpha=0.22, color="#64748B")
                if self.value_axis:
                    (ax.set_xlabel if self.horizontal else ax.set_ylabel)(self.value_axis)
                if self.category_axis:
                    (ax.set_ylabel if self.horizontal else ax.set_xlabel)(self.category_axis)
                if len(self.series) > 1:
                    ax.legend(frameon=False, fontsize=12)
            if self.title:
                ax.set_title(self.title, fontweight="bold", pad=12)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
            return buf.getvalue()
        finally:
            plt.close(fig)


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9 and abs(value) < 1e15:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _rich_text(element) -> str | None:
    if element is None:
        return None
    text = "".join(t.text or "" for t in element.iter(A + "t")).strip()
    return text or None


def _points(element) -> list[str]:
    """Ordered point values from a c:cat / c:val / c:tx style element (cached values)."""
    if element is None:
        return []
    points: dict[int, str] = {}
    count = 0
    for cache in element.iter(C + "strCache", C + "numCache", C + "strLit", C + "numLit"):
        declared = cache.find(C + "ptCount")
        if declared is not None:
            count = max(count, int(declared.get("val", "0") or 0))
        for pt in cache.findall(C + "pt"):
            value = pt.find(C + "v")
            points[int(pt.get("idx", "0"))] = (value.text or "") if value is not None else ""
        break
    size = max(count, (max(points) + 1) if points else 0)
    return [points.get(i, "") for i in range(size)]


def _number(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_chart_xml(xml: bytes) -> ChartData | None:
    root = etree.fromstring(xml)
    chart = root.find(C + "chart")
    if chart is None:
        return None
    plot_area = chart.find(C + "plotArea")
    if plot_area is None:
        return None
    result: ChartData | None = None
    for plot in plot_area:
        local = etree.QName(plot).localname
        if not local.endswith("Chart"):
            continue
        base = local[:-5].replace("3D", "")
        kind = {"bar": "bar", "line": "line", "pie": "pie", "ofPie": "pie", "doughnut": "doughnut",
                "area": "area", "scatter": "scatter", "radar": "line", "bubble": "scatter"}.get(base, "bar")
        if result is None:
            direction = plot.find(C + "barDir")
            grouping = plot.find(C + "grouping")
            result = ChartData(
                kind=kind,
                title=_rich_text(chart.find(C + "title")),
                horizontal=direction is not None and direction.get("val") == "bar",
                stacked=grouping is not None and grouping.get("val") in ("stacked", "percentStacked"),
            )
        for index, ser in enumerate(plot.findall(C + "ser")):
            name = next(iter(_points(ser.find(C + "tx"))), "") or f"Series {len(result.series) + 1}"
            categories = _points(ser.find(C + "cat")) or _points(ser.find(C + "xVal"))
            values = [_number(v) for v in (_points(ser.find(C + "val")) or _points(ser.find(C + "yVal")))]
            if not categories:
                categories = [str(i + 1) for i in range(len(values))]
            size = min(len(categories), len(values))
            if size:
                result.series.append(Series(name=name, categories=categories[:size], values=values[:size]))
    if result is None or not result.series:
        return None
    for axis_tag, attribute in ((C + "catAx", "category_axis"), (C + "valAx", "value_axis"), (C + "dateAx", "category_axis")):
        axis = plot_area.find(axis_tag)
        if axis is not None and getattr(result, attribute) is None:
            setattr(result, attribute, _rich_text(axis.find(C + "title")))
    return result
