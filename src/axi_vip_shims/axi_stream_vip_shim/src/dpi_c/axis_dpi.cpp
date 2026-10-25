/*
 * axis_dpi.cpp -- thin DPI-C trampoline between cocotb and the SV shims
 *
 * The queues live in SystemVerilog (axis_vip_dpi.sv). This file only
 *   - keeps a name -> SV-instance table (and the interface description, so the
 *     cocotb constructor can be checked against the shim's parameters),
 *   - turns bytes into 4096-bit DPI vectors and back,
 *   - calls the SV shim's EXPORTED functions with svSetScope() pointing at the
 *     right shim instance.
 *
 * Nothing here runs unless cocotb makes a call: the SV shims never poll this
 * library, so a quiet interface costs no DPI crossings at all.
 *
 * Loaded twice into the simulator process: by the simulator (-sv_lib; the shim
 * calls axis_dpi_sv_register) and by cocotb (ctypes.CDLL). Same file => same
 * library instance => shared table. No locking (same thread).
 *
 * Written in C++ for the table and the scope guard only; every entry point is
 * extern "C", so ctypes and the simulator see plain C symbols.
 *
 * The exports are provided by the shims. They are declared weak so that the
 * library still loads when a design only instantiates masters or only slaves.
 *
 * Every call into an export is wrapped in a ScopeGuard: svSetScope(shim), and
 * the scope that was current before is restored afterwards. (If none was,
 * nothing is restored: svSetScope(NULL) is not guaranteed to be legal, and
 * nothing here reads the stale scope -- simulators set it on entry to every
 * context import.)
 *
 * svGetScope/svSetScope are the standard svdpi.h functions; they are declared
 * here by hand so no simulator include path is needed to build.
 *
 * Debug log: with AXIS_DPI_DEBUG set (and not "0") every call into this library
 * prints one line, through vpi_printf when the simulator provides it (so it
 * lands in the simulator log next to $display), else to stdout, flushed so it
 * stays in order with the Python log. The SV shims and the Python side ask
 * axis_dpi_debug() and log too.
 *
 * Build:  c++ -std=c++17 -shared -fPIC -O2 -Wall -o libaxis_dpi.so axis_dpi.cpp
 */
#include <array>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

extern "C" {

typedef void *svScope;
svScope svGetScope(void);
svScope svSetScope(const svScope scope);

#define AXIS_WEAK __attribute__((weak))

/* simulator's log, if it has VPI; else the debug log goes to stdout */
int vpi_printf(const char *fmt, ...) AXIS_WEAK;

/* exported by axis_vip_dpi_mst */
int axis_mst_push(const unsigned int *data, const unsigned int *keep,
                  const unsigned int *strb, const unsigned int *user,
                  int last, int id, int dest, int delay) AXIS_WEAK;
int axis_mst_flush(void) AXIS_WEAK;
/* exported by axis_vip_dpi_slv */
int axis_slv_pop(unsigned int *data, unsigned int *keep,
                 unsigned int *strb, unsigned int *user,
                 int *last, int *id, int *dest) AXIS_WEAK;
int axis_slv_set_bp(int mode, int low_min, int low_max,
                    int high_min, int high_max) AXIS_WEAK;
int axis_slv_flush(void) AXIS_WEAK;

}  // extern "C"

namespace {

constexpr int VEC_WORDS = 128;             // 4096 bits = AXIS_VEC_BITS in SV
constexpr int VEC_BYTES = VEC_WORDS * 4;

// return codes of the cocotb-facing calls
constexpr int E_INST   = -1;
constexpr int E_ARG    = -2;
constexpr int E_NOSHIM = -4;               // SV shim has not registered
constexpr int E_NOEXP  = -5;               // export missing from the design

// config fields, same order everywhere (cfg_check's mismatch bits,
// axis_dpi_sv_cfg_get, the Python side)
enum CfgField { ROLE, DATA_BYTES, HAS_TKEEP, HAS_TSTRB, HAS_TLAST,
                ID_W, DEST_W, USER_W, CFG_N };

struct Cfg {
    bool valid = false;
    std::array<int, CFG_N> v{};

    void set(int role, int data_bytes, int has_tkeep, int has_tstrb,
             int has_tlast, int id_w, int dest_w, int user_w)
    {
        v = {role, data_bytes, has_tkeep ? 1 : 0, has_tstrb ? 1 : 0,
             has_tlast ? 1 : 0, id_w, dest_w, user_w};
        valid = true;
    }
};

struct Inst {
    std::string name;
    svScope scope = nullptr;               // the SV shim instance
    Cfg sv;                                // what the shim was compiled with
    Cfg py;                                // what the cocotb constructor said

    bool is_master() const { return sv.v[ROLE] == 0; }
};

// handle = index into g_inst; instances are never removed
std::vector<Inst> g_inst;
std::unordered_map<std::string, int> g_by_name;

Inst *get(int i)
{
    return (i >= 0 && i < static_cast<int>(g_inst.size())) ? &g_inst[i] : nullptr;
}

// E_NOSHIM / E_NOEXP if the call can't reach the shim, else 0
int reachable(const Inst &s, bool has_export)
{
    if (!s.scope || !s.sv.valid) return E_NOSHIM;
    if (!has_export) return E_NOEXP;
    return 0;
}

struct ScopeGuard {
    svScope old;
    explicit ScopeGuard(svScope s) : old(svSetScope(s)) {}
    ~ScopeGuard() { if (old) svSetScope(old); }
    ScopeGuard(const ScopeGuard &) = delete;
    ScopeGuard &operator=(const ScopeGuard &) = delete;
};

// bytes <-> svBitVecVal words (host-endian independent)
using Vec = std::array<unsigned int, VEC_WORDS>;

Vec vec_put(const unsigned char *in, int n)
{
    Vec w{};
    for (int i = 0; i < n && i < VEC_BYTES; i++)
        w[i >> 2] |= static_cast<unsigned int>(in[i]) << (8 * (i & 3));
    return w;
}

void vec_get(const Vec &w, unsigned char *out, int n)
{
    for (int i = 0; i < n && i < VEC_BYTES; i++)
        out[i] = static_cast<unsigned char>((w[i >> 2] >> (8 * (i & 3))) & 0xFF);
}

bool len_ok(int n) { return n >= 0 && n <= VEC_BYTES; }

/* ------------------------------------------------------------ debug log */
bool env_debug()
{
    const char *e = std::getenv("AXIS_DPI_DEBUG");
    return e && *e && std::strcmp(e, "0") != 0;
}

const bool g_debug = env_debug();

// Only evaluates its arguments when debugging, so hex() etc. cost nothing else.
#define DBG(...) do { if (g_debug) dbg_print(__VA_ARGS__); } while (0)

__attribute__((format(printf, 1, 2)))
void dbg_print(const char *fmt, ...)
{
    va_list ap, ap2;
    va_start(ap, fmt);
    va_copy(ap2, ap);
    std::string line(std::vsnprintf(nullptr, 0, fmt, ap) + 1, '\0');
    std::vsnprintf(&line[0], line.size(), fmt, ap2);
    va_end(ap2);
    va_end(ap);
    if (vpi_printf)
        vpi_printf("axis_dpi: %s\n", line.c_str());
    else
        std::printf("axis_dpi: %s\n", line.c_str());
    std::fflush(stdout);
}

// little-endian bytes as one hex number, "-" for an absent (empty) signal
std::string hex(const unsigned char *b, int n)
{
    if (n <= 0 || !b) return "-";
    std::string s = "0x";
    char buf[3];
    for (int i = n - 1; i >= 0; i--) {
        std::snprintf(buf, sizeof buf, "%02x", b[i]);
        s += buf;
    }
    return s;
}

const char *name_of(int i)
{
    Inst *s = get(i);
    return s ? s->name.c_str() : "?";
}

const char *rc_str(int rc)
{
    switch (rc) {
    case E_INST:   return " (bad instance handle)";
    case E_ARG:    return " (bad arguments)";
    case E_NOSHIM: return " (SV shim not registered)";
    case E_NOEXP:  return " (SV export missing from the design)";
    default:       return "";
    }
}

std::string cfg_str(const Cfg &c)
{
    char buf[160];
    std::snprintf(buf, sizeof buf,
                  "%s, %d data bytes, tkeep=%d tstrb=%d tlast=%d id_w=%d "
                  "dest_w=%d user_w=%d",
                  c.v[ROLE] == 0 ? "master" : "slave", c.v[DATA_BYTES],
                  c.v[HAS_TKEEP], c.v[HAS_TSTRB], c.v[HAS_TLAST], c.v[ID_W],
                  c.v[DEST_W], c.v[USER_W]);
    return buf;
}

const char *const BP_NAMES[] = {"none", "osc", "random", "stall"};

int dbg_fail(const char *what, int inst, int rc)
{
    DBG("%s: %s -> %d%s", name_of(inst), what, rc, rc_str(rc));
    return rc;
}

// the byte buffers of tx_push / rx_pop: TDATA required, the others optional
bool vecs_ok(const void *data, int dn, const void *keep, int kn,
             const void *strb, int sn, const void *user, int un)
{
    return dn >= 1 && len_ok(dn) && len_ok(kn) && len_ok(sn) && len_ok(un) &&
           data && (!kn || keep) && (!sn || strb) && (!un || user);
}

}  // namespace

extern "C" {

/* ------------------------------------------------------------- registry */
int axis_dpi_open(const char *name)
{
    if (!name || !*name) return -1;
    auto [it, added] = g_by_name.try_emplace(name, static_cast<int>(g_inst.size()));
    if (added) g_inst.emplace_back().name = name;
    return it->second;
}

/* nonzero when AXIS_DPI_DEBUG is set: the SV shims and Python log too */
int axis_dpi_debug(void) { return g_debug ? 1 : 0; }

/* SV shim, declared as a `context` DPI import INSIDE the shim module, so
 * svGetScope() is that shim instance. Called once from its initial block. */
int axis_dpi_sv_register(const char *name, int role, int data_bytes,
                         int has_tkeep, int has_tstrb, int has_tlast,
                         int id_w, int dest_w, int user_w)
{
    int i = axis_dpi_open(name);
    if (i < 0) return -1;
    Inst &s = g_inst[i];
    s.scope = svGetScope();
    s.sv.set(role, data_bytes, has_tkeep, has_tstrb, has_tlast, id_w, dest_w, user_w);
    DBG("%s: inst %d registered by SV shim: %s", name, i, cfg_str(s.sv).c_str());
    return i;
}

/* cocotb constructor: what the test believes the interface looks like. */
void axis_dpi_py_config(int inst, int role, int data_bytes, int has_tkeep,
                        int has_tstrb, int has_tlast, int id_w, int dest_w,
                        int user_w)
{
    if (Inst *s = get(inst)) {
        s->py.set(role, data_bytes, has_tkeep, has_tstrb, has_tlast, id_w, dest_w, user_w);
        DBG("%s: inst %d configured by cocotb: %s", s->name.c_str(), inst,
            cfg_str(s->py).c_str());
    }
}

/* -1: SV shim not registered yet, -2: Python config missing,
 * else bitmask of mismatching fields (0 = match; bit order = CfgField) */
int axis_dpi_cfg_check(int inst)
{
    Inst *s = get(inst);
    if (!s || !s->sv.valid) return -1;
    if (!s->py.valid) return -2;
    int mask = 0;
    for (int i = 0; i < CFG_N; i++)
        if (s->sv.v[i] != s->py.v[i]) mask |= 1 << i;
    DBG("%s: cfg_check -> %s (mask %#x)", s->name.c_str(),
        mask ? "MISMATCH" : "match", mask);
    return mask;
}

void axis_dpi_sv_cfg_get(int inst, int *out)
{
    if (Inst *s = get(inst))
        for (int i = 0; i < CFG_N; i++) out[i] = s->sv.v[i];
}

/* --------------------------------------------------------------- master */
int axis_dpi_tx_push(int inst,
                     const unsigned char *data, int dn,
                     const unsigned char *keep, int kn,
                     const unsigned char *strb, int sn,
                     const unsigned char *user, int un,
                     int last, int id, int dest, int delay)
{
    Inst *s = get(inst);
    if (!s) return dbg_fail("tx_push", inst, E_INST);
    if (!vecs_ok(data, dn, keep, kn, strb, sn, user, un))
        return dbg_fail("tx_push", inst, E_ARG);
    if (int e = reachable(*s, axis_mst_push != nullptr))
        return dbg_fail("tx_push", inst, e);
    DBG("%s: tx_push data=%s keep=%s strb=%s user=%s last=%d id=%d dest=%d "
        "delay=%d", s->name.c_str(), hex(data, dn).c_str(), hex(keep, kn).c_str(),
        hex(strb, sn).c_str(), hex(user, un).c_str(), last, id, dest, delay);
    Vec d = vec_put(data, dn), k = vec_put(keep, kn);
    Vec st = vec_put(strb, sn), u = vec_put(user, un);
    ScopeGuard g(s->scope);
    return axis_mst_push(d.data(), k.data(), st.data(), u.data(), last, id, dest, delay);
}

/* ---------------------------------------------------------------- slave */
/* Returns 1 and fills the buffers if a beat was available, 0 if empty. */
int axis_dpi_rx_pop(int inst,
                    unsigned char *data, int dn,
                    unsigned char *keep, int kn,
                    unsigned char *strb, int sn,
                    unsigned char *user, int un,
                    int *last, int *id, int *dest)
{
    Inst *s = get(inst);
    if (!s) return dbg_fail("rx_pop", inst, E_INST);
    if (!vecs_ok(data, dn, keep, kn, strb, sn, user, un))
        return dbg_fail("rx_pop", inst, E_ARG);
    if (int e = reachable(*s, axis_slv_pop != nullptr))
        return dbg_fail("rx_pop", inst, e);
    Vec d, k, st, u;
    int rc;
    {
        ScopeGuard g(s->scope);
        rc = axis_slv_pop(d.data(), k.data(), st.data(), u.data(), last, id, dest);
    }
    if (rc == 1) {
        vec_get(d, data, dn);
        vec_get(k, keep, kn);
        vec_get(st, strb, sn);
        vec_get(u, user, un);
        DBG("%s: rx_pop data=%s keep=%s strb=%s user=%s last=%d id=%d dest=%d",
            s->name.c_str(), hex(data, dn).c_str(), hex(keep, kn).c_str(),
            hex(strb, sn).c_str(), hex(user, un).c_str(), *last, *id, *dest);
    } else {
        DBG("%s: rx_pop -> %d%s", s->name.c_str(), rc, rc ? "" : " (empty)");
    }
    return rc;
}

/* modes: 0 none, 1 osc, 2 random, 3 stall */
int axis_dpi_bp_set(int inst, int mode, int low_min, int low_max,
                    int high_min, int high_max)
{
    Inst *s = get(inst);
    if (!s) return dbg_fail("bp_set", inst, E_INST);
    if (mode < 0 || mode > 3 || low_min < 0 || high_min < 0 ||
        low_max < low_min || high_max < high_min)
        return dbg_fail("bp_set", inst, E_ARG);
    if (int e = reachable(*s, axis_slv_set_bp != nullptr))
        return dbg_fail("bp_set", inst, e);
    DBG("%s: bp_set mode=%s low=%d..%d high=%d..%d", s->name.c_str(),
        BP_NAMES[mode], low_min, low_max, high_min, high_max);
    ScopeGuard g(s->scope);
    return axis_slv_set_bp(mode, low_min, low_max, high_min, high_max);
}

/* ----------------------------------------------------------- both roles */
/* Drop everything queued in the shim (not what the VIP already has in flight). */
int axis_dpi_flush(int inst)
{
    Inst *s = get(inst);
    if (!s) return dbg_fail("flush", inst, E_INST);
    bool master = s->is_master();
    if (int e = reachable(*s, master ? axis_mst_flush != nullptr
                                     : axis_slv_flush != nullptr))
        return dbg_fail("flush", inst, e);
    DBG("%s: flush (%s)", s->name.c_str(), master ? "master" : "slave");
    ScopeGuard g(s->scope);
    return master ? axis_mst_flush() : axis_slv_flush();
}

}  // extern "C"
