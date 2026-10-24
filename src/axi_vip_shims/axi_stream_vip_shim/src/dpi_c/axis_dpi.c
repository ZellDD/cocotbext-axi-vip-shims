/*
 * axis_dpi.c -- thin DPI-C trampoline between cocotb and the SV shims
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
 * The exports are provided by the shims. They are declared weak so that the
 * library still loads when a design only instantiates masters or only slaves.
 *
 * Every call into an export is wrapped in svSetScope(shim); a scope that was
 * current before is restored afterwards. (If none was, nothing is restored:
 * svSetScope(NULL) is not guaranteed to be legal, and nothing here reads the
 * stalaxis_dpi_opene scope -- simulators set it on entry to every context import.)
 *
 * svGetScope/svSetScope are the standard svdpi.h functions; they are declared
 * here by hand so no simulator include path is needed to build.
 *
 * Build:  cc -shared -fPIC -O2 -Wall -o libaxis_dpi.so axis_dpi.c
 */
#include <stdlib.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void *svScope;
extern svScope svGetScope(void);
extern svScope svSetScope(const svScope scope);

#define AXIS_WEAK __attribute__((weak))

/* exported by axis_vip_dpi_mst */
extern int axis_mst_push(const unsigned int *data, const unsigned int *keep,
                         const unsigned int *strb, const unsigned int *user,
                         int last, int id, int dest, int delay) AXIS_WEAK;
extern int axis_mst_flush(void) AXIS_WEAK;
/* exported by axis_vip_dpi_slv */
extern int axis_slv_pop(unsigned int *data, unsigned int *keep,
                        unsigned int *strb, unsigned int *user,
                        int *last, int *id, int *dest) AXIS_WEAK;
extern int axis_slv_set_bp(int mode, int low_min, int low_max,
                           int high_min, int high_max) AXIS_WEAK;
extern int axis_slv_flush(void) AXIS_WEAK;

#define AXIS_MAX_INST  64
#define AXIS_NAME_LEN  64
#define AXIS_VEC_WORDS 128                 /* 4096 bits = AXIS_VEC_BITS in SV */
#define AXIS_VEC_BYTES (AXIS_VEC_WORDS * 4)
#define AXIS_CFG_N     8

/* return codes of the cocotb-facing calls */
#define AXIS_E_INST    (-1)
#define AXIS_E_ARG     (-2)
#define AXIS_E_NOSHIM  (-4)                /* SV shim has not registered */
#define AXIS_E_NOEXP   (-5)                /* export missing from the design */

/* config fields, same order everywhere:
 * role(0 master,1 slave) data_bytes has_tkeep has_tstrb has_tlast
 * id_w dest_w user_w */
typedef struct { int valid; int v[AXIS_CFG_N]; } cfg_t;

typedef struct {
    int     used;
    char    name[AXIS_NAME_LEN];
    svScope scope;                         /* the SV shim instance */
    cfg_t   sv_cfg;                        /* what the shim was compiled with */
    cfg_t   py_cfg;                        /* what the cocotb constructor said */
} inst_t;

static inst_t g_inst[AXIS_MAX_INST];

static inst_t *get(int i)
{
    if (i < 0 || i >= AXIS_MAX_INST || !g_inst[i].used) return NULL;
    return &g_inst[i];
}

/* bytes <-> svBitVecVal words (host-endian independent) */
static void vec_put(unsigned int *w, const unsigned char *in, int n)
{
    int i;
    memset(w, 0, AXIS_VEC_BYTES);
    for (i = 0; i < n && i < AXIS_VEC_BYTES; i++)
        w[i >> 2] |= ((unsigned int)in[i]) << (8 * (i & 3));
}

static void vec_get(const unsigned int *w, unsigned char *out, int n)
{
    int i;
    for (i = 0; i < n && i < AXIS_VEC_BYTES; i++)
        out[i] = (unsigned char)((w[i >> 2] >> (8 * (i & 3))) & 0xFF);
}

static int len_ok(int n) { return n >= 0 && n <= AXIS_VEC_BYTES; }

/* ------------------------------------------------------------- registry */
int axis_dpi_open(const char *name)
{
    int i, free_slot = -1;
    if (!name || !*name) return -1;
    for (i = 0; i < AXIS_MAX_INST; i++) {
        if (g_inst[i].used) {
            if (strncmp(g_inst[i].name, name, AXIS_NAME_LEN - 1) == 0) return i;
        } else if (free_slot < 0) {
            free_slot = i;
        }
    }
    if (free_slot < 0) return -1;
    memset(&g_inst[free_slot], 0, sizeof(inst_t));
    g_inst[free_slot].used = 1;
    strncpy(g_inst[free_slot].name, name, AXIS_NAME_LEN - 1);
    return free_slot;
}

static void cfg_set(cfg_t *c, int role, int data_bytes, int has_tkeep,
                    int has_tstrb, int has_tlast, int id_w, int dest_w, int user_w)
{
    c->v[0] = role;      c->v[1] = data_bytes; c->v[2] = has_tkeep ? 1 : 0;
    c->v[3] = has_tstrb ? 1 : 0; c->v[4] = has_tlast ? 1 : 0;
    c->v[5] = id_w;      c->v[6] = dest_w;     c->v[7] = user_w;
    c->valid = 1;
}

/* SV shim, declared as a `context` DPI import INSIDE the shim module, so
 * svGetScope() is that shim instance. Called once from its initial block. */
int axis_dpi_sv_register(const char *name, int role, int data_bytes,
                         int has_tkeep, int has_tstrb, int has_tlast,
                         int id_w, int dest_w, int user_w)
{
    int i = axis_dpi_open(name);
    if (i < 0) return -1;
    g_inst[i].scope = svGetScope();
    cfg_set(&g_inst[i].sv_cfg, role, data_bytes, has_tkeep, has_tstrb,
            has_tlast, id_w, dest_w, user_w);
    return i;
}

/* cocotb constructor: what the test believes the interface looks like. */
void axis_dpi_py_config(int inst, int role, int data_bytes, int has_tkeep,
                        int has_tstrb, int has_tlast, int id_w, int dest_w,
                        int user_w)
{
    inst_t *s = get(inst);
    if (s) cfg_set(&s->py_cfg, role, data_bytes, has_tkeep, has_tstrb,
                   has_tlast, id_w, dest_w, user_w);
}

/* -1: SV shim not registered yet, -2: Python config missing,
 * else bitmask of mismatching fields (0 = match; bit order = cfg field order) */
int axis_dpi_cfg_check(int inst)
{
    inst_t *s = get(inst);
    int i, mask = 0;
    if (!s || !s->sv_cfg.valid) return -1;
    if (!s->py_cfg.valid) return -2;
    for (i = 0; i < AXIS_CFG_N; i++)
        if (s->sv_cfg.v[i] != s->py_cfg.v[i]) mask |= 1 << i;
    return mask;
}

void axis_dpi_sv_cfg_get(int inst, int *out)
{
    inst_t *s = get(inst);
    int i;
    if (!s) return;
    for (i = 0; i < AXIS_CFG_N; i++) out[i] = s->sv_cfg.v[i];
}

/* --------------------------------------------------------------- master */
int axis_dpi_tx_push(int inst,
                     const unsigned char *data, int dn,
                     const unsigned char *keep, int kn,
                     const unsigned char *strb, int sn,
                     const unsigned char *user, int un,
                     int last, int id, int dest, int delay)
{
    inst_t *s = get(inst);
    unsigned int d[AXIS_VEC_WORDS], k[AXIS_VEC_WORDS];
    unsigned int st[AXIS_VEC_WORDS], u[AXIS_VEC_WORDS];
    svScope old;
    int rc;
    if (!s) return AXIS_E_INST;
    if (dn < 1 || !len_ok(dn) || !len_ok(kn) || !len_ok(sn) || !len_ok(un) ||
        !data || (kn && !keep) || (sn && !strb) || (un && !user))
        return AXIS_E_ARG;
    if (!s->scope) return AXIS_E_NOSHIM;
    if (!axis_mst_push) return AXIS_E_NOEXP;
    vec_put(d, data, dn);
    vec_put(k, keep, kn);
    vec_put(st, strb, sn);
    vec_put(u, user, un);
    old = svSetScope(s->scope);
    rc = axis_mst_push(d, k, st, u, last, id, dest, delay);
    if (old) svSetScope(old);
    return rc;
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
    inst_t *s = get(inst);
    unsigned int d[AXIS_VEC_WORDS], k[AXIS_VEC_WORDS];
    unsigned int st[AXIS_VEC_WORDS], u[AXIS_VEC_WORDS];
    svScope old;
    int rc;
    if (!s) return AXIS_E_INST;
    if (dn < 1 || !len_ok(dn) || !len_ok(kn) || !len_ok(sn) || !len_ok(un) ||
        !data || (kn && !keep) || (sn && !strb) || (un && !user))
        return AXIS_E_ARG;
    if (!s->scope) return AXIS_E_NOSHIM;
    if (!axis_slv_pop) return AXIS_E_NOEXP;
    old = svSetScope(s->scope);
    rc = axis_slv_pop(d, k, st, u, last, id, dest);
    if (old) svSetScope(old);
    if (rc == 1) {
        vec_get(d, data, dn);
        vec_get(k, keep, kn);
        vec_get(st, strb, sn);
        vec_get(u, user, un);
    }
    return rc;
}

/* modes: 0 none, 1 osc, 2 random, 3 stall */
int axis_dpi_bp_set(int inst, int mode, int low_min, int low_max,
                    int high_min, int high_max)
{
    inst_t *s = get(inst);
    svScope old;
    int rc;
    if (!s) return AXIS_E_INST;
    if (mode < 0 || mode > 3 || low_min < 0 || high_min < 0 ||
        low_max < low_min || high_max < high_min)
        return AXIS_E_ARG;
    if (!s->scope) return AXIS_E_NOSHIM;
    if (!axis_slv_set_bp) return AXIS_E_NOEXP;
    old = svSetScope(s->scope);
    rc = axis_slv_set_bp(mode, low_min, low_max, high_min, high_max);
    if (old) svSetScope(old);
    return rc;
}

/* ----------------------------------------------------------- both roles */
/* Drop everything queued in the shim (not what the VIP already has in flight). */
int axis_dpi_flush(int inst)
{
    inst_t *s = get(inst);
    svScope old;
    int rc;
    if (!s) return AXIS_E_INST;
    if (!s->scope || !s->sv_cfg.valid) return AXIS_E_NOSHIM;
    if (s->sv_cfg.v[0] == 0 ? !axis_mst_flush : !axis_slv_flush) return AXIS_E_NOEXP;
    old = svSetScope(s->scope);
    rc = (s->sv_cfg.v[0] == 0) ? axis_mst_flush() : axis_slv_flush();
    if (old) svSetScope(old);
    return rc;
}

#ifdef __cplusplus
}
#endif
