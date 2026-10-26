/*
 * test_c_side_mock.cpp -- test double for the simulator + SV shims.
 *
 * Linked together with axis_dpi.cpp by test_c_side.py. It provides what a
 * simulator would: svGetScope/svSetScope, and the exported SV functions
 * (axis_mst_push, axis_slv_pop, ...) backed by per-"scope" queues, mirroring
 * the queues in axis_vip_dpi.sv. An export called without the right scope
 * selected fails loudly (-99), which is how scope bugs show up in the test.
 *
 * The mock_* functions let the test play the SV side (the VIP driver popping
 * beats, the VIP monitor pushing received beats).
 *
 * Every function is extern "C": axis_dpi.cpp declares the simulator and SV
 * side with C signatures, and the test reaches the mock_* hooks through ctypes.
 */
#include <array>
#include <cstring>
#include <deque>

namespace {

constexpr int VW = 128;                    // words per 4096-bit DPI vector
constexpr int E_SCOPE = -99;               // export called in the wrong scope

using Vec = std::array<unsigned int, VW>;

struct Beat {
    Vec d{}, k{}, s{}, u{};
    int last = 0, id = 0, dest = 0, delay = 0;
};

struct Scope {
    int kind = 0;                          // 0 master, 1 slave
    std::deque<Beat> q;
    int pushed = 0, done = 0;
    int bp_mode = 0, lmin = 0, lmax = 0, hmin = 1, hmax = 1, bp_version = 0;
};

Scope *g_cur;                              // what svSetScope selected
Scope *g_registering;                      // what svGetScope returns

Scope *scope(void *s) { return static_cast<Scope *>(s); }

bool in(int kind) { return g_cur && g_cur->kind == kind; }

void load(Vec &v, const unsigned int *p) { std::memcpy(v.data(), p, sizeof v); }
void store(unsigned int *p, const Vec &v) { std::memcpy(p, v.data(), sizeof v); }

Beat beat(const unsigned int *d, const unsigned int *k, const unsigned int *s,
          const unsigned int *u, int last, int id, int dest, int delay)
{
    Beat b;
    load(b.d, d); load(b.k, k); load(b.s, s); load(b.u, u);
    b.last = last; b.id = id; b.dest = dest; b.delay = delay;
    return b;
}

// false if empty
bool pop(Scope &m, Beat &b)
{
    if (m.q.empty()) return false;
    b = m.q.front();
    m.q.pop_front();
    return true;
}

}  // namespace

extern "C" {

typedef void *svScope;

svScope svGetScope(void) { return g_registering; }
svScope svSetScope(const svScope s) { svScope o = g_cur; g_cur = scope(s); return o; }

// ---- exports the real SV shims provide ----
int axis_mst_push(const unsigned int *d, const unsigned int *k,
                  const unsigned int *s, const unsigned int *u,
                  int last, int id, int dest, int delay)
{
    if (!in(0)) return E_SCOPE;
    g_cur->q.push_back(beat(d, k, s, u, last, id, dest, delay));
    g_cur->pushed++;
    return 0;
}

int axis_mst_flush(void)
{
    if (!in(0)) return E_SCOPE;
    g_cur->pushed -= static_cast<int>(g_cur->q.size());
    g_cur->q.clear();
    return 0;
}

int axis_slv_pop(unsigned int *d, unsigned int *k, unsigned int *s,
                 unsigned int *u, int *last, int *id, int *dest)
{
    if (!in(1)) return E_SCOPE;
    Beat b;                                // all zero if nothing is queued
    bool got = pop(*g_cur, b);
    store(d, b.d); store(k, b.k); store(s, b.s); store(u, b.u);
    *last = b.last; *id = b.id; *dest = b.dest;
    return got ? 1 : 0;
}

int axis_slv_set_bp(int mode, int lmin, int lmax, int hmin, int hmax)
{
    if (!in(1)) return E_SCOPE;
    g_cur->bp_mode = mode; g_cur->lmin = lmin; g_cur->lmax = lmax;
    g_cur->hmin = hmin; g_cur->hmax = hmax; g_cur->bp_version++;
    return 0;
}

int axis_slv_flush(void)
{
    if (!in(1)) return E_SCOPE;
    g_cur->q.clear();
    return 0;
}

// ---- test hooks: the "SV side" and the simulator ----
// scopes live as long as the process, like SV instances
void *mock_new_scope(int kind)
{
    Scope *m = new Scope;
    m->kind = kind;
    return m;
}
void mock_set_registering(void *s) { g_registering = scope(s); }
// simulate "cocotb call made while some DPI scope is already current"
void  mock_set_current(void *s) { g_cur = scope(s); }
void *mock_get_current(void) { return g_cur; }

// VIP master driver takes the next beat
int mock_mst_pop(void *sc, unsigned int *d, unsigned int *k, unsigned int *s,
                 unsigned int *u, int *last, int *id, int *dest, int *delay)
{
    Beat b;
    if (!pop(*scope(sc), b)) return 0;
    store(d, b.d); store(k, b.k); store(s, b.s); store(u, b.u);
    *last = b.last; *id = b.id; *dest = b.dest; *delay = b.delay;
    return 1;
}
void mock_mst_done(void *sc, int n) { scope(sc)->done += n; }
int  mock_outstanding(void *sc) { return scope(sc)->pushed - scope(sc)->done; }

// VIP slave monitor delivers a beat
void mock_slv_push(void *sc, const unsigned int *d, const unsigned int *k,
                   const unsigned int *s, const unsigned int *u,
                   int last, int id, int dest)
{
    scope(sc)->q.push_back(beat(d, k, s, u, last, id, dest, 0));
}
int  mock_slv_count(void *sc) { return static_cast<int>(scope(sc)->q.size()); }
void mock_slv_bp(void *sc, int *out)
{
    const Scope &m = *scope(sc);
    out[0] = m.bp_mode; out[1] = m.lmin; out[2] = m.lmax;
    out[3] = m.hmin; out[4] = m.hmax; out[5] = m.bp_version;
}

#ifdef AXIS_DPI_MOCK_COVERAGE
// Built with --coverage (AXI_VIP_SHIMS_TEST_COVERAGE): write the .gcda
// counters now. They are otherwise written when the process exits, i.e.
// after pytest has printed its report (see conftest.py).
void __gcov_dump(void);
void mock_gcov_dump(void) { __gcov_dump(); }
#endif

}  // extern "C"
