"""Sign-invariant quaternion calculations shared by Blender tooling and tests.
Quaternions use Blender order: (w, x, y, z).
"""
import math


def normalize(q):
    norm = math.sqrt(sum(float(v) ** 2 for v in q))
    if norm <= 1e-15:
        raise ValueError("zero-length quaternion")
    return tuple(float(v) / norm for v in q)


def dot(a, b):
    return sum(x * y for x, y in zip(normalize(a), normalize(b)))


def angular_distance(a, b):
    """Shortest angular distance in radians; q and -q represent the same pose."""
    cosine = min(1.0, max(-1.0, abs(dot(a, b))))
    return 2.0 * math.acos(cosine)


def conjugate(q):
    w, x, y, z = normalize(q)
    return (w, -x, -y, -z)


def multiply(a, b):
    aw, ax, ay, az = normalize(a)
    bw, bx, by, bz = normalize(b)
    return normalize((aw*bw-ax*bx-ay*by-az*bz,
                      aw*bx+ax*bw+ay*bz-az*by,
                      aw*by-ax*bz+ay*bw+az*bx,
                      aw*bz+ax*by-ay*bx+az*bw))


def relative(a, b):
    """Orientation of b relative to a."""
    return multiply(conjugate(a), b)


def pearson(a, b):
    if len(a) != len(b) or len(a) < 2:
        return None
    ma, mb = sum(a)/len(a), sum(b)/len(b)
    da, db = [x-ma for x in a], [x-mb for x in b]
    denom = math.sqrt(sum(x*x for x in da) * sum(x*x for x in db))
    return sum(x*y for x, y in zip(da, db))/denom if denom > 1e-15 else None
