// ofi_math.h — Math primitives, LOB book, VPIN, Sweep, RegimeIC, StreamingEngineer
// Parity target: build_live_features_volbars_from_rithmic_raw_standalone_livepatch_z20.py
#pragma once

#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <deque>
#include <limits>
#include <map>
#include <string>
#include <vector>

// ── constants ──────────────────────────────────────────────────────────────────
static constexpr int    ENTROPY_WIN        = 20;   // rolling window for entropy_score / flow_alignment
static constexpr double CUSUM_K_FACTOR     = 0.5;  // CUSUM slack = K_FACTOR * volatility_5
static constexpr double CUSUM_H_FACTOR     = 5.0;  // CUSUM threshold = H_FACTOR * volatility_5
static constexpr double CUSUM_FALLBACK_K   = 1.0;  // used when volatility_5 is NaN
static constexpr double CUSUM_FALLBACK_H   = 5.0;
static constexpr int    OFI_K_RAW          = 10;
static constexpr int    OFI_K_DECAY        = 10;
static constexpr int    OFI_PRESSURE_DEPTH = 50;
static constexpr double BOOK_TICK_SIZE     = 0.25; // NQ Rithmic visible-depth tick normalization
static constexpr int    VPIN_WINDOW        = 10;
static constexpr int64_t SWEEP_GAP_NS      = 50'000'000LL;  // 50 ms
static constexpr double  SWEEP_MIN_SIZE    = 5.0;
static constexpr int    RESID_Z_WIN        = 20;
// RegimeIC
static constexpr int    R_MAXLEN  = 286;
static constexpr int    R_H40     = 40;
static constexpr int    R_H20     = 20;
static constexpr int    R_WIN     = 200;
static constexpr int    R_MIN     = 100;
static constexpr int    R_VOLWIN  = 100;
static constexpr int    R_VOLMIN  = 20;
static constexpr int    R_Z40_S   = 41;
static constexpr int    R_Z40_E   = 240;
static constexpr int    R_Z20_S   = 61;
static constexpr int    R_Z20_E   = 260;

// ── math utilities ─────────────────────────────────────────────────────────────
inline bool is_nan_v(double x) { return !std::isfinite(x) || std::isnan(x); }

inline double stdev_ddof1(const double* data, int n) {
    if (n < 2) return NAN;
    double sum = 0, sum2 = 0;
    for (int i = 0; i < n; i++) { sum += data[i]; sum2 += data[i]*data[i]; }
    double mean = sum / n;
    double var  = (sum2 - n*mean*mean) / (n-1);
    return (var > 0.0) ? std::sqrt(var) : 0.0;
}

inline double stdev_ddof1(const std::deque<double>& dq) {
    std::vector<double> v(dq.begin(), dq.end());
    return stdev_ddof1(v.data(), (int)v.size());
}

inline double mean_of(const std::deque<double>& dq, int min_periods) {
    if ((int)dq.size() < min_periods) return NAN;
    double s = 0;
    for (double x : dq) s += x;
    return s / dq.size();
}

inline double deque_lag(const std::deque<double>& dq, int k) {
    if ((int)dq.size() < k) return NAN;
    return dq[dq.size() - k];
}

// Pearson correlation (matches np.corrcoef(x,y)[0,1])
inline double pearson_corr(const std::vector<double>& x, const std::vector<double>& y) {
    int n = (int)x.size();
    if (n < 2) return 0.0;
    double sx=0,sy=0,sxy=0,sx2=0,sy2=0;
    for (int i=0;i<n;i++) { sx+=x[i];sy+=y[i];sxy+=x[i]*y[i];sx2+=x[i]*x[i];sy2+=y[i]*y[i]; }
    double num = n*sxy - sx*sy;
    double den2 = (n*sx2 - sx*sx) * (n*sy2 - sy*sy);
    if (den2 <= 0.0) return 0.0;
    return num / std::sqrt(den2);
}

// Timestamp: matches pandas Timestamp str "YYYY-MM-DD HH:MM:SS.xxxxxxxxx+00:00"
inline std::string ns_to_ts_str(int64_t ts_ns) {
    if (ts_ns <= 0) return "";
    time_t secs = (time_t)(ts_ns / 1'000'000'000LL);
    int64_t frac = ts_ns % 1'000'000'000LL;
    struct tm t{}; gmtime_r(&secs, &t);
    char buf[64];
    snprintf(buf, sizeof(buf),
             "%04d-%02d-%02d %02d:%02d:%02d.%09lld+00:00",
             t.tm_year+1900, t.tm_mon+1, t.tm_mday,
             t.tm_hour, t.tm_min, t.tm_sec,
             (long long)frac);
    return buf;
}

inline int day_int_from_ns(int64_t ts_ns) {
    time_t s = (time_t)(ts_ns / 1'000'000'000LL);
    struct tm t{}; gmtime_r(&s, &t);
    return (t.tm_year+1900)*10000 + (t.tm_mon+1)*100 + t.tm_mday;
}

// z-bucket labels (matches Python z_bucket_label)
inline const char* z_bucket(double v) {
    if (!std::isfinite(v)) return "";
    if (v <= -4.0) return "z<=-4";
    if (v <= -3.0) return "-4<z<=-3";
    if (v <= -2.0) return "-3<z<=-2";
    if (v <= -1.5) return "-2<z<=-1.5";
    if (v <=  0.0) return "-1.5<z<=0";
    if (v <=  1.5) return "0<z<=1.5";
    if (v <=  2.0) return "1.5<z<=2";
    if (v <=  3.0) return "2<z<=3";
    if (v <=  4.0) return "3<z<=4";
    return "z>4";
}

// ── LOB Book ──────────────────────────────────────────────────────────────────
struct Level { double px; double sz; };

class LOBBook {
public:
    std::map<double, double, std::greater<double>> bids;  // price→size, best-first
    std::map<double, double>                        asks;  // price→size, best-first

    void clear()                           { bids.clear(); asks.clear(); }

    static double norm_px(double px) {
        return std::round(px / BOOK_TICK_SIZE) * BOOK_TICK_SIZE;
    }

    void update_bid(double px, double sz)  {
        double k = norm_px(px);
        if (sz<=0) bids.erase(k); else bids[k]=sz;
    }
    void update_ask(double px, double sz)  {
        double k = norm_px(px);
        if (sz<=0) asks.erase(k); else asks[k]=sz;
    }

    double best_bid() const { return bids.empty() ? 0.0 : bids.begin()->first; }
    double best_ask() const { return asks.empty() ? 0.0 : asks.begin()->first; }
    double mid()      const {
        double b=best_bid(), a=best_ask();
        return (b>0&&a>0) ? 0.5*(b+a) : 0.0;
    }

    std::vector<Level> top_bids(int k) const {
        std::vector<Level> v; v.reserve(k);
        for (auto& [px,sz]:bids) { if((int)v.size()>=k) break; v.push_back({px,sz}); }
        return v;
    }
    std::vector<Level> top_asks(int k) const {
        std::vector<Level> v; v.reserve(k);
        for (auto& [px,sz]:asks) { if((int)v.size()>=k) break; v.push_back({px,sz}); }
        return v;
    }
};

// ── OFI Math ──────────────────────────────────────────────────────────────────
// Matches Python compute_raw_ofi
inline double compute_raw_ofi(const std::vector<Level>& pb, const std::vector<Level>& pa,
                               const std::vector<Level>& cb, const std::vector<Level>& ca,
                               int k = OFI_K_RAW)
{
    if (pb.empty()||pa.empty()) return 0.0;
    std::map<double,double> mpb, mpa;
    for (int i=0;i<k&&i<(int)pb.size();i++) mpb[pb[i].px]=pb[i].sz;
    for (int i=0;i<k&&i<(int)pa.size();i++) mpa[pa[i].px]=pa[i].sz;
    double tot=0;
    for (int i=0;i<k&&i<(int)cb.size();i++) { auto it=mpb.find(cb[i].px); tot+=cb[i].sz-(it!=mpb.end()?it->second:0.0); }
    for (int i=0;i<k&&i<(int)ca.size();i++) { auto it=mpa.find(ca[i].px); tot-=ca[i].sz-(it!=mpa.end()?it->second:0.0); }
    return tot;
}

// Matches Python compute_decayed_ofi
inline double compute_decayed_ofi(const std::vector<Level>& pb, const std::vector<Level>& pa,
                                   const std::vector<Level>& cb, const std::vector<Level>& ca,
                                   int k = OFI_K_DECAY)
{
    if (pb.empty()||pa.empty()) return 0.0;
    std::map<double,double> mpb, mpa;
    for (int i=0;i<k&&i<(int)pb.size();i++) mpb[pb[i].px]=pb[i].sz;
    for (int i=0;i<k&&i<(int)pa.size();i++) mpa[pa[i].px]=pa[i].sz;
    double tot=0;
    for (int i=0;i<k&&i<(int)cb.size();i++) {
        double w=std::exp(-0.5*i); auto it=mpb.find(cb[i].px);
        tot+=(cb[i].sz-(it!=mpb.end()?it->second:0.0))*w;
    }
    for (int i=0;i<k&&i<(int)ca.size();i++) {
        double w=std::exp(-0.5*i); auto it=mpa.find(ca[i].px);
        tot-=(ca[i].sz-(it!=mpa.end()?it->second:0.0))*w;
    }
    return tot;
}

// Matches Python pressure_ratio
inline double layered_pressure(const std::vector<Level>& lvl, int lim=OFI_PRESSURE_DEPTH) {
    double tot=0; int n=std::min((int)lvl.size(),lim);
    for (int i=0;i<n;i++) tot+=lvl[i].sz*std::exp(-i/10.0);
    return tot;
}
inline double pressure_ratio(const std::vector<Level>& b, const std::vector<Level>& a) {
    return layered_pressure(b,OFI_PRESSURE_DEPTH) / (layered_pressure(a,OFI_PRESSURE_DEPTH)+1e-9);
}

// ── VPIN Tracker ──────────────────────────────────────────────────────────────
// Matches Python VPINTracker exactly
class VPINTracker {
    double bvol_; int win_;
    double buy_=0,sell_=0,filled_=0,cur_=0.5;
    std::deque<double> imb_,vol_;

    void flush() {
        imb_.push_back(std::abs(buy_-sell_)); vol_.push_back(filled_);
        while((int)imb_.size()>win_) imb_.pop_front();
        while((int)vol_.size()>win_) vol_.pop_front();
        double sv=0; for(auto v:vol_) sv+=v;
        if(sv>0){ double si=0; for(auto v:imb_) si+=v; cur_=si/sv; }
        buy_=sell_=filled_=0;
    }
public:
    VPINTracker(double bvol, int w=VPIN_WINDOW): bvol_(bvol),win_(w) {}
    void update(bool buy, double vol) {
        double rem=vol;
        while(rem>0){ double sp=bvol_-filled_,tk=(rem<=sp)?rem:sp;
            if(buy) buy_+=tk; else sell_+=tk; filled_+=tk; rem-=tk;
            if(filled_>=bvol_-1e-12) flush(); }
    }
    double vpin() const { return cur_; }
};

// ── Sweep Tracker ─────────────────────────────────────────────────────────────
struct SweepOut { double bvol,svol; int bcnt,scnt; };

class SweepTracker {
    int64_t gap_ns_; double min_sz_;
    int64_t last_ts_=0; char last_side_=0; double cluster_=0; bool active_=false;

    SweepOut _fin() {
        if(!active_) return {0,0,0,0};
        bool q=(cluster_>=min_sz_);
        SweepOut o{(q&&last_side_=='B')?cluster_:0.0,
                   (q&&last_side_=='A')?cluster_:0.0,
                   (q&&last_side_=='B')?1:0,
                   (q&&last_side_=='A')?1:0};
        active_=false; return o;
    }
public:
    SweepTracker(int64_t g=SWEEP_GAP_NS, double m=SWEEP_MIN_SIZE): gap_ns_(g),min_sz_(m) {}
    SweepOut update(int64_t ts, char side, double vol) {
        if(!active_){ active_=true; last_ts_=ts; last_side_=side; cluster_=vol; return {0,0,0,0}; }
        if(ts-last_ts_<=gap_ns_ && side==last_side_){ cluster_+=vol; last_ts_=ts; return {0,0,0,0}; }
        SweepOut o=_fin(); active_=true; last_ts_=ts; last_side_=side; cluster_=vol; return o;
    }
    SweepOut flush() { return _fin(); }
};

// ── Rolling Residual Z-score ──────────────────────────────────────────────────
// Matches Python RollingResidualZ(window=20)
class RollingResidZ {
    int w_; std::deque<double> raw_,res_;
    bool all_ok(const std::deque<double>& d){ for(double v:d) if(!std::isfinite(v)) return false; return !d.empty(); }
public:
    explicit RollingResidZ(int w=RESID_Z_WIN): w_(w) {}
    double update(double v) {
        double x=std::isfinite(v)?v:NAN;
        raw_.push_back(x); while((int)raw_.size()>w_) raw_.pop_front();
        double res=NAN;
        if((int)raw_.size()==w_&&all_ok(raw_)){
            double s=0; for(double r:raw_) s+=r; double m=s/w_; res=x-m;
        }
        res_.push_back(res); while((int)res_.size()>w_) res_.pop_front();
        if((int)res_.size()==w_&&all_ok(res_)){
            double std_r=stdev_ddof1(res_);
            if(std_r>0&&std::isfinite(std_r)) return res/std_r;
        }
        return NAN;
    }
};

// ── Regime IC Tracker ─────────────────────────────────────────────────────────
// Matches Python RegimeICTracker exactly (200-bar rolling IC, 286-bar buffer)
struct RegimeIC { double mlofi=0,delta=0,mlofi20=0; };

class RegimeICTracker {
    std::deque<double> px_,mlofi_,delta_;

    double ic(const std::array<double,R_MAXLEN>& px,
              const std::array<double,R_MAXLEN>& sig,
              const std::array<double,R_MAXLEN>& cv,
              int H, int s, int e)
    {
        double sqH=std::sqrt((double)H);
        std::vector<double> flow,ztgt;
        for(int i=s;i<=e;i++){
            double si=sig[i],p0=px[i],p1=px[i+H],cvi=cv[i];
            if(!std::isfinite(si)||!std::isfinite(p0)||!std::isfinite(p1)||
               !std::isfinite(cvi)||p0<=0||cvi<=0) continue;
            double z=std::clamp(std::log(p1/p0)/(cvi*sqH),-5.0,5.0);
            flow.push_back(si); ztgt.push_back(z);
        }
        if((int)flow.size()<R_MIN) return 0.0;
        return pearson_corr(flow,ztgt);
    }

public:
    RegimeIC update(double px_close, double mlofi_norm, double delta_norm) {
        px_.push_back(std::isfinite(px_close)&&px_close>0 ? px_close : NAN);
        mlofi_.push_back(std::isfinite(mlofi_norm) ? mlofi_norm : NAN);
        delta_.push_back(std::isfinite(delta_norm) ? delta_norm : NAN);
        while((int)px_.size()>R_MAXLEN)    px_.pop_front();
        while((int)mlofi_.size()>R_MAXLEN) mlofi_.pop_front();
        while((int)delta_.size()>R_MAXLEN) delta_.pop_front();

        if((int)px_.size()<R_MAXLEN) return {};

        std::array<double,R_MAXLEN> pa,ma,da;
        std::copy(px_.begin(),px_.end(),pa.begin());
        std::copy(mlofi_.begin(),mlofi_.end(),ma.begin());
        std::copy(delta_.begin(),delta_.end(),da.begin());

        // log-returns
        std::array<double,R_MAXLEN> lr; lr.fill(NAN);
        for(int i=1;i<R_MAXLEN;i++)
            if(std::isfinite(pa[i])&&std::isfinite(pa[i-1])&&pa[i-1]>0)
                lr[i]=std::log(pa[i]/pa[i-1]);

        // causal vol: std(logret[max(0,i-100)..i-1]) → matches .rolling(100).std().shift(1)
        std::array<double,R_MAXLEN> cv; cv.fill(NAN);
        for(int i=0;i<R_MAXLEN;i++){
            std::vector<double> w; w.reserve(R_VOLWIN);
            for(int j=std::max(0,i-R_VOLWIN);j<i;j++)
                if(std::isfinite(lr[j])) w.push_back(lr[j]);
            if((int)w.size()>=R_VOLMIN) cv[i]=stdev_ddof1(w.data(),(int)w.size());
        }

        RegimeIC out;
        out.mlofi   = ic(pa,ma,cv,R_H40,R_Z40_S,R_Z40_E);
        out.delta   = ic(pa,da,cv,R_H40,R_Z40_S,R_Z40_E);
        out.mlofi20 = ic(pa,ma,cv,R_H20,R_Z20_S,R_Z20_E);
        return out;
    }
};

// ── FlowEntropyTracker ────────────────────────────────────────────────────────
// Rolling Shannon entropy of the buy/sell volume split.
// entropy_score = mean(H_binary(buy_vol/vol_total)) over last ENTROPY_WIN bars
// Range: [0, 1].  1.0 = 50/50 every bar (chaotic).  0.0 = all one side (directed).
class FlowEntropyTracker {
    std::deque<double> ents_;
    static double binary_entropy(double p) {
        if (p <= 0.0 || p >= 1.0) return 0.0;
        return -(p * std::log2(p) + (1.0-p) * std::log2(1.0-p));
    }
public:
    double update(double buy_vol, double vol_total) {
        if (vol_total > 1e-9 && std::isfinite(buy_vol)) {
            double p = std::max(0.0, std::min(1.0, buy_vol / vol_total));
            ents_.push_back(binary_entropy(p));
            while ((int)ents_.size() > ENTROPY_WIN) ents_.pop_front();
        }
        if ((int)ents_.size() < ENTROPY_WIN) return NAN;
        double s = 0; for (double v : ents_) s += v;
        return s / ENTROPY_WIN;
    }
};

// ── FlowAlignmentTracker ──────────────────────────────────────────────────────
// Rolling mean of sign(delta_norm) over last ENTROPY_WIN bars.
// Range: [-1, 1].  +1 = all bars net-buy.  -1 = all bars net-sell.  0 = mixed.
class FlowAlignmentTracker {
    std::deque<double> signs_;
public:
    double update(double delta_norm) {
        if (std::isfinite(delta_norm)) {
            double s = (delta_norm > 0.0) ? 1.0 : (delta_norm < 0.0) ? -1.0 : 0.0;
            signs_.push_back(s);
            while ((int)signs_.size() > ENTROPY_WIN) signs_.pop_front();
        }
        if ((int)signs_.size() < ENTROPY_WIN) return NAN;
        double sum = 0; for (double v : signs_) sum += v;
        return sum / ENTROPY_WIN;
    }
};

// ── CUSUMTracker ──────────────────────────────────────────────────────────────
// Sequential CUSUM on mid_ret1 with adaptive threshold from volatility_5.
// cusum_up_break   = 1.0 if upward regime break active,   else 0.0
// cusum_down_break = 1.0 if downward regime break active, else 0.0
class CUSUMTracker {
    double c_up_ = 0.0, c_down_ = 0.0;
public:
    struct Out { double up_break; double down_break; };

    Out update(double mid_ret, double vol5) {
        if (!std::isfinite(mid_ret)) return {c_up_ > 0 ? 1.0:0.0, c_down_ > 0 ? 1.0:0.0};
        double scale = (std::isfinite(vol5) && vol5 > 1e-9) ? vol5 : CUSUM_FALLBACK_K;
        double k = CUSUM_K_FACTOR * scale;
        double h = CUSUM_H_FACTOR * scale;
        c_up_   = std::max(0.0, c_up_   + mid_ret - k);
        c_down_ = std::max(0.0, c_down_ - mid_ret - k);
        return {c_up_ > h ? 1.0 : 0.0, c_down_ > h ? 1.0 : 0.0};
    }
};

// ── StreamingEngineer ─────────────────────────────────────────────────────────
// All 102 engineered features — exact parity with Python StreamingEngineer
struct EngineerInput {
    double vol_total,delta_sum,mlofi_sum,mlofi_decay_sum;
    double buy_vol,sell_vol;
    double sweep_buy_vol,sweep_sell_vol;
    int    sweep_buy_count,sweep_sell_count;
    double vpin,mid_mean;
    double px_close;
    int64_t bar_end_ts_ns;
};

struct EngFeatures {
    // normalized flow
    double delta_norm,mlofi_norm,decay_norm,buy_ratio,sell_ratio;
    double sweep_norm,sweep_ratio,sweep_vol,sweep_signed_vol,sweep_abs_vol;
    double sweep_buy_ratio,sweep_sell_ratio,sweep_imbalance,sweep_imbalance_norm;
    int sweep_count;
    // lags (with both _ and no-underscore aliases)
    double delta_norm_lag_1,delta_norm_lag_2,delta_norm_lag_3;
    double mlofi_norm_lag_1,mlofi_norm_lag_2,mlofi_norm_lag_3;
    double decay_norm_lag_1,decay_norm_lag_2,decay_norm_lag_3;
    double vpin_lag_1,vpin_lag_2,vpin_lag_3;
    // mid / volatility
    double mid_ret1,volatility_5,mid_roll20,mid_resid,mid_resid_std,mid_resid_z;
    // residual z-scores (native — computed alongside their base feature)
    double delta_norm_resid_z20,volatility_5_resid_z20;
    double sweep_imbalance_norm_resid_z20,vpin_resid_z20;
    std::string delta_norm_resid_z20_bucket,volatility_5_resid_z20_bucket;
    std::string sweep_imbalance_norm_resid_z20_bucket,vpin_resid_z20_bucket;
    // master-layer z20 (previously materialized by z20_master_lib.py; now native).
    // residual_z(window=20) on the listed base feature. Parity with
    // ofi_live_dashboard.residual_z VERBATIM.
    double decay_norm_resid_z20;
    double mlofi_decay_sum_resid_z20;
    double mlofi_norm_resid_z20;
    double mlofi_rolling_5_resid_z20;
    // rolling OFI
    double mlofi_rolling_5,delta_rolling_5,mlofi_accel;
    // time
    int minute_of_day,dow,tod_minute;
    // kalman
    double mid_kf;
    // expected vol
    double expected_vol_total=0,rvol_expected=0;
    // regime
    double regime_ic_mlofi,regime_ic_delta,regime_ic_mlofi20;
    double prev_wk_h40_ic,prev_wk_val_loss,prev_wk_us_ic;
    // crash-veto features (Fix 4)
    double entropy_score;       // rolling Shannon entropy of buy/sell split [0,1]
    double flow_alignment;      // rolling sign consistency of delta_norm [-1,1]
    double cusum_up_break;      // 1.0 if CUSUM upward break active
    double cusum_down_break;    // 1.0 if CUSUM downward break active
};

class StreamingEngineer {
    std::deque<double> p_delta_{}, p_mlofi_{}, p_decay_{}, p_vpin_{};
    double prev_mid_    = NAN;
    std::deque<double> ret1_{}, midv_{}, midr_{}, mlofi5_{}, delta5_{};
    RollingResidZ rz_delta_,rz_vol5_,rz_swimb_,rz_vpin_;
    // master-layer z20 trackers (previously in z20_master_lib.py)
    RollingResidZ rz_decay_norm_, rz_mlofi_decay_sum_, rz_mlofi_norm_, rz_mlofi_rolling_5_;
    double kf_x_=NAN,kf_p_=1.0;
    static constexpr double kf_q_=1e-3, kf_r_=0.0625; // 0.25^2
    RegimeICTracker      ric_;
    FlowEntropyTracker   fent_;
    FlowAlignmentTracker falign_;
    CUSUMTracker         cusum_;
    double pwk_h40_=0,pwk_loss_=1.0,pwk_us_=0;

    double kalman(double z) {
        if(!std::isfinite(z)) return NAN;
        if(!std::isfinite(kf_x_)){ kf_x_=z; kf_p_=1.0; return z; }
        kf_p_+=kf_q_;
        double k=kf_p_/(kf_p_+kf_r_);
        kf_x_+=k*(z-kf_x_); kf_p_=(1-k)*kf_p_;
        return kf_x_;
    }

public:
    void set_prev_wk(double h40_ic, double val_loss, double us_ic) {
        pwk_h40_=h40_ic; pwk_loss_=val_loss; pwk_us_=us_ic;
    }

    EngFeatures add(const EngineerInput& in) {
        EngFeatures f{};
        double vt=in.vol_total;
        double sweep_buy=in.sweep_buy_vol, sweep_sell=in.sweep_sell_vol;
        double sa=sweep_buy+sweep_sell, ss=sweep_buy-sweep_sell;
        double st_safe=(sa>1e-12)?sa:1e-12;

        if(vt>0){
            f.delta_norm=in.delta_sum/vt; f.mlofi_norm=in.mlofi_sum/vt;
            f.decay_norm=in.mlofi_decay_sum/vt;
            f.buy_ratio=in.buy_vol/vt; f.sell_ratio=in.sell_vol/vt;
            f.sweep_buy_ratio=sweep_buy/vt; f.sweep_sell_ratio=sweep_sell/vt;
            f.sweep_vol=sa; f.sweep_ratio=sa/vt; f.sweep_norm=f.sweep_ratio;
        } else {
            f.delta_norm=f.mlofi_norm=f.decay_norm=NAN;
            f.buy_ratio=f.sell_ratio=NAN;
            f.sweep_buy_ratio=f.sweep_sell_ratio=NAN;
            f.sweep_vol=f.sweep_ratio=f.sweep_norm=NAN;
        }
        f.sweep_abs_vol=sa; f.sweep_signed_vol=ss; f.sweep_imbalance=ss;
        f.sweep_imbalance_norm=ss/st_safe;
        f.sweep_count=in.sweep_buy_count+in.sweep_sell_count;

        // lags — read BEFORE appending current
        f.delta_norm_lag_1=deque_lag(p_delta_,1); f.delta_norm_lag_2=deque_lag(p_delta_,2); f.delta_norm_lag_3=deque_lag(p_delta_,3);
        f.mlofi_norm_lag_1=deque_lag(p_mlofi_,1); f.mlofi_norm_lag_2=deque_lag(p_mlofi_,2); f.mlofi_norm_lag_3=deque_lag(p_mlofi_,3);
        f.decay_norm_lag_1=deque_lag(p_decay_,1); f.decay_norm_lag_2=deque_lag(p_decay_,2); f.decay_norm_lag_3=deque_lag(p_decay_,3);
        f.vpin_lag_1=deque_lag(p_vpin_,1); f.vpin_lag_2=deque_lag(p_vpin_,2); f.vpin_lag_3=deque_lag(p_vpin_,3);

        // mid_ret1
        double mm=in.mid_mean;
        f.mid_ret1 = (std::isfinite(prev_mid_)&&std::isfinite(mm)) ? mm-prev_mid_ : NAN;

        // volatility_5
        if(std::isfinite(f.mid_ret1)){ ret1_.push_back(f.mid_ret1); while((int)ret1_.size()>5) ret1_.pop_front(); }
        f.volatility_5 = ((int)ret1_.size()>=5) ? stdev_ddof1(ret1_) : NAN;

        // residual z-scores
        f.delta_norm_resid_z20  = rz_delta_.update(f.delta_norm);
        f.volatility_5_resid_z20= rz_vol5_.update(f.volatility_5);
        f.sweep_imbalance_norm_resid_z20 = rz_swimb_.update(f.sweep_imbalance_norm);
        f.vpin_resid_z20        = rz_vpin_.update(in.vpin);
        f.delta_norm_resid_z20_bucket        = z_bucket(f.delta_norm_resid_z20);
        f.volatility_5_resid_z20_bucket      = z_bucket(f.volatility_5_resid_z20);
        f.sweep_imbalance_norm_resid_z20_bucket = z_bucket(f.sweep_imbalance_norm_resid_z20);
        f.vpin_resid_z20_bucket              = z_bucket(f.vpin_resid_z20);

        // mid rolling features
        if(std::isfinite(mm)){ midv_.push_back(mm); while((int)midv_.size()>20) midv_.pop_front(); }
        f.mid_roll20 = mean_of(midv_,20);
        f.mid_resid  = (std::isfinite(mm)&&std::isfinite(f.mid_roll20)) ? mm-f.mid_roll20 : NAN;
        if(std::isfinite(f.mid_resid)){ midr_.push_back(f.mid_resid); while((int)midr_.size()>20) midr_.pop_front(); }
        f.mid_resid_std = ((int)midr_.size()>=20) ? stdev_ddof1(midr_) : NAN;
        f.mid_resid_z   = (std::isfinite(f.mid_resid)&&std::isfinite(f.mid_resid_std)&&f.mid_resid_std>0)
                          ? f.mid_resid/f.mid_resid_std : NAN;

        // rolling OFI/MLOFI
        if(std::isfinite(f.mlofi_norm)){ mlofi5_.push_back(f.mlofi_norm); while((int)mlofi5_.size()>5) mlofi5_.pop_front(); }
        if(std::isfinite(f.delta_norm)){ delta5_.push_back(f.delta_norm); while((int)delta5_.size()>5) delta5_.pop_front(); }
        f.mlofi_rolling_5 = mean_of(mlofi5_,5);
        f.delta_rolling_5 = mean_of(delta5_,5);

        double pm1=deque_lag(p_mlofi_,1), pm2=deque_lag(p_mlofi_,2);
        f.mlofi_accel = (std::isfinite(f.mlofi_norm)&&std::isfinite(pm1)&&std::isfinite(pm2))
                        ? f.mlofi_norm-2*pm1+pm2 : NAN;

        // master-layer residual z-scores (z20 on these 4 base features).
        // Identical formula and window to the parser-native ones above —
        // RollingResidZ(window=20) which mirrors the dashboard's
        // residual_z(window=20). Previously computed by z20_master_lib.py
        // (now in-parser for low-latency direct master write).
        f.decay_norm_resid_z20         = rz_decay_norm_.update(f.decay_norm);
        f.mlofi_decay_sum_resid_z20    = rz_mlofi_decay_sum_.update(in.mlofi_decay_sum);
        f.mlofi_norm_resid_z20         = rz_mlofi_norm_.update(f.mlofi_norm);
        f.mlofi_rolling_5_resid_z20    = rz_mlofi_rolling_5_.update(f.mlofi_rolling_5);

        // time features from bar_end_ts_ns
        {
            time_t s=(time_t)(in.bar_end_ts_ns/1'000'000'000LL);
            struct tm t{}; gmtime_r(&s,&t);
            f.minute_of_day = t.tm_hour*60+t.tm_min;
            f.dow           = (t.tm_wday==0)?6:(t.tm_wday-1); // 0=Mon…6=Sun like Python
            f.tod_minute    = f.minute_of_day;
        }

        // Kalman filter
        f.mid_kf = std::isfinite(mm) ? kalman(mm) : NAN;

        // expected vol placeholders
        f.expected_vol_total=0.0; f.rvol_expected=0.0;

        // regime IC
        auto ric=ric_.update(in.px_close, f.mlofi_norm, f.delta_norm);
        f.regime_ic_mlofi  =ric.mlofi;
        f.regime_ic_delta  =ric.delta;
        f.regime_ic_mlofi20=ric.mlofi20;

        // prev-week static
        f.prev_wk_h40_ic  =pwk_h40_;
        f.prev_wk_val_loss=pwk_loss_;
        f.prev_wk_us_ic   =pwk_us_;

        // crash-veto features (Fix 4)
        f.entropy_score    = fent_.update(in.buy_vol, vt);
        f.flow_alignment   = falign_.update(f.delta_norm);
        {
            auto co = cusum_.update(f.mid_ret1, f.volatility_5);
            f.cusum_up_break   = co.up_break;
            f.cusum_down_break = co.down_break;
        }

        // append lags AFTER using them
        auto push3=[](std::deque<double>& dq,double v){ dq.push_back(v); while((int)dq.size()>3) dq.pop_front(); };
        push3(p_delta_,std::isfinite(f.delta_norm)?f.delta_norm:NAN);
        push3(p_mlofi_,std::isfinite(f.mlofi_norm)?f.mlofi_norm:NAN);
        push3(p_decay_,std::isfinite(f.decay_norm)?f.decay_norm:NAN);
        push3(p_vpin_, std::isfinite(in.vpin)?in.vpin:NAN);
        prev_mid_ = std::isfinite(mm) ? mm : NAN;

        return f;
    }

    // Replay a previously-computed bar to restore rolling state (cross-day continuity)
    void seed(const EngineerInput& in) { add(in); }
};
