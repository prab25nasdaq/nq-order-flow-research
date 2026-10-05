#!/usr/bin/env python3
"""
GPU-accelerated standalone chart for the OFI dashboard.

Reads /home/prabh/OFI_Live_Features/master.ndjsonl, plots OHLC candles +
POC/VAH/VAL lines in a pyqtgraph window with OpenGL backing.

Standalone process — own Qt event loop. Dashboard launches it via subprocess.
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

# GPU/CPU mode: try OpenGL first; if it fails the user can disable via env.
# Right now the system NVIDIA driver has a kernel/userspace version mismatch
# (nvidia-smi reports NVML failure), so OpenGL context creation fails. Set
# `OFI_CHART_GL=0` to force software fallback. Once you `sudo reboot` to
# realign the driver, leave OFI_CHART_GL unset to get real GPU rendering.
_USE_GL = os.environ.get("OFI_CHART_GL", "0") not in ("0", "false", "False")
pg.setConfigOptions(
    useOpenGL=_USE_GL,
    antialias=False,
    background="#050505",
    foreground="#dddddd",
    leftButtonPan=True,
)

# SHADOW/RESEARCH ONLY. Defaults to the same continuous adjusted NQ master as
# the dashboard (master_NQ_continuous_backadjusted_shadow.ndjsonl); override
# via OFI_MASTER_PATH.
DEFAULT_MASTER = Path(os.environ.get(
    "OFI_MASTER_PATH",
    "/home/prabh/OFI_Live_Features/master_NQ_continuous_backadjusted_shadow.ndjsonl",
))
TICK = 0.25


# ── Candle item — batched paint via QLineF / QRectF arrays ────────────────── #
class CandleItem(pg.GraphicsObject):
    """Draws ALL candles every paint(). For 5k bars at 60 fps this is well
    under 1 ms on any sane GPU. No exposedRect clipping — pyqtgraph already
    clips at the scene level when zoomed in."""
    def __init__(self):
        super().__init__()
        self._x = np.empty(0, dtype=float)
        self._o = np.empty(0, dtype=float)
        self._h = np.empty(0, dtype=float)
        self._l = np.empty(0, dtype=float)
        self._c = np.empty(0, dtype=float)
        # Pre-built batches updated in set_data
        self._wicks: list[QtCore.QLineF] = []
        self._up_rects: list[QtCore.QRectF] = []
        self._down_rects: list[QtCore.QRectF] = []
        self._bounds = QtCore.QRectF()
        # Cosmetic pens (1-device-pixel wide regardless of zoom)
        self._up_pen     = QtGui.QPen(QtGui.QColor("#00d27a"));  self._up_pen.setCosmetic(True)
        self._down_pen   = QtGui.QPen(QtGui.QColor("#ff4d4d"));  self._down_pen.setCosmetic(True)
        self._wick_pen   = QtGui.QPen(QtGui.QColor("#888888"));  self._wick_pen.setCosmetic(True)
        self._up_brush   = QtGui.QBrush(QtGui.QColor("#00d27a"))
        self._down_brush = QtGui.QBrush(QtGui.QColor("#ff4d4d"))

    def set_data(self, ts_idx: np.ndarray, o: np.ndarray, h: np.ndarray,
                  l: np.ndarray, c: np.ndarray) -> None:
        self._x = ts_idx.astype(float, copy=False)
        self._o = o.astype(float, copy=False)
        self._h = h.astype(float, copy=False)
        self._l = l.astype(float, copy=False)
        self._c = c.astype(float, copy=False)
        # Rebuild geometry batches
        self._wicks.clear()
        self._up_rects.clear()
        self._down_rects.clear()
        w = 0.6
        for i in range(len(ts_idx)):
            x_i = float(ts_idx[i])
            op, hp, lp, cp = float(o[i]), float(h[i]), float(l[i]), float(c[i])
            if np.isfinite(hp) and np.isfinite(lp):
                self._wicks.append(QtCore.QLineF(x_i, lp, x_i, hp))
            if np.isfinite(op) and np.isfinite(cp):
                # QRectF(x, y, width, height) — Qt takes y as 'top'.
                # pyqtgraph PlotItem inverts Y so a positive height grows
                # toward the top of the chart at higher price.
                lo_y = min(op, cp); hi_y = max(op, cp)
                # If body is zero-height, still draw a 1-px line (cosmetic)
                if hi_y - lo_y < 1e-9:
                    hi_y = lo_y + 1e-9
                rect = QtCore.QRectF(x_i - w/2, lo_y, w, hi_y - lo_y)
                if cp >= op: self._up_rects.append(rect)
                else:        self._down_rects.append(rect)
        # Bounding rect (in data coords)
        if len(ts_idx):
            lo = float(np.nanmin(l)); hi = float(np.nanmax(h))
            self._bounds = QtCore.QRectF(
                float(ts_idx[0]) - 1.0, lo,
                float(ts_idx[-1] - ts_idx[0]) + 2.0,
                hi - lo,
            )
        else:
            self._bounds = QtCore.QRectF()
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter, option, widget=None):
        if not self._wicks and not self._up_rects and not self._down_rects:
            return
        # Wicks (one batched drawLines call — fastest path on any backend)
        if self._wicks:
            painter.setPen(self._wick_pen)
            painter.drawLines(self._wicks)
        # Up bodies (filled)
        if self._up_rects:
            painter.setPen(self._up_pen)
            painter.setBrush(self._up_brush)
            painter.drawRects(self._up_rects)
        # Down bodies
        if self._down_rects:
            painter.setPen(self._down_pen)
            painter.setBrush(self._down_brush)
            painter.drawRects(self._down_rects)

    def boundingRect(self):
        return self._bounds


# ── Loader + volume profile ───────────────────────────────────────────────── #
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
    # Continuous adjusted master: continuous_open/high/low/close hold the
    # back-adjusted (gap-free) series; px_open/high/low/close otherwise hold
    # the raw per-contract prices. Swap so candles render the continuous
    # series, matching the dashboard.
    if "continuous_close" in df.columns:
        for c in ("open", "high", "low", "close"):
            cont_col, px_col = f"continuous_{c}", f"px_{c}"
            if cont_col in df.columns and px_col in df.columns:
                df[px_col] = df[cont_col]
    return df


# ── S/R levels — VERBATIM math from ofi_live_dashboard ────────────────────── #
def swing_levels(win: pd.DataFrame, lb: int = 3):
    """Centered rolling max/min over (2*lb+1) bars; same as dashboard."""
    if "px_high" not in win.columns or "px_low" not in win.columns:
        return [], []
    h = pd.to_numeric(win["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(win["px_low"],  errors="coerce").to_numpy()
    n = len(h); win_n = lb * 2 + 1
    if n < win_n: return [], []
    h_roll = pd.Series(h).rolling(win_n, center=True, min_periods=win_n).max().to_numpy()
    l_roll = pd.Series(l).rolling(win_n, center=True, min_periods=win_n).min().to_numpy()
    hi_idx = np.where(np.isfinite(h) & np.isfinite(h_roll) & (h >= h_roll))[0]
    lo_idx = np.where(np.isfinite(l) & np.isfinite(l_roll) & (l <= l_roll))[0]
    return [(int(i), float(h[i])) for i in hi_idx], [(int(i), float(l[i])) for i in lo_idx]


def compute_sr_levels(df: pd.DataFrame, lb: int = 3,
                       cluster_dist: float = 2.0) -> tuple[list, list]:
    """Cluster swing pivots + score by count × recency. VERBATIM from dashboard."""
    if df.empty or "px_high" not in df.columns:
        return [], []
    raw_h, raw_l = swing_levels(df, lb=lb)
    n_total = max(len(df), 1)

    def _cluster(raw: list) -> list:
        if not raw: return []
        raw_s = sorted(raw, key=lambda t: t[1])
        groups: list = [[raw_s[0]]]
        for bi, px in raw_s[1:]:
            if abs(px - groups[-1][-1][1]) <= cluster_dist:
                groups[-1].append((bi, px))
            else:
                groups.append([(bi, px)])
        out = []
        for grp in groups:
            med_px = float(np.median([p for _, p in grp]))
            count  = len(grp)
            recency = max(bi for bi, _ in grp) / n_total
            score = count * (0.4 + 0.6 * recency)
            out.append((med_px, count, score))
        return sorted(out, key=lambda t: -t[2])

    return _cluster(raw_h), _cluster(raw_l)


def volume_profile(df: pd.DataFrame, tick: float = TICK) -> dict:
    """Full volume profile — verbatim formula from ofi_live_dashboard.

    Returns a dict with:
      poc, vah, val:            scalar prices
      levels (np.array):        all tick-grid prices
      tot_v, buy_v, sel_v:      per-level totals
      hvn_px, lvn_px (lists):   prices flagged as HVN/LVN by local mean test
    """
    h = pd.to_numeric(df["px_high"], errors="coerce").to_numpy()
    l = pd.to_numeric(df["px_low"],  errors="coerce").to_numpy()
    vt = pd.to_numeric(df["vol_total"], errors="coerce").to_numpy()
    bv = pd.to_numeric(df.get("buy_vol", pd.Series([0.0]*len(df))), errors="coerce").to_numpy()
    sv = pd.to_numeric(df.get("sell_vol", pd.Series([0.0]*len(df))), errors="coerce").to_numpy()
    valid = np.isfinite(h) & np.isfinite(l)
    if not valid.any(): return {}
    pmin = float(np.nanmin(l[valid])); pmax = float(np.nanmax(h[valid]))
    lo_t = int(round(pmin / tick)) - 2
    hi_t = int(round(pmax / tick)) + 2
    n_lev = hi_t - lo_t + 1
    if n_lev < 2 or n_lev > 12000: return {}
    levels = np.array([round(j * tick, 2) for j in range(lo_t, hi_t + 1)])
    lev_lo = levels - tick * 0.5; lev_hi = levels + tick * 0.5
    tot_v = np.zeros(n_lev)
    buy_v = np.zeros(n_lev)
    sel_v = np.zeros(n_lev)
    for i in range(len(df)):
        if not (np.isfinite(h[i]) and np.isfinite(l[i])): continue
        br = max(h[i] - l[i], tick)
        ov = np.minimum(h[i], lev_hi) - np.maximum(l[i], lev_lo)
        m = ov > 0
        if m.any():
            w = np.where(m, ov / br, 0.0)
            tot_v += vt[i] * w
            if np.isfinite(bv[i]) and np.isfinite(sv[i]):
                buy_v += bv[i] * w
                sel_v += sv[i] * w
    if tot_v.max() == 0: return {}
    poc_idx = int(np.argmax(tot_v))
    tot_sum = tot_v.sum(); va_target = tot_sum * 0.70
    va = tot_v[poc_idx]; vah_i = poc_idx; val_i = poc_idx
    while va < va_target and (val_i > 0 or vah_i < n_lev - 1):
        up   = tot_v[vah_i+1] if vah_i < n_lev-1 else 0
        down = tot_v[val_i-1] if val_i > 0      else 0
        if up >= down: vah_i += 1; va += up
        else:          val_i -= 1; va += down
    # HVN / LVN — local-mean deviation (same as dashboard's _draw_vp)
    k = max(3, n_lev // 15)
    kernel = np.ones(k) / k
    smooth = np.convolve(tot_v, kernel, mode="same")
    hvn_mask = (tot_v > 0) & (tot_v > smooth * 1.45)
    lvn_mask = (tot_v > 0) & (tot_v < smooth * 0.55)
    return {
        "poc": float(levels[poc_idx]),
        "vah": float(levels[vah_i]),
        "val": float(levels[val_i]),
        "levels": levels,
        "tot_v":  tot_v,
        "buy_v":  buy_v,
        "sel_v":  sel_v,
        "hvn_px": levels[hvn_mask].tolist(),
        "lvn_px": levels[lvn_mask].tolist(),
    }


# ── Volume profile item (right-side histogram) ───────────────────────────── #
class VolumeProfileItem(pg.GraphicsObject):
    """Horizontal volume-profile histogram — green=buy, red=sell, split.

    Drawn on a PlotItem to the right of the candle plot. Y-axis shared with
    the candle plot (so HVN/LVN/POC/VAH/VAL all line up with the bars).

    X axis range is normalized to 0..1 (POC = 1).
    """
    def __init__(self):
        super().__init__()
        self._levels: np.ndarray = np.empty(0, dtype=float)
        self._buy:    np.ndarray = np.empty(0, dtype=float)
        self._sel:    np.ndarray = np.empty(0, dtype=float)
        self._hvn:    set = set()
        self._lvn:    set = set()
        self._bounds = QtCore.QRectF(0, 0, 1, 1)
        self._buy_rects: list = []
        self._sel_rects: list = []
        self._buy_color  = QtGui.QColor("#00d27a")
        self._sel_color  = QtGui.QColor("#ff4d4d")
        self._hvn_color  = QtGui.QColor("#ffd70066")
        self._lvn_color  = QtGui.QColor("#88888833")

    def set_data(self, levels: np.ndarray, buy_v: np.ndarray, sel_v: np.ndarray,
                  hvn_px: list, lvn_px: list, tick: float) -> None:
        tot_v = buy_v + sel_v
        if tot_v.max() == 0:
            self._buy_rects = []; self._sel_rects = []
            return
        mv = float(tot_v.max())
        self._levels = levels
        self._buy = buy_v
        self._sel = sel_v
        self._hvn = set(hvn_px)
        self._lvn = set(lvn_px)
        bar_h = tick * 0.92
        self._buy_rects = []
        self._sel_rects = []
        for j in range(len(levels)):
            if tot_v[j] < mv * 0.002: continue
            px = float(levels[j])
            t  = min(tot_v[j] / mv, 1.0)
            tv_j = tot_v[j] if tot_v[j] > 0 else 1.0
            bq = t * (buy_v[j] / tv_j)
            sq = t * (sel_v[j] / tv_j)
            if bq > 1e-4:
                self._buy_rects.append(QtCore.QRectF(0.0, px - bar_h/2, bq, bar_h))
            if sq > 1e-4:
                self._sel_rects.append(QtCore.QRectF(bq, px - bar_h/2, sq, bar_h))
        ymin = float(levels[0]); ymax = float(levels[-1])
        self._bounds = QtCore.QRectF(0, ymin, 1.0, ymax - ymin)
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter, option, widget=None):
        # HVN/LVN backgrounds (drawn first so bars overlay them)
        if len(self._levels):
            tick = TICK
            hvn_brush = QtGui.QBrush(self._hvn_color)
            lvn_brush = QtGui.QBrush(self._lvn_color)
            painter.setPen(QtCore.Qt.NoPen)
            for px in self._hvn:
                painter.setBrush(hvn_brush)
                painter.drawRect(QtCore.QRectF(0.0, px - tick/2, 1.0, tick))
            for px in self._lvn:
                painter.setBrush(lvn_brush)
                painter.drawRect(QtCore.QRectF(0.0, px - tick/2, 1.0, tick))
        # Buy bars (green, left side from x=0)
        if self._buy_rects:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QBrush(self._buy_color))
            painter.drawRects(self._buy_rects)
        # Sell bars (red, immediately right of buy)
        if self._sel_rects:
            painter.setBrush(QtGui.QBrush(self._sel_color))
            painter.drawRects(self._sel_rects)

    def boundingRect(self):
        return self._bounds


# ── Main window ────────────────────────────────────────────────────────────── #
class GPUChartWindow(QtWidgets.QMainWindow):
    def __init__(self, master_path: Path, tail_bars: int, refresh_ms: int):
        super().__init__()
        self.master_path = master_path
        self.tail_bars   = tail_bars
        self.refresh_ms  = refresh_ms
        self.df: pd.DataFrame = pd.DataFrame()
        self.last_mtime = 0.0
        # View-preservation state — mirrors ofi_live_dashboard's
        # _chart_user_zoomed pattern: once the user pans/zooms, _reload()
        # never calls setXRange/setYRange again (only "Reset view" does).
        self._user_zoomed = False
        self._in_reload = False
        self._build_ui()
        # First load — using QTimer.singleShot so the window paints first
        QtCore.QTimer.singleShot(50, self._reload)
        # Periodic refresh
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(refresh_ms)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()

    def _build_ui(self) -> None:
        title = "OFI Chart — GPU (pyqtgraph + OpenGL)"
        if "continuous" in str(self.master_path).lower():
            title += "  —  CONTINUOUS ADJUSTED NQ (SHADOW/RESEARCH ONLY)"
        self.setWindowTitle(title)
        self.resize(1600, 900)

        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        layout = QtWidgets.QVBoxLayout(cw)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # Toolbar
        tb = QtWidgets.QToolBar()
        tb.setStyleSheet("background:#0c0c0c; color:#aaaaaa;")
        layout.addWidget(tb)

        tb.addWidget(QtWidgets.QLabel("  tail bars: "))
        self.tail_combo = QtWidgets.QComboBox()
        self.tail_combo.addItems(["500", "1000", "2000", "5000", "10000", "full"])
        cur = str(self.tail_bars) if self.tail_bars > 0 else "full"
        idx = self.tail_combo.findText(cur)
        self.tail_combo.setCurrentIndex(idx if idx >= 0 else 3)
        self.tail_combo.currentTextChanged.connect(self._on_tail_change)
        tb.addWidget(self.tail_combo)

        tb.addWidget(QtWidgets.QLabel("    "))
        self.aa_cb = QtWidgets.QCheckBox("anti-alias")
        self.aa_cb.setChecked(False)
        self.aa_cb.stateChanged.connect(self._on_aa_change)
        tb.addWidget(self.aa_cb)

        tb.addWidget(QtWidgets.QLabel("    "))
        btn_reset = QtWidgets.QPushButton("Reset view")
        btn_reset.clicked.connect(self._reset_view)
        btn_reset.setStyleSheet("background:#222; color:#ddd; padding:3px 10px;")
        tb.addWidget(btn_reset)

        tb.addWidget(QtWidgets.QLabel("    "))
        self.gpu_lbl = QtWidgets.QLabel("backend: " + ("OpenGL" if _USE_GL else "software (CPU)"))
        self.gpu_lbl.setStyleSheet("color:#00ccff;" if _USE_GL else "color:#ffaa55;")
        tb.addWidget(self.gpu_lbl)

        tb.addWidget(QtWidgets.QLabel("    "))
        self.master_lbl = QtWidgets.QLabel(f"master: {self.master_path.name}")
        self.master_lbl.setStyleSheet("color:#cc99ff;")
        tb.addWidget(self.master_lbl)

        # Right side: status
        spacer = QtWidgets.QWidget(); spacer.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.status_lbl = QtWidgets.QLabel("loading…")
        self.status_lbl.setStyleSheet("color:#ffd28c; padding-right:10px;")
        tb.addWidget(self.status_lbl)

        # Plot (use GraphicsLayoutWidget so we can pack candle chart + vol-profile)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("#050505")
        if _USE_GL:
            try: self.glw.useOpenGL(True)
            except Exception: pass
        layout.addWidget(self.glw)

        # Left: candle plot (85% width)
        self.plot = self.glw.addPlot(row=0, col=0)
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        for ax in ("left", "right", "top", "bottom"):
            try:
                self.plot.getAxis(ax).setPen("#222")
                self.plot.getAxis(ax).setTextPen("#aaa")
            except Exception: pass

        # Right: volume profile panel (compact)
        self.vp_plot = self.glw.addPlot(row=0, col=1)
        self.vp_plot.showGrid(x=False, y=True, alpha=0.05)
        for ax in ("left", "right", "top", "bottom"):
            try:
                self.vp_plot.getAxis(ax).setPen("#222")
                self.vp_plot.getAxis(ax).setTextPen("#666")
            except Exception: pass
        self.vp_plot.hideAxis("left")        # Y already labeled on main chart
        self.vp_plot.hideAxis("bottom")      # X is normalized 0..1
        self.vp_plot.setMouseEnabled(x=False, y=False)
        self.vp_plot.setMenuEnabled(False)
        # Stretch ratios — candle plot 85%, vp 15%
        self.glw.ci.layout.setColumnStretchFactor(0, 85)
        self.glw.ci.layout.setColumnStretchFactor(1, 15)
        self.glw.ci.layout.setHorizontalSpacing(0)
        self.glw.ci.layout.setContentsMargins(0, 0, 0, 0)
        # Link Y axes — volume profile must always match candle plot vertically
        self.vp_plot.setYLink(self.plot)

        self.candles = CandleItem()
        self.plot.addItem(self.candles)
        self.vp_item = VolumeProfileItem()
        self.vp_plot.addItem(self.vp_item)

        # POC / VAH / VAL on main chart
        self.line_poc = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ffd700", width=1.6))
        self.line_vah = pg.InfiniteLine(angle=0, pen=pg.mkPen("#4488ff", width=0.9,
                                                              style=QtCore.Qt.DashLine))
        self.line_val = pg.InfiniteLine(angle=0, pen=pg.mkPen("#4488ff", width=0.9,
                                                              style=QtCore.Qt.DashLine))
        self.plot.addItem(self.line_poc)
        self.plot.addItem(self.line_vah)
        self.plot.addItem(self.line_val)
        self.lbl_poc = pg.TextItem("POC", color="#ffd700", anchor=(0, 0.5))
        self.lbl_vah = pg.TextItem("VAH", color="#4488ff", anchor=(0, 0.5))
        self.lbl_val = pg.TextItem("VAL", color="#4488ff", anchor=(0, 0.5))
        self.plot.addItem(self.lbl_poc)
        self.plot.addItem(self.lbl_vah)
        self.plot.addItem(self.lbl_val)

        # Value-area shaded band (faint green between VAL and VAH)
        self.va_region = pg.LinearRegionItem(orientation="horizontal",
                                              brush=pg.mkBrush(60, 180, 80, 18),
                                              pen=pg.mkPen("#00000000"),
                                              movable=False)
        self.va_region.setZValue(-10)
        self.plot.addItem(self.va_region)

        # HVN / LVN lines — added/cleared dynamically on each reload
        self._hvn_lines: list[pg.InfiniteLine] = []
        self._lvn_lines: list[pg.InfiniteLine] = []

        # ── S/R level pool: 6 resistance + 6 support — VERBATIM dashboard pool ── #
        # Style: dashed-dotted, red #ff3333 (R) and green #00cc44 (S);
        # alpha + width vary with touch count.
        self._sr_R_lines: list[pg.InfiniteLine] = []
        self._sr_S_lines: list[pg.InfiniteLine] = []
        self._sr_R_labels: list[pg.TextItem] = []
        self._sr_S_labels: list[pg.TextItem] = []
        for _ in range(6):
            ln_r = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ff3333", width=0.9,
                                                          style=QtCore.Qt.DashDotLine))
            ln_r.setVisible(False); ln_r.setZValue(-3)
            self.plot.addItem(ln_r, ignoreBounds=True)
            self._sr_R_lines.append(ln_r)
            lbl_r = pg.TextItem(text="", color="#ff3333", anchor=(0, 1))
            lbl_r.setVisible(False)
            self.plot.addItem(lbl_r)
            self._sr_R_labels.append(lbl_r)
            ln_s = pg.InfiniteLine(angle=0, pen=pg.mkPen("#00cc44", width=0.9,
                                                          style=QtCore.Qt.DashDotLine))
            ln_s.setVisible(False); ln_s.setZValue(-3)
            self.plot.addItem(ln_s, ignoreBounds=True)
            self._sr_S_lines.append(ln_s)
            lbl_s = pg.TextItem(text="", color="#00cc44", anchor=(0, 0))
            lbl_s.setVisible(False)
            self.plot.addItem(lbl_s)
            self._sr_S_labels.append(lbl_s)
        # Cache so recompute happens only when the window changes
        self._sr_cache_key: Optional[tuple] = None
        self._sr_R: list = []
        self._sr_S: list = []

        # Crosshair (cheap to update — Qt scene cache handles it)
        self.crosshair_v = pg.InfiniteLine(angle=90, pen=pg.mkPen("#aaaaaa55", width=0.7))
        self.crosshair_h = pg.InfiniteLine(angle=0,  pen=pg.mkPen("#aaaaaa55", width=0.7))
        self.plot.addItem(self.crosshair_v, ignoreBounds=True)
        self.plot.addItem(self.crosshair_h, ignoreBounds=True)
        self.crosshair_v.setVisible(False); self.crosshair_h.setVisible(False)
        self.tooltip = pg.TextItem(text="", color="#dddddd", anchor=(0, 1),
                                    border=pg.mkPen("#444"),
                                    fill=pg.mkBrush("#101010cc"))
        self.plot.addItem(self.tooltip)
        self.tooltip.setVisible(False)
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_move)

        # Disable pyqtgraph's auto-range so set_data() on new bars never
        # silently rescales the view; only our explicit setXRange/setYRange
        # (in _reload/_reset_view) or the user's own pan/zoom move it.
        self.plot.vb.disableAutoRange()
        self.plot.vb.sigRangeChanged.connect(self._on_view_range_changed)

        self.setStyleSheet("background:#050505;")

    # ── Controls ──────────────────────────────────────────────────────────── #
    def _on_tail_change(self, txt: str) -> None:
        try: self.tail_bars = 0 if txt == "full" else int(txt)
        except Exception: self.tail_bars = 2000
        self.last_mtime = 0.0
        self._reload()

    def _on_aa_change(self, state) -> None:
        pg.setConfigOptions(antialias=bool(state))
        self.candles.update()

    def _reset_view(self) -> None:
        if self.df.empty:
            return
        idx = np.arange(len(self.df), dtype=float)
        ymin = float(pd.to_numeric(self.df["px_low"], errors="coerce").min())
        ymax = float(pd.to_numeric(self.df["px_high"], errors="coerce").max())
        self._user_zoomed = False
        self._in_reload = True
        try:
            self.plot.setXRange(float(idx[0]) - 0.5, float(idx[-1]) + 0.5, padding=0.02)
            self.plot.setYRange(ymin - 1, ymax + 1, padding=0.02)
        finally:
            self._in_reload = False

    def _on_view_range_changed(self, vb, ranges) -> None:
        """User pan/zoom — remember it so _reload() never resets the view."""
        if self._in_reload:
            return
        self._user_zoomed = True

    def _on_tick(self) -> None:
        try:
            mt = os.path.getmtime(self.master_path)
        except Exception:
            return
        if mt > self.last_mtime:
            self.last_mtime = mt
            self._reload()

    def _reload(self) -> None:
        t0 = time.time()
        try:
            df = load_master_tail(self.master_path, self.tail_bars)
        except Exception as e:
            self.status_lbl.setText(f"load error: {e}")
            return
        if df.empty:
            self.status_lbl.setText("empty master")
            return
        self.df = df
        n = len(self.df)
        idx = np.arange(n, dtype=float)
        o = pd.to_numeric(self.df["px_open"],  errors="coerce").to_numpy()
        h = pd.to_numeric(self.df["px_high"],  errors="coerce").to_numpy()
        l = pd.to_numeric(self.df["px_low"],   errors="coerce").to_numpy()
        c = pd.to_numeric(self.df["px_close"], errors="coerce").to_numpy()
        self.candles.set_data(idx, o, h, l, c)

        # Volume profile / level lines
        vp = volume_profile(self.df)
        # Clear previous HVN/LVN lines first
        for ln in self._hvn_lines: self.plot.removeItem(ln)
        for ln in self._lvn_lines: self.plot.removeItem(ln)
        self._hvn_lines.clear(); self._lvn_lines.clear()
        if vp:
            # POC / VAH / VAL
            self.line_poc.setPos(vp["poc"]); self.line_poc.setVisible(True)
            self.line_vah.setPos(vp["vah"]); self.line_vah.setVisible(True)
            self.line_val.setPos(vp["val"]); self.line_val.setVisible(True)
            x_right = float(idx[-1])
            self.lbl_poc.setPos(x_right, vp["poc"]); self.lbl_poc.setVisible(True)
            self.lbl_vah.setPos(x_right, vp["vah"]); self.lbl_vah.setVisible(True)
            self.lbl_val.setPos(x_right, vp["val"]); self.lbl_val.setVisible(True)
            # Value-area shaded band
            self.va_region.setRegion((vp["val"], vp["vah"]))
            self.va_region.setVisible(True)
            # Volume profile right panel
            self.vp_item.set_data(vp["levels"], vp["buy_v"], vp["sel_v"],
                                    vp["hvn_px"], vp["lvn_px"], TICK)
            self.vp_plot.setXRange(0.0, 1.0, padding=0.02)
            # HVN markers on main chart (gold horizontal lines, thin)
            hvn_pen = pg.mkPen("#ffd70066", width=0.7, style=QtCore.Qt.SolidLine)
            for px in vp["hvn_px"]:
                ln = pg.InfiniteLine(angle=0, pos=float(px), pen=hvn_pen)
                ln.setZValue(-5)
                self.plot.addItem(ln, ignoreBounds=True)
                self._hvn_lines.append(ln)
            # LVN markers on main chart (subtle gray dashed)
            lvn_pen = pg.mkPen("#88888833", width=0.7, style=QtCore.Qt.DotLine)
            for px in vp["lvn_px"]:
                ln = pg.InfiniteLine(angle=0, pos=float(px), pen=lvn_pen)
                ln.setZValue(-6)
                self.plot.addItem(ln, ignoreBounds=True)
                self._lvn_lines.append(ln)
        else:
            for it in (self.line_poc, self.line_vah, self.line_val,
                       self.lbl_poc, self.lbl_vah, self.lbl_val):
                it.setVisible(False)
            self.va_region.setVisible(False)

        # Set view range explicitly (autoRange tends to be fragile on first
        # paint) — but only if the user hasn't manually panned/zoomed yet.
        # Once they have, never touch the range again on reload (mirrors
        # ofi_live_dashboard's _chart_user_zoomed behavior); "Reset view"
        # re-enables auto-fit.
        ymin = float(np.nanmin(l)); ymax = float(np.nanmax(h))
        if not self._user_zoomed:
            self._in_reload = True
            try:
                self.plot.setXRange(float(idx[0]) - 0.5, float(idx[-1]) + 0.5, padding=0.02)
                self.plot.setYRange(ymin - 1, ymax + 1, padding=0.02)
            finally:
                self._in_reload = False

        # ── S/R levels (dashboard formula: 6+6, lb=4, cluster_dist=6.0 or 3.0)
        try:
            sr_key = (n, float(c[0]), float(c[-1]))
            if self._sr_cache_key != sr_key:
                cluster_dist = 6.0 if n >= 300 else 3.0
                _R, _S = compute_sr_levels(self.df, lb=4, cluster_dist=cluster_dist)
                self._sr_R = _R; self._sr_S = _S
                self._sr_cache_key = sr_key
            # Visible range with 5-pt pad (matches dashboard)
            pad = 5.0
            ch_lo = ymin - pad; ch_hi = ymax + pad
            vis_R = [(px, cnt, sc) for px, cnt, sc in self._sr_R
                     if ch_lo <= px <= ch_hi][:6]
            vis_S = [(px, cnt, sc) for px, cnt, sc in self._sr_S
                     if ch_lo <= px <= ch_hi][:6]
            x_left = float(idx[0])
            x_right = float(idx[-1])
            # Resistance: paint top 6, hide unused
            for i in range(6):
                if i < len(vis_R):
                    px_, cnt, _sc = vis_R[i]
                    lw = 1.4 if cnt >= 3 else 0.9
                    alp = min(0.85, 0.40 + 0.20 * min(cnt, 3))
                    pen = pg.mkPen(QtGui.QColor(255, 51, 51, int(alp * 255)),
                                    width=lw, style=QtCore.Qt.DashDotLine)
                    self._sr_R_lines[i].setPen(pen)
                    self._sr_R_lines[i].setPos(px_)
                    self._sr_R_lines[i].setVisible(True)
                    txt = f"R {px_:.0f}" + (f" ×{cnt}" if cnt > 1 else "")
                    self._sr_R_labels[i].setText(txt, color=QtGui.QColor(255, 51, 51))
                    self._sr_R_labels[i].setPos(x_left, px_)
                    self._sr_R_labels[i].setVisible(True)
                else:
                    self._sr_R_lines[i].setVisible(False)
                    self._sr_R_labels[i].setVisible(False)
            # Support: paint top 6
            for i in range(6):
                if i < len(vis_S):
                    px_, cnt, _sc = vis_S[i]
                    lw = 1.4 if cnt >= 3 else 0.9
                    alp = min(0.85, 0.40 + 0.20 * min(cnt, 3))
                    pen = pg.mkPen(QtGui.QColor(0, 204, 68, int(alp * 255)),
                                    width=lw, style=QtCore.Qt.DashDotLine)
                    self._sr_S_lines[i].setPen(pen)
                    self._sr_S_lines[i].setPos(px_)
                    self._sr_S_lines[i].setVisible(True)
                    txt = f"S {px_:.0f}" + (f" ×{cnt}" if cnt > 1 else "")
                    self._sr_S_labels[i].setText(txt, color=QtGui.QColor(0, 204, 68))
                    self._sr_S_labels[i].setPos(x_left, px_)
                    self._sr_S_labels[i].setVisible(True)
                else:
                    self._sr_S_lines[i].setVisible(False)
                    self._sr_S_labels[i].setVisible(False)
        except Exception:
            pass

        # Master-source label: roll gap quality/method, when present (continuous master).
        last = self.df.iloc[-1]
        master_txt = f"master: {self.master_path.name}"
        if "roll_quality_flag" in self.df.columns:
            rqf = str(last.get("roll_quality_flag", "") or "")
            rgm = str(last.get("roll_gap_method", "") or "")
            if rqf and rqf != "nan":
                master_txt += f"  |  {rqf}  ({rgm})  |  SHADOW/RESEARCH ONLY"
        self.master_lbl.setText(master_txt)

        ts = self.df.iloc[-1].get("timestamp_utc") or self.df.iloc[-1].get("timestamp")
        elapsed = (time.time() - t0) * 1000
        backend = "GPU OpenGL" if _USE_GL else "software (CPU fallback — fix nvidia driver to enable GPU)"
        self.status_lbl.setText(
            f"bars: {n:,}  •  close: {float(c[-1]):.2f}  •  latest: {str(ts)[:19]}  "
            f"•  reload {elapsed:.0f} ms  •  {backend}")

    # ── Cursor ────────────────────────────────────────────────────────────── #
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
            ts = row.get("timestamp_utc") or row.get("timestamp") or ""
            txt = (f" bar {bar_i}  |  {str(ts)[:19]}\n"
                   f" O {row.get('px_open','--')}  H {row.get('px_high','--')}  "
                   f"L {row.get('px_low','--')}  C {row.get('px_close','--')}\n"
                   f" px @ cursor: {y:.2f}")
        else:
            txt = f" px @ cursor: {y:.2f}"
        self.tooltip.setText(txt); self.tooltip.setPos(x, y); self.tooltip.setVisible(True)


# ── CLI ────────────────────────────────────────────────────────────────────── #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    ap.add_argument("--tail-bars", type=int, default=5000)
    ap.add_argument("--refresh-ms", type=int, default=1500)
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
    win = GPUChartWindow(args.master, args.tail_bars, args.refresh_ms)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
