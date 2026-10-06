"""The rasteriser of the map window in C (raster.c, built into raster.dll by build.bat), called through ctypes.

`lib` is None when the DLL is missing or does not load (another platform, a 32-bit Python): the map then draws
with the Python code of tsmap/mapview.py, the same pixels (except the depth buffer of the 3D view), only slower.
ctypes releases the GIL during a call, so a frame drawn in a thread does not hold up the window.
"""
import array
import ctypes
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load():
    if os.name != 'nt' or os.environ.get('TSMAP_NO_NATIVE'):
        return None
    try:
        lib = ctypes.CDLL(os.path.join(_HERE, 'raster.dll'))
    except OSError:
        return None
    p, i, d = ctypes.c_void_p, ctypes.c_int, ctypes.c_double
    lib.render2d.argtypes = (p, p, i, d, d, d, d, i, d, i, i, p, p)
    lib.render2d.restype = None
    lib.render3d.argtypes = (p, p, p, i, p, p, p, i, p, i, d, d, p, i, i, p, p)
    lib.render3d.restype = i
    lib.floor_at.argtypes = (p, p, p, p, i, d, d, d, ctypes.POINTER(d))
    lib.floor_at.restype = i
    lib.shade.argtypes = (p, p, i, d, d, p, i, p)
    lib.shade.restype = None
    return lib


lib = _load()


def ptr(buf):
    """Address of a writable buffer (array.array, bytearray); the buffer must outlive the call."""
    return ctypes.addressof((ctypes.c_char * len(memoryview(buf).cast('B'))).from_buffer(buf))


def render2d(tris, colors, n, x0, z1, sx, sz, cut, w, h, rgb, hgt):
    lib.render2d(ptr(tris), ptr(colors), n, x0, z1, sx, sz, cut is not None, cut or 0.0, w, h, ptr(rgb), ptr(hgt))


def render3d(vx, vy, vz, faces, ylo, colors, cam, cut, fog, bg, w, h, rgb, ids):
    if lib.render3d(ptr(vx), ptr(vy), ptr(vz), len(vx), ptr(faces), ptr(ylo), ptr(colors), len(ylo), ptr(cam),
                    cut is not None, cut or 0.0, fog, ptr(bg), w, h, ptr(rgb), ptr(ids)) != 0:
        raise MemoryError('raster.dll: out of memory')


def shade(light, height, ymin, span, ramp):
    """Colours (bytearray, 3 bytes each) of heights on the ramp ((position, (r, g, b)), ...) times the light."""
    stops = array.array('d', [v for pos, rgb in ramp for v in (pos,) + tuple(rgb)])
    out = bytearray(3 * len(light))
    if light:
        lib.shade(ptr(light), ptr(height), len(light), ymin, span, ptr(stops), len(ramp), ptr(out))
    return out


def floor_at(vx, vy, vz, faces, x, z, below):
    out = ctypes.c_double()
    found = lib.floor_at(ptr(vx), ptr(vy), ptr(vz), ptr(faces), len(faces) // 3, x, z, below, ctypes.byref(out))
    return out.value if found else None
