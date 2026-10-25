/*
 * test_c_side_mock.c -- test double for the simulator + SV shims.
 *
 * Linked together with axis_dpi.cpp by test_c_side.py. It provides what a
 * simulator would: svGetScope/svSetScope, and the exported SV functions
 * (axis_mst_push, axis_slv_pop, ...) backed by per-"scope" queues, mirroring
 * the queues in axis_vip_dpi.sv. An export called without the right scope
 * selected fails loudly (-99), which is how scope bugs show up in the test.
 *
 * The mock_* functions let the test play the SV side (the VIP driver popping
 * beats, the VIP monitor pushing received beats).
 */
#include <stdlib.h>
#include <string.h>

typedef void *svScope;
#define VW 128

typedef struct {
    unsigned int d[VW], k[VW], s[VW], u[VW];
    int last, id, dest, delay;
} mbeat;

typedef struct {
    int kind;                        /* 0 master, 1 slave */
    mbeat *q; int n, cap;
    int pushed, done;
    int bp_mode, lmin, lmax, hmin, hmax, bp_version;
} mscope;

static mscope *g_cur;                /* what svSetScope selected */
static mscope *g_registering;        /* what svGetScope returns */

svScope svGetScope(void) { return g_registering; }
svScope svSetScope(const svScope s) { svScope o = g_cur; g_cur = (mscope *)s; return o; }

static void q_push(mscope *m, const mbeat *b)
{
    if (m->n == m->cap) {
        m->cap = m->cap ? m->cap * 2 : 16;
        m->q = (mbeat *)realloc(m->q, (size_t)m->cap * sizeof(mbeat));
    }
    m->q[m->n++] = *b;
}

static int q_pop(mscope *m, mbeat *b)
{
    if (!m->n) return 0;
    *b = m->q[0];
    memmove(m->q, m->q + 1, (size_t)(m->n - 1) * sizeof(mbeat));
    m->n--;
    return 1;
}

/* ---- exports the real SV shims provide ---- */
int axis_mst_push(const unsigned int *d, const unsigned int *k,
                  const unsigned int *s, const unsigned int *u,
                  int last, int id, int dest, int delay)
{
    mbeat b;
    if (!g_cur || g_cur->kind != 0) return -99;
    memcpy(b.d, d, sizeof b.d); memcpy(b.k, k, sizeof b.k);
    memcpy(b.s, s, sizeof b.s); memcpy(b.u, u, sizeof b.u);
    b.last = last; b.id = id; b.dest = dest; b.delay = delay;
    q_push(g_cur, &b);
    g_cur->pushed++;
    return 0;
}

int axis_mst_flush(void)
{
    if (!g_cur || g_cur->kind != 0) return -99;
    g_cur->pushed -= g_cur->n;
    g_cur->n = 0;
    return 0;
}

int axis_slv_pop(unsigned int *d, unsigned int *k, unsigned int *s,
                 unsigned int *u, int *last, int *id, int *dest)
{
    mbeat b;
    if (!g_cur || g_cur->kind != 1) return -99;
    memset(d, 0, VW * 4); memset(k, 0, VW * 4);
    memset(s, 0, VW * 4); memset(u, 0, VW * 4);
    *last = *id = *dest = 0;
    if (!q_pop(g_cur, &b)) return 0;
    memcpy(d, b.d, VW * 4); memcpy(k, b.k, VW * 4);
    memcpy(s, b.s, VW * 4); memcpy(u, b.u, VW * 4);
    *last = b.last; *id = b.id; *dest = b.dest;
    return 1;
}

int axis_slv_set_bp(int mode, int lmin, int lmax, int hmin, int hmax)
{
    if (!g_cur || g_cur->kind != 1) return -99;
    g_cur->bp_mode = mode; g_cur->lmin = lmin; g_cur->lmax = lmax;
    g_cur->hmin = hmin; g_cur->hmax = hmax; g_cur->bp_version++;
    return 0;
}

int axis_slv_flush(void)
{
    if (!g_cur || g_cur->kind != 1) return -99;
    g_cur->n = 0;
    return 0;
}

/* ---- test hooks: the "SV side" and the simulator ---- */
void *mock_new_scope(int kind)
{
    mscope *m = (mscope *)calloc(1, sizeof *m);
    m->kind = kind; m->hmin = m->hmax = 1;
    return m;
}
void mock_set_registering(void *s) { g_registering = (mscope *)s; }
/* simulate "cocotb call made while some DPI scope is already current" */
void  mock_set_current(void *s) { g_cur = (mscope *)s; }
void *mock_get_current(void) { return g_cur; }

/* VIP master driver takes the next beat */
int mock_mst_pop(void *sc, unsigned int *d, unsigned int *k, unsigned int *s,
                 unsigned int *u, int *last, int *id, int *dest, int *delay)
{
    mbeat b;
    if (!q_pop((mscope *)sc, &b)) return 0;
    memcpy(d, b.d, VW * 4); memcpy(k, b.k, VW * 4);
    memcpy(s, b.s, VW * 4); memcpy(u, b.u, VW * 4);
    *last = b.last; *id = b.id; *dest = b.dest; *delay = b.delay;
    return 1;
}
void mock_mst_done(void *sc, int n) { ((mscope *)sc)->done += n; }
int  mock_outstanding(void *sc) { mscope *m = (mscope *)sc; return m->pushed - m->done; }

/* VIP slave monitor delivers a beat */
void mock_slv_push(void *sc, const unsigned int *d, const unsigned int *k,
                   const unsigned int *s, const unsigned int *u,
                   int last, int id, int dest)
{
    mbeat b;
    memcpy(b.d, d, VW * 4); memcpy(b.k, k, VW * 4);
    memcpy(b.s, s, VW * 4); memcpy(b.u, u, VW * 4);
    b.last = last; b.id = id; b.dest = dest; b.delay = 0;
    q_push((mscope *)sc, &b);
}
int  mock_slv_count(void *sc) { return ((mscope *)sc)->n; }
void mock_slv_bp(void *sc, int *out)
{
    mscope *m = (mscope *)sc;
    out[0] = m->bp_mode; out[1] = m->lmin; out[2] = m->lmax;
    out[3] = m->hmin; out[4] = m->hmax; out[5] = m->bp_version;
}

#ifdef AXIS_DPI_MOCK_COVERAGE
/* Built with --coverage (AXI_VIP_SHIMS_TEST_COVERAGE): write the .gcda
 * counters now. They are otherwise written when the process exits, i.e.
 * after pytest has printed its report (see conftest.py). */
void __gcov_dump(void);
void mock_gcov_dump(void) { __gcov_dump(); }
#endif
