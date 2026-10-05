// ofi_live_parser.cpp — OFI Live Feature Parser (C++17)
// Reads Rithmic NDJSON raw files, builds vol500/vol200 bars with all 102 features,
// writes NDJSON output files, supports historical catchup then live inotify tail.
//
// Usage:
//   ofi_live_parser --raw_dir <path> --symbol <NQM6>
//                   [--out_dir <path>] [--vol500 500] [--vol200 200]
//                   [--catchup] [--prev_wk_h40_ic 0.0612]
//                   [--prev_wk_val_loss 1.0] [--prev_wk_us_ic 0.0]

#include "../include/ofi_math.h"
#include "../include/bar_builder.h"

#include <simdjson.h>
#include <sys/inotify.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>

#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <tuple>
#include <unordered_map>

// ── clean shutdown on SIGTERM / SIGINT ────────────────────────────────────────
static volatile std::sig_atomic_t g_stop = 0;
static void on_signal(int) { g_stop = 1; }

// ── Raw event kinds ───────────────────────────────────────────────────────────
enum class EvKind { DEPTH_SNAPSHOT, QUOTE_UPDATE, TRADE };

struct RithEvent {
    int64_t recv_ts_ns  = 0;
    int64_t event_ts_ns = 0;
    EvKind  kind        = EvKind::TRADE;

    // Depth snapshot fields
    char    depth_side  = 0;   // 'B' or 'A'
    double  depth_px    = 0;
    double  depth_sz    = 0;
    int64_t lob_seq     = 0;

    // Quote update fields (bid/ask)
    char    quote_side  = 0;
    double  quote_px    = 0;
    double  quote_sz    = 0;
    int     upd_type    = 0;   // 1=SOLO 2=BEGIN 3=MIDDLE 4=END
    int     cb_type     = 0;   // 1=IMAGE 2=UPDATE

    // Trade fields
    double  trade_px    = 0;
    double  trade_sz    = 0;
    char    trade_side  = 0;   // 'B' or 'A' (aggressor)

    bool operator>(const RithEvent& o) const {
        if (event_ts_ns != o.event_ts_ns) return event_ts_ns > o.event_ts_ns;
        return recv_ts_ns > o.recv_ts_ns;
    }
};

// ── simdjson helper: safe field extraction ────────────────────────────────────
inline double jdbl(simdjson::dom::element el, const char* k, double def=0.0) {
    double v; if(el[k].get(v)==simdjson::SUCCESS) return v; return def;
}
inline int64_t ji64(simdjson::dom::element el, const char* k, int64_t def=0) {
    int64_t v; if(el[k].get(v)==simdjson::SUCCESS) return v;
    uint64_t u; if(el[k].get(u)==simdjson::SUCCESS) return (int64_t)u;
    return def;
}
inline std::string_view jstr(simdjson::dom::element el, const char* k) {
    std::string_view v; el[k].get(v); return v;
}

// ── File line reader (supports seek-to-end, incremental reads) ────────────────
class FileReader {
    FILE*        fh_  = nullptr;
    std::string  path_;
    std::string  buf_;
    bool         at_end_ = false;  // catchup: start at beginning; tail: start at end

public:
    FileReader() = default;
    explicit FileReader(const std::string& path, bool start_at_end=false)
        : path_(path)
    {
        fh_ = fopen(path.c_str(), "r");
        if (!fh_) return;
        if (start_at_end) { fseek(fh_, 0, SEEK_END); at_end_ = true; }
    }
    ~FileReader() { if(fh_) fclose(fh_); }

    bool valid() const { return fh_ != nullptr; }
    const std::string& path() const { return path_; }

    // Read next complete line into `out`. Returns false if no complete line yet.
    bool next_line(std::string& out) {
        while(true) {
            int c = fgetc(fh_);
            if (c == EOF) {
                clearerr(fh_);  // Clear EOF flag so appended data is visible on next call
                return false;
            }
            if (c == '\n') {
                out = buf_; buf_.clear(); return true;
            }
            buf_ += (char)c;
        }
    }
};

// ── Parse one NDJSON line into RithEvent(s) ───────────────────────────────────
// Returns 0..N events (trades batch may expand to multiple)
static void parse_line(const std::string& line, simdjson::dom::parser& jp,
                       std::vector<RithEvent>& out)
{
    if (line.empty() || line[0] != '{') return;
    simdjson::dom::element doc;
    simdjson::padded_string ps(line);
    auto err = jp.parse(ps).get(doc);
    if (err) return;

    std::string_view schema = jstr(doc, "schema");

    // ── Depth snapshot ────────────────────────────────────────────────────────
    if (schema == "xgofi.depth.v1") {
        RithEvent ev;
        ev.kind         = EvKind::DEPTH_SNAPSHOT;
        ev.recv_ts_ns   = ji64(doc,"recv_ts_ns");
        ev.event_ts_ns  = ji64(doc,"timestamp_ns", ev.recv_ts_ns);
        auto sv = jstr(doc,"side");
        ev.depth_side   = (!sv.empty()) ? (char)sv[0] : 'A';
        // NOTE: depth.ndjson's "price" carries truncated precision on captures before the
        // SampleMD.cpp setprecision(6) fix (see rithmic_build/13.6.0.0/samples/SampleMD.cpp) --
        // LOBBook::update_bid/update_ask's norm_px() below already recovers the true tick value,
        // so no correction is needed here. Don't re-derive this without re-checking that call path.
        ev.depth_px     = jdbl(doc,"price");
        ev.depth_sz     = jdbl(doc,"size");
        ev.lob_seq      = ji64(doc,"lob_snapshot_seq");
        out.push_back(ev);
        return;
    }

    // ── Bid/Ask quote update ──────────────────────────────────────────────────
    if (schema == "xgofi.bidquote.v1" || schema == "xgofi.askquote.v1") {
        RithEvent ev;
        ev.kind         = EvKind::QUOTE_UPDATE;
        ev.recv_ts_ns   = ji64(doc,"recv_ts_ns");
        ev.event_ts_ns  = ji64(doc,"event_ts_ns", ev.recv_ts_ns);
        auto sv = jstr(doc,"side");
        ev.quote_side   = (!sv.empty()) ? (char)sv[0] : (schema[6]=='b' ? 'B' : 'A');
        bool pv=false; doc["price_valid"].get(pv);
        ev.quote_px     = pv ? jdbl(doc,"price") : 0.0;
        bool sv2=false; doc["size_valid"].get(sv2);
        ev.quote_sz     = sv2 ? jdbl(doc,"size") : 0.0;
        ev.upd_type     = (int)ji64(doc,"update_type_code",1);
        ev.cb_type      = (int)ji64(doc,"callback_type_code",2);
        out.push_back(ev);
        return;
    }

    // ── Trade ─────────────────────────────────────────────────────────────────
    if (schema == "xgofi.trade.v1") {
        RithEvent ev;
        ev.kind         = EvKind::TRADE;
        ev.recv_ts_ns   = ji64(doc,"recv_ts_ns");
        ev.event_ts_ns  = ji64(doc,"timestamp_ns", ev.recv_ts_ns);
        ev.trade_px     = jdbl(doc,"price");
        ev.trade_sz     = jdbl(doc,"size");
        auto agg = jstr(doc,"aggressor_side");
        // "B"=buy "S"=sell (Rithmic uses 'S' for sell, Python uses 'A')
        ev.trade_side   = (!agg.empty() && (agg[0]=='B'||agg[0]=='b')) ? 'B' : 'A';
        out.push_back(ev);
        return;
    }
}

// ── Stream Processor: shared LOB → two bar builders ───────────────────────────
class StreamProcessor {
    LOBBook      book_;
    std::vector<Level> last_b_, last_a_;
    double       last_mid_ = 0.0;

    VolumeBarBuilder b500_, b200_;

    // Legacy pending quote packet state. Kept for compatibility with the old
    // packet-flush path, but current parity logic processes every quote row
    // immediately using event-time order, matching the trusted Python livepatch.
    struct PendingPkt {
        bool active       = false;
        bool is_image     = false;  // IMAGE callback → baseline rebuild only
        std::vector<std::pair<char,std::pair<double,double>>> updates;  // {side, {px,sz}}
    } pkt_;

    int64_t last_lob_seq_ = -1;
    int     day_int_      = 0;

    void push_book_obs(int64_t ts_ns, double ofi_raw, double ofi_dec, double mid, double pr) {
        b500_.apply_book_obs(ts_ns, ofi_raw, ofi_dec, mid, pr);
        b200_.apply_book_obs(ts_ns, ofi_raw, ofi_dec, mid, pr);
    }

    void flush_packet(int64_t ts_ns) {
        if (!pkt_.active || pkt_.updates.empty()) { pkt_={};  return; }

        bool baseline_missing = last_b_.empty() || last_a_.empty();

        // ── Image / baseline-missing case: apply all updates as a snapshot
        // refresh and do NOT push OFI. (Matches Python: on snapshot/reset
        // events the master builder rebuilds the book without computing
        // delta-OFI against an undefined baseline.)
        if (pkt_.is_image || baseline_missing) {
            for (auto& [side, pxsz] : pkt_.updates) {
                if (side == 'B') book_.update_bid(pxsz.first, pxsz.second);
                else             book_.update_ask(pxsz.first, pxsz.second);
            }
            last_b_ = book_.top_bids(10);
            last_a_ = book_.top_asks(10);
            double m = book_.mid();
            if (m > 0) last_mid_ = m;
            pkt_ = {};
            return;
        }

        // ── PARITY PATCH (2026-06-08) ─────────────────────────────────────
        // Match the Python master builder semantics: every individual book
        // update produces its own snapshot-before-vs-snapshot-after OFI
        // computation. Bundling multiple updates into a single before/after
        // comparison silently changed the decay-weighting structure and
        // inflated mlofi_decay_sum / decay_norm / mlofi_norm by ~5-7x in
        // live data vs the offline master.
        //
        // book_count semantics:
        //   BEFORE patch: incremented once per packet flush
        //   AFTER  patch: incremented once per individual update (matches
        //                 Python's per-MBO-event book_count++)
        // ─────────────────────────────────────────────────────────────────
        auto cur_b = last_b_, cur_a = last_a_;
        for (auto& [side, pxsz] : pkt_.updates) {
            auto prev_b_local = cur_b;
            auto prev_a_local = cur_a;

            if (side == 'B') book_.update_bid(pxsz.first, pxsz.second);
            else             book_.update_ask(pxsz.first, pxsz.second);

            cur_b = book_.top_bids(10);
            cur_a = book_.top_asks(10);

            // Mirror Python `if curr_b and curr_a:` — only push when the
            // book is two-sided after this update.
            if (cur_b.empty() || cur_a.empty()) continue;

            double raw_ofi = compute_raw_ofi(prev_b_local, prev_a_local,
                                              cur_b, cur_a, OFI_K_RAW);
            double dec_ofi = compute_decayed_ofi(prev_b_local, prev_a_local,
                                                  cur_b, cur_a, OFI_K_DECAY);
            double pr  = pressure_ratio(cur_b, cur_a);
            double bb  = cur_b[0].px;
            double ba  = cur_a[0].px;
            double mid = (bb > 0 && ba > 0) ? 0.5 * (bb + ba)
                                            : (last_mid_ > 0 ? last_mid_ : 0.0);
            if (mid > 0) last_mid_ = mid;

            push_book_obs(ts_ns, raw_ofi, dec_ofi, mid, pr);
        }

        last_b_ = cur_b;
        last_a_ = cur_a;
        pkt_ = {};
    }

    void process_quote_row(const RithEvent& ev) {
        if (ev.quote_px <= 0) return;

        auto prev_b = last_b_;
        auto prev_a = last_a_;

        if (ev.quote_side == 'B') book_.update_bid(ev.quote_px, ev.quote_sz);
        else                      book_.update_ask(ev.quote_px, ev.quote_sz);

        auto cur_b = book_.top_bids(10);
        auto cur_a = book_.top_asks(10);

        // Trusted Python livepatch semantics:
        // - each bid/ask quote row is an absolute level replace/delete
        // - compute OFI immediately after that row
        // - if either side lacks a baseline/current snapshot, set baseline only
        if (prev_b.empty() || prev_a.empty() || cur_b.empty() || cur_a.empty()) {
            last_b_ = cur_b;
            last_a_ = cur_a;
            double m = book_.mid();
            if (m > 0) last_mid_ = m;
            return;
        }

        double raw_ofi = compute_raw_ofi(prev_b, prev_a, cur_b, cur_a, OFI_K_RAW);
        double dec_ofi = compute_decayed_ofi(prev_b, prev_a, cur_b, cur_a, OFI_K_DECAY);
        double pr  = pressure_ratio(cur_b, cur_a);
        double bb  = cur_b[0].px;
        double ba  = cur_a[0].px;
        double mid = (bb > 0 && ba > 0) ? 0.5 * (bb + ba)
                                        : (last_mid_ > 0 ? last_mid_ : 0.0);
        if (mid > 0) last_mid_ = mid;

        push_book_obs(ev.event_ts_ns, raw_ofi, dec_ofi, mid, pr);
        last_b_ = cur_b;
        last_a_ = cur_a;
    }

    // Shared NDJSON writer for the master file. Opened iff --master_out is
    // provided; mirrored into b500_ via set_master_writer().
    NDJSONWriter master_writer_;

public:
    StreamProcessor(const std::string& raw_dir, const std::string& symbol,
                    const std::string& out_dir, double vol500=500.0, double vol200=200.0,
                    const std::string& master_out="")
        : b500_(vol500, out_dir+"/"+symbol+"_vol500.ndjsonl")
        , b200_(vol200, out_dir+"/"+symbol+"_vol200.ndjsonl")
    {
        if (!master_out.empty()) {
            if (master_writer_.open(master_out)) {
                b500_.set_master_writer(&master_writer_);
                fprintf(stderr, "[OFI] master_out: %s (mirrored from vol500 bars)\n",
                        master_out.c_str());
            } else {
                fprintf(stderr, "[OFI] WARNING: failed to open master_out=%s; "
                                 "vol500/vol200 will still be written\n",
                        master_out.c_str());
            }
        }
    }

    void set_prev_wk(double h40, double vloss, double us) {
        b500_.set_prev_wk(h40,vloss,us);
        b200_.set_prev_wk(h40,vloss,us);
    }

    void set_day(int d) { day_int_=d; }

    void seed_vol500(const std::string& path) { b500_.seed_from_file(path); }
    void seed_vol200(const std::string& path) { b200_.seed_from_file(path); }

    void process(const RithEvent& ev) {
        switch (ev.kind) {

        case EvKind::DEPTH_SNAPSHOT: {
            // Group by lob_snapshot_seq: when seq changes, previous snapshot is complete
            if (ev.lob_seq != last_lob_seq_) {
                if (last_lob_seq_ >= 0) {
                    // Previous snapshot group complete: apply to book (baseline only, no OFI)
                    // (already applied incrementally above; here just confirm baseline)
                }
                // New snapshot: rebuild book from scratch
                if (ev.lob_seq != last_lob_seq_) {
                    book_.clear();
                    last_b_.clear(); last_a_.clear();
                }
                last_lob_seq_ = ev.lob_seq;
            }
            // Apply this level to book (depth snapshots are absolute levels)
            if (ev.depth_side=='B') book_.update_bid(ev.depth_px, ev.depth_sz);
            else                    book_.update_ask(ev.depth_px, ev.depth_sz);
            // Update baselines for next OFI computation
            last_b_ = book_.top_bids(10);
            last_a_ = book_.top_asks(10);
            double m = book_.mid();
            if (m>0) last_mid_=m;
            break;
        }

        case EvKind::QUOTE_UPDATE: {
            process_quote_row(ev);
            break;
        }

        case EvKind::TRADE: {
            double price = ev.trade_px;
            double size  = ev.trade_sz;
            if (price<=0 || size<=0) break;

            double bb=book_.best_bid(), ba=book_.best_ask();
            bool depth_ok = (bb>0 && ba>0);
            double mid = depth_ok ? 0.5*(bb+ba) : (last_mid_>0 ? last_mid_ : price);
            double pr;
            if (depth_ok) {
                auto cb=book_.top_bids(50), ca=book_.top_asks(50);
                pr = pressure_ratio(cb, ca);
                last_mid_ = mid;
            } else {
                pr = 1.0;
            }

            if (day_int_==0) day_int_ = day_int_from_ns(ev.event_ts_ns);
            b500_.apply_trade(ev.event_ts_ns, ev.trade_side, price, size, mid, pr, depth_ok, day_int_);
            b200_.apply_trade(ev.event_ts_ns, ev.trade_side, price, size, mid, pr, depth_ok, day_int_);
            break;
        }
        }
    }

    void print_status() {
        fprintf(stderr, "[OFI] book bid=%.2f ask=%.2f mid=%.2f  last_mid=%.2f\n",
                book_.best_bid(), book_.best_ask(), book_.mid(), last_mid_);
    }
};

// ── Multi-file merge reader ────────────────────────────────────────────────────
// Keeps one "head" event per file in a min-heap, yielding events in recv_ts_ns order.
struct HeapItem {
    RithEvent     ev;
    FileReader*   reader;
    simdjson::dom::parser* jp;
    bool operator>(const HeapItem& o) const { return ev.recv_ts_ns > o.ev.recv_ts_ns; }
};

static bool refill(HeapItem& item, std::vector<RithEvent>& tmp) {
    tmp.clear();
    while (tmp.empty()) {
        std::string line;
        if (!item.reader->next_line(line)) return false;
        parse_line(line, *item.jp, tmp);
    }
    item.ev = tmp[0];
    // Multi-event lines (shouldn't happen with Rithmic NDJSON but handle gracefully)
    return true;
}

// ── Historical catchup ────────────────────────────────────────────────────────
static void run_catchup(StreamProcessor& sp,
                        const std::string& raw_dir, const std::string& symbol,
                        bool verbose)
{
    // Files to merge (in priority order: depth, bid, ask, trades)
    std::vector<std::string> files = {
        raw_dir + "/" + symbol + "/depth.ndjson",
        raw_dir + "/" + symbol + "/bid_quote_updates.ndjson",
        raw_dir + "/" + symbol + "/ask_quote_updates.ndjson",
        raw_dir + "/" + symbol + "/trades.ndjson",
    };

    std::vector<std::unique_ptr<FileReader>>             readers;
    std::vector<std::unique_ptr<simdjson::dom::parser>>  parsers;

    std::priority_queue<HeapItem, std::vector<HeapItem>, std::greater<HeapItem>> heap;
    std::vector<RithEvent> tmp;

    for (auto& f : files) {
        auto r = std::make_unique<FileReader>(f, false /*start from beginning*/);
        if (!r->valid()) {
            if (verbose) fprintf(stderr, "[OFI] skip missing: %s\n", f.c_str());
            continue;
        }
        if (verbose) fprintf(stderr, "[OFI] catchup: %s\n", f.c_str());
        auto p = std::make_unique<simdjson::dom::parser>();

        HeapItem item; item.reader=r.get(); item.jp=p.get();
        if (refill(item, tmp)) heap.push(item);

        readers.push_back(std::move(r));
        parsers.push_back(std::move(p));
    }

    int64_t events=0, report_every=500000;
    auto t0 = std::chrono::steady_clock::now();

    while (!heap.empty()) {
        HeapItem top = heap.top(); heap.pop();

        sp.process(top.ev);
        events++;

        if (verbose && events % report_every == 0) {
            auto dt = std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now()-t0).count();
            fprintf(stderr, "[OFI] catchup %lld events  %.1fs\n",
                    (long long)events, dt/1000.0);
        }

        if (refill(top, tmp)) heap.push(top);
    }

    if (verbose) {
        auto dt = std::chrono::duration_cast<std::chrono::milliseconds>(
                  std::chrono::steady_clock::now()-t0).count();
        fprintf(stderr, "[OFI] catchup complete: %lld events in %.2fs\n",
                (long long)events, dt/1000.0);
    }
}

// ── Live tail using inotify ───────────────────────────────────────────────────
static void run_tail(StreamProcessor& sp,
                     const std::string& raw_dir, const std::string& symbol,
                     bool verbose)
{
    std::string sym_dir = raw_dir + "/" + symbol;

    // Open tail readers starting at END of current files
    struct TailFile { FileReader* reader; simdjson::dom::parser jp; };
    std::vector<std::pair<std::string, TailFile*>> tail_files;

    auto open_tail = [&](const std::string& name) -> TailFile* {
        auto* tf = new TailFile();
        tf->reader = new FileReader(sym_dir + "/" + name, true /*start at end*/);
        if (!tf->reader->valid()) { delete tf->reader; delete tf; return nullptr; }
        return tf;
    };

    auto* tf_depth = open_tail("depth.ndjson");
    auto* tf_bid   = open_tail("bid_quote_updates.ndjson");
    auto* tf_ask   = open_tail("ask_quote_updates.ndjson");
    auto* tf_trade = open_tail("trades.ndjson");

    // inotify on the symbol directory
    int ifd = inotify_init1(IN_NONBLOCK);
    int wd  = (ifd>=0) ? inotify_add_watch(ifd, sym_dir.c_str(), IN_MODIFY|IN_CREATE) : -1;

    if (verbose) fprintf(stderr, "[OFI] tail mode started on %s\n", sym_dir.c_str());

    std::vector<RithEvent> tmp;
    char inbuf[4096];

    auto drain = [&](TailFile* tf) {
        if (!tf || !tf->reader->valid()) return;
        std::string line;
        while (tf->reader->next_line(line)) {
            tmp.clear();
            parse_line(line, tf->jp, tmp);
            for (auto& ev : tmp) sp.process(ev);
        }
    };

    while (!g_stop) {
        // Drain inotify events (non-blocking)
        if (wd >= 0) {
            ssize_t len = read(ifd, inbuf, sizeof(inbuf));
            if (len > 0) {
                // File changed — drain all tail files
                drain(tf_depth);
                drain(tf_bid);
                drain(tf_ask);
                drain(tf_trade);
            } else {
                // No inotify event — short sleep, then drain anyway (safety)
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
                drain(tf_depth);
                drain(tf_bid);
                drain(tf_ask);
                drain(tf_trade);
            }
        } else {
            // Fallback: polling if inotify unavailable
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            drain(tf_depth);
            drain(tf_bid);
            drain(tf_ask);
            drain(tf_trade);
        }
    }
}

// ── CLI ───────────────────────────────────────────────────────────────────────
static void usage(const char* prog) {
    fprintf(stderr,
        "Usage: %s --raw_dir DIR --symbol SYM [options]\n"
        "  --raw_dir DIR           Rithmic raw data root (e.g. /home/prabh/OFI_Live_Data/Rithmic_Raw/2026-06-03)\n"
        "  --symbol SYM            Instrument symbol (e.g. NQM6)\n"
        "  --out_dir DIR           Output directory (default /home/prabh/OFI_Live_Features/YYYY-MM-DD)\n"
        "  --master_out FILE       Append every completed vol500 bar to FILE (master.ndjsonl).\n"
        "                          Writes synchronously alongside the vol500 file — sub-ms latency.\n"
        "  --no_catchup            Skip historical replay, tail only\n"
        "  --catchup_only          Catchup then exit (no tail) — for historical batch processing\n"
        "  --seed_vol500 FILE      Seed vol500 state from last 286 bars of FILE\n"
        "  --seed_vol200 FILE      Seed vol200 state from last 286 bars of FILE\n"
        "  --prev_wk_h40_ic F      Previous week H40 IC (default 0.0)\n"
        "  --prev_wk_val_loss F    Previous week val_loss (default 1.0)\n"
        "  --prev_wk_us_ic F       Previous week US mlofi IC (default 0.0)\n"
        "  --quiet                 Suppress progress output\n",
        prog);
}

int main(int argc, char* argv[]) {
    std::string raw_dir, symbol, out_dir, master_out;
    std::string seed_vol500, seed_vol200;
    bool do_catchup = true, catchup_only = false, verbose = true;
    double pwk_h40=0.0, pwk_loss=1.0, pwk_us=0.0;

    std::signal(SIGTERM, on_signal);
    std::signal(SIGINT,  on_signal);

    for (int i=1; i<argc; i++) {
        std::string a=argv[i];
        auto nx=[&]()->std::string{ return (i+1<argc)?argv[++i]:""; };
        if      (a=="--raw_dir")          raw_dir      = nx();
        else if (a=="--symbol")           symbol       = nx();
        else if (a=="--out_dir")          out_dir      = nx();
        else if (a=="--master_out")       master_out   = nx();
        else if (a=="--no_catchup")       do_catchup   = false;
        else if (a=="--catchup_only")     catchup_only = true;
        else if (a=="--seed_vol500")      seed_vol500  = nx();
        else if (a=="--seed_vol200")      seed_vol200  = nx();
        else if (a=="--prev_wk_h40_ic")   pwk_h40      = std::stod(nx());
        else if (a=="--prev_wk_val_loss") pwk_loss     = std::stod(nx());
        else if (a=="--prev_wk_us_ic")    pwk_us       = std::stod(nx());
        else if (a=="--quiet")            verbose      = false;
        else if (a=="--help"||a=="-h")    { usage(argv[0]); return 0; }
    }

    if (raw_dir.empty() || symbol.empty()) {
        fprintf(stderr, "Error: --raw_dir and --symbol are required.\n");
        usage(argv[0]); return 1;
    }

    // Auto-detect date from raw_dir for output path
    if (out_dir.empty()) {
        // Extract date part from raw_dir (last path component like 2026-06-03)
        std::string date_part = std::filesystem::path(raw_dir).filename().string();
        out_dir = "/home/prabh/OFI_Live_Features/" + date_part;
    }

    std::filesystem::create_directories(out_dir);

    if (verbose) {
        fprintf(stderr, "[OFI] raw_dir  : %s\n", (raw_dir+"/"+symbol).c_str());
        fprintf(stderr, "[OFI] out_dir  : %s\n", out_dir.c_str());
        fprintf(stderr, "[OFI] symbol   : %s\n", symbol.c_str());
        fprintf(stderr, "[OFI] catchup  : %s\n", do_catchup?"yes":"no");
        fprintf(stderr, "[OFI] prev_wk  : h40_ic=%.4f val_loss=%.4f us_ic=%.4f\n",
                pwk_h40, pwk_loss, pwk_us);
    }

    StreamProcessor sp(raw_dir, symbol, out_dir, 500.0, 200.0, master_out);
    sp.set_prev_wk(pwk_h40, pwk_loss, pwk_us);

    // Seed rolling state from previous session output (cross-day lag / regime IC continuity)
    if (!seed_vol500.empty()) sp.seed_vol500(seed_vol500);
    if (!seed_vol200.empty()) sp.seed_vol200(seed_vol200);

    // Set day from directory name
    {
        auto date_str = std::filesystem::path(raw_dir).filename().string();
        // Expects YYYY-MM-DD
        if (date_str.size()==10 && date_str[4]=='-' && date_str[7]=='-') {
            int y=std::stoi(date_str.substr(0,4));
            int m=std::stoi(date_str.substr(5,2));
            int d=std::stoi(date_str.substr(8,2));
            sp.set_day(y*10000+m*100+d);
        }
    }

    if (do_catchup) run_catchup(sp, raw_dir, symbol, verbose);
    if (!catchup_only && !g_stop) run_tail(sp, raw_dir, symbol, verbose);

    if (verbose) fprintf(stderr, "[OFI] parser exiting cleanly.\n");
    return 0;
}
