"""Statistics behind the built-in evaluators, in pure Python.

Why pure Python: the Platform image carries no numpy/scipy (only the sandboxed
experiments-runner does), yet the built-in evaluators must run in-process, on
every new batch of measurements, for every experiment. So the special
functions are implemented here from their textbook definitions:

- incomplete beta: Lentz's continued fraction (Numerical Recipes ``betacf``)
  with the prefactor x^a (1-x)^b / B(a, b) computed through Stirling-series
  corrections, so large parameters (a t test with a million observations, a
  binomial test on ten million impressions) do not cancel lgamma values of
  1e8 against each other. Student's t with large df uses the large-a limit
  near the centre (see _t_tails);
- incomplete gamma: series / continued fraction, Temme's uniform asymptotic
  expansion for shape parameters beyond ten million;
- quantiles by safeguarded Newton iteration on the logarithm of the smaller
  tail, solving for whichever of x and 1 - x is small at the root, so both
  tails keep their relative precision;
- the normal quantile by Wichura's AS 241 (PPND16).

Measured against scipy and mpmath: relative error below 1e-10 for the t,
normal, chi-square and gamma functions over the whole tested range; the
incomplete beta loses about (large parameter) x 1e-16 when one parameter is
small (below ~10) and the other above 1e6 (1e-9 at 1e7), the conditioning of
the continued fraction there. p-values are never read at that precision.

Every public function is deterministic (the Monte Carlo in
``bayes_beta_binomial`` uses a fixed seed). Test functions return plain dicts
of floats and ints; a quantity that is undefined for the data (a variance from
one observation, a ratio over zero) is ``None``, never NaN or infinity, so the
result can go into JSON and PostgreSQL as it is.

Arguments that make no sense (negative counts, successes above trials, alpha
outside (0, 1), NaN) raise ``StatsInputError``: a ValidationError with a German
message, and a ValueError for callers that treat it as one.
"""

from __future__ import annotations

import math
import random
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from experiments.errors import ValidationError

__all__ = [
    "StatsInputError",
    "normal_cdf", "normal_ppf", "student_t_cdf", "student_t_ppf",
    "chi2_sf", "chi2_ppf", "betainc", "beta_ppf", "gammaincc", "binom_cdf",
    "wilson_interval", "poisson_interval", "t_interval",
    "two_proportion_test", "bayes_beta_binomial", "welch_t_test",
    "paired_t_test", "poisson_rate_test", "ratio_delta",
    "chi_square_independence", "chi_square_goodness_of_fit",
    "binomial_test", "holm", "sample_size_proportion", "sample_size_mean",
    "variance",
]


class StatsInputError(ValidationError, ValueError):
    """An argument a caller should have refused before calling in here."""


_TINY = 1e-300
_LOG_SQRT_2PI = 0.5 * math.log(2.0 * math.pi)
_SQRT2 = math.sqrt(2.0)
_INF = float("inf")

#: From this many degrees of freedom on, t tails near the centre come from the
#: large-a limit of the incomplete beta (see _t_tails): the continued fraction
#: evaluated next to x = 1 loses digits in proportion to df there.
_T_ASYMPTOTIC_DF = 1e5
#: Shape parameters above this use Temme's uniform asymptotic expansion for
#: the incomplete gamma (relative error about 1e-10); below it the series and
#: continued fraction converge in a few thousand steps.
_GAMMA_ASYMPTOTIC_A = 1e7


# -- argument checks -----------------------------------------------------------


def _real(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatsInputError(f"{name} muss eine Zahl sein.")
    value = float(value)
    if math.isnan(value):
        raise StatsInputError(f"{name} muss eine Zahl sein.")
    return value


def _finite(value, name: str) -> float:
    value = _real(value, name)
    if not math.isfinite(value):
        raise StatsInputError(f"{name} muss eine endliche Zahl sein.")
    return value


def _positive(value, name: str) -> float:
    value = _real(value, name)
    if not value > 0:
        raise StatsInputError(f"{name} muss gr\u00f6sser als 0 sein.")
    return value


def _nonneg(value, name: str) -> float:
    value = _finite(value, name)
    if value < 0:
        raise StatsInputError(f"{name} darf nicht negativ sein.")
    return value


def _probability(value, name: str) -> float:
    value = _finite(value, name)
    if not 0.0 < value < 1.0:
        raise StatsInputError(f"{name} muss zwischen 0 und 1 liegen.")
    return value


def _alpha(value) -> float:
    return _probability(value, "alpha")


def _clean(result: Dict[str, object]) -> Dict[str, object]:
    """Non-finite floats become None (the module's promise to its callers)."""
    for key, value in result.items():
        if isinstance(value, float) and not math.isfinite(value):
            result[key] = None
    return result


# -- normal distribution -------------------------------------------------------


def normal_cdf(x) -> float:
    """P(Z <= x) for a standard normal Z, via erfc (precise in both tails)."""
    x = _real(x, "x")
    return 0.5 * math.erfc(-x / _SQRT2)


def _normal_sf(x: float) -> float:
    return 0.5 * math.erfc(x / _SQRT2)


def _poly(coefficients: Sequence[float], r: float) -> float:
    total = 0.0
    for c in coefficients:
        total = total * r + c
    return total


# Wichura, Algorithm AS 241 (PPND16), Applied Statistics 37 (1988) 477-484.
# Coefficients listed from the highest power down, for Horner's scheme.
_AS241_A = (2.5090809287301226727e3, 3.3430575583588128105e4, 6.7265770927008700853e4,
            4.5921953931549871457e4, 1.3731693765509461125e4, 1.9715909503065514427e3,
            1.3314166789178437745e2, 3.3871328727963666080e0)
_AS241_B = (5.2264952788528545610e3, 2.8729085735721942674e4, 3.9307895800092710610e4,
            2.1213794301586595867e4, 5.3941960214247511077e3, 6.8718700749205790830e2,
            4.2313330701600911252e1, 1.0)
_AS241_C = (7.74545014278341407640e-4, 2.27238449892691845833e-2, 2.41780725177450611770e-1,
            1.27045825245236838258e0, 3.64784832476320460504e0, 5.76949722146069140550e0,
            4.63033784615654529590e0, 1.42343711074968357734e0)
_AS241_D = (1.05075007164441684324e-9, 5.47593808499534494600e-4, 1.51986665636164571966e-2,
            1.48103976427480074590e-1, 6.89767334985100004550e-1, 1.67638483018380384940e0,
            2.05319162663775882187e0, 1.0)
_AS241_E = (2.01033439929228813265e-7, 2.71155556874348757815e-5, 1.24266094738807843860e-3,
            2.65321895265761230930e-2, 2.96560571828504891230e-1, 1.78482653991729133580e0,
            5.46378491116411436990e0, 6.65790464350110377720e0)
_AS241_F = (2.04426310338993978564e-15, 1.42151175831644588870e-7, 1.84631831751005468180e-5,
            7.86869131145613259100e-4, 1.48753612908506148525e-2, 1.36929880922735805310e-1,
            5.99832206555887937690e-1, 1.0)


def normal_ppf(p) -> float:
    """The standard normal quantile, Wichura's AS 241 (relative error ~1e-16)."""
    p = _probability(p, "p")
    q = p - 0.5
    if abs(q) <= 0.425:
        r = 0.180625 - q * q
        return q * _poly(_AS241_A, r) / _poly(_AS241_B, r)
    r = p if q < 0 else 1.0 - p
    r = math.sqrt(-math.log(r))
    if r <= 5.0:
        r -= 1.6
        value = _poly(_AS241_C, r) / _poly(_AS241_D, r)
    else:
        r -= 5.0
        value = _poly(_AS241_E, r) / _poly(_AS241_F, r)
    return -value if q < 0 else value


def _z(level_tail: float) -> float:
    """The normal quantile for an upper tail probability, e.g. 0.025 -> 1.96."""
    return -normal_ppf(level_tail)


# -- log-gamma helpers ---------------------------------------------------------


def _stirling_correction(z: float) -> float:
    """lgamma(z) minus Stirling's leading terms: (z-1/2)log z - z + log sqrt(2 pi).

    Separating it lets the large terms of Gamma ratios cancel analytically
    instead of numerically (lgamma(1e9) is 2e10; its rounding error alone
    would be 1e-6).
    """
    if z >= 10.0:
        zi = 1.0 / z
        z2 = zi * zi
        return zi * (1.0 / 12.0 + z2 * (-1.0 / 360.0 + z2 * (1.0 / 1260.0 + z2 * (
            -1.0 / 1680.0 + z2 * (1.0 / 1188.0 + z2 * (-691.0 / 360360.0 + z2 / 156.0))))))
    return math.lgamma(z) - ((z - 0.5) * math.log(z) - z + _LOG_SQRT_2PI)


def _log1pmx(u: float) -> float:
    """log(1 + u) - u without cancellation for small u."""
    if abs(u) > 0.1:
        return math.log1p(u) - u
    # -u^2/2 + u^3/3 - u^4/4 + ...
    total = 0.0
    power = u
    for k in range(2, 60):
        power *= -u
        delta = power / k
        total += delta
        if abs(delta) <= 1e-17 * abs(total):
            break
    return total


def _log_beta_power(a: float, b: float, x: float, y: float) -> float:
    """log(x^a * y^b / B(a, b)) with x + y = 1 given separately (both precise)."""
    c = a + b
    ua = (x * b - y * a) / a          # x*c/a - 1
    ub = (y * a - x * b) / b          # y*c/b - 1
    if abs(ua) < 0.5 and abs(ub) < 0.5:
        # a*ua + b*ub == 0, so only the log1p(u) - u parts remain.
        main = a * _log1pmx(ua) + b * _log1pmx(ub)
    else:
        # log of whichever of x, y is near 1 through log1p of the other (both
        # are exact inputs; 1 - x rounded would cost digits in b*log(y)).
        log_x = math.log(x) if x < 0.5 else math.log1p(-y)
        log_y = math.log(y) if y < 0.5 else math.log1p(-x)
        main = a * (log_x + math.log1p(b / a)) + b * (log_y + math.log1p(a / b))
    return (main + 0.5 * (math.log(a) + math.log(b) - math.log(c)) - _LOG_SQRT_2PI
            + _stirling_correction(c) - _stirling_correction(a) - _stirling_correction(b))


def _log_gamma_power(a: float, x: float) -> float:
    """log(x^a * e^-x / Gamma(a))."""
    u = (x - a) / a
    if abs(u) < 0.5:
        main = a * _log1pmx(u)
    else:
        main = a * (math.log(x) - math.log(a)) - (x - a)
    return main + 0.5 * math.log(a) - _LOG_SQRT_2PI - _stirling_correction(a)


class _NoConvergence(ArithmeticError):
    """A series or continued fraction did not settle within its step budget
    (only for parameters far beyond any experiment, around 1e16 and up)."""


#: Hard ceiling on the steps of any series or continued fraction: about a
#: third of a second, enough for parameters up to ~1e15 at their worst point.
_MAX_STEPS = 300_000


def _steps(a: float) -> int:
    return int(min(_MAX_STEPS, 200 + 40.0 * math.sqrt(a)))


# -- incomplete beta -----------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for I_x(a, b), modified Lentz (Numerical Recipes 6.4)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    h = d
    # At most O(sqrt(max(a, b))) steps (x at the mean); measured: 45'000 at 1e12.
    for m in range(1, _steps(max(a, b)) + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + aa / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + aa / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            return h
    raise _NoConvergence("incomplete beta continued fraction")


def _ibeta(a: float, b: float, x: float, y: float) -> Tuple[float, float]:
    """(I_x(a, b), 1 - I_x(a, b)); whichever is small is computed directly."""
    if x <= 0.0:
        return 0.0, 1.0
    if y <= 0.0:
        return 1.0, 0.0
    front = math.exp(_log_beta_power(a, b, x, y))
    lower_side = x * (a + b + 2.0) < a + 1.0
    if front == 0.0:
        # Beyond the double range in the far tail: no fraction to evaluate.
        return (0.0, 1.0) if lower_side else (1.0, 0.0)
    if lower_side:
        w = min(1.0, front * _betacf(a, b, x) / a)
        return w, 1.0 - w
    w = min(1.0, front * _betacf(b, a, y) / b)
    return 1.0 - w, w


def betainc(a, b, x) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    a = _positive(a, "a")
    b = _positive(b, "b")
    x = _finite(x, "x")
    if not 0.0 <= x <= 1.0:
        raise StatsInputError("x muss zwischen 0 und 1 liegen.")
    if not (math.isfinite(a) and math.isfinite(b)):
        raise StatsInputError("a und b m\u00fcssen endlich sein.")
    return _ibeta(a, b, x, 1.0 - x)[0]


def _ibeta_guess(a: float, b: float, p: float, q: float) -> Tuple[float, float]:
    """Starting point (x, 1 - x) for inverting I_x(a, b) (Numerical Recipes
    ``invbetai``); both computed directly, so a root next to 1 is not lost."""
    if a >= 1.0 and b >= 1.0:
        pp = p if p < 0.5 else q
        t = math.sqrt(-2.0 * math.log(pp))
        x = (2.30753 + t * 0.27061) / (1.0 + t * (0.99229 + t * 0.04481)) - t
        if p < 0.5:
            x = -x
        al = (x * x - 3.0) / 6.0
        h = 2.0 / (1.0 / (2.0 * a - 1.0) + 1.0 / (2.0 * b - 1.0))
        w = (x * math.sqrt(al + h) / h
             - (1.0 / (2.0 * b - 1.0) - 1.0 / (2.0 * a - 1.0)) * (al + 5.0 / 6.0 - 2.0 / (3.0 * h)))
        w2 = min(700.0, max(-700.0, 2.0 * w))
        ratio = b * math.exp(w2)
        return a / (a + ratio), ratio / (a + ratio)
    lna = math.log(a / (a + b))
    lnb = math.log(b / (a + b))
    t = math.exp(a * lna) / a
    u = math.exp(b * lnb) / b
    w = t + u
    if p < t / w:
        x = (a * w * p) ** (1.0 / a)
        return x, 1.0 - x
    y = (b * w * q) ** (1.0 / b)
    return 1.0 - y, y


def _bisect(lo: float, hi: float) -> float:
    """A bisection point for a bracket in (0, inf); geometric when it spans decades."""
    if hi == _INF:
        return max(lo * 4.0, 1.0)
    if lo <= 0.0:
        return hi / 64.0
    if hi > 4.0 * lo:
        return math.sqrt(lo) * math.sqrt(hi)
    return 0.5 * (lo + hi)


def _ibeta_inv(a: float, b: float, p: float, q: float) -> Tuple[float, float]:
    """(x, 1 - x) with I_x(a, b) = p, where q = 1 - p is given precisely.

    Newton on log(tail) - log(target) for the smaller tail, in the variable
    (x or 1 - x) that is small at the root, inside a shrinking bracket.
    For log-concave densities (a, b >= 1) log-Newton approaches the root
    monotonically after at most one step; the bracket covers the rest.
    """
    if p <= 0.0:
        return 0.0, 1.0
    if q <= 0.0:
        return 1.0, 0.0
    x0, y0 = _ibeta_guess(a, b, p, q)
    swap = x0 > y0
    lower = p <= q
    target = p if lower else q
    log_target = math.log(target)
    increasing = lower != swap          # does the chosen tail grow with v?
    v = y0 if swap else x0
    if not 0.0 < v < 1.0:
        v = min(max(v, 1e-300), 0.5) if math.isfinite(v) else 0.5
    lo, hi = 0.0, 1.0
    for _ in range(300):
        x, y = (1.0 - v, v) if swap else (v, 1.0 - v)
        i, j = _ibeta(a, b, x, y)
        tail = i if lower else j
        if tail == target:
            break
        if (tail < target) == increasing:
            lo = v
        else:
            hi = v
        new_v = None
        if tail > 0.0:
            density = math.exp(_log_beta_power(a, b, x, y)) / (x * y)
            if density > 0.0 and math.isfinite(density):
                slope = (density if increasing else -density) / tail
                new_v = v - (math.log(tail) - log_target) / slope
        if new_v is None or not lo < new_v < hi:
            new_v = _bisect(lo, hi)
        if abs(new_v - v) <= 1e-15 * new_v or hi - lo <= 1e-15 * hi:
            v = new_v
            break
        v = new_v
    return (1.0 - v, v) if swap else (v, 1.0 - v)


# -- incomplete gamma ----------------------------------------------------------


def _gamma_series(a: float, x: float) -> float:
    """P(a, x) by its power series (x < a + 1)."""
    ap = a
    term = 1.0 / a
    total = term
    for _ in range(_steps(a)):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-16:
            return total * math.exp(_log_gamma_power(a, x))
    raise _NoConvergence("incomplete gamma series")


def _gamma_cf(a: float, x: float) -> float:
    """Q(a, x) by its continued fraction (x >= a + 1), modified Lentz."""
    b = x + 1.0 - a
    c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    for i in range(1, _steps(a) + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        c = b + an / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            return h * math.exp(_log_gamma_power(a, x))
    raise _NoConvergence("incomplete gamma continued fraction")


def _gamma_temme(a: float, x: float) -> Tuple[float, float]:
    """(P, Q) by Temme's uniform asymptotic expansion, first correction term.

    Q = erfc(eta sqrt(a/2))/2 + exp(-a eta^2/2)/sqrt(2 pi a) * C0(eta), with
    lambda = x/a, eta = sign(lambda-1) sqrt(2(lambda - 1 - log lambda)) and
    C0 = 1/(lambda-1) - 1/eta. The next term is O(1/a) smaller.
    """
    u = (x - a) / a
    half_eta2 = -_log1pmx(u) if u > -1.0 else _INF
    eta = math.copysign(math.sqrt(2.0 * half_eta2), u)
    if abs(eta) < 1e-3:
        c0 = -1.0 / 3.0 + eta / 12.0 - 2.0 * eta * eta / 135.0
    else:
        c0 = 1.0 / u - 1.0 / eta
    correction = math.exp(-a * half_eta2) / math.sqrt(2.0 * math.pi * a) * c0
    z = eta * math.sqrt(a / 2.0)
    q = 0.5 * math.erfc(z) + correction
    p = 0.5 * math.erfc(-z) - correction
    return min(1.0, max(0.0, p)), min(1.0, max(0.0, q))


def _gamma_pq(a: float, x: float) -> Tuple[float, float]:
    """(P(a, x), Q(a, x)), regularized lower and upper incomplete gamma."""
    if x <= 0.0:
        return 0.0, 1.0
    if x == _INF:
        return 1.0, 0.0
    if a > _GAMMA_ASYMPTOTIC_A:
        return _gamma_temme(a, x)
    if x < a + 1.0:
        p = min(1.0, _gamma_series(a, x))
        return p, 1.0 - p
    q = min(1.0, _gamma_cf(a, x))
    return 1.0 - q, q


def gammaincc(s, x) -> float:
    """The regularized upper incomplete gamma function Q(s, x)."""
    s = _positive(s, "s")
    x = _real(x, "x")
    if not math.isfinite(s):
        raise StatsInputError("s muss endlich sein.")
    if x < 0:
        raise StatsInputError("x darf nicht negativ sein.")
    return _gamma_pq(s, x)[1]


def _gamma_guess(a: float, p: float, q: float) -> float:
    """Starting point for inverting P(a, x) (Numerical Recipes ``invgammp``)."""
    if a > 1.0:
        pp = p if p < 0.5 else q
        t = math.sqrt(-2.0 * math.log(pp))
        x = (2.30753 + t * 0.27061) / (1.0 + t * (0.99229 + t * 0.04481)) - t
        if p < 0.5:
            x = -x
        return max(1e-3, a * (1.0 - 1.0 / (9.0 * a) - x / (3.0 * math.sqrt(a))) ** 3)
    t = 1.0 - a * (0.253 + a * 0.12)
    if p < t:
        return (p / t) ** (1.0 / a)
    return 1.0 - math.log(q / (1.0 - t)) if q > 0 else 1.0


def _gamma_inv(a: float, p: float, q: float) -> float:
    """x with P(a, x) = p, where q = 1 - p is given precisely (log-Newton, bracketed)."""
    if p <= 0.0:
        return 0.0
    lower = p <= q
    target = p if lower else q
    log_target = math.log(target)
    x = _gamma_guess(a, p, q)
    if not 0.0 < x < _INF:
        x = a
    lo, hi = 0.0, _INF
    for _ in range(300):
        pp, qq = _gamma_pq(a, x)
        tail = pp if lower else qq
        if tail == target:
            break
        # P grows with x, Q falls.
        if (tail < target) == lower:
            lo = x
        else:
            hi = x
        new_x = None
        if tail > 0.0:
            density = math.exp(_log_gamma_power(a, x)) / x
            if density > 0.0 and math.isfinite(density):
                slope = (density if lower else -density) / tail
                new_x = x - (math.log(tail) - log_target) / slope
        if new_x is None or not lo < new_x < hi:
            new_x = _bisect(lo, hi)
        if abs(new_x - x) <= 1e-15 * new_x or (hi < _INF and hi - lo <= 1e-15 * hi):
            x = new_x
            break
        x = new_x
    return x


# -- chi-square and t ----------------------------------------------------------


def chi2_sf(x, df) -> float:
    """P(X > x) for a chi-square variable with ``df`` degrees of freedom."""
    df = _positive(df, "df")
    x = _real(x, "x")
    if not math.isfinite(df):
        raise StatsInputError("df muss endlich sein.")
    if x <= 0.0:
        return 1.0
    return _gamma_pq(df / 2.0, x / 2.0)[1]


def chi2_ppf(p, df) -> float:
    """The chi-square quantile, by inverting the incomplete gamma function."""
    p = _probability(p, "p")
    df = _positive(df, "df")
    if not math.isfinite(df):
        raise StatsInputError("df muss endlich sein.")
    return 2.0 * _gamma_inv(df / 2.0, p, 1.0 - p)


def _t_tails(t: float, df: float) -> Tuple[float, float]:
    """(P(T <= t), P(T > t)) for Student's t with df degrees of freedom.

    Incomplete beta: P(T > |t|) = I_x(df/2, 1/2) / 2 with x = df / (df + t^2).
    For large df and moderate t the large-a limit of the incomplete beta
    (DiDonato and Morris, BGRAT's leading term) is used instead:
    P(T > |t|) = erfc(sqrt(u)) / 2 with u = (df/2 - 1/4) log(1 + t^2/df); its
    relative error is about 0.02 t^4 / df^2, so it is taken where that stays
    below 1e-11 and the continued fraction everywhere else.
    """
    if t == 0.0:
        return 0.5, 0.5
    if math.isinf(t):
        return (1.0, 0.0) if t > 0 else (0.0, 1.0)
    if math.isinf(df):
        return 0.5 * math.erfc(-t / _SQRT2), 0.5 * math.erfc(t / _SQRT2)
    t2 = t * t
    if df >= _T_ASYMPTOTIC_DF and t2 * t2 < 5e-10 * df * df:
        tail = 0.5 * math.erfc(math.sqrt((df / 2.0 - 0.25) * math.log1p(t2 / df)))
        rest = 1.0 - tail
        return (rest, tail) if t > 0 else (tail, rest)
    if t2 > df:
        r = df / t2
        x, y = r / (1.0 + r), 1.0 / (1.0 + r)
    else:
        r = t2 / df
        x, y = 1.0 / (1.0 + r), r / (1.0 + r)
    i, j = _ibeta(df / 2.0, 0.5, x, y)
    tail = 0.5 * i                      # P(T > |t|)
    rest = 0.5 + 0.5 * j                # P(T <= |t|)
    return (rest, tail) if t > 0 else (tail, rest)


def student_t_cdf(t, df) -> float:
    """P(T <= t) for Student's t, via the incomplete beta function.

    df may be fractional (Welch). Beyond 1e7 degrees of freedom the normal
    distribution is used.
    """
    t = _real(t, "t")
    df = _positive(df, "df")
    return _t_tails(t, df)[0]


def _t_log_pdf(t: float, df: float) -> float:
    # log Gamma((df+1)/2) - log Gamma(df/2), with Stirling corrections so that
    # huge df does not cancel lgamma values of 1e11 against each other.
    a = df / 2.0
    log_ratio = ((a - 0.5) * math.log1p(0.5 / a) + 0.5 * math.log(a + 0.5) - 0.5
                 + _stirling_correction(a + 0.5) - _stirling_correction(a))
    return log_ratio - 0.5 * math.log(df * math.pi) - (df + 1.0) / 2.0 * math.log1p(t * t / df)


def _t_sf(t: float, df: float) -> float:
    return _t_tails(t, df)[1]


def _t_two_sided(t: float, df: float) -> float:
    return min(1.0, 2.0 * _t_sf(abs(t), df))


def student_t_ppf(p, df) -> float:
    """The quantile of Student's t: closed forms for df 1 and 2, else by
    inverting the incomplete beta function (tail probability 2q = I_x(df/2, 1/2),
    t = sqrt(df (1-x)/x))."""
    p = _probability(p, "p")
    df = _positive(df, "df")
    if math.isinf(df):
        return normal_ppf(p)
    if p == 0.5:
        return 0.0
    q = p if p < 0.5 else 1.0 - p       # the smaller tail, exact
    if df == 1.0:
        magnitude = 1.0 / math.tan(math.pi * q)
    elif df == 2.0:
        magnitude = (1.0 - 2.0 * q) / math.sqrt(2.0 * q * (1.0 - q))
    elif df >= _T_ASYMPTOTIC_DF:
        # Invert the large-df form of _t_tails in closed form, then polish with
        # Newton steps on the exact tail.
        z = -normal_ppf(q)
        magnitude = math.sqrt(df * math.expm1(z * z / (df - 0.5)))
        for _ in range(4):
            tail = _t_tails(magnitude, df)[1]
            step = (tail - q) / math.exp(_t_log_pdf(magnitude, df))
            magnitude += step
            if abs(step) <= 1e-15 * magnitude:
                break
    else:
        x, y = _ibeta_inv(df / 2.0, 0.5, 2.0 * q, 2.0 * (0.5 - q))
        magnitude = math.sqrt(df * y / x) if x > 0.0 else _INF
    return -magnitude if p < 0.5 else magnitude


def _t_crit(alpha: float, df: float) -> float:
    return student_t_ppf(1.0 - alpha / 2.0, df) if alpha / 2.0 >= 1e-300 else _INF


# -- binomial ------------------------------------------------------------------


def _count(value, name: str) -> int:
    value = _nonneg(value, name)
    return int(math.floor(value + 0.5))


def beta_ppf(p, a, b) -> float:
    """The quantile of the Beta(a, b) distribution (inverse incomplete beta)."""
    p = _probability(p, "p")
    a = _positive(a, "a")
    b = _positive(b, "b")
    if not (math.isfinite(a) and math.isfinite(b)):
        raise StatsInputError("a und b m\u00fcssen endlich sein.")
    return _ibeta_inv(a, b, p, 1.0 - p)[0]


def binom_cdf(k, n, p) -> float:
    """P(X <= k) for X ~ Binomial(n, p), as I_{1-p}(n - k, k + 1)."""
    n = _count(n, "n")
    p = _finite(p, "p")
    if not 0.0 <= p <= 1.0:
        raise StatsInputError("p muss zwischen 0 und 1 liegen.")
    k = math.floor(_finite(k, "k"))
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p == 0.0:
        return 1.0
    if p == 1.0:
        return 0.0
    return _ibeta(n - k, k + 1.0, 1.0 - p, p)[0]


def _binom_sf(k: int, n: int, p: float) -> float:
    """P(X > k)."""
    if k < 0:
        return 1.0
    if k >= n:
        return 0.0
    return _ibeta(n - k, k + 1.0, 1.0 - p, p)[1]


def _binom_logpmf(k: int, n: int, p: float) -> float:
    return (math.lgamma(n + 1.0) - math.lgamma(k + 1.0) - math.lgamma(n - k + 1.0)
            + k * math.log(p) + (n - k) * math.log1p(-p))


def binomial_test(k, n, p=0.5) -> Dict[str, object]:
    """Two-sided exact binomial test: the probability of every outcome no more
    likely than the observed one (the rule scipy.stats.binomtest uses, with the
    same 1e-7 relative tolerance for ties)."""
    n = _count(n, "n")
    k = _count(k, "k")
    p = _finite(p, "p")
    if not 0.0 <= p <= 1.0:
        raise StatsInputError("p muss zwischen 0 und 1 liegen.")
    if k > n:
        raise StatsInputError("k darf nicht gr\u00f6sser als n sein.")
    if n == 0:
        return {"p_value": 1.0}
    if p == 0.0:
        return {"p_value": 1.0 if k == 0 else 0.0}
    if p == 1.0:
        return {"p_value": 1.0 if k == n else 0.0}
    mean = p * n
    if k == mean:
        return {"p_value": 1.0}
    threshold = _binom_logpmf(k, n, p) + math.log1p(1e-7)
    if k < mean:
        # Upper side: the pmf falls on [ceil(np), n]; find the first index whose
        # pmf is at most the observed one.
        lo, hi = int(math.ceil(mean)), n + 1
        while lo < hi:
            mid = (lo + hi) // 2
            if _binom_logpmf(mid, n, p) <= threshold:
                hi = mid
            else:
                lo = mid + 1
        pval = binom_cdf(k, n, p) + (_binom_sf(lo - 1, n, p) if lo <= n else 0.0)
    else:
        # Lower side: the pmf rises on [0, floor(np)]; find the last index whose
        # pmf is at most the observed one.
        lo, hi = -1, int(math.floor(mean))
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _binom_logpmf(mid, n, p) <= threshold:
                lo = mid
            else:
                hi = mid - 1
        pval = (binom_cdf(lo, n, p) if lo >= 0 else 0.0) + _binom_sf(k - 1, n, p)
    return {"p_value": min(1.0, pval)}


def _central_binomial_p(k: int, n: int, p: float) -> float:
    """Two-sided exact p-value by doubling the smaller tail,
    2 * min(P(X <= k), P(X >= k)), capped at 1: the test whose acceptance
    region is the Clopper-Pearson interval."""
    lower = binom_cdf(k, n, p)
    upper = _binom_sf(k - 1, n, p)
    return min(1.0, 2.0 * min(lower, upper))


def _clopper_pearson(k: int, n: int, alpha: float) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Exact binomial CI as ((lo, 1 - lo), (hi, 1 - hi)) for k successes of n."""
    lo = (0.0, 1.0) if k == 0 else _ibeta_inv(k, n - k + 1.0, alpha / 2.0, 1.0 - alpha / 2.0)
    hi = (1.0, 0.0) if k == n else _ibeta_inv(k + 1.0, n - k, 1.0 - alpha / 2.0, alpha / 2.0)
    return lo, hi


# -- intervals -----------------------------------------------------------------


def wilson_interval(s, n, alpha=0.05) -> Tuple[Optional[float], Optional[float]]:
    """Wilson score interval for a proportion s/n; (None, None) when n = 0."""
    alpha = _alpha(alpha)
    s = _nonneg(s, "Erfolge")
    n = _nonneg(n, "Versuche")
    if s > n:
        raise StatsInputError("Es kann nicht mehr Erfolge als Versuche geben.")
    if n == 0:
        return None, None
    z = _z(alpha / 2.0)
    phat = s / n
    z2n = z * z / n
    denom = 1.0 + z2n
    center = (phat + z2n / 2.0) / denom
    half = z * math.sqrt(phat * (1.0 - phat) / n + z2n / (4.0 * n)) / denom
    lo = 0.0 if s == 0 else max(0.0, center - half)
    hi = 1.0 if s == n else min(1.0, center + half)
    return lo, hi


def poisson_interval(k, t, alpha=0.05) -> Tuple[Optional[float], Optional[float]]:
    """Exact (Garwood) interval for the rate k/t of a Poisson count k over
    exposure t: chi-square quantiles chi2(alpha/2; 2k)/2t and
    chi2(1 - alpha/2; 2k + 2)/2t, i.e. gamma quantiles. (None, None) when t = 0."""
    alpha = _alpha(alpha)
    k = _nonneg(k, "Ereignisse")
    t = _nonneg(t, "Exposition")
    if t == 0:
        return None, None
    lo = 0.0 if k == 0 else _gamma_inv(k, alpha / 2.0, 1.0 - alpha / 2.0) / t
    hi = _gamma_inv(k + 1.0, 1.0 - alpha / 2.0, alpha / 2.0) / t
    return lo, hi


def t_interval(mean, var, n, alpha=0.05) -> Tuple[Optional[float], Optional[float]]:
    """mean +- t(1 - alpha/2; n - 1) * sqrt(var / n); (None, None) when the
    variance is unknown or n < 2."""
    alpha = _alpha(alpha)
    if mean is None or var is None or n is None:
        return None, None
    mean = _finite(mean, "Mittelwert")
    var = _nonneg(var, "Varianz")
    n = _nonneg(n, "n")
    if n < 2:
        return None, None
    half = _t_crit(alpha, n - 1.0) * math.sqrt(var / n)
    return mean - half, mean + half


# -- two-sample tests ----------------------------------------------------------


def _restricted_p1(w1s: float, w1f: float, w2s: float, w2f: float, delta: float) -> float:
    """The maximum-likelihood p1 of two binomial samples under p2 = p1 + delta.

    w1s/w1f are the successes/failures of group 1, w2s/w2f those of group 2,
    on any common scale (the maximum does not depend on it). The
    log-likelihood is concave in p1, so its derivative falls monotonically
    over the feasible [max(0, -delta), min(1, 1 - delta)]: the root comes from
    Newton steps kept inside a shrinking bracket (a bisection step whenever
    Newton would leave it), or is an end of the interval when the derivative
    does not change sign there. At delta = 0 it is the pooled proportion.
    """
    # 1 - p - delta as top - p: never rounds to 0 while p < top.
    top = 1.0 - delta
    lo, hi = max(0.0, -delta), min(1.0, top)
    if not hi > lo:
        return lo

    def score(p: float) -> float:
        total = 0.0
        if w1s:
            total += w1s / p
        if w1f:
            total -= w1f / (1.0 - p)
        if w2s:
            total += w2s / (p + delta)
        if w2f:
            total -= w2f / (top - p)
        return total

    def slope(p: float) -> float:
        total = 0.0
        if w1s:
            total -= w1s / (p * p)
        if w1f:
            total -= w1f / ((1.0 - p) * (1.0 - p))
        if w2s:
            total -= w2s / ((p + delta) * (p + delta))
        if w2f:
            total -= w2f / ((top - p) * (top - p))
        return total

    # An end of the interval is the maximum unless a count pushes the
    # derivative to +-infinity there (a success at p = 0, a failure at 1).
    if not ((lo == 0.0 and w1s) or (lo == -delta and w2s)) and score(lo) <= 0.0:
        return lo
    if not ((hi == 1.0 and w1f) or (hi == top and w2f)) and score(hi) >= 0.0:
        return hi
    a, b = lo, hi
    weight = w1s + w1f + w2s + w2f
    p = (w1s + w2s - delta * (w2s + w2f)) / weight if weight > 0.0 else 0.5 * (a + b)
    if not a < p < b:
        p = 0.5 * (a + b)
    for _ in range(300):
        s = score(p)
        if s > 0.0:
            a = p
        elif s < 0.0:
            b = p
        else:
            return p
        d = slope(p)
        step = p - s / d if d < 0.0 and math.isfinite(d) else None
        nxt = step if step is not None and a < step < b else 0.5 * (a + b)
        if abs(nxt - p) <= 4e-16 * abs(nxt) or not a < nxt < b:
            return nxt if a <= nxt <= b else p
        p = nxt
    return p


def _score_interval(s1: float, n1: float, s2: float, n2: float,
                    alpha: float) -> Tuple[float, float]:
    """Mee's score interval for p2 - p1: every delta that the score test of
    p2 - p1 = delta does not reject at level alpha, the variance taken at the
    restricted maximum-likelihood proportions (Farrington-Manning; the
    Miettinen-Nurminen interval without its n / (n - 1) factor). The ends
    are found by bisection between the estimate and -1 / +1."""
    z = _z(alpha / 2.0)
    p1_hat, p2_hat = s1 / n1, s2 / n2
    diff = p2_hat - p1_hat
    # Group weights n1 / (n1 + n2) and n2 / (n1 + n2) without overflow.
    if n1 >= n2:
        r = n2 / n1
        f1, f2 = 1.0 / (1.0 + r), r / (1.0 + r)
    else:
        r = n1 / n2
        f1, f2 = r / (1.0 + r), 1.0 / (1.0 + r)
    weights = (p1_hat * f1, (1.0 - p1_hat) * f1, p2_hat * f2, (1.0 - p2_hat) * f2)

    def stat(delta: float) -> float:
        gap = diff - delta
        if gap == 0.0:
            return 0.0
        p1 = _restricted_p1(*weights, delta)
        p2 = min(1.0, max(0.0, p1 + delta))
        var = p1 * (1.0 - p1) / n1 + p2 * (1.0 - p2) / n2
        if not var > 0.0:
            return math.copysign(_INF, gap)
        return gap / math.sqrt(var)

    def end(outside: float, sign: float) -> float:
        # Invariant: sign * stat(a) > z (rejected), sign * stat(b) <= z.
        a, b = outside, diff
        for _ in range(200):
            mid = 0.5 * (a + b)
            if mid == a or mid == b:
                break
            if sign * stat(mid) > z:
                a = mid
            else:
                b = mid
            if abs(b - a) <= 1e-15 * max(abs(a), abs(b)):
                break
        return b

    lower = -1.0 if diff <= -1.0 else end(-1.0, 1.0)
    upper = 1.0 if diff >= 1.0 else end(1.0, -1.0)
    return lower, upper


def two_proportion_test(s1, n1, s2, n2, alpha=0.05) -> Dict[str, object]:
    """Compare p2 = s2/n2 with p1 = s1/n1 (group 1 is the baseline).

    z and the two-sided p-value come from the pooled z test (identical to
    Pearson's chi-square on the 2x2 table without continuity correction).
    diff = p2 - p1 with Mee's score interval (see _score_interval): it
    inverts the same score test, whose statistic at delta = 0 is the pooled
    z, so the interval excludes 0 exactly when p_value < alpha and a verdict
    taken from p_value never contradicts the interval printed beside it.
    Like Newcombe's interval it keeps its coverage near 0 and 1, unlike the
    Wald interval. relative_lift = diff / p1.
    """
    alpha = _alpha(alpha)
    s1, n1, s2, n2 = (_nonneg(v, name) for v, name in
                      ((s1, "s1"), (n1, "n1"), (s2, "s2"), (n2, "n2")))
    if s1 > n1 or s2 > n2:
        raise StatsInputError("Es kann nicht mehr Erfolge als Versuche geben.")
    result: Dict[str, object] = {"p1": None, "p2": None, "diff": None, "ci_low": None,
                                 "ci_high": None, "z": None, "p_value": None,
                                 "relative_lift": None}
    if n1 == 0 or n2 == 0:
        if n1:
            result["p1"] = s1 / n1
        if n2:
            result["p2"] = s2 / n2
        return result
    p1, p2 = s1 / n1, s2 / n2
    diff = p2 - p1
    ci_low, ci_high = _score_interval(s1, n1, s2, n2, alpha)
    pooled = (s1 + s2) / (n1 + n2)
    se0 = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2))
    if se0 > 0.0:
        z = diff / se0
        p_value = math.erfc(abs(z) / _SQRT2)
    else:
        # Every observation a success, or every one a failure: no difference.
        z, p_value = 0.0, 1.0
    result.update({
        "p1": p1, "p2": p2, "diff": diff,
        "ci_low": max(-1.0, ci_low), "ci_high": min(1.0, ci_high),
        "z": z, "p_value": min(1.0, p_value),
        "relative_lift": (diff / p1) if p1 > 0 else None,
    })
    return _clean(result)


def _quantile_sorted(values: List[float], prob: float) -> float:
    """Linear interpolation between order statistics (type 7, numpy's default)."""
    pos = (len(values) - 1) * prob
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return values[lo] + (values[hi] - values[lo]) * frac


def bayes_beta_binomial(s1, n1, s2, n2, prior_a=1.0, prior_b=1.0, draws=40000,
                        seed=7) -> Dict[str, object]:
    """Beta-binomial comparison of group 2 with group 1 (the baseline).

    Posteriors Beta(prior_a + s, prior_b + n - s); ``draws`` Monte Carlo
    samples from a ``random.Random(seed)`` (deterministic). prob_better =
    P(p2 > p1); expected_loss = E[max(p1 - p2, 0)], what choosing group 2
    costs in expectation if it is in fact worse; diff_mean = E[p2 - p1];
    ci_low/ci_high = the central 95 % credible interval of p2 - p1. The Monte
    Carlo error of prob_better is at most 0.0025 (one standard error).
    """
    s1, n1, s2, n2 = (_nonneg(v, name) for v, name in
                      ((s1, "s1"), (n1, "n1"), (s2, "s2"), (n2, "n2")))
    if s1 > n1 or s2 > n2:
        raise StatsInputError("Es kann nicht mehr Erfolge als Versuche geben.")
    prior_a = _positive(prior_a, "prior_a")
    prior_b = _positive(prior_b, "prior_b")
    if not (math.isfinite(prior_a) and math.isfinite(prior_b)):
        raise StatsInputError("Die Prior-Parameter m\u00fcssen endlich sein.")
    draws = int(draws)
    if draws < 100:
        raise StatsInputError("Es braucht mindestens 100 Ziehungen.")
    a1, b1 = prior_a + s1, prior_b + (n1 - s1)
    a2, b2 = prior_a + s2, prior_b + (n2 - s2)
    rng = random.Random(seed)
    beta = rng.betavariate
    diffs = [beta(a2, b2) - beta(a1, b1) for _ in range(draws)]
    wins = 0.0
    loss = 0.0
    for d in diffs:
        if d > 0.0:
            wins += 1.0
        elif d == 0.0:
            wins += 0.5
        else:
            loss -= d
    diffs.sort()
    return _clean({
        "prob_better": wins / draws,
        "expected_loss": loss / draws,
        "diff_mean": math.fsum(diffs) / draws,
        "ci_low": _quantile_sorted(diffs, 0.025),
        "ci_high": _quantile_sorted(diffs, 0.975),
    })


def welch_t_test(mean1, var1, n1, mean2, var2, n2, alpha=0.05) -> Dict[str, object]:
    """Welch's unequal-variance t test of mean2 - mean1 (group 1 the baseline),
    with Welch-Satterthwaite degrees of freedom and the matching t interval.

    t, df, p_value and the interval are None when either group has fewer than
    two observations, a variance is unknown, or both variances are zero.
    """
    alpha = _alpha(alpha)
    result: Dict[str, object] = {"diff": None, "ci_low": None, "ci_high": None,
                                 "t": None, "df": None, "p_value": None}
    if mean1 is None or mean2 is None:
        return result
    mean1 = _finite(mean1, "Mittelwert 1")
    mean2 = _finite(mean2, "Mittelwert 2")
    result["diff"] = mean2 - mean1
    if var1 is None or var2 is None or n1 is None or n2 is None:
        return result
    var1 = _nonneg(var1, "Varianz 1")
    var2 = _nonneg(var2, "Varianz 2")
    n1 = _nonneg(n1, "n1")
    n2 = _nonneg(n2, "n2")
    if n1 < 2 or n2 < 2:
        return result
    v1, v2 = var1 / n1, var2 / n2
    se2 = v1 + v2
    if not se2 > 0.0:
        return result
    se = math.sqrt(se2)
    df = se2 * se2 / (v1 * v1 / (n1 - 1.0) + v2 * v2 / (n2 - 1.0))
    t = (mean2 - mean1) / se
    half = _t_crit(alpha, df) * se
    result.update({"ci_low": result["diff"] - half, "ci_high": result["diff"] + half,
                   "t": t, "df": df, "p_value": _t_two_sided(t, df)})
    return _clean(result)


def paired_t_test(diffs: Iterable[float], alpha=0.05) -> Dict[str, object]:
    """One-sample t test of the paired differences against 0, with the t
    interval of their mean. t, p_value and the interval are None for fewer
    than two pairs or when every difference is the same."""
    alpha = _alpha(alpha)
    values = [_finite(d, "Differenz") for d in diffs]
    n = len(values)
    result: Dict[str, object] = {"mean_diff": None, "ci_low": None, "ci_high": None,
                                 "t": None, "df": None, "p_value": None, "n_pairs": n}
    if n == 0:
        return result
    mean = math.fsum(values) / n
    result["mean_diff"] = mean
    if n < 2:
        return result
    var = math.fsum((v - mean) ** 2 for v in values) / (n - 1)
    result["df"] = n - 1
    se = math.sqrt(var / n)
    if not se > 0.0 or se <= 1e-15 * abs(mean):
        return result
    t = mean / se
    half = _t_crit(alpha, n - 1.0) * se
    result.update({"ci_low": mean - half, "ci_high": mean + half, "t": t,
                   "p_value": _t_two_sided(t, n - 1.0)})
    return _clean(result)


#: Above this many events the exact conditional test of two Poisson rates
#: gives way to the Wald test on the log rate ratio (they agree to far more
#: digits than are ever shown, and the exact one would need seconds).
_POISSON_EXACT_MAX = 10_000_000


def poisson_rate_test(e1, t1, e2, t2, alpha=0.05) -> Dict[str, object]:
    """Compare the Poisson rate e2/t2 with e1/t1 (group 1 the baseline).

    Exact conditional method: given e1 + e2 events, e2 is binomial with
    p0 = t2 / (t1 + t2) under equal rates. The interval of ratio = rate2 /
    rate1 transforms the Clopper-Pearson interval of that binomial
    proportion; p_value is the central exact p-value, twice the smaller
    binomial tail, the test that interval inverts: the interval excludes 1
    exactly when p_value < alpha. (binomial_test's minimum-likelihood rule,
    the one scipy's binomtest follows, is not dual to it and could call a
    ratio significant whose interval still contains 1.) Beyond ten million
    events the Wald test on log(ratio) and its interval are used. ratio and
    the upper bound are None when e1 = 0 (unbounded).
    """
    alpha = _alpha(alpha)
    e1 = _nonneg(e1, "Ereignisse 1")
    e2 = _nonneg(e2, "Ereignisse 2")
    t1 = _nonneg(t1, "Exposition 1")
    t2 = _nonneg(t2, "Exposition 2")
    result: Dict[str, object] = {"rate1": None, "rate2": None, "ratio": None,
                                 "ci_low": None, "ci_high": None, "p_value": None}
    if t1 == 0 or t2 == 0:
        return result
    k1, k2 = int(round(e1)), int(round(e2))
    rate1, rate2 = e1 / t1, e2 / t2
    result.update({"rate1": rate1, "rate2": rate2,
                   "ratio": rate2 / rate1 if rate1 > 0 else None})
    n = k1 + k2
    if n == 0:
        result["p_value"] = 1.0
        return result
    scale = t1 / t2
    if n > _POISSON_EXACT_MAX and k1 > 0 and k2 > 0:
        log_ratio = math.log(k2 / k1) + math.log(scale)
        se = math.sqrt(1.0 / k1 + 1.0 / k2)
        z = _z(alpha / 2.0)
        result.update({"ci_low": math.exp(log_ratio - z * se),
                       "ci_high": math.exp(log_ratio + z * se),
                       "p_value": math.erfc(abs(log_ratio) / se / _SQRT2)})
        return _clean(result)
    p0 = t2 / (t1 + t2)
    result["p_value"] = _central_binomial_p(k2, n, p0)
    (lo, lo_c), (hi, hi_c) = _clopper_pearson(k2, n, alpha)
    result["ci_low"] = (lo / lo_c) * scale if lo_c > 0 else None
    result["ci_high"] = (hi / hi_c) * scale if hi_c > 0 else None
    return _clean(result)


def _ratio_parts(num: Sequence[float], den: Sequence[float]) -> Tuple[Optional[float], Optional[float], int]:
    """(ratio, variance of the ratio by the delta method, units)."""
    xs = [_finite(v, "Z\u00e4hler") for v in num]
    ys = [_finite(v, "Nenner") for v in den]
    if len(xs) != len(ys):
        raise StatsInputError("Z\u00e4hler und Nenner m\u00fcssen gleich viele Werte haben.")
    n = len(xs)
    sum_y = math.fsum(ys)
    if n == 0 or sum_y == 0:
        return None, None, n
    ratio = math.fsum(xs) / sum_y
    if n < 2:
        return ratio, None, n
    mean_y = sum_y / n
    # Var(R) ~ Var(x - R y) / (n * mean_y^2): the residuals x - R y have mean 0.
    resid_ss = math.fsum((x - ratio * y) ** 2 for x, y in zip(xs, ys))
    return ratio, resid_ss / (n - 1) / (n * mean_y * mean_y), n


def ratio_delta(num1: Sequence[float], den1: Sequence[float], num2: Sequence[float],
                den2: Sequence[float], alpha=0.05) -> Dict[str, object]:
    """Compare two ratio metrics sum(num)/sum(den) (group 1 the baseline).

    Each row is one unit. The variance of each ratio comes from the delta
    method, Var(x - R y) / (n * mean(y)^2); diff = ratio2 - ratio1 with a
    normal (z) interval and two-sided p-value. None where a group has fewer
    than two units, a zero denominator sum, or no variation at all.
    """
    alpha = _alpha(alpha)
    r1, v1, _ = _ratio_parts(num1, den1)
    r2, v2, _ = _ratio_parts(num2, den2)
    result: Dict[str, object] = {"ratio1": r1, "ratio2": r2, "diff": None,
                                 "ci_low": None, "ci_high": None, "p_value": None}
    if r1 is None or r2 is None:
        return _clean(result)
    diff = r2 - r1
    result["diff"] = diff
    if v1 is None or v2 is None:
        return _clean(result)
    se = math.sqrt(v1 + v2)
    if not se > 0.0:
        return _clean(result)
    half = _z(alpha / 2.0) * se
    result.update({"ci_low": diff - half, "ci_high": diff + half,
                   "p_value": math.erfc(abs(diff / se) / _SQRT2)})
    return _clean(result)


# -- chi-square tests ----------------------------------------------------------


def chi_square_independence(table: Sequence[Sequence[float]]) -> Dict[str, object]:
    """Pearson's chi-square test of independence (no continuity correction).

    Rows and columns that are entirely zero are dropped first (they carry no
    information and would divide by zero). cramers_v = sqrt(chi2 / (N (min(r,
    c) - 1))). With fewer than two non-empty rows or columns there is nothing
    to test: chi2 0, df 0, p_value and cramers_v None.
    """
    empty = {"chi2": 0.0, "df": 0, "p_value": None, "cramers_v": None}
    rows = [[_nonneg(v, "H\u00e4ufigkeit") for v in row] for row in table]
    if not rows:
        return empty
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise StatsInputError("Alle Zeilen der Tabelle brauchen gleich viele Spalten.")
    # chi2 is proportional to the total: work on the table scaled to a largest
    # cell of 1, so counts near the float limit cannot overflow a sum.
    scale = max((max(row) for row in rows if row), default=0.0)
    if scale == 0.0:
        return empty
    rows = [[v / scale for v in row] for row in rows]
    rows = [row for row in rows if math.fsum(row) > 0]
    columns = [j for j in range(width) if any(row[j] > 0 for row in rows)]
    rows = [[row[j] for j in columns] for row in rows]
    r, c = len(rows), len(columns)
    if r < 2 or c < 2:
        return empty
    row_sums = [math.fsum(row) for row in rows]
    col_sums = [math.fsum(row[j] for row in rows) for j in range(c)]
    total = math.fsum(row_sums)
    df = (r - 1) * (c - 1)
    expected = [[row_sums[i] * col_sums[j] / total for j in range(c)] for i in range(r)]
    if any(e <= 0.0 for row in expected for e in row):
        # Cells so small next to others that the product underflows.
        return {"chi2": None, "df": df, "p_value": None, "cramers_v": None}
    chi2_scaled = math.fsum((rows[i][j] - expected[i][j]) ** 2 / expected[i][j]
                            for i in range(r) for j in range(c))
    cramers_v = math.sqrt(chi2_scaled / (total * (min(r, c) - 1)))
    chi2 = chi2_scaled * scale
    if not math.isfinite(chi2):
        return {"chi2": None, "df": df, "p_value": None, "cramers_v": cramers_v}
    return _clean({"chi2": chi2, "df": df, "p_value": chi2_sf(chi2, df), "cramers_v": cramers_v})


def chi_square_goodness_of_fit(observed: Sequence[float],
                               expected: Optional[Sequence[float]] = None) -> Dict[str, object]:
    """Pearson's goodness-of-fit test of observed counts against expected shares.

    ``expected`` are weights (> 0) scaled to the observed total; missing means
    equal shares. df = categories - 1. p_value None without observations.
    """
    obs = [_nonneg(v, "H\u00e4ufigkeit") for v in observed]
    k = len(obs)
    if k < 2:
        raise StatsInputError("Es braucht mindestens zwei Kategorien.")
    if expected is None:
        weights = [1.0] * k
    else:
        weights = [_finite(v, "Erwartung") for v in expected]
        if len(weights) != k:
            raise StatsInputError("Es braucht gleich viele erwartete wie beobachtete Werte.")
        if any(w <= 0 for w in weights):
            raise StatsInputError("Erwartete Anteile m\u00fcssen gr\u00f6sser als 0 sein.")
    scale = max(obs)
    if scale == 0.0:
        return {"chi2": 0.0, "df": k - 1, "p_value": None}
    # As for independence: scaled so that huge counts cannot overflow.
    wmax = max(weights)
    wsum = math.fsum(w / wmax for w in weights)
    shares = [w / wmax / wsum for w in weights]
    scaled_total = math.fsum(o / scale for o in obs)
    if any(scaled_total * sh <= 0.0 for sh in shares):
        return {"chi2": None, "df": k - 1, "p_value": None}
    chi2 = scale * math.fsum((o / scale - scaled_total * sh) ** 2 / (scaled_total * sh)
                             for o, sh in zip(obs, shares))
    if not math.isfinite(chi2):
        return {"chi2": None, "df": k - 1, "p_value": None}
    return _clean({"chi2": chi2, "df": k - 1, "p_value": chi2_sf(chi2, k - 1)})


# -- multiple testing, planning, moments -----------------------------------------


def holm(p_values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Holm's step-down adjustment (controls the family-wise error rate).

    Returned in the input order; None entries stay None and do not count as
    tests.
    """
    checked: List[Optional[float]] = []
    for p in p_values:
        if p is None:
            checked.append(None)
            continue
        p = _finite(p, "p")
        if not 0.0 <= p <= 1.0:
            raise StatsInputError("p-Werte m\u00fcssen zwischen 0 und 1 liegen.")
        checked.append(p)
    present = [i for i, p in enumerate(checked) if p is not None]
    m = len(present)
    order = sorted(present, key=lambda i: checked[i])
    adjusted: List[Optional[float]] = [None] * len(checked)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * checked[i]))
        adjusted[i] = running
    return adjusted


def _ceil(value: float) -> int:
    # 3841.0000000000005 is 3841 computed with rounding error, not 3842.
    return int(math.ceil(value - 1e-9 * max(1.0, value)))


_MSG_SIZE_UNBOUNDED = ("Der gesuchte Effekt ist im Verh\u00e4ltnis zur Streuung zu klein; "
                       "die Stichprobe l\u00e4sst sich nicht berechnen.")


def _finite_size(value: float) -> float:
    """A sample size before rounding; StatsInputError when it is not finite
    (a huge spread or a tiny effect), never an OverflowError from ceil."""
    if not math.isfinite(value):
        raise StatsInputError(_MSG_SIZE_UNBOUNDED)
    return value


def _power_z(alpha, power) -> Tuple[float, float]:
    alpha = _probability(alpha, "alpha")
    power = _finite(power, "Testst\u00e4rke")
    if not 0.0 < power < 1.0:
        raise StatsInputError("Die Testst\u00e4rke muss zwischen 0 und 1 liegen.")
    return _z(alpha / 2.0), normal_ppf(power)


def sample_size_proportion(p_base, mde_abs, alpha=0.05, power=0.8) -> int:
    """Units per variant to detect p_base -> p_base + mde_abs with a two-sided
    two-proportion z test (Fleiss, without continuity correction):
    n = (z_a sqrt(2 pbar (1 - pbar)) + z_b sqrt(p1 q1 + p2 q2))^2 / mde^2."""
    p1 = _finite(p_base, "Basisrate")
    if not 0.0 < p1 < 1.0:
        raise StatsInputError("Die Basisrate muss zwischen 0 und 1 liegen.")
    mde = _finite(mde_abs, "Effekt")
    if mde == 0:
        raise StatsInputError("Der gesuchte Effekt darf nicht 0 sein.")
    p2 = p1 + mde
    if not 0.0 < p2 < 1.0:
        raise StatsInputError("Basisrate plus Effekt muss zwischen 0 und 1 liegen.")
    za, zb = _power_z(alpha, power)
    pbar = (p1 + p2) / 2.0
    root = (za * math.sqrt(2.0 * pbar * (1.0 - pbar))
            + zb * math.sqrt(p1 * (1.0 - p1) + p2 * (1.0 - p2)))
    # The ratio before the square: mde * mde underflows to 0 for a tiny
    # effect, and the square of the ratio overflows to infinity.
    ratio = root / abs(mde)
    return max(2, _ceil(_finite_size(ratio * ratio)))


def _t_test_power(n: int, sd: float, mde: float, alpha: float) -> float:
    """Power of the two-sided two-sample t test with n per group.

    The noncentral t probability P(|T'| > t_crit) written as an expectation
    over the chi-square scale W = sqrt(V/df):
    E[Phi(delta - t_crit W) + Phi(-delta - t_crit W)], integrated with
    Simpson's rule over the part of W's distribution that carries mass.
    """
    df = 2.0 * n - 2.0
    if not math.isfinite(df):
        raise StatsInputError(_MSG_SIZE_UNBOUNDED)
    # Effect over its standard error with the ratio taken first: sd * sqrt(2 / n)
    # underflows to 0 for a tiny sd. An infinite delta (an effect beyond any
    # spread) is fine: erfc turns it into a power of 1.
    delta = abs(mde) / sd * math.sqrt(n / 2.0)
    t_crit = student_t_ppf(1.0 - alpha / 2.0, df)

    def tail(w: float) -> float:
        return (0.5 * math.erfc(-(delta - t_crit * w) / _SQRT2)
                + 0.5 * math.erfc(-(-delta - t_crit * w) / _SQRT2))

    if df > 1e15:
        # W's variance, 1 / (2 df), is below the last digit of the power: the
        # t test is the z test (and Simpson's grid would shrink to one point).
        return tail(1.0)
    k = df / 2.0
    spread = math.sqrt(2.0 / df)
    lo = math.sqrt(max(0.0, 1.0 - 12.0 * spread))
    hi = math.sqrt(1.0 + 12.0 * spread + 12.0 / df)
    steps = 2000
    h = (hi - lo) / steps
    total = 0.0
    for i in range(steps + 1):
        w = lo + i * h
        if w <= 0.0:
            continue
        v = df * w * w
        # The chi-square density of V written as (v/2)^k e^(-v/2) / Gamma(k) / v,
        # so that _log_gamma_power cancels its large terms analytically;
        # (k - 1) log v - v / 2 - lgamma(k) cancelled them in floating point
        # (at df = 2e14 the power was 9 % off, at 2e16 it was 5e34).
        density = math.exp(_log_gamma_power(k, v / 2.0) - math.log(v)) * 2.0 * df * w
        weight = 1.0 if i in (0, steps) else (4.0 if i % 2 else 2.0)
        total += weight * density * tail(w)
    power = total * h / 3.0
    if not math.isfinite(power):
        raise StatsInputError(_MSG_SIZE_UNBOUNDED)
    return power


def sample_size_mean(sd, mde_abs, alpha=0.05, power=0.8) -> int:
    """Observations per variant to detect a mean difference mde_abs with a
    two-sided two-sample t test: the smallest n whose exact power (noncentral
    t) reaches ``power``. The start is the normal formula
    2 (z_a + z_b)^2 sd^2 / mde^2 plus Guenther's correction z_a^2 / 4, which
    is already exact except for very small n."""
    sd = _finite(sd, "Standardabweichung")
    if not sd > 0:
        raise StatsInputError("Die Standardabweichung muss gr\u00f6sser als 0 sein.")
    mde = _finite(mde_abs, "Effekt")
    if mde == 0:
        raise StatsInputError("Der gesuchte Effekt darf nicht 0 sein.")
    za, zb = _power_z(alpha, power)
    # The ratio before the square, as in sample_size_proportion.
    ratio = sd / abs(mde)
    n = max(2, _ceil(_finite_size(2.0 * (za + zb) ** 2 * (ratio * ratio) + za * za / 4.0)))
    for _ in range(50):
        if _t_test_power(n, sd, mde, alpha) >= power:
            break
        n += 1
    else:
        return n
    for _ in range(50):
        if n <= 2 or _t_test_power(n - 1, sd, mde, alpha) < power:
            break
        n -= 1
    return n


#: A variance below this share of the mean square is cancellation in
#: sum_sq - sum^2/n (eleven values of 4.3 leave 1e-14, not 0), not spread.
_VARIANCE_NOISE = 1e-12


def variance(sum_, sum_sq, n) -> Optional[float]:
    """Sample variance from sufficient statistics:
    max(0, (sum_sq - sum^2/n) / (n - 1)); None if n < 2 or sum_sq is unknown.
    A result below 1e-12 of the mean square sum_sq / n is rounding left over
    from identical values and comes back as exactly 0, so "no spread" is
    recognisable (describe then gives no interval instead of one of width
    1e-7)."""
    if sum_ is None or sum_sq is None or n is None:
        return None
    try:
        sum_ = float(sum_)
        sum_sq = float(sum_sq)
        n = float(n)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(sum_) and math.isfinite(sum_sq) and math.isfinite(n)) or n < 2:
        return None
    value = (sum_sq - sum_ * sum_ / n) / (n - 1.0)
    if not math.isfinite(value):
        return None
    if value <= _VARIANCE_NOISE * abs(sum_sq) / n:
        return 0.0
    return value
