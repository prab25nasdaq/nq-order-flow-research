// bar_builder.h — VolumeBarBuilder: accumulates raw book/trade observations,
// closes bars at bucket_vol, calls StreamingEngineer, writes NDJSON output.
#pragma once

#include "ofi_math.h"
#include <simdjson.h>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

// ── Raw bar accumulator ────────────────────────────────────────────────────────
struct BarState {
    int64_t bar_index=0;
    int64_t bar_start_ts_ns=0, bar_end_ts_ns=0;
    double  vol_total=0, buy_vol=0, sell_vol=0, delta_sum=0;
    double  dollar_value=0;
    double  mlofi_sum=0, mlofi_decay=0;
    double  pr_sum=0, mid_sum=0;
    double  trade_pv_sum=0, trade_v_sum=0, trade_px_sum=0;
    int     trade_px_cnt=0, trade_count=0, book_count=0, obs_count=0;
    double  px_open=NAN, px_high=NAN, px_low=NAN, px_close=NAN;
    double  vpin=NAN;
    double  sweep_buy_vol=0, sweep_sell_vol=0;
    int     sweep_buy_count=0, sweep_sell_count=0;
};

// ── NDJSON writer helper ───────────────────────────────────────────────────────
class NDJSONWriter {
    FILE*  fh_ = nullptr;
    std::string path_;
public:
    NDJSONWriter() = default;
    ~NDJSONWriter() { close(); }

    bool open(const std::string& path) {
        std::filesystem::create_directories(std::filesystem::path(path).parent_path());
        // append mode: if file exists resume from end (tail restart safe)
        fh_ = fopen(path.c_str(), "a");
        path_ = path;
        return fh_ != nullptr;
    }
    void close() { if(fh_){ fflush(fh_); fclose(fh_); fh_=nullptr; } }

    void write(const std::string& line) {
        if(!fh_) return;
        fputs(line.c_str(), fh_); fputc('\n', fh_); fflush(fh_);
    }
    const std::string& path() const { return path_; }
};

// ── JSON serialiser for one complete bar ──────────────────────────────────────
inline void append_f(std::string& s, const char* k, double v, bool comma=true) {
    if(comma) s+=',';
    s+='"'; s+=k; s+="\":";
    if(!std::isfinite(v)) { s+="null"; return; }
    char buf[32]; snprintf(buf,sizeof(buf),"%.10g",v); s+=buf;
}
inline void append_i(std::string& s, const char* k, int64_t v, bool comma=true) {
    if(comma) s+=',';
    s+='"'; s+=k; s+="\":";
    char buf[32]; snprintf(buf,sizeof(buf),"%lld",(long long)v); s+=buf;
}
inline void append_s(std::string& s, const char* k, const std::string& v, bool comma=true) {
    if(comma) s+=',';
    s+='"'; s+=k; s+="\":\""; s+=v; s+="\"";
}

// ── Volume bar builder ─────────────────────────────────────────────────────────
class VolumeBarBuilder {
    double       bucket_;
    BarState*    cur_=nullptr;
    int64_t      next_idx_=0;
    VPINTracker  vpin_;
    SweepTracker sweep_;
    StreamingEngineer eng_;
    NDJSONWriter writer_;
    // Optional shared writer that mirrors each completed bar into the master
    // file. Pointed at the StreamProcessor's master_writer_ when caller uses
    // --master_out. Only the vol500 builder should be wired through (master
    // is built from vol500 bars by project convention).
    NDJSONWriter* master_writer_ = nullptr;

    BarState* new_bar(int64_t ts_ns) {
        auto* b = new BarState{};
        b->bar_index      = next_idx_++;
        b->bar_start_ts_ns= ts_ns;
        b->bar_end_ts_ns  = ts_ns;
        return b;
    }

    BarState* ensure(int64_t ts_ns) {
        if(!cur_) cur_=new_bar(ts_ns);
        return cur_;
    }

    // Serialize one closed bar + all engineered features → NDJSON line
    std::string serialize(const BarState& b, const EngFeatures& f, int day_int) {
        int64_t bsns = b.bar_start_ts_ns, bens = b.bar_end_ts_ns;
        int64_t dur_ns = bens - bsns;
        double  dur_s  = dur_ns / 1e9;

        int obs = b.obs_count;
        double pr_mean   = (obs>0) ? b.pr_sum/obs   : NAN;
        double mid_mean  = (obs>0) ? b.mid_sum/obs  : NAN;
        double mlofi_mean= (obs>0) ? b.mlofi_sum/obs: NAN;
        double mdec_mean = (obs>0) ? b.mlofi_decay/obs: NAN;
        double mdec_sum  = b.mlofi_decay;

        double tv=b.trade_v_sum;
        double mid_trade = (tv>0) ? b.trade_pv_sum/tv : NAN;
        double tpv_mean  = mid_trade;

        std::string bar_start = ns_to_ts_str(bsns);
        std::string bar_end   = ns_to_ts_str(bens);
        int has_trade = (b.trade_count>0)?1:0;
        int has_book  = (b.book_count>0)?1:0;

        std::string s;
        s.reserve(2048);
        s+='{';

        // Identifier/time columns
        append_s(s,"timestamp",bar_end,false);
        append_i(s,"bar_index",b.bar_index);
        append_s(s,"bar_start",bar_start);
        append_s(s,"bar_end",bar_end);
        append_i(s,"bar_duration_ns",dur_ns);
        append_f(s,"bar_duration_s",dur_s);

        // Volume
        append_f(s,"vol_total",b.vol_total);
        append_f(s,"buy_vol",b.buy_vol);
        append_f(s,"sell_vol",b.sell_vol);
        append_f(s,"delta_sum",b.delta_sum);
        append_f(s,"delta_norm",f.delta_norm);
        append_f(s,"dollar_value",b.dollar_value);

        // Mid / PR
        append_f(s,"mid_mean",mid_mean);
        append_f(s,"pr_mean",pr_mean);
        append_f(s,"vpin",b.vpin);

        // MLOFI
        append_f(s,"mlofi_sum",b.mlofi_sum);
        append_f(s,"mlofi_decay_sum",mdec_sum);
        append_f(s,"mlofi_mean",mlofi_mean);

        // Sweep
        append_f(s,"sweep_vol",f.sweep_vol);
        append_i(s,"sweep_count",f.sweep_count);
        append_f(s,"sweep_buy_vol",b.sweep_buy_vol);
        append_f(s,"sweep_sell_vol",b.sweep_sell_vol);
        append_i(s,"sweep_buy_count",b.sweep_buy_count);
        append_i(s,"sweep_sell_count",b.sweep_sell_count);
        append_f(s,"sweep_signed_vol",f.sweep_signed_vol);
        append_f(s,"sweep_abs_vol",f.sweep_abs_vol);
        append_f(s,"sweep_buy_ratio",f.sweep_buy_ratio);
        append_f(s,"sweep_sell_ratio",f.sweep_sell_ratio);
        append_f(s,"sweep_imbalance",f.sweep_imbalance);
        append_f(s,"sweep_imbalance_norm",f.sweep_imbalance_norm);

        // Counts
        append_i(s,"trade_count",b.trade_count);
        append_i(s,"book_count",b.book_count);
        append_i(s,"obs_count",b.obs_count);
        append_f(s,"trade_pv_sum",b.trade_pv_sum);
        append_f(s,"trade_v_sum",b.trade_v_sum);
        append_f(s,"trade_px_sum",b.trade_px_sum);
        append_i(s,"trade_px_cnt",b.trade_px_cnt);
        append_i(s,"bar_start_ts_ns",bsns);
        append_i(s,"bar_end_ts_ns",bens);
        append_f(s,"mid_sum",b.mid_sum);
        append_f(s,"pr_sum",b.pr_sum);
        append_i(s,"has_trade",has_trade);
        append_i(s,"has_book",has_book);

        // OHLC
        append_f(s,"px_open",b.px_open);
        append_f(s,"px_high",b.px_high);
        append_f(s,"px_low",b.px_low);
        append_f(s,"px_close",b.px_close);

        // Day / timestamp
        append_i(s,"day",day_int);
        append_s(s,"timestamp_utc",bar_end);

        // Normalized flow ratios
        append_f(s,"mlofi_norm",f.mlofi_norm);
        append_f(s,"decay_norm",f.decay_norm);
        append_f(s,"buy_ratio",f.buy_ratio);
        append_f(s,"sell_ratio",f.sell_ratio);
        append_f(s,"sweep_norm",f.sweep_norm);
        append_f(s,"sweep_ratio",f.sweep_ratio);

        // Lags (underscore variants)
        append_f(s,"delta_norm_lag_1",f.delta_norm_lag_1);
        append_f(s,"delta_norm_lag_2",f.delta_norm_lag_2);
        append_f(s,"delta_norm_lag_3",f.delta_norm_lag_3);
        append_f(s,"mlofi_norm_lag_1",f.mlofi_norm_lag_1);
        append_f(s,"mlofi_norm_lag_2",f.mlofi_norm_lag_2);
        append_f(s,"mlofi_norm_lag_3",f.mlofi_norm_lag_3);
        append_f(s,"decay_norm_lag_1",f.decay_norm_lag_1);
        append_f(s,"decay_norm_lag_2",f.decay_norm_lag_2);
        append_f(s,"decay_norm_lag_3",f.decay_norm_lag_3);
        append_f(s,"vpin_lag_1",f.vpin_lag_1);
        append_f(s,"vpin_lag_2",f.vpin_lag_2);
        append_f(s,"vpin_lag_3",f.vpin_lag_3);
        // no-underscore aliases
        append_f(s,"delta_norm_lag1",f.delta_norm_lag_1);
        append_f(s,"delta_norm_lag2",f.delta_norm_lag_2);
        append_f(s,"delta_norm_lag3",f.delta_norm_lag_3);
        append_f(s,"mlofi_norm_lag1",f.mlofi_norm_lag_1);
        append_f(s,"mlofi_norm_lag2",f.mlofi_norm_lag_2);
        append_f(s,"mlofi_norm_lag3",f.mlofi_norm_lag_3);

        // Mid returns / rolling
        append_f(s,"mid_ret1",f.mid_ret1);
        append_f(s,"volatility_5",f.volatility_5);
        append_f(s,"mid_roll20",f.mid_roll20);
        append_f(s,"mid_resid",f.mid_resid);
        append_f(s,"mid_resid_std",f.mid_resid_std);
        append_f(s,"mid_resid_z",f.mid_resid_z);
        append_f(s,"mid_kf",f.mid_kf);
        append_f(s,"mlofi_rolling_5",f.mlofi_rolling_5);
        append_f(s,"delta_rolling_5",f.delta_rolling_5);
        append_f(s,"mlofi_accel",f.mlofi_accel);

        // Time features
        append_i(s,"minute_of_day",f.minute_of_day);
        append_i(s,"dow",f.dow);
        append_i(s,"tod_minute",f.tod_minute);

        // Expected vol
        append_f(s,"expected_vol_total",f.expected_vol_total);
        append_f(s,"rvol_expected",f.rvol_expected);

        // Regime IC
        append_f(s,"regime_ic_mlofi",f.regime_ic_mlofi);
        append_f(s,"regime_ic_delta",f.regime_ic_delta);
        append_f(s,"regime_ic_mlofi20",f.regime_ic_mlofi20);
        append_f(s,"prev_wk_h40_ic",f.prev_wk_h40_ic);
        append_f(s,"prev_wk_val_loss",f.prev_wk_val_loss);
        append_f(s,"prev_wk_us_ic",f.prev_wk_us_ic);

        // Residual z-scores
        append_f(s,"delta_norm_resid_z20",f.delta_norm_resid_z20);
        append_s(s,"delta_norm_resid_z20_bucket",f.delta_norm_resid_z20_bucket);
        append_f(s,"volatility_5_resid_z20",f.volatility_5_resid_z20);
        append_s(s,"volatility_5_resid_z20_bucket",f.volatility_5_resid_z20_bucket);
        append_f(s,"sweep_imbalance_norm_resid_z20",f.sweep_imbalance_norm_resid_z20);
        append_s(s,"sweep_imbalance_norm_resid_z20_bucket",f.sweep_imbalance_norm_resid_z20_bucket);
        append_f(s,"vpin_resid_z20",f.vpin_resid_z20);
        append_s(s,"vpin_resid_z20_bucket",f.vpin_resid_z20_bucket);

        // crash-veto features (Fix 4)
        append_f(s,"entropy_score",    f.entropy_score);
        append_f(s,"flow_alignment",   f.flow_alignment);
        append_f(s,"cusum_up_break",   f.cusum_up_break);
        append_f(s,"cusum_down_break", f.cusum_down_break);

        // master-layer z20 (previously in z20_master_lib.py; now native)
        append_f(s,"decay_norm_resid_z20",         f.decay_norm_resid_z20);
        append_f(s,"mlofi_decay_sum_resid_z20",    f.mlofi_decay_sum_resid_z20);
        append_f(s,"mlofi_norm_resid_z20",         f.mlofi_norm_resid_z20);
        append_f(s,"mlofi_rolling_5_resid_z20",    f.mlofi_rolling_5_resid_z20);

        s+='}';
        return s;
    }

    void close_bar(int64_t ts_ns, int day_int) {
        if(!cur_) return;
        cur_->bar_end_ts_ns = ts_ns;

        // flush remaining sweep cluster
        auto sw=sweep_.flush();
        cur_->sweep_buy_vol  +=sw.bvol; cur_->sweep_sell_vol  +=sw.svol;
        cur_->sweep_buy_count+=sw.bcnt; cur_->sweep_sell_count+=sw.scnt;

        // build EngineerInput
        EngineerInput ei{};
        ei.vol_total      = cur_->vol_total;
        ei.delta_sum      = cur_->delta_sum;
        ei.mlofi_sum      = cur_->mlofi_sum;
        ei.mlofi_decay_sum= cur_->mlofi_decay;
        ei.buy_vol        = cur_->buy_vol;
        ei.sell_vol       = cur_->sell_vol;
        ei.sweep_buy_vol  = cur_->sweep_buy_vol;
        ei.sweep_sell_vol = cur_->sweep_sell_vol;
        ei.sweep_buy_count= cur_->sweep_buy_count;
        ei.sweep_sell_count=cur_->sweep_sell_count;
        ei.vpin           = cur_->vpin;
        ei.px_close       = cur_->px_close;
        ei.bar_end_ts_ns  = cur_->bar_end_ts_ns;
        int obs = cur_->obs_count;
        ei.mid_mean = (obs>0) ? cur_->mid_sum/obs : NAN;

        EngFeatures ef = eng_.add(ei);
        std::string json = serialize(*cur_, ef, day_int);
        writer_.write(json);
        if (master_writer_) master_writer_->write(json);
        delete cur_; cur_=nullptr;
    }

public:
    VolumeBarBuilder(double bucket, const std::string& out_path)
        : bucket_(bucket), vpin_(bucket), sweep_()
    {
        writer_.open(out_path);
    }

    ~VolumeBarBuilder() { delete cur_; }

    void set_prev_wk(double h40, double vloss, double us) { eng_.set_prev_wk(h40,vloss,us); }
    // Wire a shared writer that mirrors each closed bar into a master file.
    // Pass nullptr to disable. The pointer is borrowed (caller owns lifetime).
    void set_master_writer(NDJSONWriter* w) { master_writer_ = w; }

    // Apply book observation (called after each complete OFI packet)
    void apply_book_obs(int64_t ts_ns, double ofi_raw, double ofi_dec, double mid, double pr) {
        auto* b=ensure(ts_ns);
        b->bar_end_ts_ns=ts_ns;
        b->mlofi_sum  +=ofi_raw;
        b->mlofi_decay+=ofi_dec;
        b->mid_sum    +=mid;
        b->pr_sum     +=pr;
        b->obs_count  ++;
        b->book_count ++;
    }

    // Apply one trade tick — splits across bars if needed, returns number of bars closed
    int apply_trade(int64_t ts_ns, char side, double price, double size,
                    double mid, double pr, bool depth_ok, int day_int)
    {
        int closed=0;
        double rem=size;
        bool buy=(side=='B');

        while(rem>0){
            auto* b=ensure(ts_ns);
            double space=bucket_-b->vol_total;
            double take=(rem<=space)?rem:space;

            b->vol_total   +=take;
            b->dollar_value+=take*price;
            if(buy){ b->buy_vol+=take; b->delta_sum+=take; }
            else   { b->sell_vol+=take; b->delta_sum-=take; }
            b->trade_count ++;
            b->trade_pv_sum+=take*price;
            b->trade_v_sum +=take;
            b->trade_px_sum+=price;
            b->trade_px_cnt++;

            if(!std::isfinite(b->px_open)) b->px_open=b->px_high=b->px_low=b->px_close=price;
            else { if(price>b->px_high) b->px_high=price; if(price<b->px_low) b->px_low=price; b->px_close=price; }

            // book obs at trade time
            if(depth_ok){ b->book_count++; b->mid_sum+=mid; b->pr_sum+=pr; }
            else         { b->mid_sum+=mid; b->pr_sum+=1.0; }
            b->obs_count++;

            // VPIN
            vpin_.update(buy,take);
            b->vpin=vpin_.vpin();

            // Sweep
            SweepOut so=sweep_.update(ts_ns,side,take);
            b->sweep_buy_vol  +=so.bvol; b->sweep_sell_vol  +=so.svol;
            b->sweep_buy_count+=so.bcnt; b->sweep_sell_count+=so.scnt;

            b->bar_end_ts_ns=ts_ns;
            rem-=take;

            if(b->vol_total>=bucket_-1e-9){
                b->vol_total=bucket_;
                close_bar(ts_ns, day_int);
                closed++;
            }
        }
        return closed;
    }

    const std::string& output_path() const { return writer_.path(); }

    // Seed rolling state from the last max_bars of a previous session's output file.
    // Replays bars through StreamingEngineer (no I/O output) so lags, rolling windows,
    // ResidZ, Kalman, and RegimeIC buffers are warm for cross-day continuity.
    void seed_from_file(const std::string& path, int max_bars = 286) {
        if (path.empty()) return;
        std::vector<std::string> lines;
        {
            std::ifstream f(path);
            if (!f.is_open()) {
                fprintf(stderr, "[OFI] seed_from_file: cannot open %s\n", path.c_str());
                return;
            }
            std::string line;
            while (std::getline(f, line))
                if (!line.empty() && line[0] == '{') lines.push_back(line);
        }
        if (lines.empty()) return;

        int start = std::max(0, (int)lines.size() - max_bars);
        simdjson::dom::parser jp;
        int count = 0;

        for (int i = start; i < (int)lines.size(); i++) {
            simdjson::dom::element doc;
            if (jp.parse(lines[i]).get(doc)) continue;

            auto gf = [&](const char* k, double def = NAN) -> double {
                double v; return doc[k].get(v) ? def : v;
            };
            auto gi = [&](const char* k) -> int64_t {
                int64_t v; return doc[k].get(v) ? 0LL : v;
            };

            EngineerInput ein{};
            ein.vol_total        = gf("vol_total",     0.0);
            ein.delta_sum        = gf("delta_sum",     0.0);
            ein.mlofi_sum        = gf("mlofi_sum",     0.0);
            ein.mlofi_decay_sum  = gf("mlofi_decay_sum", 0.0);
            ein.buy_vol          = gf("buy_vol",       0.0);
            ein.sell_vol         = gf("sell_vol",      0.0);
            ein.sweep_buy_vol    = gf("sweep_buy_vol", 0.0);
            ein.sweep_sell_vol   = gf("sweep_sell_vol",0.0);
            ein.sweep_buy_count  = (int)gi("sweep_buy_count");
            ein.sweep_sell_count = (int)gi("sweep_sell_count");
            ein.vpin             = gf("vpin",         0.5);
            ein.mid_mean         = gf("mid_mean");
            ein.px_close         = gf("px_close");
            ein.bar_end_ts_ns    = gi("bar_end_ts_ns");

            // Advance bar index so new bars continue from where seed left off
            int64_t bidx = gi("bar_index");
            if (bidx + 1 > next_idx_) next_idx_ = bidx + 1;

            eng_.seed(ein);
            count++;
        }
        fprintf(stderr, "[OFI] seeded %d bars from %s  (next_idx=%lld)\n",
                count, path.c_str(), (long long)next_idx_);
    }
};
