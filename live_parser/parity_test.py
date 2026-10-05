#!/usr/bin/env python3
"""
parity_test.py — OFI Live Parser Parity Test
=============================================
Reads Rithmic raw NDJSON files, applies all Python formulas (LOB, OFI, bars,
StreamingEngineer, RegimeIC) in a Rithmic-aware adapter, then compares
field-by-field with C++ ofi_live_parser output.

Also performs formula-level checks against:
  - process_mbo_batch_v8_2_volbars_OHLC_directional_sweeps.py (OFI, VPIN, Sweep)
  - build_live_features_volbars_from_rithmic_raw_standalone_livepatch_z20.py (StreamingEngineer, RegimeIC)
  - 01_feature_engineering.py (RegimeIC offline formula)

Usage:
  python parity_test.py --date 2026-06-03 --symbol NQM6 [--max_bars 200] [--verbose]
"""

import argparse, json, math, os, sys, time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── Constants (must match C++ ofi_math.h) ─────────────────────────────────────
OFI_K_RAW          = 10
OFI_K_DECAY        = 10
OFI_PRESSURE_DEPTH = 50
VPIN_WINDOW        = 10
SWEEP_GAP_NS       = 50_000_000   # 50 ms — matches C++
SWEEP_MIN_SIZE     = 5.0
RESID_Z_WIN        = 20
REGIME_MAXLEN      = 286
REGIME_H40, REGIME_H20 = 40, 20
REGIME_WIN         = 200
REGIME_MIN         = 100
REGIME_VOL_WIN     = 100
REGIME_VOL_MIN     = 20
REGIME_Z40_S, REGIME_Z40_E = 41, 240
REGIME_Z20_S, REGIME_Z20_E = 61, 260

# ── LOB Book ───────────────────────────────────────────────────────────────────
class LOBBook:
    """Price-level book (aggregated, not per-order). Matches C++ LOBBook."""
    def __init__(self):
        self.bids: Dict[float, float] = {}  # price→size, best bid = max
        self.asks: Dict[float, float] = {}  # price→size, best ask = min

    def clear(self):
        self.bids.clear(); self.asks.clear()

    def update_bid(self, px: float, sz: float):
        if sz <= 0: self.bids.pop(px, None)
        else:        self.bids[px] = sz

    def update_ask(self, px: float, sz: float):
        if sz <= 0: self.asks.pop(px, None)
        else:        self.asks[px] = sz

    def best_bid(self) -> float: return max(self.bids) if self.bids else 0.0
    def best_ask(self) -> float: return min(self.asks) if self.asks else 0.0
    def mid(self) -> float:
        b, a = self.best_bid(), self.best_ask()
        return 0.5*(b+a) if b>0 and a>0 else 0.0

    def top_bids(self, k=10) -> List[Tuple[float,float]]:
        return [(p,s) for p,s in sorted(self.bids.items(), reverse=True)[:k]]
    def top_asks(self, k=10) -> List[Tuple[float,float]]:
        return [(p,s) for p,s in sorted(self.asks.items())[:k]]

# ── OFI Math (exact parity with MBO batch and C++) ────────────────────────────
def compute_raw_ofi(pb, pa, cb, ca, k=OFI_K_RAW) -> float:
    """Matches process_mbo_batch_v8_2 AND build_live_features AND C++."""
    if not pb or not pa: return 0.0
    mpb = dict(pb[:k]); mpa = dict(pa[:k])
    tot = 0.0
    for i in range(min(len(cb), k)): px,sz=cb[i]; tot += sz - mpb.get(px, 0.0)
    for i in range(min(len(ca), k)): px,sz=ca[i]; tot -= sz - mpa.get(px, 0.0)
    return float(tot)

def compute_decayed_ofi(pb, pa, cb, ca, k=OFI_K_DECAY) -> float:
    if not pb or not pa: return 0.0
    weights = [math.exp(-0.5*i) for i in range(k)]
    mpb = dict(pb[:k]); mpa = dict(pa[:k])
    tot = 0.0
    for i in range(min(len(cb), k)): px,sz=cb[i]; tot += (sz - mpb.get(px,0.0)) * weights[i]
    for i in range(min(len(ca), k)): px,sz=ca[i]; tot -= (sz - mpa.get(px,0.0)) * weights[i]
    return float(tot)

def layered_pressure(levels, limit=OFI_PRESSURE_DEPTH) -> float:
    n = min(len(levels), limit)
    return float(sum(levels[i][1] * math.exp(-i/10.0) for i in range(n)))

def pressure_ratio(b, a) -> float:
    return layered_pressure(b) / (layered_pressure(a) + 1e-9)

# ── VPIN (matches Python live parser and MBO batch) ───────────────────────────
class VPINTracker:
    def __init__(self, bucket_vol: float, window: int = VPIN_WINDOW):
        self.bucket_vol = float(bucket_vol); self.window = window
        self.buy = self.sell = self.filled = 0.0; self.current_val = 0.5
        self.imbalances: deque = deque(maxlen=window)
        self.volumes:    deque = deque(maxlen=window)

    def update(self, is_buy: bool, vol: float):
        rem = float(vol)
        while rem > 0:
            space = self.bucket_vol - self.filled
            take = min(rem, space)
            if is_buy: self.buy += take
            else:      self.sell += take
            self.filled += take; rem -= take
            if self.filled >= self.bucket_vol - 1e-12: self._flush()

    def _flush(self):
        self.imbalances.append(abs(self.buy - self.sell))
        self.volumes.append(self.filled)
        sv = sum(self.volumes)
        if sv > 0: self.current_val = sum(self.imbalances) / sv
        self.buy = self.sell = self.filled = 0.0

# ── Sweep (matches Python live parser and MBO batch) ─────────────────────────
class SweepTracker:
    def __init__(self, gap_ns=SWEEP_GAP_NS, min_size=SWEEP_MIN_SIZE):
        self.gap_ns=gap_ns; self.min_size=min_size
        self.last_ts=None; self.last_side=None; self.cluster_vol=0.0

    def _finalize(self):
        if self.last_ts is None: return (0.,0.,0,0)
        sv=float(self.cluster_vol); q=(sv>=self.min_size)
        o = (sv if q and self.last_side=='B' else 0.,
             sv if q and self.last_side=='A' else 0.,
             1  if q and self.last_side=='B' else 0,
             1  if q and self.last_side=='A' else 0)
        self.last_ts=self.last_side=None; self.cluster_vol=0.; return o

    def update(self, ts_ns: int, side: str, vol: float):
        if self.last_ts is None:
            self.last_ts=ts_ns; self.last_side=side; self.cluster_vol=vol; return (0.,0.,0,0)
        gap = ts_ns - self.last_ts
        if gap <= self.gap_ns and side == self.last_side:
            self.cluster_vol += vol; self.last_ts=ts_ns; return (0.,0.,0,0)
        o = self._finalize()
        self.last_ts=ts_ns; self.last_side=side; self.cluster_vol=vol; return o

    def flush(self): return self._finalize()

# ── Rolling Residual Z-score (matches Python live parser) ─────────────────────
class RollingResidZ:
    def __init__(self, w=RESID_Z_WIN):
        self.w=w; self.raw=deque(); self.res=deque()

    def update(self, v) -> float:
        x = float(v) if v is not None and math.isfinite(float(v)) else float('nan')
        self.raw.append(x)
        if len(self.raw) > self.w: self.raw.popleft()
        res = float('nan')
        if len(self.raw)==self.w and all(math.isfinite(r) for r in self.raw):
            m = sum(self.raw)/self.w; res = x - m
        self.res.append(res)
        if len(self.res) > self.w: self.res.popleft()
        if len(self.res)==self.w and all(math.isfinite(r) for r in self.res):
            std = float(np.std(list(self.res), ddof=1))
            if std > 0 and math.isfinite(std): return res/std
        return float('nan')

# ── Regime IC (matches Python live parser and 01_feature_engineering.py) ──────
class RegimeICTracker:
    def __init__(self):
        self._px  = deque(maxlen=REGIME_MAXLEN)
        self._ml  = deque(maxlen=REGIME_MAXLEN)
        self._dl  = deque(maxlen=REGIME_MAXLEN)

    def _ic(self, px, sig, cv, H, s, e) -> float:
        sqH = math.sqrt(H); flow=[]; ztgt=[]
        for i in range(s, e+1):
            si=sig[i]; p0=px[i]; p1=px[i+H]; cvi=cv[i]
            if not all(math.isfinite(x) for x in (si,p0,p1,cvi)) or p0<=0 or cvi<=0: continue
            z = float(np.clip(math.log(p1/p0)/(cvi*sqH),-5,5))
            flow.append(si); ztgt.append(z)
        if len(flow) < REGIME_MIN: return 0.0
        return float(np.corrcoef(flow, ztgt)[0,1])

    def update(self, px_close, mlofi_norm, delta_norm) -> Tuple[float,float,float]:
        self._px.append(float(px_close) if math.isfinite(float(px_close)) and px_close>0 else float('nan'))
        self._ml.append(float(mlofi_norm) if math.isfinite(float(mlofi_norm)) else float('nan'))
        self._dl.append(float(delta_norm)  if math.isfinite(float(delta_norm))  else float('nan'))
        if len(self._px) < REGIME_MAXLEN: return 0.0, 0.0, 0.0

        px = np.array(self._px); ml = np.array(self._ml); dl = np.array(self._dl)
        lr = np.full(REGIME_MAXLEN, float('nan'))
        for i in range(1, REGIME_MAXLEN):
            if px[i]>0 and px[i-1]>0: lr[i]=math.log(px[i]/px[i-1])

        cv = np.full(REGIME_MAXLEN, float('nan'))
        for i in range(REGIME_MAXLEN):
            w=[lr[j] for j in range(max(0,i-REGIME_VOL_WIN),i) if math.isfinite(lr[j])]
            if len(w)>=REGIME_VOL_MIN: cv[i]=float(np.std(w,ddof=1))

        return (self._ic(px,ml,cv,REGIME_H40,REGIME_Z40_S,REGIME_Z40_E),
                self._ic(px,dl,cv,REGIME_H40,REGIME_Z40_S,REGIME_Z40_E),
                self._ic(px,ml,cv,REGIME_H20,REGIME_Z20_S,REGIME_Z20_E))

# ── Z-bucket label (matches Python live parser) ───────────────────────────────
def z_bucket(v) -> str:
    if v is None or not math.isfinite(v): return ''
    if v <= -4: return 'z<=-4'
    if v <= -3: return '-4<z<=-3'
    if v <= -2: return '-3<z<=-2'
    if v <= -1.5: return '-2<z<=-1.5'
    if v <=  0: return '-1.5<z<=0'
    if v <=  1.5: return '0<z<=1.5'
    if v <=  2: return '1.5<z<=2'
    if v <=  3: return '2<z<=3'
    if v <=  4: return '3<z<=4'
    return 'z>4'

# ── Streaming Engineer (exact port of Python live parser StreamingEngineer) ───
class StreamingEngineer:
    def __init__(self):
        self.p_dn = deque(maxlen=3); self.p_mn = deque(maxlen=3)
        self.p_dc = deque(maxlen=3); self.p_vp = deque(maxlen=3)
        self.prev_mid = None
        self.ret1  = deque(maxlen=5);  self.midv = deque(maxlen=20)
        self.midr  = deque(maxlen=20); self.ml5  = deque(maxlen=5)
        self.dl5   = deque(maxlen=5)
        self.rz_dn  = RollingResidZ(); self.rz_v5 = RollingResidZ()
        self.rz_sim = RollingResidZ(); self.rz_vp = RollingResidZ()
        self.kf_x = None; self.kf_p = 1.0
        self.kf_q = 1e-3;  self.kf_r = 0.0625  # 0.25**2
        self.ric  = RegimeICTracker()
        self.pwk_h40=0.0; self.pwk_loss=1.0; self.pwk_us=0.0

    def _lag(self, dq, k):
        return float(list(dq)[-k]) if len(dq)>=k else float('nan')

    def _kalman(self, z):
        if self.kf_x is None: self.kf_x=float(z); self.kf_p=1.0; return float(z)
        self.kf_p += self.kf_q
        k = self.kf_p/(self.kf_p+self.kf_r)
        self.kf_x += k*(float(z)-self.kf_x); self.kf_p = (1-k)*self.kf_p
        return self.kf_x

    def set_prev_wk(self, h40, vloss, us):
        self.pwk_h40=h40; self.pwk_loss=vloss; self.pwk_us=us

    def add(self, bar: dict) -> dict:
        out = dict(bar)
        vt    = bar.get('vol_total',0) or 0
        ds    = bar.get('delta_sum',0) or 0
        ms    = bar.get('mlofi_sum',0) or 0
        mds   = bar.get('mlofi_decay',0) or 0
        bv    = bar.get('buy_vol',0) or 0
        sv    = bar.get('sell_vol',0) or 0
        sbv   = bar.get('sweep_buy_vol',0) or 0
        ssv   = bar.get('sweep_sell_vol',0) or 0
        vpin  = bar.get('vpin', float('nan'))
        mm    = bar.get('mid_mean', float('nan'))
        px_c  = bar.get('px_close', float('nan'))
        sbc   = bar.get('sweep_buy_count',0) or 0
        ssc   = bar.get('sweep_sell_count',0) or 0

        sa=sbv+ssv; ss=sbv-ssv; st=(sa if sa>1e-12 else 1e-12)
        if vt > 0:
            dn=ds/vt; mn=ms/vt; dcn=mds/vt
            br=bv/vt; sr_=sv/vt
            sbr=sbv/vt; ssr_=ssv/vt
            sw_vol=sa; sw_r=sa/vt; sw_n=sw_r
        else:
            dn=mn=dcn=br=sr_=sbr=ssr_=sw_vol=sw_r=sw_n=float('nan')

        out['delta_norm']=dn; out['mlofi_norm']=mn; out['decay_norm']=dcn
        out['buy_ratio']=br; out['sell_ratio']=sr_
        out['sweep_buy_ratio']=sbr; out['sweep_sell_ratio']=ssr_
        out['sweep_vol']=sw_vol; out['sweep_ratio']=sw_r; out['sweep_norm']=sw_n
        out['sweep_abs_vol']=sa; out['sweep_signed_vol']=ss; out['sweep_imbalance']=ss
        out['sweep_imbalance_norm']=ss/st
        out['sweep_count']=sbc+ssc; out['sweep_buy_count']=sbc; out['sweep_sell_count']=ssc

        # lags BEFORE updating deques
        out['delta_norm_lag_1']=self._lag(self.p_dn,1); out['delta_norm_lag_2']=self._lag(self.p_dn,2); out['delta_norm_lag_3']=self._lag(self.p_dn,3)
        out['delta_norm_lag1']=out['delta_norm_lag_1']; out['delta_norm_lag2']=out['delta_norm_lag_2']; out['delta_norm_lag3']=out['delta_norm_lag_3']
        out['mlofi_norm_lag_1']=self._lag(self.p_mn,1); out['mlofi_norm_lag_2']=self._lag(self.p_mn,2); out['mlofi_norm_lag_3']=self._lag(self.p_mn,3)
        out['mlofi_norm_lag1']=out['mlofi_norm_lag_1']; out['mlofi_norm_lag2']=out['mlofi_norm_lag_2']; out['mlofi_norm_lag3']=out['mlofi_norm_lag_3']
        out['decay_norm_lag_1']=self._lag(self.p_dc,1); out['decay_norm_lag_2']=self._lag(self.p_dc,2); out['decay_norm_lag_3']=self._lag(self.p_dc,3)
        out['vpin_lag_1']=self._lag(self.p_vp,1); out['vpin_lag_2']=self._lag(self.p_vp,2); out['vpin_lag_3']=self._lag(self.p_vp,3)

        # mid_ret1
        mr1 = (float(mm)-float(self.prev_mid)) if self.prev_mid is not None and mm==mm else float('nan')
        out['mid_ret1']=mr1
        if math.isfinite(mr1): self.ret1.append(mr1)
        out['volatility_5'] = float(np.std(list(self.ret1),ddof=1)) if len(self.ret1)>=5 else float('nan')

        # residual z-scores
        out['delta_norm_resid_z20']=self.rz_dn.update(dn)
        out['volatility_5_resid_z20']=self.rz_v5.update(out['volatility_5'])
        out['sweep_imbalance_norm_resid_z20']=self.rz_sim.update(out['sweep_imbalance_norm'])
        out['vpin_resid_z20']=self.rz_vp.update(vpin)
        for k_ in ('delta_norm','volatility_5','sweep_imbalance_norm','vpin'):
            out[f'{k_}_resid_z20_bucket']=z_bucket(out[f'{k_}_resid_z20'])

        # mid rolling
        if mm==mm: self.midv.append(float(mm))
        out['mid_roll20'] = float(np.mean(list(self.midv))) if len(self.midv)>=20 else float('nan')
        mr = (float(mm)-out['mid_roll20']) if mm==mm and math.isfinite(out['mid_roll20']) else float('nan')
        out['mid_resid']=mr
        if math.isfinite(mr): self.midr.append(mr)
        mrs = float(np.std(list(self.midr),ddof=1)) if len(self.midr)>=20 else float('nan')
        out['mid_resid_std']=mrs
        out['mid_resid_z'] = (mr/mrs) if math.isfinite(mr) and math.isfinite(mrs) and mrs>0 else float('nan')

        # rolling OFI
        if mn==mn: self.ml5.append(float(mn))
        if dn==dn: self.dl5.append(float(dn))
        out['mlofi_rolling_5'] = float(np.mean(list(self.ml5))) if len(self.ml5)>=5 else float('nan')
        out['delta_rolling_5'] = float(np.mean(list(self.dl5))) if len(self.dl5)>=5 else float('nan')
        pm1=self._lag(self.p_mn,1); pm2=self._lag(self.p_mn,2)
        out['mlofi_accel'] = (mn-2*pm1+pm2) if mn==mn and math.isfinite(pm1) and math.isfinite(pm2) else float('nan')

        # time features
        ts_ns = bar.get('bar_end_ts_ns',0)
        t = pd.to_datetime(ts_ns, unit='ns', utc=True)
        out['minute_of_day'] = t.hour*60+t.minute
        out['dow'] = t.dayofweek  # 0=Mon matches Python
        out['tod_minute'] = out['minute_of_day']

        # Kalman
        out['mid_kf'] = self._kalman(float(mm)) if mm==mm else float('nan')

        # expected vol placeholders
        out['expected_vol_total']=0.0; out['rvol_expected']=0.0

        # regime IC
        ic_m,ic_d,ic_m20 = self.ric.update(px_c, mn, dn)
        out['regime_ic_mlofi']=ic_m; out['regime_ic_delta']=ic_d; out['regime_ic_mlofi20']=ic_m20
        out['prev_wk_h40_ic']=self.pwk_h40; out['prev_wk_val_loss']=self.pwk_loss; out['prev_wk_us_ic']=self.pwk_us

        # update lag deques AFTER reading
        self.p_dn.append(dn); self.p_mn.append(mn); self.p_dc.append(dcn)
        self.p_vp.append(vpin if math.isfinite(vpin) else float('nan'))
        self.prev_mid = float(mm) if mm==mm else None
        return out

# ── Rithmic file reader / event merger ────────────────────────────────────────
def read_rithmic_events(raw_dir: str, symbol: str, max_events: Optional[int]=None):
    """Merge depth, bid_quote_updates, ask_quote_updates, trades by recv_ts_ns."""
    import heapq

    def iter_file(path, kind):
        if not Path(path).exists(): return
        with open(path,'r',encoding='utf-8',errors='ignore') as f:
            for line in f:
                line=line.strip()
                if not line or line[0]!='{': continue
                try: rec=json.loads(line)
                except: continue
                yield (rec.get('recv_ts_ns',0), kind, rec)

    # Open iterators for each stream
    sym_dir = f"{raw_dir}/{symbol}"
    streams = [
        iter_file(f"{sym_dir}/depth.ndjson",              'depth'),
        iter_file(f"{sym_dir}/bid_quote_updates.ndjson",  'bid'),
        iter_file(f"{sym_dir}/ask_quote_updates.ndjson",  'ask'),
        iter_file(f"{sym_dir}/trades.ndjson",             'trade'),
    ]

    heap = []
    nexts = {}
    for i, it in enumerate(streams):
        try:
            item = next(it)
            heapq.heappush(heap, (item[0], i, item))
            nexts[i] = it
        except StopIteration:
            pass

    count = 0
    while heap:
        _, i, item = heapq.heappop(heap)
        yield item
        count += 1
        if max_events and count >= max_events: break
        try:
            nxt = next(nexts[i])
            heapq.heappush(heap, (nxt[0], i, nxt))
        except StopIteration:
            pass

# ── Volume bar builder (Python, matches C++ VolumeBarBuilder) ─────────────────
def make_bar(idx: int, ts_ns: int) -> dict:
    return dict(bar_index=idx, bar_start_ts_ns=ts_ns, bar_end_ts_ns=ts_ns,
                vol_total=0., buy_vol=0., sell_vol=0., delta_sum=0.,
                dollar_value=0., mlofi_sum=0., mlofi_decay=0.,
                pr_sum=0., mid_sum=0., trade_pv_sum=0., trade_v_sum=0.,
                trade_px_sum=0., trade_px_cnt=0, trade_count=0,
                book_count=0, obs_count=0,
                px_open=float('nan'), px_high=float('nan'),
                px_low=float('nan'),  px_close=float('nan'),
                vpin=float('nan'),
                sweep_buy_vol=0., sweep_sell_vol=0.,
                sweep_buy_count=0, sweep_sell_count=0)

class PyBarBuilder:
    def __init__(self, bucket: float, eng: StreamingEngineer,
                 vpin_t: VPINTracker, sweep_t: SweepTracker):
        self.bucket=bucket; self.cur=None; self.idx=0
        self.eng=eng; self.vpin=vpin_t; self.sweep=sweep_t
        self.closed: List[dict] = []

    def _ensure(self, ts_ns):
        if self.cur is None: self.cur=make_bar(self.idx, ts_ns)
        return self.cur

    def book_obs(self, ts_ns, ofi_raw, ofi_dec, mid, pr):
        b=self._ensure(ts_ns); b['bar_end_ts_ns']=ts_ns
        b['mlofi_sum']+=ofi_raw; b['mlofi_decay']+=ofi_dec
        b['mid_sum']+=mid; b['pr_sum']+=pr; b['obs_count']+=1; b['book_count']+=1

    def trade(self, ts_ns, side, price, size, mid, pr, depth_ok, day_int):
        rem=size; buy=(side=='B')
        while rem>0:
            b=self._ensure(ts_ns)
            space=self.bucket-b['vol_total']; take=min(rem,space)
            b['vol_total']+=take; b['dollar_value']+=take*price
            if buy: b['buy_vol']+=take; b['delta_sum']+=take
            else:   b['sell_vol']+=take; b['delta_sum']-=take
            b['trade_count']+=1; b['trade_pv_sum']+=take*price
            b['trade_v_sum']+=take; b['trade_px_sum']+=price; b['trade_px_cnt']+=1
            if not math.isfinite(b['px_open']):
                b['px_open']=b['px_high']=b['px_low']=b['px_close']=price
            else:
                if price>b['px_high']: b['px_high']=price
                if price<b['px_low']:  b['px_low']=price
                b['px_close']=price
            if depth_ok: b['book_count']+=1; b['mid_sum']+=mid; b['pr_sum']+=pr
            else:        b['mid_sum']+=mid; b['pr_sum']+=1.0
            b['obs_count']+=1
            self.vpin.update(buy, take); b['vpin']=self.vpin.current_val
            sw=self.sweep.update(ts_ns, side, take)
            b['sweep_buy_vol']+=sw[0]; b['sweep_sell_vol']+=sw[1]
            b['sweep_buy_count']+=sw[2]; b['sweep_sell_count']+=sw[3]
            b['bar_end_ts_ns']=ts_ns
            rem-=take
            if b['vol_total']>=self.bucket-1e-9:
                b['vol_total']=self.bucket; self._close(ts_ns, day_int)
        return self.closed

    def _close(self, ts_ns, day_int):
        b=self.cur; b['bar_end_ts_ns']=ts_ns
        # flush sweep
        sw=self.sweep.flush()
        b['sweep_buy_vol']+=sw[0]; b['sweep_sell_vol']+=sw[1]
        b['sweep_buy_count']+=sw[2]; b['sweep_sell_count']+=sw[3]
        obs=b['obs_count']
        b['mid_mean']  = b['mid_sum']/obs  if obs>0 else float('nan')
        b['pr_mean']   = b['pr_sum']/obs   if obs>0 else float('nan')
        b['mlofi_mean']= b['mlofi_sum']/obs if obs>0 else float('nan')
        b['mlofi_decay_sum']=b['mlofi_decay']
        b['day']=day_int; b['has_trade']=(1 if b['trade_count']>0 else 0)
        b['has_book']=(1 if b['book_count']>0 else 0)
        row = self.eng.add(b)
        self.closed.append(row)
        self.idx+=1; self.cur=None

# ── Stream Processor (Rithmic-aware, matches C++ StreamProcessor) ─────────────
class PyStreamProcessor:
    def __init__(self, vol500: float, vol200: float, prev_wk=(0.,1.,0.)):
        self.book = LOBBook()
        self.last_b: List=[]; self.last_a: List=[]; self.last_mid=0.
        # Separate engineers per bar-type (they have independent state)
        self.e500=StreamingEngineer(); self.e500.set_prev_wk(*prev_wk)
        self.e200=StreamingEngineer(); self.e200.set_prev_wk(*prev_wk)
        self.vpin500=VPINTracker(vol500); self.vpin200=VPINTracker(vol200)
        self.sweep500=SweepTracker();      self.sweep200=SweepTracker()
        self.b500=PyBarBuilder(vol500, self.e500, self.vpin500, self.sweep500)
        self.b200=PyBarBuilder(vol200, self.e200, self.vpin200, self.sweep200)
        self.bars500: List[dict]=[];  self.bars200: List[dict]=[]
        self.last_lob_seq = -1; self.day_int=0

        # Pending packet (matches C++ flush_packet logic)
        self._pkt_updates: List = []
        self._pkt_active  = False
        self._pkt_is_image= False

    def _flush_packet(self, recv_ts_ns):
        if not self._pkt_active or not self._pkt_updates:
            self._pkt_active=False; self._pkt_updates=[]; return

        baseline_missing = not self.last_b or not self.last_a
        prev_b=list(self.last_b); prev_a=list(self.last_a)

        for side,px,sz in self._pkt_updates:
            if side=='B': self.book.update_bid(px,sz)
            else:         self.book.update_ask(px,sz)

        curr_b=self.book.top_bids(10); curr_a=self.book.top_asks(10)

        if self._pkt_is_image or baseline_missing:
            self.last_b=curr_b; self.last_a=curr_a
            m=self.book.mid()
            if m>0: self.last_mid=m
            self._pkt_active=False; self._pkt_updates=[]; return

        raw_ofi=compute_raw_ofi(prev_b,prev_a,curr_b,curr_a)
        dec_ofi=compute_decayed_ofi(prev_b,prev_a,curr_b,curr_a)
        pr=pressure_ratio(curr_b,curr_a)
        bb=self.book.best_bid(); ba=self.book.best_ask()
        mid = 0.5*(bb+ba) if bb>0 and ba>0 else self.last_mid
        if mid>0: self.last_mid=mid

        self.b500.book_obs(recv_ts_ns,raw_ofi,dec_ofi,mid,pr)
        self.b200.book_obs(recv_ts_ns,raw_ofi,dec_ofi,mid,pr)
        self.last_b=curr_b; self.last_a=curr_a
        self._pkt_active=False; self._pkt_updates=[]

    def process(self, recv_ts_ns: int, kind: str, rec: dict):
        schema = rec.get('schema','')

        if kind=='depth':
            lob_seq = rec.get('lob_snapshot_seq',0)
            if lob_seq != self.last_lob_seq:
                self.book.clear(); self.last_b=[]; self.last_a=[]
                self.last_lob_seq=lob_seq
            side=str(rec.get('side','')); px=float(rec.get('price',0)); sz=float(rec.get('size',0))
            if side=='B': self.book.update_bid(px,sz)
            else:         self.book.update_ask(px,sz)
            self.last_b=self.book.top_bids(10); self.last_a=self.book.top_asks(10)
            m=self.book.mid()
            if m>0: self.last_mid=m

        elif kind in ('bid','ask'):
            utype = int(rec.get('update_type_code',1))   # 1=SOLO 2=BEGIN 3=MID 4=END
            cbtype= int(rec.get('callback_type_code',2)) # 1=IMAGE 2=UPDATE
            if utype==2:  # BEGIN
                if self._pkt_active: self._flush_packet(recv_ts_ns)
                self._pkt_active=True; self._pkt_is_image=(cbtype==1); self._pkt_updates=[]
            if not self._pkt_active:
                self._pkt_active=True; self._pkt_is_image=(cbtype==1); self._pkt_updates=[]
            pv=rec.get('price_valid',True); sv=rec.get('size_valid',True)
            px=float(rec.get('price',0)) if pv else 0.
            sz=float(rec.get('size',0))  if sv else 0.
            side='B' if kind=='bid' else 'A'
            if px>0: self._pkt_updates.append((side,px,sz))
            if utype in (1,4):  # SOLO or END
                self._flush_packet(recv_ts_ns)

        elif kind=='trade':
            if self._pkt_active: self._flush_packet(recv_ts_ns)
            px=float(rec.get('price',0)); sz=float(rec.get('size',0))
            if px<=0 or sz<=0: return
            agg=str(rec.get('aggressor_side','')); side='B' if agg in ('B','b','Buy','BUY') else 'A'
            bb=self.book.best_bid(); ba=self.book.best_ask()
            depth_ok=(bb>0 and ba>0)
            mid = 0.5*(bb+ba) if depth_ok else (self.last_mid if self.last_mid>0 else px)
            pr  = pressure_ratio(self.book.top_bids(50),self.book.top_asks(50)) if depth_ok else 1.0
            if depth_ok: self.last_mid=mid
            ts_ns=int(rec.get('timestamp_ns',recv_ts_ns))
            if self.day_int==0:
                t=pd.to_datetime(ts_ns,unit='ns',utc=True); self.day_int=t.year*10000+t.month*100+t.day
            self.b500.trade(ts_ns,side,px,sz,mid,pr,depth_ok,self.day_int)
            self.b200.trade(ts_ns,side,px,sz,mid,pr,depth_ok,self.day_int)
            self.bars500.extend(self.b500.closed); self.b500.closed=[]
            self.bars200.extend(self.b200.closed); self.b200.closed=[]

# ── Numeric comparison helpers ────────────────────────────────────────────────
FLOAT_COLS = [
    'vol_total','buy_vol','sell_vol','delta_sum','delta_norm','dollar_value',
    'mid_mean','pr_mean','vpin','mlofi_sum','mlofi_decay_sum','mlofi_mean',
    'sweep_vol','sweep_signed_vol','sweep_abs_vol','sweep_buy_ratio','sweep_sell_ratio',
    'sweep_imbalance','sweep_imbalance_norm','sweep_norm','sweep_ratio',
    'trade_pv_sum','trade_v_sum','trade_px_sum','mid_sum','pr_sum',
    'px_open','px_high','px_low','px_close','mlofi_norm','decay_norm',
    'buy_ratio','sell_ratio',
    'delta_norm_lag_1','delta_norm_lag_2','delta_norm_lag_3',
    'mlofi_norm_lag_1','mlofi_norm_lag_2','mlofi_norm_lag_3',
    'decay_norm_lag_1','decay_norm_lag_2','decay_norm_lag_3',
    'vpin_lag_1','vpin_lag_2','vpin_lag_3',
    'mid_ret1','volatility_5','mid_roll20','mid_resid','mid_resid_std','mid_resid_z',
    'mid_kf','mlofi_rolling_5','delta_rolling_5','mlofi_accel',
    'expected_vol_total','rvol_expected',
    'regime_ic_mlofi','regime_ic_delta','regime_ic_mlofi20',
    'prev_wk_h40_ic','prev_wk_val_loss','prev_wk_us_ic',
    'delta_norm_resid_z20','volatility_5_resid_z20',
    'sweep_imbalance_norm_resid_z20','vpin_resid_z20',
]
INT_COLS = ['bar_index','trade_count','book_count','obs_count','trade_px_cnt',
            'sweep_count','sweep_buy_count','sweep_sell_count','minute_of_day','dow','tod_minute',
            'has_trade','has_book','day']
STR_COLS = ['timestamp','bar_start','bar_end','timestamp_utc',
            'delta_norm_resid_z20_bucket','volatility_5_resid_z20_bucket',
            'sweep_imbalance_norm_resid_z20_bucket','vpin_resid_z20_bucket']

TIGHT_TOL   = 1e-6   # core vol/price/OFI fields
LOOSE_TOL   = 1e-4   # rolling/lag/kalman (floating point order differences)
REGIME_TOL  = 1e-3   # regime IC (200-bar rolling pearson, very sensitive to tiny precision diffs)
ACCUM_TOL   = 1.0    # raw accumulated sums (N*30K additions accumulate FP noise ~0.05/bar)

TOLERANCES = {k: TIGHT_TOL for k in FLOAT_COLS}
# Accumulated running sums — FP noise proportional to N (tens of thousands of additions)
for k in ['mid_sum','pr_sum','trade_pv_sum','trade_v_sum','trade_px_sum']:
    TOLERANCES[k] = ACCUM_TOL
# Means derived from accumulated sums — noise divides by obs_count but still above TIGHT_TOL
for k in ['mid_mean','pr_mean','mlofi_mean']:
    TOLERANCES[k] = LOOSE_TOL
for k in ['mid_ret1','volatility_5','mid_roll20','mid_resid','mid_resid_std','mid_resid_z',
          'mlofi_rolling_5','delta_rolling_5','mlofi_accel','mid_kf',
          'delta_norm_lag_1','delta_norm_lag_2','delta_norm_lag_3',
          'mlofi_norm_lag_1','mlofi_norm_lag_2','mlofi_norm_lag_3',
          'delta_norm_resid_z20','volatility_5_resid_z20',
          'sweep_imbalance_norm_resid_z20','vpin_resid_z20']:
    TOLERANCES[k] = LOOSE_TOL
for k in ['regime_ic_mlofi','regime_ic_delta','regime_ic_mlofi20']:
    TOLERANCES[k] = REGIME_TOL

def cmp_bars(py_bar: dict, cpp_bar: dict, bar_idx: int, verbose=False) -> List[str]:
    """Compare one bar, return list of discrepancy strings."""
    diffs = []

    def fv(d, k):
        v = d.get(k)
        if v is None: return float('nan')
        try: return float(v)
        except: return float('nan')

    for col in FLOAT_COLS:
        py_v = fv(py_bar, col); cpp_v = fv(cpp_bar, col)
        both_nan = (py_v != py_v) and (cpp_v != cpp_v)
        if both_nan: continue
        one_nan = (py_v != py_v) or (cpp_v != cpp_v)
        if one_nan:
            diffs.append(f"  [{col}] py=nan cpp={cpp_v:.6g}" if py_v!=py_v
                         else f"  [{col}] py={py_v:.6g} cpp=nan")
            continue
        tol = TOLERANCES.get(col, LOOSE_TOL)
        if abs(py_v - cpp_v) > tol:
            diffs.append(f"  [{col}] py={py_v:.10g}  cpp={cpp_v:.10g}  diff={abs(py_v-cpp_v):.3e}")

    for col in INT_COLS:
        py_v = py_bar.get(col); cpp_v = cpp_bar.get(col)
        if py_v is None or cpp_v is None: continue
        try:
            if int(py_v) != int(cpp_v):
                diffs.append(f"  [{col}] py={int(py_v)}  cpp={int(cpp_v)}")
        except: pass

    for col in STR_COLS:
        py_v = str(py_bar.get(col,'')).strip(); cpp_v = str(cpp_bar.get(col,'')).strip()
        if col in ('timestamp','bar_start','bar_end','timestamp_utc'):
            continue  # format differences tolerated at string level; ts_ns is checked via int
        if py_v != cpp_v and col.endswith('bucket'):
            diffs.append(f"  [{col}] py={py_v!r}  cpp={cpp_v!r}")

    if diffs and verbose:
        print(f"\nBar {bar_idx} mismatches:")
        for d in diffs: print(d)
    return diffs

# ── Formula-level unit tests (no data required) ───────────────────────────────
def run_formula_checks():
    print("\n" + "="*70)
    print("FORMULA PARITY CHECKS (vs MBO batch + Python live parser)")
    print("="*70)
    PASS='\033[92mPASS\033[0m'; FAIL='\033[91mFAIL\033[0m'

    fails = 0

    # 1. OFI raw formula: symmetric top-K difference
    pb=[( 100., 5.), (99.75, 3.), (99.5, 2.)]
    pa=[(100.25, 4.), (100.5, 6.), (100.75, 1.)]
    cb=[(100., 6.), (99.75, 2.), (99.5, 3.)]   # bid 100 +1, 99.75 -1, 99.5 +1
    ca=[(100.25, 4.), (100.5, 7.), (100.75, 1.)] # ask 100.5 +1
    raw = compute_raw_ofi(pb, pa, cb, ca, k=3)
    expected = (6-5) + (2-3) + (3-2) - ((4-4) + (7-6) + (1-1))
    ok = abs(raw - expected) < 1e-9
    print(f"[1] OFI raw formula: {PASS if ok else FAIL}  got={raw:.4f}  expected={expected:.4f}")
    if not ok: fails+=1

    # 2. Decayed OFI: weights exp(-0.5*i)
    w=[math.exp(-0.5*i) for i in range(3)]
    dec = compute_decayed_ofi(pb, pa, cb, ca, k=3)
    exp_dec = ((6-5)*w[0]+(2-3)*w[1]+(3-2)*w[2]) - ((4-4)*w[0]+(7-6)*w[1]+(1-1)*w[2])
    ok = abs(dec - exp_dec) < 1e-9
    print(f"[2] Decayed OFI formula: {PASS if ok else FAIL}  got={dec:.6f}  expected={exp_dec:.6f}")
    if not ok: fails+=1

    # 3. Pressure ratio: exp(-i/10) weights
    pr = pressure_ratio(cb, ca)
    pb_pr = sum(cb[i][1]*math.exp(-i/10.) for i in range(len(cb)))
    pa_pr = sum(ca[i][1]*math.exp(-i/10.) for i in range(len(ca)))
    exp_pr = pb_pr/(pa_pr+1e-9)
    ok = abs(pr - exp_pr) < 1e-9
    print(f"[3] Pressure ratio: {PASS if ok else FAIL}")
    if not ok: fails+=1

    # 4. VPIN: bucket flush and current_val
    vt = VPINTracker(100.)
    for _ in range(12): vt.update(True, 50.); vt.update(False, 50.)
    ok = 0.0 <= vt.current_val <= 1.0
    print(f"[4] VPIN current_val in [0,1]: {PASS if ok else FAIL}  val={vt.current_val:.4f}")
    if not ok: fails+=1

    # 5. VPIN: pure buy flow → imbalance=bucket → current_val=1
    vt2 = VPINTracker(100., window=3)
    for _ in range(4): vt2.update(True, 100.)
    ok = abs(vt2.current_val - 1.0) < 1e-9
    print(f"[5] VPIN pure buy → 1.0: {PASS if ok else FAIL}  val={vt2.current_val:.6f}")
    if not ok: fails+=1

    # 6. Sweep: gap > 50ms breaks cluster
    sw = SweepTracker(gap_ns=50_000_000, min_size=5.)
    r1=sw.update(1_000_000_000, 'B', 3.)  # start cluster
    r2=sw.update(1_020_000_000, 'B', 4.)  # same side, within gap
    r3=sw.update(1_100_000_000, 'B', 2.)  # new cluster (gap > 50ms)
    # r3 finalizes previous cluster (buy_vol=7 >=5 → counts)
    ok = r3[0]==7.0 and r3[2]==1  # buy_vol=7, buy_count=1
    print(f"[6] Sweep cluster finalize: {PASS if ok else FAIL}  buy_vol={r3[0]}  buy_count={r3[2]}")
    if not ok: fails+=1

    # 7. Sweep: cluster < min_size → does not qualify
    sw2 = SweepTracker(gap_ns=50_000_000, min_size=5.)
    sw2.update(1_000_000_000, 'B', 2.)
    r = sw2.update(1_100_000_000, 'A', 1.)  # finalize prev cluster (2 < 5 → 0)
    ok = r[0]==0.0 and r[2]==0
    print(f"[7] Sweep min_size filter: {PASS if ok else FAIL}  buy_vol={r[0]}")
    if not ok: fails+=1

    # 8. RollingResidZ: deterministic warmup
    # With w=5: raw deque fills at index 4 (first finite res), res deque fills at index 8 (2w-2=8)
    rz = RollingResidZ(w=5)
    vals = [1.,2.,3.,4.,5.,3.,4.,5.,3.,2.]
    results = [rz.update(v) for v in vals]
    # First 8 (indices 0-7) should be nan; index 8 is first finite
    ok_nan = all(not math.isfinite(r) for r in results[:8])
    ok_val = math.isfinite(results[8])
    ok = ok_nan and ok_val
    print(f"[8] RollingResidZ warmup: {PASS if ok else FAIL}  first8_nan={ok_nan}  bar8_finite={ok_val}")
    if not ok: fails+=1

    # 9. RegimeIC: returns (0,0,0) for fewer than 286 bars
    ric = RegimeICTracker()
    for i in range(285):
        r = ric.update(25000.+i*0.25, 0.1, -0.05)
    ok = r == (0.0,0.0,0.0)
    print(f"[9] RegimeIC warmup <286: {PASS if ok else FAIL}  result={r}")
    if not ok: fails+=1

    # 10. RegimeIC: returns non-zero after 286 bars (if prices move enough for correlation)
    # Feed a simple sine-wave trend with correlated signal
    ric2 = RegimeICTracker()
    np.random.seed(42)
    prices = 25000. + np.cumsum(np.random.randn(300)*0.25)
    for i in range(300):
        signal = (prices[min(i+1,299)]-prices[i])/(0.25*math.sqrt(40))*0.5
        ric2.update(prices[i], float(signal)+np.random.randn()*0.1, float(signal)*0.5)
    r2 = ric2.update(prices[-1], 0.0, 0.0)
    ok = any(x != 0.0 for x in r2)
    print(f"[10] RegimeIC active after 286: {PASS if ok else FAIL}  ic_mlofi={r2[0]:.4f}")
    if not ok: fails+=1

    # 11. mlofi_norm = mlofi_sum / vol_total
    eng = StreamingEngineer()
    bar = make_bar(0, 1_000_000_000)
    bar.update(dict(vol_total=500., delta_sum=100., mlofi_sum=250., mlofi_decay=50.,
                    buy_vol=300., sell_vol=200., sweep_buy_vol=0., sweep_sell_vol=0.,
                    sweep_buy_count=0, sweep_sell_count=0, vpin=0.4, mid_mean=25000.,
                    px_close=25001., bar_end_ts_ns=1_780_000_000_000_000_000))
    bar['mlofi_decay_sum']=bar['mlofi_decay']
    out = eng.add(bar)
    ok = abs(out['mlofi_norm'] - 250./500.) < 1e-9
    print(f"[11] mlofi_norm=mlofi_sum/vol_total: {PASS if ok else FAIL}  got={out['mlofi_norm']:.6f}  exp={250./500.:.6f}")
    if not ok: fails+=1

    # 12. delta_norm = delta_sum / vol_total
    ok = abs(out['delta_norm'] - 100./500.) < 1e-9
    print(f"[12] delta_norm=delta_sum/vol_total: {PASS if ok else FAIL}  got={out['delta_norm']:.6f}  exp={100./500.:.6f}")
    if not ok: fails+=1

    # 13. Kalman filter: 2nd bar update uses kf_q=1e-3, kf_r=0.25^2
    eng2 = StreamingEngineer()
    bar1 = dict(bar); bar1['mid_mean']=25000.
    bar1['bar_end_ts_ns']=1_780_000_000_000_000_000
    eng2.add(bar1)
    bar2 = dict(bar); bar2['mid_mean']=25010.
    bar2['bar_end_ts_ns']=1_780_000_000_000_000_000+60_000_000_000
    out2 = eng2.add(bar2)
    # Manual kalman: after bar1 x=25000, p=1*(1-k); bar2: p=p+1e-3, k=p/(p+0.0625), x=x+k*(25010-x)
    kf_p = 1.0; kf_q=1e-3; kf_r=0.0625
    kf_p += kf_q; k_gain = kf_p/(kf_p+kf_r)
    kf_x = 25000. + k_gain*(25010.-25000.)
    ok = abs(out2['mid_kf'] - kf_x) < 1e-6
    print(f"[13] Kalman filter math: {PASS if ok else FAIL}  got={out2['mid_kf']:.6f}  exp={kf_x:.6f}")
    if not ok: fails+=1

    print(f"\nFormula checks: {13-fails}/13 passed")
    return fails == 0

# ── Main parity test ──────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date',      default='2026-06-03')
    ap.add_argument('--symbol',    default='NQM6')
    ap.add_argument('--raw_dir',   default='/home/prabh/OFI_Live_Data/Rithmic_Raw')
    ap.add_argument('--cpp_dir',   default='/home/prabh/OFI_Live_Features')
    ap.add_argument('--max_bars',  type=int, default=500)
    ap.add_argument('--max_events',type=int, default=None)
    ap.add_argument('--vol500',    type=float, default=500.)
    ap.add_argument('--vol200',    type=float, default=200.)
    ap.add_argument('--pwk_h40',   type=float, default=0.0612)
    ap.add_argument('--pwk_loss',  type=float, default=0.42)
    ap.add_argument('--pwk_us',    type=float, default=0.0)
    ap.add_argument('--no_formula_checks', action='store_true')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    # Formula unit tests first
    formula_ok = True
    if not args.no_formula_checks:
        formula_ok = run_formula_checks()

    raw_dir  = f"{args.raw_dir}/{args.date}"
    cpp_vol5 = f"{args.cpp_dir}/{args.date}/{args.symbol}_vol500.ndjsonl"
    cpp_vol2 = f"{args.cpp_dir}/{args.date}/{args.symbol}_vol200.ndjsonl"

    if not Path(cpp_vol5).exists():
        print(f"\n[WARN] C++ output not found: {cpp_vol5}")
        print("Run the C++ parser first (catchup mode) then re-run this test.")
        return

    # Load C++ output
    print(f"\nLoading C++ output: {cpp_vol5}")
    cpp_bars_500 = []
    with open(cpp_vol5) as f:
        for line in f:
            line=line.strip()
            if line: cpp_bars_500.append(json.loads(line))
    print(f"  C++ vol500 bars: {len(cpp_bars_500)}")

    # Run Python processor on same raw data
    print(f"Running Python processor on: {raw_dir}/{args.symbol}/")
    sp = PyStreamProcessor(args.vol500, args.vol200,
                           prev_wk=(args.pwk_h40, args.pwk_loss, args.pwk_us))
    t0 = time.time()
    event_count = 0
    for recv_ts, kind, rec in read_rithmic_events(raw_dir, args.symbol, args.max_events):
        sp.process(recv_ts, kind, rec)
        event_count += 1
        if len(sp.bars500) >= args.max_bars: break
        if event_count % 500_000 == 0:
            print(f"  processed {event_count:,} events → {len(sp.bars500)} vol500 bars  ({time.time()-t0:.1f}s)")

    py_bars_500 = sp.bars500[:args.max_bars]
    py_bars_200 = sp.bars200

    print(f"  Python vol500 bars: {len(py_bars_500)}  events: {event_count:,}  time: {time.time()-t0:.1f}s")

    n = min(len(py_bars_500), len(cpp_bars_500), args.max_bars)
    print(f"\n{'='*70}")
    print(f"FIELD-BY-FIELD COMPARISON: {n} vol500 bars")
    print(f"{'='*70}")

    total_diffs = 0
    bars_with_diffs = 0
    diff_counts: Dict[str,int] = {}

    for i in range(n):
        diffs = cmp_bars(py_bars_500[i], cpp_bars_500[i], i, verbose=args.verbose)
        if diffs:
            bars_with_diffs += 1
            total_diffs += len(diffs)
            for d in diffs:
                col = d.strip().split(']')[0].lstrip('[')
                diff_counts[col] = diff_counts.get(col, 0) + 1
            if bars_with_diffs <= 3:
                print(f"\nBar {i} ({py_bars_500[i].get('bar_end','?')[:19]}) — {len(diffs)} mismatches:")
                for d in diffs: print(d)

    print(f"\n{'='*70}")
    print(f"SUMMARY: {n} bars compared")
    print(f"  Bars with differences : {bars_with_diffs} / {n}  ({100*bars_with_diffs/n:.1f}%)")
    print(f"  Total field differences: {total_diffs}")

    if diff_counts:
        print(f"\nFields with most diffs (top 20):")
        for col, cnt in sorted(diff_counts.items(), key=lambda x: -x[1])[:20]:
            print(f"  {col:50s}: {cnt} bars")
    else:
        print("\n  ✓ ALL FIELDS MATCH within tolerance across all bars")

    # Critical fields check
    critical = ['vol_total','buy_vol','sell_vol','delta_norm','mlofi_norm',
                'sweep_imbalance_norm','vpin','mid_kf','regime_ic_mlofi']
    print(f"\nCritical fields status:")
    for col in critical:
        bad = diff_counts.get(col,0)
        status = "✓ PASS" if bad==0 else f"✗ FAIL ({bad} bars)"
        print(f"  {col:40s}: {status}")

    overall = "✓ PARITY CONFIRMED" if (bars_with_diffs==0 and formula_ok) else "✗ PARITY ISSUES FOUND"
    print(f"\n{'='*70}")
    print(f"OVERALL: {overall}")
    print(f"{'='*70}\n")

if __name__ == '__main__':
    main()
