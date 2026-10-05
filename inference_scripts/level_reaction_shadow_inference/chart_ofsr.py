#!/usr/bin/env python3
"""
Order Flow S/R Chart — standalone pyqtgraph process.

Same structure as chart_gpu.py but replaces:
  - Standard S/R lines → 12 OF S/R lines: 6 support (BidAdd+AskPull highest
    concentration) + 6 resistance (AskAdd+BidPull highest concentration), each
    pair computed across 6 different bar windows (25B / 50B / 100B / 200B /
    500B / full) from BF level candles.  Plus 4 size-scaled scatter-peak
    markers with halo glow for the 4 OF metrics.
  - Standard vol profile → 4-color order-book vol profile (bid_add/ask_pull/
    ask_add/bid_pull per price level, from BF level candles).

SHADOW / RESEARCH ONLY. No execution. No broker. No paper trading.
Launched via subprocess from the dashboard OF S/R CHART tab.
"""
from __future__ import annotations
import argparse, glob, json, os, sys, time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

_USE_GL = os.environ.get("OFI_CHART_GL", "0") not in ("0", "false", "False")
pg.setConfigOptions(
    useOpenGL=_USE_GL,
    antialias=False,
    background="#050505",
    foreground="#dddddd",
    leftButtonPan=True,
)

DEFAULT_MASTER = Path(os.environ.get(
    "OFI_MASTER_PATH",
    "/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl",
))
BF_CACHE = Path("/home/prabh/OFI_Production/book_flow_chart/cache")
TICK = 0.25

# ── Per-metric color palette ─────────────────────────────────────────────── #
COLOR_BID_ADD  = "#00ff88"   # bright green   — bullish bid absorption
COLOR_ASK_PULL = "#00eeff"   # bright cyan    — ask withdrawal (bullish)
COLOR_ASK_ADD  = "#ff3344"   # bright red     — aggressive offer
COLOR_BID_PULL = "#ffaa00"   # amber orange   — bid withdrawal (bearish)

# OF S/R line colors — 6 windows × 2 pairs = 12 lines (6 S + 6 R)
# Bullish pair (BidAdd+AskPull) → support lines, dark→bright green/cyan gradient
_BULL_COLORS = ["#003322", "#005533", "#008855", "#00bb77", "#00dd99", "#00ffcc"]
# Bearish pair (AskAdd+BidPull) → resistance lines, dark→bright red/orange gradient
_BEAR_COLORS = ["#440011", "#771100", "#aa2200", "#cc4400", "#ee6600", "#ff9900"]
# Line styles: pairs of (DotLine, DashLine, DashDotLine) repeated for 6 windows
_WIN_STYLES  = [QtCore.Qt.DotLine, QtCore.Qt.DotLine,
                QtCore.Qt.DashLine, QtCore.Qt.DashLine,
                QtCore.Qt.DashDotLine, QtCore.Qt.DashDotLine]
_WIN_WIDTHS  = [0.8, 0.9, 1.0, 1.1, 1.4, 1.6]
_WIN_LABELS  = ["25B", "50B", "100B", "200B", "500B", "full"]
_WINDOWS     = [25, 50, 100, 200, 500, None]   # None = full displayed window


# ── CandleItem — verbatim from chart_gpu.py ──────────────────────────────── #
class CandleItem(pg.GraphicsObject):
    def __init__(self):
        super().__init__()
        self._x  = np.empty(0, dtype=float)
        self._o  = np.empty(0, dtype=float)
        self._h  = np.empty(0, dtype=float)
        self._l  = np.empty(0, dtype=float)
        self._c  = np.empty(0, dtype=float)
        self._wicks:      list[QtCore.QLineF] = []
        self._up_rects:   list[QtCore.QRectF] = []
        self._down_rects: list[QtCore.QRectF] = []
        self._bounds = QtCore.QRectF()
        self._up_pen     = QtGui.QPen(QtGui.QColor("#00d27a")); self._up_pen.setCosmetic(True)
        self._down_pen   = QtGui.QPen(QtGui.QColor("#ff4d4d")); self._down_pen.setCosmetic(True)
        self._wick_pen   = QtGui.QPen(QtGui.QColor("#888888")); self._wick_pen.setCosmetic(True)
        self._up_brush   = QtGui.QBrush(QtGui.QColor("#00d27a"))
        self._down_brush = QtGui.QBrush(QtGui.QColor("#ff4d4d"))

    def set_data(self, ts_idx, o, h, l, c):
        self._x = ts_idx.astype(float, copy=False)
        self._o = o.astype(float, copy=False)
        self._h = h.astype(float, copy=False)
        self._l = l.astype(float, copy=False)
        self._c = c.astype(float, copy=False)
        self._wicks.clear(); self._up_rects.clear(); self._down_rects.clear()
        w = 0.6
        for i in range(len(ts_idx)):
            xi = float(ts_idx[i])
            op, hp, lp, cp = float(o[i]), float(h[i]), float(l[i]), float(c[i])
            if np.isfinite(hp) and np.isfinite(lp):
                self._wicks.append(QtCore.QLineF(xi, lp, xi, hp))
            if np.isfinite(op) and np.isfinite(cp):
                lo_y = min(op, cp); hi_y = max(op, cp)
                if hi_y - lo_y < 1e-9: hi_y = lo_y + 1e-9
                r = QtCore.QRectF(xi - w / 2, lo_y, w, hi_y - lo_y)
                if cp >= op: self._up_rects.append(r)
                else:        self._down_rects.append(r)
        if len(ts_idx):
            lo = float(np.nanmin(l)); hi = float(np.nanmax(h))
            self._bounds = QtCore.QRectF(
                float(ts_idx[0]) - 1.0, lo,
                float(ts_idx[-1] - ts_idx[0]) + 2.0, hi - lo)
        else:
            self._bounds = QtCore.QRectF()
        self.prepareGeometryChange(); self.update()

    def paint(self, painter, option, widget=None):
        if not self._wicks and not self._up_rects and not self._down_rects:
            return
        if self._wicks:
            painter.setPen(self._wick_pen); painter.drawLines(self._wicks)
        if self._up_rects:
            painter.setPen(self._up_pen); painter.setBrush(self._up_brush)
            painter.drawRects(self._up_rects)
        if self._down_rects:
            painter.setPen(self._down_pen); painter.setBrush(self._down_brush)
            painter.drawRects(self._down_rects)

    def boundingRect(self):
        return self._bounds


# ── Master loader — verbatim from chart_gpu.py ───────────────────────────── #
def load_master_tail(path: Path, tail: int) -> pd.DataFrame:
    rows = []
    with open(path) as f:
        lines = f.readlines()
    if tail and tail > 0:
        lines = lines[-tail:]
    for ln in lines:
        try: rows.append(json.loads(ln))
        except Exception: continue
    df = pd.DataFrame(rows)
    if df.empty: return df
    df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")
    df = df.dropna(subset=["bar_end_ts_ns"])
    df["bar_end_ts_ns"] = df["bar_end_ts_ns"].astype("int64")
    df = df.sort_values("bar_end_ts_ns").reset_index(drop=True)
    if "continuous_close" in df.columns:
        for col in ("open", "high", "low", "close"):
            cc, px = f"continuous_{col}", f"px_{col}"
            if cc in df.columns and px in df.columns:
                df[px] = df[cc]
    return df


# ── BF level candle loader ────────────────────────────────────────────────── #
def load_bf_level_candles(bar_ts_set: set, cache_dir: Path) -> pd.DataFrame:
    """Load CLOSED BF level candles for bar_end_ts_ns values in bar_ts_set."""
    files = sorted(glob.glob(str(cache_dir / "book_flow_level_candles_NQU6_*_top10.parquet")))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        try:
            df = pd.read_parquet(f, columns=[
                "bar_end_ts_ns", "price_level",
                "bid_add", "bid_pull", "ask_add", "ask_pull", "bar_state",
            ])
            df = df[df["bar_state"] == "CLOSED"]
            df["bar_end_ts_ns"] = pd.to_numeric(df["bar_end_ts_ns"], errors="coerce")
            df = df[df["bar_end_ts_ns"].isin(bar_ts_set)]
            if not df.empty:
                dfs.append(df)
        except Exception:
            continue
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _bf_cache_mtime(cache_dir: Path) -> float:
    """Max mtime of all BF daily parquet files in cache_dir."""
    files = glob.glob(str(cache_dir / "book_flow_level_candles_NQU6_*_top10.parquet"))
    if not files:
        return 0.0
    return max(os.path.getmtime(f) for f in files)


# ── OF volume profile aggregation ────────────────────────────────────────── #
def of_volume_profile(bf_df: pd.DataFrame, tick: float = TICK) -> dict:
    """Per-price-level 4-metric OF profile for all displayed bars."""
    if bf_df.empty or not {"price_level","bid_add","bid_pull","ask_add","ask_pull"}.issubset(bf_df.columns):
        return {}
    agg = bf_df.groupby("price_level").agg(
        bid_add=("bid_add",  "sum"),
        ask_pull=("ask_pull", "sum"),
        ask_add=("ask_add",  "sum"),
        bid_pull=("bid_pull", "sum"),
    ).reset_index().sort_values("price_level")
    if agg.empty: return {}
    levels = agg["price_level"].to_numpy(dtype=float)
    ba_v   = agg["bid_add"].to_numpy(dtype=float)
    ap_v   = agg["ask_pull"].to_numpy(dtype=float)
    aa_v   = agg["ask_add"].to_numpy(dtype=float)
    bp_v   = agg["bid_pull"].to_numpy(dtype=float)
    total  = ba_v + ap_v + aa_v + bp_v
    if total.max() == 0: return {}
    poc_i = int(np.argmax(total))
    return {"levels": levels, "ba_v": ba_v, "ap_v": ap_v,
            "aa_v": aa_v, "bp_v": bp_v, "total": total,
            "of_poc": float(levels[poc_i])}


# ── OF S/R levels from pair concentration ─────────────────────────────────── #
def compute_of_sr_levels(
    bf_df: pd.DataFrame,
    bar_ts_ordered: list,
    windows: list = _WINDOWS,
) -> list[tuple]:
    """For each window, find the price level where BidAdd+AskPull and AskAdd+BidPull
    are most concentrated.

    Returns list of (price_level, window_label, pair_type, window_idx)
      pair_type: 'bullish' (BidAdd+AskPull) or 'bearish' (AskAdd+BidPull)
      window_idx: 0/1/2 → maps to _WIN_LABELS
    """
    if bf_df.empty:
        return []
    results = []
    for wi, (w, lbl) in enumerate(zip(windows, _WIN_LABELS)):
        if w is None or w >= len(bar_ts_ordered):
            ts_set = set(bar_ts_ordered)
        else:
            ts_set = set(bar_ts_ordered[-w:])
        sub = bf_df[bf_df["bar_end_ts_ns"].isin(ts_set)]
        if sub.empty:
            continue
        agg = sub.groupby("price_level").agg(
            bid_add=("bid_add",  "sum"),
            ask_pull=("ask_pull", "sum"),
            ask_add=("ask_add",  "sum"),
            bid_pull=("bid_pull", "sum"),
        ).reset_index()
        agg["bull"] = agg["bid_add"]  + agg["ask_pull"]
        agg["bear"] = agg["ask_add"]  + agg["bid_pull"]
        if agg["bull"].max() > 0:
            px = float(agg.loc[agg["bull"].idxmax(), "price_level"])
            val = float(agg["bull"].max())
            results.append((px, lbl, "bullish", wi, val))
        if agg["bear"].max() > 0:
            px = float(agg.loc[agg["bear"].idxmax(), "price_level"])
            val = float(agg["bear"].max())
            results.append((px, lbl, "bearish", wi, val))
    return results


# ── OF peak detector ─────────────────────────────────────────────────────── #
def find_of_peaks_tiered(values: np.ndarray, lb: int = 3) -> tuple:
    """Return (indices, size_array) where size_array is per-peak marker size.

    Size tiers (device pixels):
      >= P80 of peak values → 22 px
      >= P50               → 14 px
      below P50            → 8 px

    Uses the same centered rolling max logic as chart_gpu.py swing_levels().
    """
    s = pd.Series(values.astype(float))
    win = lb * 2 + 1
    rolling_max = s.rolling(win, center=True, min_periods=win).max().to_numpy()
    peaks = np.where(
        np.isfinite(values) & np.isfinite(rolling_max)
        & (values >= rolling_max) & (values > 0)
    )[0]
    if len(peaks) == 0:
        return peaks, np.empty(0, dtype=int)
    peak_vals = values[peaks]
    finite_v  = peak_vals[np.isfinite(peak_vals)]
    if len(finite_v) == 0:
        return peaks, np.full(len(peaks), 8, dtype=int)
    q80 = np.percentile(finite_v, 80)
    q50 = np.percentile(finite_v, 50)
    sizes = np.where(peak_vals >= q80, 22, np.where(peak_vals >= q50, 14, 8))
    return peaks, sizes.astype(int)


# ── Order-book volume profile item ───────────────────────────────────────── #
class OrderFlowVolumeProfileItem(pg.GraphicsObject):
    """4-color stacked horizontal OF profile (bid_add | ask_pull | ask_add | bid_pull)."""
    def __init__(self):
        super().__init__()
        self._bounds = QtCore.QRectF(0, 0, 1, 1)
        self._ba_rects: list = []; self._ap_rects: list = []
        self._aa_rects: list = []; self._bp_rects: list = []

    def set_data(self, levels, ba_v, ap_v, aa_v, bp_v, tick):
        total = ba_v + ap_v + aa_v + bp_v
        mv = float(total.max()) if total.max() > 0 else 1.0
        self._ba_rects.clear(); self._ap_rects.clear()
        self._aa_rects.clear(); self._bp_rects.clear()
        bh = tick * 0.92
        for j in range(len(levels)):
            if total[j] < mv * 0.002: continue
            px  = float(levels[j])
            t   = min(total[j] / mv, 1.0)
            tv  = max(total[j], 1.0)
            y0  = px - bh / 2
            x   = 0.0
            for arr, lst in [(ba_v, self._ba_rects), (ap_v, self._ap_rects),
                              (aa_v, self._aa_rects), (bp_v, self._bp_rects)]:
                w = t * (arr[j] / tv)
                if w > 1e-5:
                    lst.append(QtCore.QRectF(x, y0, w, bh))
                x += t * (arr[j] / tv)
        if len(levels):
            ymin = float(levels[0]); ymax = float(levels[-1])
            self._bounds = QtCore.QRectF(0, ymin, 1.0, ymax - ymin)
        self.prepareGeometryChange(); self.update()

    def paint(self, painter, option, widget=None):
        painter.setPen(QtCore.Qt.NoPen)
        for rects, color in [
            (self._ba_rects, COLOR_BID_ADD),
            (self._ap_rects, COLOR_ASK_PULL),
            (self._aa_rects, COLOR_ASK_ADD),
            (self._bp_rects, COLOR_BID_PULL),
        ]:
            if rects:
                painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))
                painter.drawRects(rects)

    def boundingRect(self):
        return self._bounds


# ── Main window ──────────────────────────────────────────────────────────── #
class OFSRChartWindow(QtWidgets.QMainWindow):
    """Order-flow S/R chart — same structure as GPUChartWindow, enhanced visuals."""

    def __init__(self, master_path: Path, tail_bars: int, refresh_ms: int):
        super().__init__()
        self.master_path  = master_path
        self.tail_bars    = tail_bars
        self.refresh_ms   = refresh_ms
        self.df: pd.DataFrame = pd.DataFrame()
        self.last_mtime   = 0.0
        self.last_bf_mt   = 0.0          # BF cache mtime for live BF updates
        self._user_zoomed = False
        self._in_reload   = False
        self._bar_of: pd.DataFrame = pd.DataFrame()
        self._build_ui()
        QtCore.QTimer.singleShot(50, self._reload)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(refresh_ms)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()

    # ── UI ────────────────────────────────────────────────────────────────── #
    def _build_ui(self) -> None:
        self.setWindowTitle(
            "Order Flow S/R Chart  |  6 OF-SR lines (BidAdd+AskPull / AskAdd+BidPull)  "
            "|  ORDER BOOK VOL PROFILE  |  SHADOW/RESEARCH ONLY"
        )
        self.resize(1600, 900)
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        layout = QtWidgets.QVBoxLayout(cw)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # ── Toolbar ──────────────────────────────────────────────────────── #
        tb = QtWidgets.QToolBar()
        tb.setStyleSheet("background:#0c0c0c; color:#aaaaaa;")
        layout.addWidget(tb)

        tb.addWidget(QtWidgets.QLabel("  tail bars: "))
        self.tail_combo = QtWidgets.QComboBox()
        self.tail_combo.addItems(["500", "1000", "2000", "5000", "10000", "full"])
        cur = str(self.tail_bars) if self.tail_bars > 0 else "full"
        idx_c = self.tail_combo.findText(cur)
        self.tail_combo.setCurrentIndex(idx_c if idx_c >= 0 else 3)
        self.tail_combo.currentTextChanged.connect(self._on_tail_change)
        tb.addWidget(self.tail_combo)

        tb.addWidget(QtWidgets.QLabel("    "))
        self.aa_cb = QtWidgets.QCheckBox("anti-alias")
        self.aa_cb.setChecked(False)
        self.aa_cb.stateChanged.connect(self._on_aa_change)
        tb.addWidget(self.aa_cb)

        tb.addWidget(QtWidgets.QLabel("    "))
        btn_r = QtWidgets.QPushButton("Reset view")
        btn_r.clicked.connect(self._reset_view)
        btn_r.setStyleSheet("background:#222; color:#ddd; padding:3px 10px;")
        tb.addWidget(btn_r)

        # OF metric legend
        tb.addWidget(QtWidgets.QLabel("    "))
        for label, color in [
            ("▲ BidAdd",  COLOR_BID_ADD),
            ("◆ AskPull", COLOR_ASK_PULL),
            ("▼ AskAdd",  COLOR_ASK_ADD),
            ("✕ BidPull", COLOR_BID_PULL),
        ]:
            lbl = QtWidgets.QLabel(f"  {label}")
            lbl.setStyleSheet(f"color:{color}; font-weight:bold;")
            tb.addWidget(lbl)

        # OF S/R line legend — compact: show darkest→brightest per pair type
        tb.addWidget(QtWidgets.QLabel("    "))
        for lbl_txt, color in [
            ("S 25B", _BULL_COLORS[0]),
            ("S 50B", _BULL_COLORS[1]),
            ("S 100B", _BULL_COLORS[2]),
            ("S 200B", _BULL_COLORS[3]),
            ("S 500B", _BULL_COLORS[4]),
            ("S full", _BULL_COLORS[5]),
            ("  R 25B", _BEAR_COLORS[0]),
            ("R 50B", _BEAR_COLORS[1]),
            ("R 100B", _BEAR_COLORS[2]),
            ("R 200B", _BEAR_COLORS[3]),
            ("R 500B", _BEAR_COLORS[4]),
            ("R full", _BEAR_COLORS[5]),
        ]:
            lbl = QtWidgets.QLabel(f" {lbl_txt}")
            lbl.setStyleSheet(f"color:{color}; font-family:Consolas; font-size:8px; font-weight:bold;")
            tb.addWidget(lbl)

        tb.addWidget(QtWidgets.QLabel("    "))
        self.gpu_lbl = QtWidgets.QLabel("backend: " + ("OpenGL" if _USE_GL else "software (CPU)"))
        self.gpu_lbl.setStyleSheet("color:#00ccff;" if _USE_GL else "color:#ffaa55;")
        tb.addWidget(self.gpu_lbl)

        tb.addWidget(QtWidgets.QLabel("    "))
        self.master_lbl = QtWidgets.QLabel(f"master: {self.master_path.name}")
        self.master_lbl.setStyleSheet("color:#cc99ff;")
        tb.addWidget(self.master_lbl)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.status_lbl = QtWidgets.QLabel("loading…")
        self.status_lbl.setStyleSheet("color:#ffd28c; padding-right:10px;")
        tb.addWidget(self.status_lbl)

        # ── Chart layout: 85% candles / 15% OF VP ────────────────────────── #
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("#050505")
        if _USE_GL:
            try: self.glw.useOpenGL(True)
            except Exception: pass
        layout.addWidget(self.glw)

        self.plot = self.glw.addPlot(row=0, col=0)
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        for ax in ("left", "right", "top", "bottom"):
            try:
                self.plot.getAxis(ax).setPen("#222")
                self.plot.getAxis(ax).setTextPen("#aaa")
            except Exception: pass

        self.vp_plot = self.glw.addPlot(row=0, col=1)
        self.vp_plot.showGrid(x=False, y=True, alpha=0.05)
        for ax in ("left", "right", "top", "bottom"):
            try:
                self.vp_plot.getAxis(ax).setPen("#222")
                self.vp_plot.getAxis(ax).setTextPen("#666")
            except Exception: pass
        self.vp_plot.hideAxis("left")
        self.vp_plot.hideAxis("bottom")
        self.vp_plot.setMouseEnabled(x=False, y=False)
        self.vp_plot.setMenuEnabled(False)
        self.glw.ci.layout.setColumnStretchFactor(0, 85)
        self.glw.ci.layout.setColumnStretchFactor(1, 15)
        self.glw.ci.layout.setHorizontalSpacing(0)
        self.glw.ci.layout.setContentsMargins(0, 0, 0, 0)
        self.vp_plot.setYLink(self.plot)

        # ── Candles + OF VP ──────────────────────────────────────────────── #
        self.candles = CandleItem()
        self.plot.addItem(self.candles)
        self.ofvp_item = OrderFlowVolumeProfileItem()
        self.vp_plot.addItem(self.ofvp_item)

        # OF-POC line on main chart
        self.line_poc = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ffd700", width=1.6))
        self.line_poc.setVisible(False)
        self.plot.addItem(self.line_poc)
        self.lbl_poc = pg.TextItem("OF-POC", color="#ffd700", anchor=(0, 0.5))
        self.lbl_poc.setVisible(False)
        self.plot.addItem(self.lbl_poc)

        # ── 12 OF S/R lines: 6 support + 6 resistance (one per window each) ── #
        # Pool layout (interleaved per window):
        #   0,2,4,6,8,10  = bull/support  for windows 25B/50B/100B/200B/500B/full
        #   1,3,5,7,9,11  = bear/resistance for same windows
        self._ofsr_lines:  list[pg.InfiniteLine] = []
        self._ofsr_labels: list[pg.TextItem]     = []
        for i in range(6):
            # Bullish S-type line
            pen_b = pg.mkPen(QtGui.QColor(_BULL_COLORS[i]),
                             width=_WIN_WIDTHS[i], style=_WIN_STYLES[i])
            ln = pg.InfiniteLine(angle=0, pen=pen_b)
            ln.setVisible(False); ln.setZValue(-4)
            self.plot.addItem(ln, ignoreBounds=True)
            self._ofsr_lines.append(ln)
            lbl = pg.TextItem(text="", color=_BULL_COLORS[i], anchor=(1, 1))
            lbl.setVisible(False)
            self.plot.addItem(lbl)
            self._ofsr_labels.append(lbl)
            # Bearish R-type line
            pen_r = pg.mkPen(QtGui.QColor(_BEAR_COLORS[i]),
                             width=_WIN_WIDTHS[i], style=_WIN_STYLES[i])
            ln2 = pg.InfiniteLine(angle=0, pen=pen_r)
            ln2.setVisible(False); ln2.setZValue(-4)
            self.plot.addItem(ln2, ignoreBounds=True)
            self._ofsr_lines.append(ln2)
            lbl2 = pg.TextItem(text="", color=_BEAR_COLORS[i], anchor=(1, 0))
            lbl2.setVisible(False)
            self.plot.addItem(lbl2)
            self._ofsr_labels.append(lbl2)

        # ── Enhanced scatter items: halo + symbol, per OF metric ─────────── #
        # 8 scatter items total: 4 halo (large faint circle) + 4 symbol
        # Halo: 'o', alpha≈70, size = primary_size + 10, no border
        # Symbol: actual shape, full color, thin white border for contrast
        _metrics = ["bid_add", "ask_pull", "ask_add", "bid_pull"]
        _colors  = [COLOR_BID_ADD, COLOR_ASK_PULL, COLOR_ASK_ADD, COLOR_BID_PULL]
        _symbols = ["t", "d", "t1", "x"]  # up-tri, diamond, right-tri, cross

        self._halo_items: dict[str, pg.ScatterPlotItem] = {}
        self._spot_items: dict[str, pg.ScatterPlotItem] = {}

        for metric, color, sym in zip(_metrics, _colors, _symbols):
            qc = QtGui.QColor(color)
            qc_halo = QtGui.QColor(color)
            qc_halo.setAlpha(60)          # faint halo
            halo = pg.ScatterPlotItem(
                symbol='o', pen=pg.mkPen(None),
                brush=pg.mkBrush(qc_halo),
                hoverable=False,
            )
            halo.setZValue(8)
            self.plot.addItem(halo)
            self._halo_items[metric] = halo

            border_pen = pg.mkPen(QtGui.QColor("#ffffff"), width=0.7, cosmetic=True)
            spot = pg.ScatterPlotItem(
                symbol=sym, pen=border_pen,
                brush=pg.mkBrush(qc),
                hoverable=False,
            )
            spot.setZValue(10)
            self.plot.addItem(spot)
            self._spot_items[metric] = spot

        # ── Crosshair + tooltip (verbatim from chart_gpu.py) ────────────── #
        self.crosshair_v = pg.InfiniteLine(angle=90, pen=pg.mkPen("#aaaaaa55", width=0.7))
        self.crosshair_h = pg.InfiniteLine(angle=0,  pen=pg.mkPen("#aaaaaa55", width=0.7))
        self.plot.addItem(self.crosshair_v, ignoreBounds=True)
        self.plot.addItem(self.crosshair_h, ignoreBounds=True)
        self.crosshair_v.setVisible(False); self.crosshair_h.setVisible(False)
        self.tooltip = pg.TextItem(
            text="", color="#dddddd", anchor=(0, 1),
            border=pg.mkPen("#444"), fill=pg.mkBrush("#101010cc"))
        self.plot.addItem(self.tooltip)
        self.tooltip.setVisible(False)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_move)
        self.plot.vb.disableAutoRange()
        self.plot.vb.sigRangeChanged.connect(self._on_view_range_changed)

        self.setStyleSheet("background:#050505;")

    # ── Controls ─────────────────────────────────────────────────────────── #
    def _on_tail_change(self, txt: str) -> None:
        try: self.tail_bars = 0 if txt == "full" else int(txt)
        except Exception: self.tail_bars = 2000
        self.last_mtime = 0.0; self.last_bf_mt = 0.0
        self._reload()

    def _on_aa_change(self, state) -> None:
        pg.setConfigOptions(antialias=bool(state)); self.candles.update()

    def _reset_view(self) -> None:
        if self.df.empty: return
        idx  = np.arange(len(self.df), dtype=float)
        ymin = float(pd.to_numeric(self.df["px_low"],  errors="coerce").min())
        ymax = float(pd.to_numeric(self.df["px_high"], errors="coerce").max())
        lane_pad = 7 * TICK
        self._user_zoomed = False
        self._in_reload = True
        try:
            self.plot.setXRange(float(idx[0]) - 0.5, float(idx[-1]) + 0.5, padding=0.02)
            self.plot.setYRange(ymin - lane_pad, ymax + lane_pad, padding=0.02)
        finally:
            self._in_reload = False

    def _on_view_range_changed(self, vb, ranges) -> None:
        if self._in_reload: return
        self._user_zoomed = True

    def _on_tick(self) -> None:
        """Poll master + BF cache for any file change; reload if newer."""
        master_changed = False
        bf_changed     = False
        try:
            mt = os.path.getmtime(self.master_path)
            if mt > self.last_mtime:
                self.last_mtime = mt; master_changed = True
        except Exception:
            pass
        try:
            bmt = _bf_cache_mtime(BF_CACHE)
            if bmt > self.last_bf_mt:
                self.last_bf_mt = bmt; bf_changed = True
        except Exception:
            pass
        if master_changed or bf_changed:
            self._reload()

    # ── Main reload ──────────────────────────────────────────────────────── #
    def _reload(self) -> None:
        t0 = time.time()
        # Load master bars
        try:
            df = load_master_tail(self.master_path, self.tail_bars)
        except Exception as e:
            self.status_lbl.setText(f"load error: {e}"); return
        if df.empty:
            self.status_lbl.setText("empty master"); return
        self.df = df
        n = len(self.df)
        idx = np.arange(n, dtype=float)
        o = pd.to_numeric(self.df["px_open"],  errors="coerce").to_numpy()
        h = pd.to_numeric(self.df["px_high"],  errors="coerce").to_numpy()
        l = pd.to_numeric(self.df["px_low"],   errors="coerce").to_numpy()
        c = pd.to_numeric(self.df["px_close"], errors="coerce").to_numpy()
        self.candles.set_data(idx, o, h, l, c)

        # Load BF level candles
        ts_arr    = self.df["bar_end_ts_ns"].dropna().astype("int64").tolist()
        ts_set    = set(ts_arr)
        try:
            bf_df = load_bf_level_candles(ts_set, BF_CACHE)
        except Exception:
            bf_df = pd.DataFrame()

        # ── Order-book volume profile ─────────────────────────────────────
        if not bf_df.empty:
            vp = of_volume_profile(bf_df, TICK)
        else:
            vp = {}
        if vp:
            self.ofvp_item.set_data(
                vp["levels"], vp["ba_v"], vp["ap_v"], vp["aa_v"], vp["bp_v"], TICK)
            self.vp_plot.setXRange(0.0, 1.0, padding=0.02)
            self.line_poc.setPos(vp["of_poc"]); self.line_poc.setVisible(True)
            self.lbl_poc.setPos(float(idx[-1]), vp["of_poc"]); self.lbl_poc.setVisible(True)
        else:
            self.line_poc.setVisible(False); self.lbl_poc.setVisible(False)

        # ── Per-bar OF aggregation for scatter + tooltip ──────────────────
        if not bf_df.empty:
            bar_of = bf_df.groupby("bar_end_ts_ns").agg(
                bid_add=("bid_add",  "sum"),
                bid_pull=("bid_pull", "sum"),
                ask_add=("ask_add",  "sum"),
                ask_pull=("ask_pull", "sum"),
            ).reset_index()
            bar_of_m = self.df[["bar_end_ts_ns"]].reset_index().merge(
                bar_of, on="bar_end_ts_ns", how="left"
            ).set_index("index").reindex(range(n))
        else:
            bar_of_m = pd.DataFrame({
                "bid_add": np.full(n, np.nan), "bid_pull": np.full(n, np.nan),
                "ask_add": np.full(n, np.nan), "ask_pull": np.full(n, np.nan)})
        self._bar_of = bar_of_m

        # ── Enhanced scatter peaks (size-tiered + halo, non-overlapping lanes) #
        # Each metric gets its own fixed vertical lane anchored to the bar's
        # high or low — bullish metrics float above the wick, bearish below it.
        # This guarantees no two metrics ever share the same Y at the same bar.
        #
        #  BidAdd  → h + 2*TICK   (inner-above  — closest bullish)
        #  AskPull → h + 5*TICK   (outer-above  — secondary bullish)
        #  AskAdd  → l - 2*TICK   (inner-below  — closest bearish)
        #  BidPull → l - 5*TICK   (outer-below  — secondary bearish)
        _mspec = [
            ("bid_add",  COLOR_BID_ADD,  h, +2),   # above wick, inner
            ("ask_pull", COLOR_ASK_PULL, h, +5),   # above wick, outer
            ("ask_add",  COLOR_ASK_ADD,  l, -2),   # below wick, inner
            ("bid_pull", COLOR_BID_PULL, l, -5),   # below wick, outer
        ]
        for metric, color, ref_arr, tick_off in _mspec:
            vals = (bar_of_m[metric].to_numpy(dtype=float)
                    if metric in bar_of_m.columns else np.full(n, np.nan))
            if np.isfinite(vals).sum() < 7:
                self._halo_items[metric].setData([], [])
                self._spot_items[metric].setData([], [])
                continue
            peaks, sizes = find_of_peaks_tiered(vals, lb=3)
            if len(peaks) == 0:
                self._halo_items[metric].setData([], [])
                self._spot_items[metric].setData([], [])
                continue
            px_x = idx[peaks]
            px_y = ref_arr[peaks] + tick_off * TICK  # dedicated lane, no overlap
            vm   = np.isfinite(ref_arr[peaks])        # valid if high/low is finite
            if not vm.any():
                self._halo_items[metric].setData([], [])
                self._spot_items[metric].setData([], [])
                continue
            xs = px_x[vm].tolist(); ys = px_y[vm].tolist()
            szs = sizes[vm].tolist()
            halo_szs = [s + 6 for s in szs]   # tighter halo to reduce crowd
            self._halo_items[metric].setData(x=xs, y=ys, size=halo_szs)
            self._spot_items[metric].setData(x=xs, y=ys, size=szs)

        # ── 12 OF S/R lines: 6 support (BullS) + 6 resistance (BearR) ──────
        x_left  = float(idx[0])
        x_right = float(idx[-1])
        if not bf_df.empty and len(ts_arr) > 0:
            sr_results = compute_of_sr_levels(bf_df, ts_arr, _WINDOWS)
        else:
            sr_results = []
        # Build lookup: (wi, pair_type) → (price, val, label)
        sr_lookup: dict[tuple, tuple] = {}
        for px, lbl, ptype, wi, val in sr_results:
            sr_lookup[(wi, ptype)] = (px, val, lbl)

        line_idx = 0
        for wi in range(6):  # 6 windows
            for ptype in ("bullish", "bearish"):
                ln    = self._ofsr_lines[line_idx]
                lbobj = self._ofsr_labels[line_idx]
                if (wi, ptype) in sr_lookup:
                    px, val, win_lbl = sr_lookup[(wi, ptype)]
                    ln.setPos(px); ln.setVisible(True)
                    pair_tag = "S" if ptype == "bullish" else "R"
                    lbobj.setText(f"{pair_tag}[{win_lbl}] {px:.0f}")
                    # Labels at left edge; stagger vertically by window index
                    y_off = 0.8 if ptype == "bullish" else -0.8
                    lbobj.setPos(x_left, px + y_off * (wi * 0.4 + 0.2))
                    lbobj.setVisible(True)
                else:
                    ln.setVisible(False); lbobj.setVisible(False)
                line_idx += 1

        # ── View range — pad for outer marker lanes (±5 TICK from high/low) ──
        ymin = float(np.nanmin(l)); ymax = float(np.nanmax(h))
        lane_pad = 7 * TICK   # outer lane is ±5 TICK; add 2 extra for breathing room
        if not self._user_zoomed:
            self._in_reload = True
            try:
                self.plot.setXRange(float(idx[0]) - 0.5, float(idx[-1]) + 0.5, padding=0.02)
                self.plot.setYRange(ymin - lane_pad, ymax + lane_pad, padding=0.02)
            finally:
                self._in_reload = False

        # ── Status ────────────────────────────────────────────────────────
        ts       = self.df.iloc[-1].get("timestamp_utc") or self.df.iloc[-1].get("timestamp")
        elapsed  = (time.time() - t0) * 1000
        n_bf     = (len(ts_set & set(bf_df["bar_end_ts_ns"].astype("int64").tolist()))
                    if not bf_df.empty else 0)
        n_sr     = len(sr_results)
        backend  = "GPU OpenGL" if _USE_GL else "software (CPU)"
        master_t = f"master: {self.master_path.name}"
        if "roll_quality_flag" in self.df.columns:
            rqf = str(self.df.iloc[-1].get("roll_quality_flag", "") or "")
            rgm = str(self.df.iloc[-1].get("roll_gap_method",   "") or "")
            if rqf and rqf != "nan":
                master_t += f"  |  {rqf}  ({rgm})  |  SHADOW/RESEARCH ONLY"
        self.master_lbl.setText(master_t)
        self.status_lbl.setText(
            f"bars: {n:,}  •  BF: {n_bf:,}  •  OF-SR lines: {n_sr}/12  "
            f"•  close: {float(c[-1]):.2f}  •  {str(ts)[:19]}  "
            f"•  {elapsed:.0f} ms  •  {backend}")

    # ── Crosshair / tooltip ───────────────────────────────────────────────── #
    def _on_mouse_move(self, pos) -> None:
        if not self.plot.sceneBoundingRect().contains(pos):
            self.crosshair_v.setVisible(False)
            self.crosshair_h.setVisible(False)
            self.tooltip.setVisible(False)
            return
        view = self.plot.vb.mapSceneToView(pos)
        x = view.x(); y = view.y()
        self.crosshair_v.setPos(x); self.crosshair_h.setPos(y)
        self.crosshair_v.setVisible(True); self.crosshair_h.setVisible(True)
        n = len(self.df)
        bar_i = int(round(x))
        if 0 <= bar_i < n:
            row = self.df.iloc[bar_i]
            ts  = row.get("timestamp_utc") or row.get("timestamp") or ""
            of_txt = ""
            if not self._bar_of.empty and bar_i < len(self._bar_of):
                r = self._bar_of.iloc[bar_i]
                ba = r.get("bid_add",  np.nan)
                bp = r.get("bid_pull", np.nan)
                aa = r.get("ask_add",  np.nan)
                ap = r.get("ask_pull", np.nan)
                if np.isfinite(ba):
                    of_txt = (f"\n BidAdd={ba:.0f}  AskPull={ap:.0f}"
                              f"\n AskAdd={aa:.0f}  BidPull={bp:.0f}"
                              f"\n BullPair={ba+ap:.0f}  BearPair={aa+bp:.0f}")
            txt = (f" bar {bar_i}  |  {str(ts)[:19]}\n"
                   f" O {row.get('px_open','--')}  H {row.get('px_high','--')}  "
                   f"L {row.get('px_low','--')}  C {row.get('px_close','--')}"
                   + of_txt + f"\n px @ cursor: {y:.2f}")
        else:
            txt = f" px @ cursor: {y:.2f}"
        self.tooltip.setText(txt); self.tooltip.setPos(x, y); self.tooltip.setVisible(True)


# ── CLI ──────────────────────────────────────────────────────────────────── #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master",     type=Path, default=DEFAULT_MASTER)
    ap.add_argument("--tail-bars",  type=int,  default=5000)
    ap.add_argument("--refresh-ms", type=int,  default=1500)
    args = ap.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.environ.setdefault("QT_LOGGING_RULES",
                          "qt.painting=false;qt.qpa.gl=false;qt.scenegraph=false")

    def _qt_msg_filter(mode, ctx, msg):
        if ("Painter not active" in msg or "Unbalanced save/restore" in msg
                or "Painter not active, aborted" in msg):
            return
        try: sys.stderr.write(msg + "\n")
        except Exception: pass
    try: QtCore.qInstallMessageHandler(_qt_msg_filter)
    except Exception: pass

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = OFSRChartWindow(args.master, args.tail_bars, args.refresh_ms)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
