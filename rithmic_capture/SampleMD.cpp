/*   =====================================================================

Copyright (c) 2025 by Omnesys Technologies, Inc.  All rights reserved.

Warning :
        This Software Product is protected by copyright law and international
        treaties.  Unauthorized use, reproduction or distribution of this
        Software Product (including its documentation), or any portion of it,
        may result in severe civil and criminal penalties, and will be
        prosecuted to the maximum extent possible under the law.

        Omnesys Technologies, Inc. will compensate individuals providing
        admissible evidence of any unauthorized use, reproduction, distribution
        or redistribution of this Software Product by any person, company or 
        organization.

This Software Product is licensed strictly in accordance with a separate
Software System License Agreement, granted by Omnesys Technologies, Inc., which
contains restrictions on use, reverse engineering, disclosure, confidentiality 
and other matters.

     =====================================================================   */
/*   =====================================================================
     Compile/link commands for linux and darwin using R | API+.  These should 
     work if your pwd is the ./samples directory.  You may need to change the 
     name of the RApi library if you are using one of the library variants, 
     like R | API or R | Diamond API.

     64-bit linux (2.6.32 kernel) :

     g++ -O3 -DLINUX -D_REENTRANT -Wall -Wno-sign-compare -Wno-write-strings -Wpointer-arith -Winline -Wno-deprecated -fno-strict-aliasing -I../include -o SampleMD ../samples/SampleMD.cpp -L../linux-gnu-2.6.32-x86_64/lib -lRApiPlus-optimize -lOmneStreamEngine-optimize -lOmneChannel-optimize -lOmneEngine-optimize -l_api-optimize -l_apipoll-stubs-optimize -l_kit-optimize -lssl -lcrypto -L/usr/lib64 -lz -L/usr/kerberos/lib -lkrb5 -lk5crypto -lcom_err -lresolv -lm -lpthread -lrt

     64-bit linux (3.10.0 kernel) :

     g++ -O3 -DLINUX -D_REENTRANT -Wall -Wno-sign-compare -Wno-write-strings -Wpointer-arith -Winline -Wno-deprecated -fno-strict-aliasing -I../include -o SampleMD ../samples/SampleMD.cpp -L../linux-gnu-3.10.0-x86_64/lib -lRApiPlus-optimize -lOmneStreamEngine-optimize -lOmneChannel-optimize -lOmneEngine-optimize -l_api-optimize -l_apipoll-stubs-optimize -l_kit-optimize -lssl -lcrypto -L/usr/lib64 -lz -lpthread -lrt -ldl

     64-bit linux (4.18 kernel) :

     g++ -O3 -DLINUX -D_REENTRANT -Wall -Wno-sign-compare -Wno-write-strings -Wpointer-arith -Winline -Wno-deprecated -fno-strict-aliasing -I../include -o SampleMD ../samples/SampleMD.cpp -L../linux-gnu-4.18-x86_64/lib -lRApiPlus-optimize -lOmneStreamEngine-optimize -lOmneChannel-optimize -lOmneEngine-optimize -l_api-optimize -l_apipoll-stubs-optimize -l_kit-optimize -lssl -lcrypto -L/usr/lib64 -lz -lpthread -lrt -ldl

     64-bit darwin :

     g++ -O3 -D_REENTRANT -Wall -Wno-sign-compare -fno-strict-aliasing -Wpointer-arith -Winline -Wno-deprecated -Wno-write-strings -I../include -o ./SampleMD ../samples/SampleMD.cpp -L../darwin-10/lib -lRApiPlus-optimize -lOmneStreamEngine-optimize -lOmneChannel-optimize -lOmneEngine-optimize -l_api-optimize -l_apipoll-stubs-optimize -l_kit-optimize -lssl -lcrypto -L/usr/lib -lz -Wl,-search_paths_first

     64-bit darwin arm64 :

     g++ -O3 -D_REENTRANT -Wall -Wno-sign-compare -fno-strict-aliasing -Wpointer-arith -Winline -Wno-deprecated -Wno-write-strings -I../include -o ./SampleMD ../samples/SampleMD.cpp -L../darwin-20.6-arm64/lib -lRApiPlus-optimize -lOmneStreamEngine-optimize -lOmneChannel-optimize -lOmneEngine-optimize -l_api-optimize -l_apipoll-stubs-optimize -l_kit-optimize -lssl -lcrypto -L/usr/lib -lz

     =====================================================================   */

#include "RApiPlus.h"

#include <iostream>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <chrono>
#include <filesystem>
#include <mutex>
#include <atomic>
#include <thread>
#include <cmath>
#include <csignal>
#include <stdexcept>
#include <cstdlib>
#include <cerrno>

#ifndef WinOS
#include <unistd.h>
#include <sys/select.h>
#else
#include <Windows.h>
#endif

#define GOOD 0
#define BAD  1

using namespace std;

using namespace RApi;

// ---------------------------
// XGOFI raw capture (NDJSON)
// ---------------------------
static std::ofstream g_trades_out;
static std::ofstream g_bbo_out;
static std::ofstream g_depth_out;
static std::ofstream g_bidquote_out;
static std::ofstream g_askquote_out;
static std::ofstream g_endquote_out;
static std::mutex g_file_mtx;
static std::mutex g_log_mtx;
static bool g_capture_initialized = false;
static bool g_capture_fatal_error = false;
static std::string g_capture_dir_path;
static std::string g_status_json_path;

static std::atomic<long long> g_cnt_trade(0);
static std::atomic<long long> g_cnt_bbo(0);
static std::atomic<long long> g_cnt_lob(0);
static std::atomic<long long> g_cnt_bidq(0);
static std::atomic<long long> g_cnt_askq(0);
static std::atomic<long long> g_cnt_quote(0);
static std::atomic<long long> g_cnt_endquote(0);
static std::atomic<bool> g_run_stats(true);
static std::atomic<long long> g_cnt_errors(0);
static std::atomic<long long> g_cnt_write_fail(0);
static std::atomic<long long> g_cnt_exceptions(0);
static std::atomic<long long> g_cnt_flushes(0);
static std::atomic<long long> g_cnt_zero_size_quote(0);
static std::atomic<long long> g_cnt_open_fail(0);
static std::atomic<bool> g_verbose_dumps(false);
static std::atomic<int> g_flush_every_n(250);

static std::atomic<long long> g_cnt_bbo_skip_null(0);
static std::atomic<long long> g_cnt_bbo_skip_flags(0);
static std::atomic<long long> g_cnt_bbo_skip_sanity(0);

struct BestSideCache
     {
     bool valid = false;
     double px = 0.0;
     long long sz = 0;
     int num_orders = 0;
     long long event_ts_ns = 0;
     long long timestamp_s = 0;
     int timestamp_usecs = 0;
     int conn_id = 0;
     std::string exchange;
     std::string symbol;
     };

static BestSideCache g_best_bid_cache;
static BestSideCache g_best_ask_cache;

static std::atomic<bool> g_truncate_on_start(false);
static std::atomic<bool> g_fail_if_exists(false);

static long long now_recv_ts_ns()
     {
     using namespace std::chrono;
     return duration_cast<nanoseconds>(system_clock::now().time_since_epoch()).count();
     }

static std::string ts_to_string(const tsNCharcb & s)
     {
     if (s.pData && s.iDataLen > 0)
          {
          return std::string(s.pData, s.iDataLen);
          }
     if (s.pData)
          {
          return std::string(s.pData);
          }
     return std::string();
     }

static std::string json_escape(const std::string& s)
     {
     std::ostringstream oss;
     for (char c : s)
          {
          switch (c)
               {
               case '\"': oss << "\\\""; break;
               case '\\': oss << "\\\\"; break;
               case '\b': oss << "\\b"; break;
               case '\f': oss << "\\f"; break;
               case '\n': oss << "\\n"; break;
               case '\r': oss << "\\r"; break;
               case '\t': oss << "\\t"; break;
               default:
                    if ((unsigned char)c < 0x20)
                         {
                         oss << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                             << (int)(unsigned char)c << std::dec;
                         }
                    else
                         {
                         oss << c;
                         }
               }
          }
     return oss.str();
     }


static void log_error_line(const std::string& msg)
     {
     std::lock_guard<std::mutex> lk(g_log_mtx);
     ++g_cnt_errors;
     std::cerr << "[XGOFI ERROR] " << msg << std::endl;
     }

static void write_status_json()
     {
     if (g_status_json_path.empty()) return;
     std::ofstream st(g_status_json_path.c_str(), std::ios::trunc);
     if (!st.is_open()) return;
     st << "{\n"
        << "  \"schema\": \"xgofi.capture_status.v1\",\n"
        << "  \"capture_initialized\": " << (g_capture_initialized ? "true" : "false") << ",\n"
        << "  \"capture_fatal_error\": " << (g_capture_fatal_error ? "true" : "false") << ",\n"
        << "  \"capture_dir\": \"" << json_escape(g_capture_dir_path) << "\",\n"
        << "  \"counts\": {\n"
        << "    \"trade\": " << g_cnt_trade.load() << ",\n"
        << "    \"bbo\": " << g_cnt_bbo.load() << ",\n"
        << "    \"lob\": " << g_cnt_lob.load() << ",\n"
        << "    \"bidq\": " << g_cnt_bidq.load() << ",\n"
        << "    \"askq\": " << g_cnt_askq.load() << ",\n"
        << "    \"quote\": " << g_cnt_quote.load() << ",\n"
        << "    \"endquote\": " << g_cnt_endquote.load() << ",\n"
        << "    \"errors\": " << g_cnt_errors.load() << ",\n"
        << "    \"write_fail\": " << g_cnt_write_fail.load() << ",\n"
        << "    \"exceptions\": " << g_cnt_exceptions.load() << ",\n"
        << "    \"flushes\": " << g_cnt_flushes.load() << ",\n"
        << "    \"zero_size_quote\": " << g_cnt_zero_size_quote.load() << ",\n"
        << "    \"bbo_skip_null_or_incomplete\": " << g_cnt_bbo_skip_null.load() << ",\n"
        << "    \"bbo_skip_flags\": " << g_cnt_bbo_skip_flags.load() << ",\n"
        << "    \"bbo_skip_sanity\": " << g_cnt_bbo_skip_sanity.load() << ",\n"
        << "    \"open_fail\": " << g_cnt_open_fail.load() << "\n"
        << "  }\n"
        << "}\n";
     }

static void flush_all_outputs()
     {
     std::lock_guard<std::mutex> lk(g_file_mtx);
     if (g_trades_out.is_open()) g_trades_out.flush();
     if (g_bbo_out.is_open()) g_bbo_out.flush();
     if (g_depth_out.is_open()) g_depth_out.flush();
     if (g_bidquote_out.is_open()) g_bidquote_out.flush();
     if (g_askquote_out.is_open()) g_askquote_out.flush();
     if (g_endquote_out.is_open()) g_endquote_out.flush();
     ++g_cnt_flushes;
     write_status_json();
     }

// MISSION rithmic-rebuild-finalize (2026-08-16) Part 2: self-pipe trick -- write() and close() are
// async-signal-safe (unlike most libc calls), so the actual shutdown logic runs on the main thread
// after select() wakes up below, not inside the signal handler itself. Without this, SIGTERM was
// only clearing a flag with nothing to unblock the fgetc(stdin) wait -- confirmed live: every
// scheduler restart was hitting the full 15s SIGTERM-timeout-then-SIGKILL path in
// rithmic_scheduler.py's stop_feed(), never reaching flush_all_outputs().
static int g_shutdown_pipe[2] = { -1, -1 };

static void signal_handler_int(int)
     {
     g_run_stats.store(false, std::memory_order_relaxed);
     if (g_shutdown_pipe[1] != -1)
          {
          char byte = 1;
          ssize_t written = write(g_shutdown_pipe[1], &byte, 1);
          (void)written;  // nothing actionable to do with a failed write from inside a signal handler
          }
     }

static void write_ndjson_line(std::ofstream& out, const std::string& line)
     {
     if (!out.is_open())
          {
          ++g_cnt_write_fail;
          log_error_line("attempted write to closed stream");
          return;
          }
     out << line << "\n";
     if (!out.good())
          {
          ++g_cnt_write_fail;
          log_error_line("stream write failed");
          return;
          }
     static thread_local int tl_write_count = 0;
     ++tl_write_count;
     int flush_n = g_flush_every_n.load(std::memory_order_relaxed);
     if (flush_n > 0 && (tl_write_count % flush_n) == 0)
          {
          out.flush();
          ++g_cnt_flushes;
          }
     }



static const char * md_callback_type_label(int t)
     {
     switch (t)
          {
          case 1: return "MD_IMAGE_CB";
          case 2: return "MD_UPDATE_CB";
          case 3: return "MD_HISTORY_CB";
          default: return "MD_UNKNOWN_CB";
          }
     }

static const char * update_type_label(int t)
     {
     switch (t)
          {
          case 0: return "UPDATE_TYPE_UNDEFINED";
          case 1: return "UPDATE_TYPE_SOLO";
          case 2: return "UPDATE_TYPE_BEGIN";
          case 3: return "UPDATE_TYPE_MIDDLE";
          case 4: return "UPDATE_TYPE_END";
          case 5: return "UPDATE_TYPE_CLEAR";
          case 6: return "UPDATE_TYPE_AGGREGATED";
          default: return "UPDATE_TYPE_UNKNOWN";
          }
     }

static std::atomic<long long> g_event_seq(0);
static std::atomic<long long> g_lob_snapshot_seq(0);

static void stats_heartbeat_loop()
     {
     long long p_trade = 0, p_bbo = 0, p_lob = 0, p_bidq = 0, p_askq = 0, p_quote = 0, p_endq = 0;

     while (g_run_stats.load(std::memory_order_relaxed))
          {
#ifndef WinOS
          sleep(10);
#else
          Sleep(10000);
#endif
          long long c_trade = g_cnt_trade.load(std::memory_order_relaxed);
          long long c_bbo   = g_cnt_bbo.load(std::memory_order_relaxed);
          long long c_lob   = g_cnt_lob.load(std::memory_order_relaxed);
          long long c_bidq  = g_cnt_bidq.load(std::memory_order_relaxed);
          long long c_askq  = g_cnt_askq.load(std::memory_order_relaxed);
          long long c_quote = g_cnt_quote.load(std::memory_order_relaxed);
          long long c_endq  = g_cnt_endquote.load(std::memory_order_relaxed);

          cout << endl
               << "[XGOFI STATS 10s]"
               << " trade +" << (c_trade - p_trade)
               << " | bbo +" << (c_bbo - p_bbo)
               << " | lob +" << (c_lob - p_lob)
               << " | bidq +" << (c_bidq - p_bidq)
               << " | askq +" << (c_askq - p_askq)
               << " | quote +" << (c_quote - p_quote)
               << " | endq +" << (c_endq - p_endq)
               << endl;

          p_trade = c_trade;
          p_bbo   = c_bbo;
          p_lob   = c_lob;
          p_bidq  = c_bidq;
          p_askq  = c_askq;
          p_quote = c_quote;
          p_endq  = c_endq;
          write_status_json();
          flush_all_outputs();
          }
     }


static void ensure_capture_dirs_and_files(const std::string& exchange,
                                          const std::string& symbol)
     {
     if (g_capture_fatal_error) throw std::runtime_error("capture fatal error already set");
     if (g_capture_initialized) return;

     // MISSION rithmic-wd-work-data-loss-rebuild (2026-08-16): this revision predates
     // XGOFI_OUTPUT_DIR support (unlike XGOFI_VERBOSE_DUMPS/XGOFI_FLUSH_EVERY_N/etc. below, which
     // it already reads) -- base_root was hardcoded to the wd_work drive that was later
     // accidentally reformatted. Reading XGOFI_OUTPUT_DIR here (same env var
     // rithmic_scheduler.py already sets) matches the existing XGOFI_* pattern in this file and
     // keeps captured data on local storage, not external/removable media, going forward.
     std::string base_root =
          "/mnt/wd_work/workspace/Work Place/Data/Project OFI/OFI_Production/DATA_FILES/Rithmic_Raw";
     if (const char* v = std::getenv("XGOFI_OUTPUT_DIR"))
          {
          base_root = v;
          }

     auto now = std::chrono::system_clock::now();
     std::time_t t = std::chrono::system_clock::to_time_t(now);
     std::tm utc_tm{};
#ifndef WinOS
     gmtime_r(&t, &utc_tm);
#else
     gmtime_s(&utc_tm, &t);
#endif

     std::ostringstream date_ss;
     date_ss << std::put_time(&utc_tm, "%Y-%m-%d");

     std::filesystem::path dir = std::filesystem::path(base_root) / date_ss.str() / symbol;
     std::error_code ec_dirs;
     std::filesystem::create_directories(dir, ec_dirs);
     if (ec_dirs)
          {
          g_capture_fatal_error = true;
          ++g_cnt_open_fail;
          throw std::runtime_error(std::string("create_directories failed: ") + ec_dirs.message());
          }
     g_capture_dir_path = dir.string();
     g_status_json_path = (dir / "capture_status.json").string();

     const bool truncate_on_start = g_truncate_on_start.load(std::memory_order_relaxed);
     const bool fail_if_exists = g_fail_if_exists.load(std::memory_order_relaxed);

     auto open_mode = std::ios::out | (truncate_on_start ? std::ios::trunc : std::ios::app);

     std::filesystem::path p_trades = dir / "trades.ndjson";
     std::filesystem::path p_bbo = dir / "bbo.ndjson";
     std::filesystem::path p_depth = dir / "depth.ndjson";
     std::filesystem::path p_bidq = dir / "bid_quote_updates.ndjson";
     std::filesystem::path p_askq = dir / "ask_quote_updates.ndjson";
     std::filesystem::path p_endq = dir / "end_quote.ndjson";

     if (fail_if_exists)
          {
          for (const auto& p : {p_trades, p_bbo, p_depth, p_bidq, p_askq, p_endq})
               {
               if (std::filesystem::exists(p))
                    {
                    g_capture_fatal_error = true;
                    ++g_cnt_open_fail;
                    throw std::runtime_error(std::string("output file already exists (fail_if_exists): ") + p.string());
                    }
               }
          }

     g_trades_out.open(p_trades.string().c_str(), open_mode);
     g_bbo_out.open(p_bbo.string().c_str(), open_mode);
     g_depth_out.open(p_depth.string().c_str(), open_mode);
     g_bidquote_out.open(p_bidq.string().c_str(), open_mode);
     g_askquote_out.open(p_askq.string().c_str(), open_mode);
     g_endquote_out.open(p_endq.string().c_str(), open_mode);

     if (!(g_trades_out.is_open() && g_bbo_out.is_open() && g_depth_out.is_open() && g_bidquote_out.is_open() && g_askquote_out.is_open() && g_endquote_out.is_open()))
          {
          g_capture_fatal_error = true;
          ++g_cnt_open_fail;
          throw std::runtime_error("failed to open one or more required NDJSON output files");
          }

     std::ofstream meta((dir / "session_meta.json").string().c_str(), std::ios::trunc);
     if (meta.is_open())
          {
          // MISSION rithmic-rebuild-finalize (2026-08-16), Part 1 data-parity: this revision
          // hardcoded placeholder values here instead of the real connection identifiers -- a
          // genuine divergence from the pre-incident capture, found by diffing session_meta.json
          // field-by-field against an intact Aug 13 sample. Fixed to the same real, fixed values
          // already hardcoded elsewhere in this file for the actual connection
          // (fake_envp[1] MML_DOMAIN_NAME, oLoginParams.sMdCnnctPt) -- these are static for this
          // deployment, not something that varies per run, so hardcoding them here matches the
          // proven-correct pre-incident output exactly.
          meta << "{\n"
               << "  \"schema\": \"xgofi.session_meta.v1\",\n"
               << "  \"app_name\": \"prsi:XGOFI\",\n"
               << "  \"system\": \"rithmic_paper_prod_domain\",\n"
               << "  \"gateway\": \"login_agent_tp_paperc\",\n"
               << "  \"exchange\": \"" << json_escape(exchange) << "\",\n"
               << "  \"symbol\": \"" << json_escape(symbol) << "\",\n"
               << "  \"streams\": [\"trades.ndjson\", \"bbo.ndjson\", \"depth.ndjson\", \"bid_quote_updates.ndjson\", \"ask_quote_updates.ndjson\", \"end_quote.ndjson\"]\n"
               << "}\n";
          }

     g_capture_initialized = true;
     write_status_json();
     }


/*   =====================================================================   */

int LoginStatus_NotLoggedIn     = 0;
int LoginStatus_AwaitingResults = 1;
int LoginStatus_Failed          = 2;
int LoginStatus_Complete        = 3;

/*   =====================================================================   */

int  g_iRepLoginStatus                = LoginStatus_NotLoggedIn;
bool g_bRcvdUnacceptedAgreements      = false;
int  g_iUnacceptedMandatoryAgreements = 0;

int  g_iMdLoginStatus                 = LoginStatus_NotLoggedIn;

int main(int      argc,
         char * * argv,
         char * * envp);

/*   =====================================================================   */
/*                          class declarations                               */
/*   =====================================================================   */

class MyAdmCallbacks: public AdmCallbacks
     {
     public :
     MyAdmCallbacks()  {};
     ~MyAdmCallbacks() {};

     /*   ----------------------------------------------------------------   */

     virtual int Alert(AlertInfo * pInfo,
                       void *      pContext,
                       int *       aiCode);
     };

/*   =====================================================================   */


static void update_best_bid_cache_from_info_locked(BidInfo * pBid)
     {
     if (pBid == nullptr) return;
     if (!(pBid -> bPriceFlag && pBid -> bSizeFlag)) return;
     if (!(pBid -> dPrice > 0.0) || pBid -> llSize < 0) return;

     g_best_bid_cache.valid = true;
     g_best_bid_cache.px = pBid -> dPrice;
     g_best_bid_cache.sz = pBid -> llSize;
     g_best_bid_cache.num_orders = pBid -> iNumOrders;
     g_best_bid_cache.event_ts_ns = (pBid -> iSsboe > 0)
          ? (((long long)pBid -> iSsboe) * 1000000000LL + ((long long)pBid -> iUsecs) * 1000LL)
          : 0LL;
     g_best_bid_cache.timestamp_s = (long long)pBid -> iSsboe;
     g_best_bid_cache.timestamp_usecs = pBid -> iUsecs;
     g_best_bid_cache.conn_id = pBid -> iConnId;
     g_best_bid_cache.exchange = ts_to_string(pBid -> sExchange);
     g_best_bid_cache.symbol = ts_to_string(pBid -> sTicker);
     }

static void update_best_ask_cache_from_info_locked(AskInfo * pAsk)
     {
     if (pAsk == nullptr) return;
     if (!(pAsk -> bPriceFlag && pAsk -> bSizeFlag)) return;
     if (!(pAsk -> dPrice > 0.0) || pAsk -> llSize < 0) return;

     g_best_ask_cache.valid = true;
     g_best_ask_cache.px = pAsk -> dPrice;
     g_best_ask_cache.sz = pAsk -> llSize;
     g_best_ask_cache.num_orders = pAsk -> iNumOrders;
     g_best_ask_cache.event_ts_ns = (pAsk -> iSsboe > 0)
          ? (((long long)pAsk -> iSsboe) * 1000000000LL + ((long long)pAsk -> iUsecs) * 1000LL)
          : 0LL;
     g_best_ask_cache.timestamp_s = (long long)pAsk -> iSsboe;
     g_best_ask_cache.timestamp_usecs = pAsk -> iUsecs;
     g_best_ask_cache.conn_id = pAsk -> iConnId;
     g_best_ask_cache.exchange = ts_to_string(pAsk -> sExchange);
     g_best_ask_cache.symbol = ts_to_string(pAsk -> sTicker);
     }

static bool emit_bbo_from_best_caches_locked()
     {
     if (!g_best_bid_cache.valid || !g_best_ask_cache.valid)
          {
          ++g_cnt_bbo_skip_null;
          return false;
          }

     double bid_px = g_best_bid_cache.px;
     double ask_px = g_best_ask_cache.px;
     long long bid_sz = g_best_bid_cache.sz;
     long long ask_sz = g_best_ask_cache.sz;

     if (!(bid_px > 0.0) || !(ask_px > 0.0) || ask_px < bid_px || bid_sz < 0 || ask_sz < 0)
          {
          ++g_cnt_bbo_skip_sanity;
          return false;
          }

     std::string ex = !g_best_bid_cache.exchange.empty() ? g_best_bid_cache.exchange : g_best_ask_cache.exchange;
     std::string sym = !g_best_bid_cache.symbol.empty() ? g_best_bid_cache.symbol : g_best_ask_cache.symbol;
     ensure_capture_dirs_and_files(ex, sym);

     long long recv_ns = now_recv_ts_ns();
     long long seq = g_event_seq.fetch_add(1, std::memory_order_relaxed) + 1;
     long long event_ns = g_best_bid_cache.event_ts_ns > 0 ? g_best_bid_cache.event_ts_ns
                        : (g_best_ask_cache.event_ts_ns > 0 ? g_best_ask_cache.event_ts_ns : recv_ns);

     g_cnt_bbo.fetch_add(1, std::memory_order_relaxed);
     double spread_points = ask_px - bid_px;
     double tick_size = 0.25;
     long long spread_ticks = (tick_size > 0.0)
          ? (long long) llround(spread_points / tick_size)
          : 0;

     std::ostringstream oss;
     oss << std::fixed << std::setprecision(6);
     oss << "{"
         << "\"schema\":\"xgofi.bbo.v1\","
         << "\"recv_ts_ns\":" << recv_ns << ","
         << "\"event_ts_ns\":" << event_ns << ","
         << "\"seq\":" << seq << ","
         << "\"exchange\":\"" << json_escape(ex) << "\","
         << "\"symbol\":\"" << json_escape(sym) << "\","
         << "\"bid_px\":" << bid_px << ","
         << "\"bid_sz\":" << bid_sz << ","
         << "\"bid_num_orders\":" << g_best_bid_cache.num_orders << ","
         << "\"ask_px\":" << ask_px << ","
         << "\"ask_sz\":" << ask_sz << ","
         << "\"ask_num_orders\":" << g_best_ask_cache.num_orders << ","
         << "\"spread_points\":" << spread_points << ","
         << "\"spread_ticks\":" << spread_ticks << ","
         << "\"timestamp_s\":" << (g_best_bid_cache.timestamp_s > 0 ? g_best_bid_cache.timestamp_s : g_best_ask_cache.timestamp_s)
         << "." << std::setw(6) << std::setfill('0') << (g_best_bid_cache.timestamp_s > 0 ? g_best_bid_cache.timestamp_usecs : g_best_ask_cache.timestamp_usecs) << ","
         << "\"connection_id\":" << (g_best_bid_cache.conn_id != 0 ? g_best_bid_cache.conn_id : g_best_ask_cache.conn_id)
         << "}";

     write_ndjson_line(g_bbo_out, oss.str());
     return true;
     }

class MyCallbacks: public RCallbacks
     {
     public :
     MyCallbacks()  {};
     ~MyCallbacks() {};

     /*   ----------------------------------------------------------------   */

     virtual int Alert(AlertInfo * pInfo,
                       void *      pContext,
                       int *       aiCode);

     /*   ----------------------------------------------------------------   */

     virtual int AgreementList(AgreementListInfo * pInfo,
			       void *              pContext,
			       int *               aiCode);

     /*   ----------------------------------------------------------------   */

     virtual int AskQuote(AskInfo * pInfo,
                          void *    pContext,
                          int *     aiCode);

     virtual int BestAskQuote(AskInfo * pInfo,
                              void *    pContext,
                              int *     aiCode);

     virtual int BestBidAskQuote(BidInfo * pBid,
				 AskInfo * pAsk,
				 void *    pContext,
				 int *     aiCode);

     virtual int BestBidQuote(BidInfo * pInfo,
                              void *    pContext,
                              int *     aiCode);

     virtual int BidQuote(BidInfo * pInfo,
                          void *    pContext,
                          int *     aiCode);

     virtual int BinaryContractList(BinaryContractListInfo * pInfo,
				    void *                   pContext,
				    int *                    aiCode);

     virtual int ClosePrice(ClosePriceInfo * pInfo,
                            void *           pContext,
                            int *            aiCode);

     virtual int ClosingIndicator(ClosingIndicatorInfo * pInfo,
                                  void *                 pContext,
                                  int *                  aiCode);

     virtual int EndQuote(EndQuoteInfo * pInfo,
                          void *         pContext,
                          int *          aiCode);

     virtual int EquityOptionStrategyList(EquityOptionStrategyListInfo * pInfo,
					  void *                         pContext,
					  int *                          aiCode);

     virtual int HighPrice(HighPriceInfo * pInfo,
                           void *          pContext,
                           int *           aiCode);

     virtual int InstrumentByUnderlying(InstrumentByUnderlyingInfo * pInfo,
					void *                       pContext,
					int *                        aiCode);

     virtual int InstrumentSearch(InstrumentSearchInfo * pInfo,
				  void *                 pContext,
				  int *                  aiCode);

     virtual int LimitOrderBook(LimitOrderBookInfo * pInfo,
                                void *               pContext,
                                int *                aiCode);

     virtual int LowPrice(LowPriceInfo * pInfo,
                          void *         pContext,
                          int *          aiCode);

     virtual int MarketMode(MarketModeInfo * pInfo,
                            void *           pContext,
                            int *            aiCode);

     virtual int OpenInterest(OpenInterestInfo * pInfo,
			      void *             pContext,
			      int *              aiCode);

     virtual int OpenPrice(OpenPriceInfo * pInfo,
                           void *          pContext,
                           int *           aiCode);

     virtual int OpeningIndicator(OpeningIndicatorInfo * pInfo,
				  void *                 pContext,
				  int *                  aiCode);

     virtual int OptionList(OptionListInfo * pInfo,
                            void *           pContext,
                            int *            aiCode);

     virtual int RefData(RefDataInfo * pInfo,
                         void *        pContext,
                         int *         aiCode);

     virtual int SettlementPrice(SettlementPriceInfo * pInfo,
                                 void *                pContext,
                                 int *                 aiCode);

     virtual int Strategy(StrategyInfo * pInfo,
			  void *         pContext,
			  int *          aiCode);

     virtual int StrategyList(StrategyListInfo * pInfo,
			      void *             pContext,
			      int *              aiCode);

     virtual int TradeCondition(TradeInfo * pInfo,
                                void *      pContext,
                                int *       aiCode);

     virtual int TradePrint(TradeInfo * pInfo,
                            void *      pContext,
                            int *       aiCode);

     virtual int TradeReplay(TradeReplayInfo * pInfo,
			     void *            pContext,
			     int *             aiCode);

     virtual int TradeRoute(TradeRouteInfo * pInfo,
			    void *           pContext,
			    int *            aiCode);

     virtual int TradeRouteList(TradeRouteListInfo * pInfo,
				void *               pContext,
				int *                aiCode);

     virtual int TradeVolume(TradeVolumeInfo * pInfo,
                             void *            pContext,
                             int *             aiCode);

     /*   ----------------------------------------------------------------   */

     virtual int Bar(BarInfo * pInfo,
		     void *    pContext,
		     int *     aiCode);

     virtual int BarReplay(BarReplayInfo * pInfo,
			   void *          pContext,
			   int *           aiCode);

     /*   ----------------------------------------------------------------   */

     virtual int AccountList(AccountListInfo * pInfo,
                             void *            pContext,
                             int *             aiCode);

     virtual int PasswordChange(PasswordChangeInfo * pInfo,
				void *               pContext,
				int *                aiCode);

     /*   ----------------------------------------------------------------   */

     virtual int ExchangeList(ExchangeListInfo * pInfo,
			      void *             pContext,
			      int *              aiCode);

     virtual int ExecutionReplay(ExecutionReplayInfo * pInfo,
                                 void *                pContext,
                                 int *                 aiCode);

     virtual int LineUpdate(LineInfo * pInfo,
                            void *     pContext,
                            int *      aiCode);

     virtual int OpenOrderReplay(OrderReplayInfo * pInfo,
                                 void *            pContext,
                                 int *             aiCode);

     virtual int OrderReplay(OrderReplayInfo * pInfo,
                             void *            pContext,
                             int *             aiCode);

     virtual int PnlReplay(PnlReplayInfo * pInfo,
                           void *          pContext,
                           int *           aiCode);

     virtual int PnlUpdate(PnlInfo * pInfo,
                           void *    pContext,
                           int *     aiCode);

     virtual int PriceIncrUpdate(PriceIncrInfo * pInfo,
                                 void *          pContext,
                                 int *           aiCode);

     virtual int ProductRmsList(ProductRmsListInfo * pInfo,
				void *               pContext,
				int *                aiCode);

     virtual int SingleOrderReplay(SingleOrderReplayInfo * pInfo,
				   void *                  pContext,
				   int *                   aiCode);

     /*   ----------------------------------------------------------------   */

     virtual int BustReport(OrderBustReport * pReport,
                            void *            pContext,
                            int *             aiCode);

     virtual int CancelReport(OrderCancelReport * pReport,
                              void *              pContext,
                              int *               aiCode);

     virtual int FailureReport(OrderFailureReport * pReport,
                               void *               pContext,
                               int *                aiCode);

     virtual int FillReport(OrderFillReport * pReport,
                            void *            pContext,
                            int *             aiCode);

     virtual int ModifyReport(OrderModifyReport * pReport,
                              void *              pContext,
                              int *               aiCode);

     virtual int NotCancelledReport(OrderNotCancelledReport * pReport,
                                    void *                    pContext,
                                    int *                     aiCode);

     virtual int NotModifiedReport(OrderNotModifiedReport * pReport,
                                   void *                   pContext,
                                   int *                    aiCode);

     virtual int RejectReport(OrderRejectReport * pReport,
                              void *              pContext,
                              int *               aiCode);

     virtual int StatusReport(OrderStatusReport * pReport,
                              void *              pContext,
                              int *               aiCode);

     virtual int TradeCorrectReport(OrderTradeCorrectReport * pReport,
                                    void *                    pContext,
                                    int *                     aiCode);

     virtual int TriggerPulledReport(OrderTriggerPulledReport * pReport,
                                     void *                     pContext,
                                     int *                      aiCode);

     virtual int TriggerReport(OrderTriggerReport * pReport,
                               void *              pContext,
                               int *               aiCode);

     virtual int OtherReport(OrderReport * pReport,
                             void *        pContext,
                             int *         aiCode);

     /*   ----------------------------------------------------------------   */

     virtual int SodUpdate(SodReport * pReport,
                           void *      pContext,
                           int *       aiCode);

     /*   ----------------------------------------------------------------   */

     virtual int Quote(QuoteReport * pReport,
		       void *        pContext,
		       int *         aiCode);

     /*   ----------------------------------------------------------------   */

     private :
     };

/*   =====================================================================   */
/*                          class definitions                                */
/*   =====================================================================   */

int MyAdmCallbacks::Alert(AlertInfo * pInfo,
                          void *      pContext,
                          int *       aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::AccountList(AccountListInfo * pInfo,
                             void *            pContext,
                             int *             aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::PasswordChange(PasswordChangeInfo * pInfo,
				void *               pContext,
				int *                aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::Alert(AlertInfo * pInfo,
                       void *      pContext,
                       int *       aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */
     /*   Signal when the login to the repository sub-system (agreements)    */
     /*   is complete, and what the results are.                             */

     if (pInfo -> iConnectionId == REPOSITORY_CONNECTION_ID)
          {
	  if (pInfo -> iAlertType == ALERT_LOGIN_COMPLETE)
	       {
	       g_iRepLoginStatus = LoginStatus_Complete;
	       }
	  else if (pInfo -> iAlertType == ALERT_LOGIN_FAILED)
	       {
	       g_iRepLoginStatus = LoginStatus_Failed;
	       }
          }

     /*   ----------------------------------------------------------------   */
     /*   Signal when the login to the market data system (ticker plant)     */
     /*   is complete, and what the results are.                             */

     if (pInfo -> iConnectionId == MARKET_DATA_CONNECTION_ID)
          {
	  if (pInfo -> iAlertType == ALERT_LOGIN_COMPLETE)
	       {
	       g_iMdLoginStatus = LoginStatus_Complete;
	       }
	  else if (pInfo -> iAlertType == ALERT_LOGIN_FAILED)
	       {
	       g_iMdLoginStatus = LoginStatus_Failed;
	       }
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::AgreementList(AgreementListInfo * pInfo,
			       void *              pContext,
			       int *               aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
	  {
	  ; // do nothing ...
	  }

     /*   ----------------------------------------------------------------   */

     if (!pInfo -> bAccepted)
	  {
	  for (int i = 0; i < pInfo -> iArrayLen; i++)
	       {
	       AgreementInfo oAg     = pInfo -> asAgreementInfoArray[i];
	       tsNCharcb     sActive = {"active", 6};
	       bool          bActive = false;

	       if (oAg.sStatus.iDataLen == sActive.iDataLen &&
		   (memcmp(oAg.sStatus.pData, 
			   sActive.pData, 
			   oAg.sStatus.iDataLen) == 0))
		    {
		    bActive = true;
		    }

	       if (oAg.bMandatory && bActive)
		    {
		    g_iUnacceptedMandatoryAgreements++;
		    }
	       }

	  g_bRcvdUnacceptedAgreements = true;
	  }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::ExchangeList(ExchangeListInfo * pInfo,
			      void *             pContext,
			      int *              aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::ExecutionReplay(ExecutionReplayInfo * pInfo,
                                 void *                pContext,
                                 int *                 aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }


/*   =====================================================================   */

int MyCallbacks::LineUpdate(LineInfo * pInfo,
                            void *     pContext,
                            int *      aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::OpenOrderReplay(OrderReplayInfo * pInfo,
                                 void *            pContext,
                                 int *             aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }


/*   =====================================================================   */

int MyCallbacks::OrderReplay(OrderReplayInfo * pInfo,
                             void *            pContext,
                             int *             aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }


/*   =====================================================================   */

int MyCallbacks::PnlReplay(PnlReplayInfo * pInfo,
                           void *          pContext,
                           int *           aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::PnlUpdate(PnlInfo * pInfo,
                           void *    pContext,
                           int *     aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::PriceIncrUpdate(PriceIncrInfo * pInfo,
                                 void *          pContext,
                                 int *           aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::ProductRmsList(ProductRmsListInfo * pInfo,
				void *               pContext,
				int *                aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::SingleOrderReplay(SingleOrderReplayInfo * pInfo,
				   void *                  pContext,
				   int *                   aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::BustReport(OrderBustReport * pReport,
                            void *            pContext,
                            int *             aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::CancelReport(OrderCancelReport * pReport,
                              void *              pContext,
                              int *               aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::FailureReport(OrderFailureReport * pReport,
                               void *               pContext,
                               int *                aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::FillReport(OrderFillReport * pReport,
                            void *            pContext,
                            int *             aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::ModifyReport(OrderModifyReport * pReport,
                              void *              pContext,
                              int *               aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::NotCancelledReport(OrderNotCancelledReport * pReport,
                                    void *                    pContext,
                                    int *                     aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::NotModifiedReport(OrderNotModifiedReport * pReport,
                                   void *                   pContext,
                                   int *                    aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::RejectReport(OrderRejectReport * pReport,
                              void *              pContext,
                              int *               aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::StatusReport(OrderStatusReport * pReport,
                              void *              pContext,
                              int *               aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::TradeCorrectReport(OrderTradeCorrectReport * pReport,
                                    void *                    pContext,
                                    int *                     aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::TriggerPulledReport(OrderTriggerPulledReport * pReport,
                                     void *                     pContext,
                                     int *                      aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::TriggerReport(OrderTriggerReport * pReport,
                               void *               pContext,
                               int *                aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::OtherReport(OrderReport * pReport,
                             void *        pContext,
                             int *         aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::SodUpdate(SodReport * pReport,
                           void *      pContext,
                           int *       aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::Quote(QuoteReport * pReport,
		       void *        pContext,
		       int *         aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::AskQuote(AskInfo * pInfo,
                          void *    pContext,
                          int *     aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          std::lock_guard<std::mutex> lk(g_file_mtx);

          std::string ex = ts_to_string(pInfo -> sExchange);
          std::string sym = ts_to_string(pInfo -> sTicker);
          ensure_capture_dirs_and_files(ex, sym);

          long long recv_ns = now_recv_ts_ns();
          long long seq = g_event_seq.fetch_add(1, std::memory_order_relaxed) + 1;
          long long event_ts_ns = ((long long)pInfo -> iSsboe) * 1000000000LL
                                + ((long long)pInfo -> iUsecs) * 1000LL;
          if (pInfo -> bSizeFlag && pInfo -> llSize == 0) { ++g_cnt_zero_size_quote; }

          std::ostringstream oss;
          oss << std::fixed << std::setprecision(6);
          oss << "{"
              << "\"schema\":\"xgofi.askquote.v1\","
              << "\"recv_ts_ns\":" << recv_ns << ","
              << "\"event_ts_ns\":" << event_ts_ns << ","
              << "\"exchange\":\"" << json_escape(ex) << "\","
              << "\"symbol\":\"" << json_escape(sym) << "\","
              << "\"side\":\"A\","
              << "\"price\":" << pInfo -> dPrice << ","
              << "\"price_valid\":" << ((pInfo -> bPriceFlag) ? "true" : "false") << ","
              << "\"size\":" << pInfo -> llSize << ","
              << "\"size_valid\":" << ((pInfo -> bSizeFlag) ? "true" : "false") << ","
              << "\"implied_size\":" << pInfo -> llImpliedSize << ","
              << "\"num_orders\":" << pInfo -> iNumOrders << ",";
          if (pInfo -> bLeanPriceFlag)
               {
               oss << "\"lean_price\":" << pInfo -> dLeanPrice << ",";
               }
          else
               {
               oss << "\"lean_price\":null,";
               }
          oss << "\"lean_price_valid\":" << ((pInfo -> bLeanPriceFlag) ? "true" : "false") << ","
              << "\"update_type_code\":" << pInfo -> iUpdateType << ","
              << "\"update_type_label\":\"" << update_type_label(pInfo -> iUpdateType) << "\","
              << "\"callback_type_code\":" << pInfo -> iType << ","
              << "\"callback_type_label\":\"" << md_callback_type_label(pInfo -> iType) << "\","
              << "\"seq\":" << seq << ","
              << "\"connection_id\":" << pInfo -> iConnId
              << "}";

          write_ndjson_line(g_askquote_out, oss.str());
          g_cnt_askq.fetch_add(1, std::memory_order_relaxed);
          }
     catch (const std::exception& ex)
          {
          ++g_cnt_exceptions;
          log_error_line(std::string("callback exception: ") + ex.what());
          }
     catch (...)
          {
          ++g_cnt_exceptions;
          log_error_line("callback exception: unknown");
          }

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::BestAskQuote(AskInfo * pInfo,
                              void *    pContext,
                              int *     aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl << "Best";
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          std::lock_guard<std::mutex> lk(g_file_mtx);
          if (pInfo == nullptr)
               {
               ++g_cnt_bbo_skip_null;
               *aiCode = API_OK;
               return (OK);
               }

          if (!(pInfo -> bPriceFlag && pInfo -> bSizeFlag))
               {
               ++g_cnt_bbo_skip_flags;
               *aiCode = API_OK;
               return (OK);
               }

          update_best_ask_cache_from_info_locked(pInfo);
          (void) emit_bbo_from_best_caches_locked();
          }
     catch (const std::exception& ex)
          {
          ++g_cnt_exceptions;
          log_error_line(std::string("callback exception: ") + ex.what());
          }
     catch (...)
          {
          ++g_cnt_exceptions;
          log_error_line("callback exception: unknown");
          }

     *aiCode = API_OK;
     return (OK);
     }


/*   =====================================================================   */

int MyCallbacks::BestBidAskQuote(BidInfo * pBid,
                                 AskInfo * pAsk,
                                 void *    pContext,
                                 int *     aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl << "Best Bid/Ask";
     if (g_verbose_dumps.load(std::memory_order_relaxed) && pBid != nullptr && !pBid -> dump(&iIgnored))
          {
          cout << "error in pBid -> dump : " << iIgnored << endl;
          }

     if (g_verbose_dumps.load(std::memory_order_relaxed) && pAsk != nullptr && !pAsk -> dump(&iIgnored))
          {
          cout << "error in pAsk -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          std::lock_guard<std::mutex> lk(g_file_mtx);

          if (pBid == nullptr || pAsk == nullptr)
               {
               ++g_cnt_bbo_skip_null;
               *aiCode = API_OK;
               return (OK);
               }

          bool bid_ok = (pBid -> bPriceFlag && pBid -> bSizeFlag);
          bool ask_ok = (pAsk -> bPriceFlag && pAsk -> bSizeFlag);

          if (!bid_ok && !ask_ok)
               {
               ++g_cnt_bbo_skip_flags;
               *aiCode = API_OK;
               return (OK);
               }

          if (bid_ok) update_best_bid_cache_from_info_locked(pBid);
          else ++g_cnt_bbo_skip_flags;

          if (ask_ok) update_best_ask_cache_from_info_locked(pAsk);
          else ++g_cnt_bbo_skip_flags;

          (void) emit_bbo_from_best_caches_locked();
          }
     catch (const std::exception& ex)
          {
          ++g_cnt_exceptions;
          log_error_line(std::string("callback exception: ") + ex.what());
          }
     catch (...)
          {
          ++g_cnt_exceptions;
          log_error_line("callback exception: unknown");
          }

     *aiCode = API_OK;
     return (OK);
     }


/*   =====================================================================   */

int MyCallbacks::BestBidQuote(BidInfo * pInfo,
                              void *    pContext,
                              int *     aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl << "Best";
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          std::lock_guard<std::mutex> lk(g_file_mtx);
          if (pInfo == nullptr)
               {
               ++g_cnt_bbo_skip_null;
               *aiCode = API_OK;
               return (OK);
               }

          if (!(pInfo -> bPriceFlag && pInfo -> bSizeFlag))
               {
               ++g_cnt_bbo_skip_flags;
               *aiCode = API_OK;
               return (OK);
               }

          update_best_bid_cache_from_info_locked(pInfo);
          (void) emit_bbo_from_best_caches_locked();
          }
     catch (const std::exception& ex)
          {
          ++g_cnt_exceptions;
          log_error_line(std::string("callback exception: ") + ex.what());
          }
     catch (...)
          {
          ++g_cnt_exceptions;
          log_error_line("callback exception: unknown");
          }

     *aiCode = API_OK;
     return (OK);
     }


/*   =====================================================================   */

int MyCallbacks::BidQuote(BidInfo * pInfo,
                          void *    pContext,
                          int *     aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          std::lock_guard<std::mutex> lk(g_file_mtx);

          std::string ex = ts_to_string(pInfo -> sExchange);
          std::string sym = ts_to_string(pInfo -> sTicker);
          ensure_capture_dirs_and_files(ex, sym);

          long long recv_ns = now_recv_ts_ns();
          long long seq = g_event_seq.fetch_add(1, std::memory_order_relaxed) + 1;
          long long event_ts_ns = ((long long)pInfo -> iSsboe) * 1000000000LL
                                + ((long long)pInfo -> iUsecs) * 1000LL;
          if (pInfo -> bSizeFlag && pInfo -> llSize == 0) { ++g_cnt_zero_size_quote; }

          std::ostringstream oss;
          oss << std::fixed << std::setprecision(6);
          oss << "{"
              << "\"schema\":\"xgofi.bidquote.v1\","
              << "\"recv_ts_ns\":" << recv_ns << ","
              << "\"event_ts_ns\":" << event_ts_ns << ","
              << "\"exchange\":\"" << json_escape(ex) << "\","
              << "\"symbol\":\"" << json_escape(sym) << "\","
              << "\"side\":\"B\","
              << "\"price\":" << pInfo -> dPrice << ","
              << "\"price_valid\":" << ((pInfo -> bPriceFlag) ? "true" : "false") << ","
              << "\"size\":" << pInfo -> llSize << ","
              << "\"size_valid\":" << ((pInfo -> bSizeFlag) ? "true" : "false") << ","
              << "\"implied_size\":" << pInfo -> llImpliedSize << ","
              << "\"num_orders\":" << pInfo -> iNumOrders << ",";
          if (pInfo -> bLeanPriceFlag)
               {
               oss << "\"lean_price\":" << pInfo -> dLeanPrice << ",";
               }
          else
               {
               oss << "\"lean_price\":null,";
               }
          oss << "\"lean_price_valid\":" << ((pInfo -> bLeanPriceFlag) ? "true" : "false") << ","
              << "\"update_type_code\":" << pInfo -> iUpdateType << ","
              << "\"update_type_label\":\"" << update_type_label(pInfo -> iUpdateType) << "\","
              << "\"callback_type_code\":" << pInfo -> iType << ","
              << "\"callback_type_label\":\"" << md_callback_type_label(pInfo -> iType) << "\","
              << "\"seq\":" << seq << ","
              << "\"connection_id\":" << pInfo -> iConnId
              << "}";

          write_ndjson_line(g_bidquote_out, oss.str());
          g_cnt_bidq.fetch_add(1, std::memory_order_relaxed);
          }
     catch (const std::exception& ex)
          {
          ++g_cnt_exceptions;
          log_error_line(std::string("callback exception: ") + ex.what());
          }
     catch (...)
          {
          ++g_cnt_exceptions;
          log_error_line("callback exception: unknown");
          }

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::BinaryContractList(BinaryContractListInfo * pInfo,
				    void *                   pContext,
				    int *                    aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::ClosePrice(ClosePriceInfo * pInfo,
                            void *           pContext,
                            int *            aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::ClosingIndicator(ClosingIndicatorInfo * pInfo,
                                  void *                 pContext,
                                  int *                  aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::EndQuote(EndQuoteInfo * pInfo,
			  void *         pContext,
			  int *          aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          if (pInfo != nullptr)
               {
               std::lock_guard<std::mutex> lk(g_file_mtx);
               std::string ex = ts_to_string(pInfo -> sExchange);
               std::string sym = ts_to_string(pInfo -> sTicker);
               ensure_capture_dirs_and_files(ex, sym);

               long long recv_ns = now_recv_ts_ns();
               long long seq = g_event_seq.fetch_add(1, std::memory_order_relaxed) + 1;
               long long event_ts_ns = (pInfo -> iSsboe > 0)
                    ? (((long long)pInfo -> iSsboe) * 1000000000LL + ((long long)pInfo -> iUsecs) * 1000LL)
                    : recv_ns;

               std::ostringstream oss;
               oss << "{"
                   << "\"schema\":\"xgofi.endquote.v1\","
                   << "\"recv_ts_ns\":" << recv_ns << ","
                   << "\"event_ts_ns\":" << event_ts_ns << ","
                   << "\"seq\":" << seq << ","
                   << "\"exchange\":\"" << json_escape(ex) << "\","
                   << "\"symbol\":\"" << json_escape(sym) << "\","
                   << "\"update_type_code\":" << pInfo -> iUpdateType << ","
                   << "\"update_type_label\":\"" << update_type_label(pInfo -> iUpdateType) << "\","
                   << "\"callback_type_code\":" << pInfo -> iType << ","
                   << "\"callback_type_label\":\"" << md_callback_type_label(pInfo -> iType) << "\","
                   << "\"connection_id\":" << pInfo -> iConnId
                   << "}";
               write_ndjson_line(g_endquote_out, oss.str());
               g_cnt_endquote.fetch_add(1, std::memory_order_relaxed);
               }
          }
     catch (const std::exception& ex)
          {
          ++g_cnt_exceptions;
          log_error_line(std::string("callback exception: ") + ex.what());
          }
     catch (...)
          {
          ++g_cnt_exceptions;
          log_error_line("callback exception: unknown");
          }

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::EquityOptionStrategyList(EquityOptionStrategyListInfo * pInfo,
					  void *                         pContext,
					  int *                          aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::HighPrice(HighPriceInfo * pInfo,
                           void *          pContext,
                           int *           aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::InstrumentByUnderlying(InstrumentByUnderlyingInfo * pInfo,
					void *                       pContext,
					int *                        aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::InstrumentSearch(InstrumentSearchInfo * pInfo,
				  void *                 pContext,
				  int *                  aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::LimitOrderBook(LimitOrderBookInfo * pInfo,
                                void *               pContext,
                                int *                aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          std::lock_guard<std::mutex> lk(g_file_mtx);

          std::string ex = ts_to_string(pInfo -> sExchange);
          std::string sym = ts_to_string(pInfo -> sTicker);
          ensure_capture_dirs_and_files(ex, sym);

          long long recv_ns = now_recv_ts_ns();
          long long seq = g_event_seq.fetch_add(1, std::memory_order_relaxed) + 1;
          long long lob_snapshot_seq = g_lob_snapshot_seq.fetch_add(1, std::memory_order_relaxed) + 1;
          g_cnt_lob.fetch_add(1, std::memory_order_relaxed);

          auto to_ns = [](int ssboe, int usecs) -> long long
               {
               return (static_cast<long long>(ssboe) * 1000000000LL) +
                      (static_cast<long long>(usecs) * 1000LL);
               };

          long long event_ts_ns = to_ns(pInfo -> iSsboe, pInfo -> iUsecs);

          int i;

          /* asks */
          for (i = 0; i < pInfo -> iAskArrayLen; ++i)
               {
               double px = (pInfo -> adAskPriceArray ? pInfo -> adAskPriceArray[i] : 0.0);
               long long sz = (pInfo -> allAskSizeArray ? pInfo -> allAskSizeArray[i] : 0LL);
               long long implied_sz = (pInfo -> allAskImpliedSizeArray ? pInfo -> allAskImpliedSizeArray[i] : 0LL);
               int num_orders = (pInfo -> aiAskNumOrdersArray ? pInfo -> aiAskNumOrdersArray[i] : 0);
               int lvl_ssboe = (pInfo -> aiAskSsboeArray ? pInfo -> aiAskSsboeArray[i] : 0);
               int lvl_usecs = (pInfo -> aiAskUsecsArray ? pInfo -> aiAskUsecsArray[i] : 0);

               long long level_ts_ns = (lvl_ssboe > 0 || lvl_usecs > 0) ? to_ns(lvl_ssboe, lvl_usecs) : 0LL;

               std::ostringstream oss;
               // MISSION rithmic-depth-precision-fix: this block was missing the setprecision(6)
               // every other writer in this file sets (bbo ~584, trades ~1331/1590, quote-updates
               // ~2182) -- without it, the stream falls back to C++'s default 6-SIGNIFICANT-DIGIT
               // formatting, which silently truncated a 7-significant-digit price like 30696.25 to
               // ~30696.2/30696.3. Confirmed from captured data before this fix (see
               // raw_market_data.py's _fix_truncated_price(), the consumer-side workaround kept in
               // place for already-captured history, which this fix does not and cannot correct).
               oss << std::fixed << std::setprecision(6);
               oss << "{"
                   << "\"schema\":\"xgofi.depth.v1\","
                   << "\"recv_ts_ns\":" << recv_ns << ","
                   << "\"timestamp_ns\":" << event_ts_ns << ","
                   << "\"exchange\":\"" << json_escape(ex) << "\","
                   << "\"symbol\":\"" << json_escape(sym) << "\","
                   << "\"side\":\"A\","
                   << "\"level\":" << i << ","
                   << "\"price\":" << px << ","
                   << "\"size\":" << sz << ","
                   << "\"implied_size\":" << implied_sz << ","
                   << "\"num_orders\":" << num_orders << ","
                   << "\"level_ts_ns\":" << level_ts_ns << ","
                   << "\"iType\":" << pInfo -> iType << ","
                   << "\"callback_type_label\":\"" << md_callback_type_label(pInfo -> iType) << "\","
                   << "\"lob_snapshot_seq\":" << lob_snapshot_seq << ","
                   << "\"seq\":" << seq << ","
                   << "\"connection_id\":" << pInfo -> iConnId
                   << "}";

               write_ndjson_line(g_depth_out, oss.str());
               }

          /* bids */
          for (i = 0; i < pInfo -> iBidArrayLen; ++i)
               {
               double px = (pInfo -> adBidPriceArray ? pInfo -> adBidPriceArray[i] : 0.0);
               long long sz = (pInfo -> allBidSizeArray ? pInfo -> allBidSizeArray[i] : 0LL);
               long long implied_sz = (pInfo -> allBidImpliedSizeArray ? pInfo -> allBidImpliedSizeArray[i] : 0LL);
               int num_orders = (pInfo -> aiBidNumOrdersArray ? pInfo -> aiBidNumOrdersArray[i] : 0);
               int lvl_ssboe = (pInfo -> aiBidSsboeArray ? pInfo -> aiBidSsboeArray[i] : 0);
               int lvl_usecs = (pInfo -> aiBidUsecsArray ? pInfo -> aiBidUsecsArray[i] : 0);

               long long level_ts_ns = (lvl_ssboe > 0 || lvl_usecs > 0) ? to_ns(lvl_ssboe, lvl_usecs) : 0LL;

               std::ostringstream oss;
               // MISSION rithmic-depth-precision-fix: see the identical comment in the ask block
               // above -- same missing setprecision(6), same fix.
               oss << std::fixed << std::setprecision(6);
               oss << "{"
                   << "\"schema\":\"xgofi.depth.v1\","
                   << "\"recv_ts_ns\":" << recv_ns << ","
                   << "\"timestamp_ns\":" << event_ts_ns << ","
                   << "\"exchange\":\"" << json_escape(ex) << "\","
                   << "\"symbol\":\"" << json_escape(sym) << "\","
                   << "\"side\":\"B\","
                   << "\"level\":" << i << ","
                   << "\"price\":" << px << ","
                   << "\"size\":" << sz << ","
                   << "\"implied_size\":" << implied_sz << ","
                   << "\"num_orders\":" << num_orders << ","
                   << "\"level_ts_ns\":" << level_ts_ns << ","
                   << "\"iType\":" << pInfo -> iType << ","
                   << "\"callback_type_label\":\"" << md_callback_type_label(pInfo -> iType) << "\","
                   << "\"lob_snapshot_seq\":" << lob_snapshot_seq << ","
                   << "\"seq\":" << seq << ","
                   << "\"connection_id\":" << pInfo -> iConnId
                   << "}";

               write_ndjson_line(g_depth_out, oss.str());
               }
          }
     catch (const std::exception& ex)
          {
          ++g_cnt_exceptions;
          log_error_line(std::string("callback exception: ") + ex.what());
          }
     catch (...)
          {
          ++g_cnt_exceptions;
          log_error_line("callback exception: unknown");
          }

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::LowPrice(LowPriceInfo * pInfo,
                          void *         pContext,
                          int *          aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::MarketMode(MarketModeInfo * pInfo,
                            void *           pContext,
                            int *            aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::OpenInterest(OpenInterestInfo * pInfo,
			      void *             pContext,
			      int *              aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::OpenPrice(OpenPriceInfo * pInfo,
                           void *          pContext,
                           int *           aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::OpeningIndicator(OpeningIndicatorInfo * pInfo,
				  void *                 pContext,
				  int *                  aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::OptionList(OptionListInfo * pInfo,
			    void *           pContext,
			    int *            aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::RefData(RefDataInfo * pInfo,
                         void *        pContext,
                         int *         aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::SettlementPrice(SettlementPriceInfo * pInfo,
                                 void *                pContext,
                                 int *                 aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::Strategy(StrategyInfo * pInfo,
			  void *         pContext,
			  int *          aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::StrategyList(StrategyListInfo * pInfo,
			      void *             pContext,
			      int *              aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::TradeCondition(TradeInfo * pInfo,
                                void *      pContext,
                                int *       aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::TradePrint(TradeInfo * pInfo,
                            void *      pContext,
                            int *       aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          std::lock_guard<std::mutex> lk(g_file_mtx);

          std::string ex = ts_to_string(pInfo -> sExchange);
          std::string sym = ts_to_string(pInfo -> sTicker);
          ensure_capture_dirs_and_files(ex, sym);

          long long recv_ns = now_recv_ts_ns();
          long long seq = g_event_seq.fetch_add(1, std::memory_order_relaxed) + 1;
          g_cnt_trade.fetch_add(1, std::memory_order_relaxed);

          long long source_ts_ns = ((long long)pInfo -> iSourceSsboe) * 1000000000LL
                                 + ((long long)pInfo -> iSourceNsecs);
          long long jop_ts_ns = ((long long)pInfo -> iJopSsboe) * 1000000000LL
                              + ((long long)pInfo -> iJopNsecs);
          long long ts_ns = ((long long)pInfo -> iSsboe) * 1000000000LL
                          + ((long long)pInfo -> iUsecs) * 1000LL;

          if (!(pInfo -> bPriceFlag) || pInfo -> dPrice <= 0.0 || pInfo -> llSize <= 0)
               {
               *aiCode = API_OK;
               return (OK);
               }

          std::string aggr_side = ts_to_string(pInfo -> sAggressorSide);
          std::string cond = ts_to_string(pInfo -> sCondition);
          std::string aggr_exch_ord_id = ts_to_string(pInfo -> sAggressorExchOrdId);
          std::string exch_ord_id = ts_to_string(pInfo -> sExchOrdId);

          std::ostringstream oss;
          oss << std::fixed << std::setprecision(6);
          oss << "{"
              << "\"schema\":\"xgofi.trade.v1\","
              << "\"recv_ts_ns\":" << recv_ns << ","
              << "\"source_ts_ns\":" << source_ts_ns << ","
              << "\"jop_ts_ns\":" << jop_ts_ns << ","
              << "\"timestamp_ns\":" << ts_ns << ","
              << "\"seq\":" << seq << ","
              << "\"exchange\":\"" << json_escape(ex) << "\","
              << "\"symbol\":\"" << json_escape(sym) << "\","
              << "\"price\":" << pInfo -> dPrice << ","
              << "\"size\":" << pInfo -> llSize << ","
              << "\"aggressor_side\":\"" << json_escape(aggr_side) << "\","
              << "\"condition\":\"" << json_escape(cond) << "\","
              << "\"aggressor_exch_ord_id\":\"" << json_escape(aggr_exch_ord_id) << "\","
              << "\"exch_ord_id\":\"" << json_escape(exch_ord_id) << "\","
              << "\"connection_id\":" << pInfo -> iConnId;

          if (pInfo -> bVolumeBoughtFlag) oss << ",\"volume_bought\":" << pInfo -> llVolumeBought;
          if (pInfo -> bVolumeSoldFlag)   oss << ",\"volume_sold\":" << pInfo -> llVolumeSold;
          if (pInfo -> bVwapFlag)         oss << ",\"vwap\":" << pInfo -> dVwap;
          if (pInfo -> bVwapLongFlag)     oss << ",\"vwap_long\":" << pInfo -> dVwapLong;
          if (pInfo -> bNetChangeFlag)    oss << ",\"net_change\":" << pInfo -> dNetChange;
          if (pInfo -> bPercentChangeFlag)oss << ",\"percent_change\":" << pInfo -> dPercentChange;

          oss << "}";

          write_ndjson_line(g_trades_out, oss.str());
          }
     catch (const std::exception& ex)
          {
          ++g_cnt_exceptions;
          log_error_line(std::string("callback exception: ") + ex.what());
          }
     catch (...)
          {
          ++g_cnt_exceptions;
          log_error_line("callback exception: unknown");
          }

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::TradeReplay(TradeReplayInfo * pInfo,
			     void *            pContext,
			     int *             aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::TradeRoute(TradeRouteInfo * pInfo,
			    void *           pContext,
			    int *            aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::TradeRouteList(TradeRouteListInfo * pInfo,
				void *               pContext,
				int *                aiCode)
     {
     *aiCode = API_OK;
     return(OK);
     }

/*   =====================================================================   */

int MyCallbacks::TradeVolume(TradeVolumeInfo * pInfo,
                             void *            pContext,
                             int *             aiCode)
     {
     int iIgnored;

     /*   ----------------------------------------------------------------   */

     if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
     if (g_verbose_dumps.load(std::memory_order_relaxed) && !pInfo -> dump(&iIgnored))
          {
          cout << "error in pInfo -> dump : " << iIgnored << endl;
          }

     /*   ----------------------------------------------------------------   */

     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::Bar(BarInfo * pInfo,
		     void *    pContext,
		     int *     aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int MyCallbacks::BarReplay(BarReplayInfo * pInfo,
			   void *          pContext,
			   int *           aiCode)
     {
     *aiCode = API_OK;
     return (OK);
     }

/*   =====================================================================   */

int main(int      argc,
         char * * argv,
         char * * envp)
     {
     char * USAGE = (char *)"SampleMD user password exchange ticker";

     REngine *        pEngine;
     MyAdmCallbacks * pAdmCallbacks;
     RCallbacks *     pCallbacks;
     REngineParams    oParams;
     LoginParams      oLoginParams;
     tsNCharcb        sExchange;
     tsNCharcb        sTicker;
     char *           fake_envp[9];
     int              iFlags;
     int              iCode;

     /*   ----------------------------------------------------------------   */

     if (argc < 5)
          {
          cout << USAGE << endl;
          return (BAD);
          }

     /*   ----------------------------------------------------------------   */

     try
          {
          pAdmCallbacks = new MyAdmCallbacks();
          }
     catch (OmneException& oEx)
          {
          iCode = oEx.getErrorCode();
          cout << "MyAdmCallbacks::MyAdmCallbacks() error : " << iCode << endl;
          return (BAD);
          }

     /*   ----------------------------------------------------------------   */
     /*   The following fake envp contains the settings for connecting to    */
     /*   Rithmic Test.  To connect to a different instance of the           */
     /*   Rithmic trading platform, consult appropriate connection params    */
     /*   document in your download directory after passing conformance.     */

     fake_envp[0] = "MML_DMN_SRVR_ADDR=ritpz01004.01.rithmic.com:65000~ritpz04063.04.rithmic.com:65000~ritpz01004.01.rithmic.net:65000~ritpz04063.04.rithmic.net:65000~ritpz01004.01.theomne.net:65000~ritpz04063.04.theomne.net:65000~ritpz01004.01.theomne.com:65000~ritpz04063.04.theomne.com:65000";
     fake_envp[1] = "MML_DOMAIN_NAME=rithmic_paper_prod_domain";
     fake_envp[2] = "MML_LIC_SRVR_ADDR=ritpz04063.04.rithmic.com:56000~ritpz01004.01.rithmic.com:56000~ritpz04063.04.rithmic.net:56000~ritpz04063.04.theomne.net:56000~ritpz04063.04.theomne.com:56000~ritpz01000.01.rithmic.com:56000~ritpz01001.01.rithmic.com:56000~ritpz01000.01.rithmic.net:56000~ritpz01001.01.rithmic.net:56000~ritpz01000.01.theomne.net:56000~ritpz01001.01.theomne.net:56000~ritpz01000.01.theomne.com:56000~ritpz01001.01.theomne.com:56000~ritpz24050.rithmic.com:56000~ritpz24050.rithmic.net:56000~ritpz24050.theomne.net:56000~ritpz24050.theomne.com:56000~ritpz23010.rithmic.com:56000~ritpz23010.rithmic.net:56000~ritpz23010.theomne.net:56000~ritpz23010.theomne.com:56000~ritpz23011.rithmic.com:56000~ritpz23011.rithmic.net:56000~ritpz23011.theomne.net:56000~ritpz23011.theomne.com:56000~ritpz24013.rithmic.com:56000~ritpz24013.rithmic.net:56000~ritpz24013.theomne.net:56000~ritpz24013.theomne.com:56000";
     fake_envp[3] = "MML_LOC_BROK_ADDR=ritpz04063.04.rithmic.com:64100";
     fake_envp[4] = "MML_LOGGER_ADDR=ritpz04063.04.rithmic.com:45454~ritpz01004.01.rithmic.com:45454~ritpz04063.04.rithmic.net:45454~ritpz01004.01.rithmic.net:45454~ritpz04063.04.theomne.net:45454~ritpz01004.01.theomne.net:45454~ritpz04063.04.theomne.com:45454~ritpz01004.01.theomne.com:45454";
     fake_envp[5] = "MML_LOG_TYPE=log_net";

     /*   The SSL file is located in the ./<version>/etc directory           */
     /*   of the R | API package.  The settings below assume that this       */
     /*   file is in the current working directory.  Normally you should     */
     /*   specify the full path to the file.                                 */
     fake_envp[6] = "MML_SSL_CLNT_AUTH_FILE=/mnt/wd_work/workspace/Work Place/Data/Project OFI/OFI_Production/Rithmic setup/13.6.0.0/etc/rithmic_ssl_cert_auth_params";

     fake_envp[7] = "USER=your_user_name";
     fake_envp[8] = NULL;

     /*   ----------------------------------------------------------------   */

     oParams.sAppName.pData        = "prsi:XGOFI";
     oParams.sAppName.iDataLen     = (int)strlen(oParams.sAppName.pData);
     oParams.sAppVersion.pData     = "1.0.0.0";
     oParams.sAppVersion.iDataLen  = (int)strlen(oParams.sAppVersion.pData);
     oParams.envp                  = fake_envp;
     oParams.pAdmCallbacks         = pAdmCallbacks;
     oParams.sLogFilePath.pData    = "smd.log";
     oParams.sLogFilePath.iDataLen = (int)strlen(oParams.sLogFilePath.pData);

     if (const char* v = std::getenv("XGOFI_VERBOSE_DUMPS"))
          {
          if (std::string(v) == "1" || std::string(v) == "true" || std::string(v) == "TRUE")
               g_verbose_dumps.store(true, std::memory_order_relaxed);
          }
     if (const char* v = std::getenv("XGOFI_FLUSH_EVERY_N"))
          {
          int n = std::atoi(v);
          if (n >= 0) g_flush_every_n.store(n, std::memory_order_relaxed);
          }
     if (const char* v = std::getenv("XGOFI_TRUNCATE_ON_START"))
          {
          if (std::string(v) == "1" || std::string(v) == "true" || std::string(v) == "TRUE")
               g_truncate_on_start.store(true, std::memory_order_relaxed);
          }
     if (const char* v = std::getenv("XGOFI_FAIL_IF_EXISTS"))
          {
          if (std::string(v) == "1" || std::string(v) == "true" || std::string(v) == "TRUE")
               g_fail_if_exists.store(true, std::memory_order_relaxed);
          }
#ifndef WinOS
     if (pipe(g_shutdown_pipe) != 0)
          {
          cout << "pipe() for shutdown self-pipe failed : " << errno << endl;
          return (BAD);
          }
     std::signal(SIGINT, signal_handler_int);
     std::signal(SIGTERM, signal_handler_int);
#endif

     std::thread(stats_heartbeat_loop).detach();

     /*   ----------------------------------------------------------------   */

     try
          {
          pEngine = new REngine(&oParams);
          }
     catch (OmneException& oEx)
          {
          delete pAdmCallbacks;

          iCode = oEx.getErrorCode();
          cout << "REngine::REngine() error : " << iCode << endl;
          return (BAD);
          }

     /*   ----------------------------------------------------------------   */
     /*   instantiate a callback object - prerequisite for logging in */

     try
          {
          pCallbacks = new MyCallbacks();
          }
     catch (OmneException& oEx)
          {
          delete pEngine;
          delete pAdmCallbacks;

          iCode = oEx.getErrorCode();
          cout << "MyCallbacks::MyCallbacks() error : " << iCode << endl;
          return (BAD);
          }

     /*   ----------------------------------------------------------------   */
     /*   First, log in to the repository to check agreements.               */
     /*   (See the FAQ for more details on agreements.)                      */
     /*   ----------------------------------------------------------------   */

     tsNCharcb sRepEnvKey;
     tsNCharcb sRepUser;
     tsNCharcb sRepPassword;
     tsNCharcb sRepCnnctPt;

     sRepEnvKey.pData      = "system";
     sRepEnvKey.iDataLen   = (int)strlen(sRepEnvKey.pData);

     sRepUser.pData        = argv[1];
     sRepUser.iDataLen     = (int)strlen(sRepUser.pData);

     sRepPassword.pData    = argv[2];
     sRepPassword.iDataLen = (int)strlen(sRepPassword.pData);

     sRepCnnctPt.pData     = "login_agent_repositoryc";
     sRepCnnctPt.iDataLen  = (int)strlen(sRepCnnctPt.pData);

     if (!pEngine -> loginRepository(&sRepEnvKey,
				     &sRepUser,
				     &sRepPassword,
				     &sRepCnnctPt,
				     pCallbacks,
				     &iCode))
          {
          cout << "REngine::loginRepository() error : " << iCode << endl;

          delete pEngine;
          delete pCallbacks;
          delete pAdmCallbacks;

          return (BAD);
          }

     /*   ----------------------------------------------------------------   */
     /*   After calling REngine::loginRepository, RCallbacks::Alert will     */
     /*   be called a number of times.  Wait until the login succeeds or     */
     /*   fails.                                                             */
     /*   ----------------------------------------------------------------   */

     while (g_iRepLoginStatus != LoginStatus_Complete &&
	    g_iRepLoginStatus != LoginStatus_Failed)
          {
#ifndef WinOS
          sleep(1);
#else
          Sleep(1000);
#endif
          }

     if (g_iRepLoginStatus == LoginStatus_Failed)
	  {
	  if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
          cout << "Please make sure you entered the username and password "
	       << "correctly.  Also, please make sure your credentials match "
	       << "the system you are trying to log in to." << endl;
	  if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;

          delete pEngine;
          delete pCallbacks;
          delete pAdmCallbacks;

          return (BAD);
	  }

     /*   ----------------------------------------------------------------   */
     /*   Once logged in to the repository, we can request a list of         */
     /*   unaccepted agreements.                                             */
     /*   ----------------------------------------------------------------   */

     if (!pEngine -> listAgreements(false, NULL, &iCode))
          {
          cout << "REngine::listAgreements() error : " << iCode << endl;

	  int iIgnored;
	  pEngine -> logoutRepository(&iIgnored);

          delete pEngine;
          delete pCallbacks;
          delete pAdmCallbacks;

          return (BAD);
          }

     /*   ----------------------------------------------------------------   */
     /*   Wait for the list to arrive from the infrastructure.               */
     /*   ----------------------------------------------------------------   */

     while (!g_bRcvdUnacceptedAgreements)
          {
#ifndef WinOS
          sleep(1);
#else
          Sleep(1000);
#endif
          }

     /*   ----------------------------------------------------------------   */
     /*   If there are any unaccepted mandatory agreements, you must log     */
     /*   in using R | Trader (Pro) and accept them before being able to     */
     /*   log in to the trading platform.                                    */
     /*   ----------------------------------------------------------------   */

     if (g_iUnacceptedMandatoryAgreements > 0)
	  {
	  if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
          cout << "Please log in using R | Trader or R | Trader Pro" 
	       << " and sign the agreements." << endl;
	  if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;

	  int iIgnored;

	  pEngine -> logoutRepository(&iIgnored);

          delete pEngine;
          delete pCallbacks;
          delete pAdmCallbacks;

          return (BAD);
	  }
     
     /*   ----------------------------------------------------------------   */
     /*   Log out from the repository.  We are done with it ...              */
     /*   ----------------------------------------------------------------   */

     if (!pEngine -> logoutRepository(&iCode))
          {
          cout << "REngine::logoutRepository() error : " << iCode << endl;

          delete pEngine;
          delete pCallbacks;
          delete pAdmCallbacks;

          return (BAD);
          }

     /*   ----------------------------------------------------------------   */
     /*   Set up parameters for logging in.  Again, the MdCnnctPt and        */
     /*   TsCnnctPt have values for Rithmic 01 Test.  Add values for other   */
     /*   members of LoginParams to log into other subsystems of the         */
     /*   infrastructure like pnl and history.                               */

     oLoginParams.pCallbacks           = pCallbacks;

     oLoginParams.sMdUser.pData        = argv[1];
     oLoginParams.sMdUser.iDataLen     = (int)strlen(oLoginParams.sMdUser.pData);

     oLoginParams.sMdPassword.pData    = argv[2];
     oLoginParams.sMdPassword.iDataLen = (int)strlen(oLoginParams.sMdPassword.pData);

     oLoginParams.sMdCnnctPt.pData     = "login_agent_tp_paperc";
     oLoginParams.sMdCnnctPt.iDataLen  = (int)strlen(oLoginParams.sMdCnnctPt.pData);

     oLoginParams.sTsUser.pData        = argv[1];
     oLoginParams.sTsUser.iDataLen     = (int)strlen(oLoginParams.sTsUser.pData);

     oLoginParams.sTsPassword.pData    = argv[2];
     oLoginParams.sTsPassword.iDataLen = (int)strlen(oLoginParams.sTsPassword.pData);

     oLoginParams.sTsCnnctPt.pData     = "login_agent_op_paperc";
     oLoginParams.sTsCnnctPt.iDataLen  = (int)strlen(oLoginParams.sTsCnnctPt.pData);

     /*   ----------------------------------------------------------------   */

     if (!pEngine -> login(&oLoginParams, &iCode))
          {
          cout << "REngine::login() error : " << iCode << endl;

          delete pEngine;
          delete pCallbacks;
          delete pAdmCallbacks;

          return (BAD);
          }

     /*   ----------------------------------------------------------------   */
     /*   After calling REngine::login, RCallbacks::Alert may be called a    */
     /*   number of times.  Wait for when the login to the MdCnnctPt is      */
     /*   complete.  (See MyCallbacks::Alert() for details).                 */

     while (g_iMdLoginStatus != LoginStatus_Complete &&
	    g_iMdLoginStatus != LoginStatus_Failed)
          {
#ifndef WinOS
          sleep(1);
#else
          Sleep(1000);
#endif
          }

     if (g_iMdLoginStatus == LoginStatus_Failed)
	  {
	  if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;
          cout << "Please make sure you entered the username and password "
	       << "correctly.  Also, please make sure your credentials match "
	       << "the system you are trying to log in to." << endl;
	  if (g_verbose_dumps.load(std::memory_order_relaxed)) cout << endl << endl;

          delete pEngine;
          delete pCallbacks;
          delete pAdmCallbacks;

          return (BAD);
	  }

     /*   ----------------------------------------------------------------   */

     sExchange.pData    = argv[3];
     sExchange.iDataLen = (int)strlen(sExchange.pData);

     /*   ----------------------------------------------------------------   */

     sTicker.pData    = argv[4];
     sTicker.iDataLen = (int)strlen(sTicker.pData);

     /*   ----------------------------------------------------------------   */
     /*   Subscription flags are OR'd.  Add more flags to get more data.     */

     iFlags = (MD_PRINTS | MD_BEST | MD_QUOTES);

     /*   ----------------------------------------------------------------   */

     if (!pEngine -> subscribe(&sExchange, &sTicker, iFlags, &iCode))
          {
          cout << "REngine::subscribe() error : " << iCode << endl;

          delete pEngine;
          delete pCallbacks;
          delete pAdmCallbacks;

          return (BAD);
          }

     /*   ----------------------------------------------------------------   */
     /*   Block until SIGINT/SIGTERM (via self-pipe) or Enter key on stdin. Part 2 of the         */
     /*   2026-08-16 rebuild-finalize mission: replaces this revision's plain fgetc(stdin), which  */
     /*   had no path for a signal to cleanly unblock it -- rithmic_scheduler.py's own stop_feed() */
     /*   comment already documented this exact self-pipe contract, matching what the actually-    */
     /*   deployed (now-lost) June build did per the June 14 diagnostic report's source inspection. */
#ifndef WinOS
          {
          fd_set rfds;
          int pipe_rd  = g_shutdown_pipe[0];
          int stdin_fd = fileno(stdin);
          int nfds = (pipe_rd > stdin_fd ? pipe_rd : stdin_fd) + 1;
          FD_ZERO(&rfds);
          FD_SET(pipe_rd, &rfds);
          FD_SET(stdin_fd, &rfds);
          select(nfds, &rfds, NULL, NULL, NULL);
          }
#else
     fgetc(stdin);
#endif

     /*   ----------------------------------------------------------------   */

     g_run_stats.store(false, std::memory_order_relaxed);
     flush_all_outputs();

     delete pEngine;
     delete pCallbacks;
     delete pAdmCallbacks;

     /*   ----------------------------------------------------------------   */

     return (GOOD);
     }

/*   =====================================================================   */
