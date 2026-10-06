/* Rasteriser of the map window (tsmap/mapview.py): the same drawing as the Python code there, about a hundred
 * times faster. Built into raster.dll by build.bat; without the DLL the Python code is used.
 *
 * The arithmetic follows the Python code step by step in double precision, so both give the same pixels.
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define EXPORT __declspec(dllexport)

/* one screen triangle: rgb gets the colour, buf (4-byte values: float heights or int face indices) val */
static void fill(unsigned char *rgb, unsigned int *buf, unsigned int val, int w, int h, const double *px,
                 const double *py, const unsigned char *col)
{
    double x0 = px[0], y0 = py[0], x1 = px[1], y1 = py[1], x2 = px[2], y2 = py[2], t;
    double r0d, r1d, dl = 0.0, da = 0.0, db = 0.0, xs0 = 0.0, xs2 = 0.0;
    int row, r0, r1, flat;

    /* sort the vertices by y, keeping the order of equal ones (as Python's sorted) */
    if (y1 < y0) { t = x0; x0 = x1; x1 = t; t = y0; y0 = y1; y1 = t; }
    if (y2 < y1) {
        t = x1; x1 = x2; x2 = t; t = y1; y1 = y2; y2 = t;
        if (y1 < y0) { t = x0; x0 = x1; x1 = t; t = y0; y0 = y1; y1 = t; }
    }
    r0d = floor(y0);
    r1d = floor(y2);
    if (r0d < 0) r0d = 0;
    if (r1d > h - 1) r1d = h - 1;
    if (r0d > r1d) return;
    r0 = (int)r0d;
    r1 = (int)r1d;
    flat = y2 == y0;
    if (flat) {                               /* completely flat: one row, the whole width */
        xs0 = x0 < x1 ? (x0 < x2 ? x0 : x2) : (x1 < x2 ? x1 : x2);
        xs2 = x0 > x1 ? (x0 > x2 ? x0 : x2) : (x1 > x2 ? x1 : x2);
    } else {
        dl = (x2 - x0) / (y2 - y0);
        da = y1 > y0 ? (x1 - x0) / (y1 - y0) : 0.0;
        db = y2 > y1 ? (x2 - x1) / (y2 - y1) : 0.0;
    }
    for (row = r0; row <= r1; row++) {
        double yc = row + 0.5, lo, hi;
        int c0, c1, n, k;
        unsigned char *p;
        unsigned int *b;

        if (yc < y0) yc = y0;
        else if (yc > y2) yc = y2;
        if (flat) {
            lo = xs0;
            hi = xs2;
        } else {
            lo = x0 + (yc - y0) * dl;
            if (yc < y1 || (yc == y1 && y1 > y0)) hi = x0 + (yc - y0) * da;
            else if (y2 > y1) hi = x1 + (yc - y1) * db;
            else hi = x1;
            if (lo > hi) { t = lo; lo = hi; hi = t; }
        }
        if (lo >= w || hi <= -1) continue;    /* int(lo) > w - 1 or int(hi) < 0 */
        c0 = lo > 0 ? (int)lo : 0;
        c1 = hi < w - 1 ? (int)hi : w - 1;
        if (c0 > c1) continue;
        n = c1 - c0 + 1;
        p = rgb + 3 * ((size_t)row * w + c0);
        b = buf + (size_t)row * w + c0;
        for (k = 0; k < n; k++) {
            p[0] = col[0]; p[1] = col[1]; p[2] = col[2];
            p += 3;
            b[k] = val;
        }
    }
}

/* LevelView.render: tris = n x (y min, x0, z0, x1, z1, x2, z2, y mean) from low to high, colors = n x 3 bytes */
EXPORT void render2d(const double *tris, const unsigned char *colors, int n, double x0, double z1, double sx,
                     double sz, int usecut, double cut, int w, int h, unsigned char *rgb, float *hgt)
{
    int i;
    for (i = 0; i < n; i++) {
        const double *t = tris + 8 * i;
        const unsigned char *col = colors + 3 * i;
        double px[3], py[3], lo, hi, top, bottom;
        union { float f; unsigned int u; } v;

        if (usecut && t[0] > cut) continue;
        px[0] = (t[1] - x0) * sx; px[1] = (t[3] - x0) * sx; px[2] = (t[5] - x0) * sx;
        py[0] = (z1 - t[2]) * sz; py[1] = (z1 - t[4]) * sz; py[2] = (z1 - t[6]) * sz;
        lo = fmin(fmin(px[0], px[1]), px[2]);
        hi = fmax(fmax(px[0], px[1]), px[2]);
        if (hi < 0 || lo >= w) continue;
        top = fmin(fmin(py[0], py[1]), py[2]);
        bottom = fmax(fmax(py[0], py[1]), py[2]);
        if (bottom < 0 || top >= h) continue;
        if (hi - lo < 1 && bottom - top < 1) {    /* smaller than a pixel: one pixel */
            if (0 <= lo && 0 <= top) {
                size_t j = (size_t)(int)top * w + (int)lo;
                memcpy(rgb + 3 * j, col, 3);
                hgt[j] = (float)t[7];
            }
            continue;
        }
        v.f = (float)t[7];
        fill(rgb, (unsigned int *)hgt, v.u, w, h, px, py, col);
    }
}

typedef struct { double depth; int i; } item_t;

static int by_depth(const void *a, const void *b)
{
    const item_t *p = a, *q = b;
    if (p->depth != q->depth) return p->depth < q->depth ? 1 : -1;     /* far first */
    return p->i < q->i ? 1 : (p->i > q->i ? -1 : 0);
}

/* LevelView.render3d: faces = nf x (a, b, c), ylo = y min of every face, colors = nf x 3 bytes,
 * cam = eye x y z, right x y z, up x y z, forward x y z, focal length; bg = background colour.
 * rgb must hold the background and ids -1. Returns 0, -1 when out of memory. */
EXPORT int render3d(const double *vx, const double *vy, const double *vz, int nv, const int *faces,
                    const double *ylo, const unsigned char *colors, int nf, const double *cam, int usecut,
                    double cut, double fog, const unsigned char *bg, int w, int h, unsigned char *rgb, int *ids)
{
    double ex = cam[0], ey = cam[1], ez = cam[2], rx = cam[3], rz = cam[5];
    double ux = cam[6], uy = cam[7], uz = cam[8], fx = cam[9], fy = cam[10], fz = cam[11], foc = cam[12];
    double cx = w / 2.0, cy = h / 2.0, near_ = 0.1;
    double *SX = malloc(sizeof(double) * (nv ? nv : 1)), *SY = malloc(sizeof(double) * (nv ? nv : 1));
    double *Z = malloc(sizeof(double) * (nv ? nv : 1));
    unsigned char *ok = malloc(nv ? nv : 1);
    item_t *todo = malloc(sizeof(item_t) * (nf ? nf : 1));
    int i, n = 0;

    if (!SX || !SY || !Z || !ok || !todo) {
        free(SX); free(SY); free(Z); free(ok); free(todo);
        return -1;
    }
    for (i = 0; i < nv; i++) {
        double x = vx[i], y = vy[i], z = vz[i];
        double X = (x - ex) * rx + (z - ez) * rz;
        double Y = (x - ex) * ux + (y - ey) * uy + (z - ez) * uz;
        double D = (x - ex) * fx + (y - ey) * fy + (z - ez) * fz;
        Z[i] = D;
        ok[i] = D > near_;
        if (ok[i]) {
            SX[i] = cx + X * foc / D;
            SY[i] = cy - Y * foc / D;
        }
    }
    for (i = 0; i < nf; i++) {
        int a = faces[3 * i], b = faces[3 * i + 1], c = faces[3 * i + 2];
        double ax, bx, qx, ay, by, qy;
        if (usecut && ylo[i] > cut) continue;
        if (!ok[a] || !ok[b] || !ok[c]) continue;
        ax = SX[a]; bx = SX[b]; qx = SX[c];
        ay = SY[a]; by = SY[b]; qy = SY[c];
        if ((bx - ax) * (qy - ay) - (by - ay) * (qx - ax) <= 0) continue;     /* back face */
        if (fmax(fmax(ax, bx), qx) < 0 || fmin(fmin(ax, bx), qx) >= w || fmax(fmax(ay, by), qy) < 0
            || fmin(fmin(ay, by), qy) >= h) continue;
        todo[n].depth = Z[a] + Z[b] + Z[c];
        todo[n].i = i;
        n++;
    }
    qsort(todo, n, sizeof(item_t), by_depth);
    for (i = 0; i < n; i++) {
        int f = todo[i].i, a = faces[3 * f], b = faces[3 * f + 1], c = faces[3 * f + 2], step, k;
        double px[3], py[3], kf, lo, hi, top, bottom;
        unsigned char col[3];

        step = (int)(todo[i].depth / 3 / fog / 0.7 * 16);    /* the fog in 16 steps, up to 70 % */
        if (step > 16) step = 16;
        kf = 0.7 * step / 16;
        for (k = 0; k < 3; k++) {
            double v = colors[3 * f + k];
            col[k] = (unsigned char)(int)(v + (bg[k] - v) * kf);
        }
        px[0] = SX[a]; px[1] = SX[b]; px[2] = SX[c];
        py[0] = SY[a]; py[1] = SY[b]; py[2] = SY[c];
        lo = fmin(fmin(px[0], px[1]), px[2]);
        hi = fmax(fmax(px[0], px[1]), px[2]);
        top = fmin(fmin(py[0], py[1]), py[2]);
        bottom = fmax(fmax(py[0], py[1]), py[2]);
        if (hi - lo < 1 && bottom - top < 1) {    /* smaller than a pixel: one pixel */
            if (0 <= lo && 0 <= top) {
                size_t j = (size_t)(int)top * w + (int)lo;
                memcpy(rgb + 3 * j, col, 3);
                ids[j] = f;
            }
            continue;
        }
        fill(rgb, (unsigned int *)ids, (unsigned int)f, w, h, px, py, col);
    }
    free(SX); free(SY); free(Z); free(ok); free(todo);
    return 0;
}

/* LevelView._tri_colors / face_colors: colour of relative height (height - ymin) / span on the ramp (stops
 * x 4 doubles: position, r, g, b) times the light, n x 3 bytes into out */
EXPORT void shade(const double *light, const double *height, int n, double ymin, double span, const double *ramp,
                  int stops, unsigned char *out)
{
    int i, s, k;
    for (i = 0; i < n; i++) {
        double t = (height[i] - ymin) / span, c[3];
        const double *last = ramp + 4 * (stops - 1);
        if (t > 1.0) t = 1.0;
        if (t < 0.0) t = 0.0;
        c[0] = last[1]; c[1] = last[2]; c[2] = last[3];
        for (s = 0; s + 1 < stops; s++) {
            const double *a = ramp + 4 * s, *b = a + 4;
            if (t <= b[0]) {
                double f = (t - a[0]) / (b[0] - a[0]);
                for (k = 0; k < 3; k++) c[k] = a[k + 1] + (b[k + 1] - a[k + 1]) * f;
                break;
            }
        }
        for (k = 0; k < 3; k++) {
            int v = (int)(c[k] * light[i]);
            out[3 * i + k] = (unsigned char)(v < 255 ? v : 255);
        }
    }
}

/* LevelView.floor_at: height of the highest surface at (x, z) not above `below`; returns 0 when there is none */
EXPORT int floor_at(const double *vx, const double *vy, const double *vz, const int *faces, int nf, double x,
                    double z, double below, double *out)
{
    int i, found = 0;
    double best = 0.0;
    for (i = 0; i < nf; i++) {
        int a = faces[3 * i], b = faces[3 * i + 1], c = faces[3 * i + 2];
        double x0 = vx[a], x1 = vx[b], x2 = vx[c], z0, z1, z2, den, l0, l1, y;
        if ((x < x0 && x < x1 && x < x2) || (x > x0 && x > x1 && x > x2)) continue;
        z0 = vz[a]; z1 = vz[b]; z2 = vz[c];
        if ((z < z0 && z < z1 && z < z2) || (z > z0 && z > z1 && z > z2)) continue;
        den = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2);
        if (fabs(den) < 1e-9) continue;       /* a vertical face */
        l0 = ((z1 - z2) * (x - x2) + (x2 - x1) * (z - z2)) / den;
        l1 = ((z2 - z0) * (x - x2) + (x0 - x2) * (z - z2)) / den;
        if (l0 < -1e-6 || l1 < -1e-6 || l0 + l1 > 1 + 1e-6) continue;
        y = l0 * vy[a] + l1 * vy[b] + (1 - l0 - l1) * vy[c];
        if (y <= below && (!found || y > best)) {
            best = y;
            found = 1;
        }
    }
    *out = best;
    return found;
}
