"""Tests for experiments.stats, the pure-Python statistics of the built-in evaluators.

Reference values were computed once with scipy 1.17 (and mpmath where scipy is
itself inaccurate: the incomplete gamma for shape parameters above ~1e7) and
are pinned here, so the suite never imports scipy. Closed forms must agree to
1e-9 relative (the plan asks for 1e-6); Monte Carlo results to 0.01 absolute.
"""

import json
import math
import random
import statistics
import time

import pytest

from experiments import stats
from experiments.errors import ValidationError

# -- reference values (scipy.stats / scipy.special unless noted) --------------------

NORMAL_CDF = [[-37.5, 4.605353009581954e-308], [-10.0, 7.61985302416047e-24], [-3.0, 0.0013498980316300933],
              [-1.959963984540054, 0.025], [0.0, 0.5], [0.5, 0.6914624612740131], [2.0, 0.9772498680518208],
              [8.0, 0.9999999999999993]]

NORMAL_PPF = [[1e-300, -37.0470962993612], [1e-20, -9.262340089798409], [1e-10, -6.361340902404056],
              [0.001, -3.090232306167813], [0.025, -1.9599639845400545], [0.3, -0.5244005127080409],
              [0.5, 0.0], [0.7, 0.5244005127080407], [0.975, 1.959963984540054],
              [0.999999, 4.753424308817087], [0.999999999999, 7.0344869100478356]]

T_CDF = [[-2.0, 1, 0.14758361765043326], [1.5, 2, 0.8638034375544994],
         [-3.3, 2.5, 0.029701064389722066], [2.228, 10, 0.9749941140914443],
         [-4.0, 30, 0.0001909228180418782], [0.3, 7.7, 0.6139420918116524],
         [-12.0, 3, 0.0006225079003946681], [1.96, 1000, 0.9748634075221256],
         [-2.5, 1000000.0, 0.006209744751081627], [3.0, 1000000000.0, 0.998650101935131],
         [-40.0, 5, 9.205981085886477e-08]]

T_PPF = [[0.975, 1, 12.706204736174694], [0.025, 2, -4.302652729749464], [0.975, 3, 3.1824463052837078],
         [0.995, 4.5, 4.272823993011293], [0.9, 10, 1.372183641110336], [0.975, 29, 2.045229642132703],
         [1e-10, 5, -156.82559270889433], [0.999, 100000.0, 3.0903138094272378],
         [0.025, 100000000.0, -1.9599640082627667], [0.6, 12.3, 0.25889255986689824]]

CHI2_SF = [[3.841458820694124, 1, 0.04999999999999989], [0.1, 2, 0.951229424500714],
           [12.0, 3, 0.007383160505359769], [5.5, 3.5, 0.18636211019141324],
           [30.0, 10, 0.000856641210775301], [150.0, 100, 0.0009039320423540184],
           [10500.0, 10000, 0.00024794736798936033], [200.0, 5, 2.8406228986415534e-41]]

CHI2_PPF = [[0.95, 1, 3.841458820694124], [0.025, 2, 0.05063561596857975], [0.975, 20, 34.16960690283833],
            [1e-08, 3, 1.122333097304999e-05], [0.5, 1000, 999.333412403381],
            [0.999, 0.5, 8.752888515773373]]

BETAINC = [[0.5, 0.5, 0.2, 0.2951672353008665], [2.0, 3.0, 0.4, 0.5247999999999999],
           [10.0, 0.5, 0.97, 0.4408041535957583], [0.02, 3.0, 1e-05, 0.8183166265051582],
           [1000000.0, 1000000.0, 0.5003, 0.8019280371578701],
           [5.0, 100000.0, 3e-05, 0.18475439930418394], [300.0, 20.0, 0.9, 0.007165489809358714]]

GAMMAINCC = [[0.01, 0.5, 0.005626756193967183], [0.5, 2.0, 0.04550026389635857],
             [3.0, 1.0, 0.9196986029286058], [10.0, 25.0, 0.0002214766382487835],
             [100.0, 90.0, 0.84177901081357], [1000000.0, 1001000.0, 0.15865521363165971],
             [2.5, 0.001, 0.9999999904914654]]

GAMMAINC_HUGE = [20000000.0, 19973167.184270002, 9.708481095252356e-10]

BINOM_CDF = [[3, 10, 0.5, 0.171875], [0, 20, 0.1, 0.12157665459056925], [45, 100, 0.5, 0.18410080866334808],
             [129, 10688, 0.0125, 0.36521859821315444], [5, 1000000, 1e-05, 0.06708501704958428]]

BINOMTEST = [[3, 10, 0.5, 0.34375], [7, 10, 0.5, 0.34375], [0, 10, 0.3, 0.0388396033],
             [10, 10, 0.3, 5.9048999999999975e-06], [14, 20, 0.5, 0.11531829833984375],
             [2, 25, 0.25, 0.06177843281724905], [60, 100, 0.5, 0.05688793364098089],
             [1234, 10000, 0.12, 0.2954217334531958], [1, 3, 0.9, 0.02799999999999999]]

WILSON = [[6, 8, 0.05, [0.40927543031016883, 0.9285207872478909]],
          [129, 10688, 0.05, [0.010167692378450447, 0.014322145020389466]],
          [175, 10714, 0.05, [0.01410114466278277, 0.01891310173936806]],
          [0, 50, 0.05, [0.0, 0.07134759913335872]], [50, 50, 0.05, [0.9286524008666412, 1.0]],
          [3, 7, 0.1, [0.18644319036395607, 0.7105229089864071]]]

POISSON = [[0, 10.0, 0.05, 0.0, 0.3688879454113935],
           [1, 1.0, 0.05, 0.025317807984289876, 5.571643390938898],
           [120, 1000.0, 0.05, 0.0994919256259387, 0.14349058536935225],
           [5, 2.5, 0.01, 0.43117129626092776, 5.659903764409205],
           [1000000, 1000000.0, 0.05, 0.9980409833402939, 1.0019619119454322]]

T_INTERVAL = [[120.0, 225.0, 200, 0.05, [117.9084242330316, 122.0915757669684]],
              [0.5, 0.04, 2, 0.05, [-1.2969287064187507, 2.2969287064187505]],
              [-3.0, 10.0, 15, 0.1, [-4.438103703813548, -1.5618962961864526]]]

TWO_PROP = [{'args': [129, 10688, 175, 10714, 0.05],
             'p1': 0.012069610778443114,
             'p2': 0.016333768900504014,
             'diff': 0.0042641581220609,
             'ci_low': 0.00109264089871161,
             'ci_high': 0.007468881432094565,
             'z': 2.6358888215759615,
             'p_value': 0.008391722170333918,
             'relative_lift': 0.353297069834007},
            {'args': [250, 5000, 300, 5000, 0.05],
             'p1': 0.05,
             'p2': 0.06,
             'diff': 0.009999999999999995,
             'ci_low': 0.0010580192586747087,
             'ci_high': 0.018972359585584866,
             'z': 2.193172316532562,
             'p_value': 0.028294966290231177,
             'relative_lift': 0.1999999999999999},
            {'args': [3, 20, 9, 22, 0.1],
             'p1': 0.15,
             'p2': 0.4090909090909091,
             'diff': 0.25909090909090915,
             'ci_low': 0.028981688255564297,
             'ci_high': 0.4533444760047717,
             'z': 1.8563159997449499,
             'p_value': 0.06340852986344894,
             'relative_lift': 1.7272727272727277},
            {'args': [60, 100, 78, 100, 0.05],
             'p1': 0.6,
             'p2': 0.78,
             'diff': 0.18000000000000005,
             'ci_low': 0.05179992906096009,
             'ci_high': 0.3004229954022355,
             'z': 2.752023353565414,
             'p_value': 0.005922829700135651,
             'relative_lift': 0.3000000000000001}]

BAYES = [{'args': [129, 10688, 175, 10714, 1.0, 1.0],
          'prob_better': 0.9958038394654555,
          'diff_mean': 0.004263140784908153,
          'expected_loss': 2.163365262123741e-06,
          'ci_low': 0.0010934597766752091,
          'ci_high': 0.007462056477899749},
         {'args': [10, 100, 12, 100, 1.0, 1.0],
          'prob_better': 0.67072046521108,
          'diff_mean': 0.019607843137254888,
          'expected_loss': 0.009693869944484018,
          'ci_low': -0.06844998404567054,
          'ci_high': 0.10849407306788163},
         {'args': [5, 40, 1, 40, 0.5, 0.5],
          'prob_better': 0.04121587097964302,
          'diff_mean': -0.0975609756097561,
          'expected_loss': 0.09856691000189804,
          'ci_low': -0.22457977185011285,
          'ci_high': 0.013320392810518536},
         {'args': [0, 10, 0, 10, 1.0, 1.0],
          'prob_better': 0.4999999999990001,
          'diff_mean': 0.0,
          'expected_loss': 0.03983895901930784,
          'ci_low': -0.22825288604720761,
          'ci_high': 0.22810625158494058}]

WELCH = [{'args': [120.0, 225.0, 200, 112.0, 196.0, 180, 0.05],
          'diff': -8.0,
          't': -5.376653840862991,
          'df': 377.4929368327064,
          'p_value': 1.3325878618654554e-07,
          'ci_low': -10.925638401859935,
          'ci_high': -5.074361598140065},
         {'args': [0.51, 0.04, 12, 0.49, 0.09, 9, 0.1],
          'diff': -0.020000000000000018,
          't': -0.17320508075688787,
          'df': 13.1588785046729,
          'p_value': 0.8651261621041003,
          'ci_low': -0.2243011332090382,
          'ci_high': 0.18430113320903815},
         {'args': [5.0, 0.0, 10, 6.0, 4.0, 10, 0.05],
          'diff': 1.0,
          't': 1.5811388300841895,
          'df': 8.999999999999998,
          'p_value': 0.1483047073665595,
          'ci_low': -0.4307138119413294,
          'ci_high': 2.430713811941329}]

PAIRED = [{'args': [[0.02, -0.01, 0.05, 0.03, 0.0, 0.04, 0.01, 0.02], 0.05],
           'mean_diff': 0.02,
           't': 2.8284271247461903,
           'df': 7,
           'p_value': 0.02546356168323925,
           'ci_low': 0.0032795815674057788,
           'ci_high': 0.03672041843259422,
           'n_pairs': 8},
          {'args': [[1.5, 2.5], 0.05],
           'mean_diff': 2.0,
           't': 4.0,
           'df': 1,
           'p_value': 0.15595826075473865,
           'ci_low': -4.353102368087347,
           'ci_high': 8.353102368087347,
           'n_pairs': 2},
          {'args': [[-3.0, -1.0, -2.5, -4.0, 0.5], 0.1],
           'mean_diff': -2.0,
           't': -2.5298221281347035,
           'df': 4,
           'p_value': 0.06467689395635304,
           'ci_low': -3.685372866825629,
           'ci_high': -0.3146271331743711,
           'n_pairs': 5}]

POISSON_RATE = [{'args': [120, 1000.0, 160, 1000.0, 0.05],
                 'rate1': 0.12,
                 'rate2': 0.16,
                 'ratio': 1.3333333333333335,
                 'ci_low': 1.0457422082870456,
                 'ci_high': 1.7037935314237522,
                 'p_value': 0.019606727290281328},
                {'args': [3, 1000.0, 50, 20000.0, 0.05],
                 'rate1': 0.003,
                 'rate2': 0.0025,
                 'ratio': 0.8333333333333334,
                 'ci_low': 0.26922474422456577,
                 'ci_high': 4.177000455031714,
                 'p_value': 0.7404886388771397},
                {'args': [10, 5.0, 4, 8.0, 0.1],
                 'rate1': 2.0,
                 'rate2': 0.5,
                 'ratio': 0.25,
                 'ci_low': 0.07258147421877471,
                 'ci_high': 0.7337117396869725,
                 'p_value': 0.023768530508994745},
                {'args': [0, 10.0, 5, 10.0, 0.05],
                 'rate1': 0.0,
                 'rate2': 0.5,
                 'ratio': None,
                 'ci_low': 0.91635585731546,
                 'ci_high': None,
                 'p_value': 0.0625}]

RATIO_DELTA = {'args': [[100.0, 120.0, 90.0, 110.0, 105.0, 95.0, 130.0],
                        [80.0, 95.0, 70.0, 90.0, 85.0, 75.0, 100.0], [90.0, 85.0, 100.0, 95.0, 80.0, 99.0],
                        [95.0, 90.0, 110.0, 100.0, 92.0, 101.0], 0.05],
               'ratio1': 1.2605042016806722,
               'ratio2': 0.9336734693877551,
               'diff': -0.32683073229291715,
               'ci_low': -0.36403376806609555,
               'ci_high': -0.28962769651973874,
               'p_value': 1.932829735279689e-66}

CHI_IND = [{'table': [[20, 30, 0, 10], [30, 15, 5, 10]],
            'chi2': 12.0,
            'df': 3,
            'p_value': 0.007383160505359769,
            'cramers_v': 0.31622776601683794},
           {'table': [[10, 20], [30, 5]],
            'chi2': 18.726190476190474,
            'df': 1,
            'p_value': 1.5089565704354248e-05,
            'cramers_v': 0.5367450401216932},
           {'table': [[5, 0, 3], [0, 0, 0], [2, 0, 9]],
            'chi2': 3.9094967532467533,
            'df': 1,
            'p_value': 0.048013972894266205,
            'cramers_v': 0.45361105256925455},
           {'table': [[12, 7, 9], [8, 11, 6], [10, 10, 10]],
            'chi2': 2.400807936507937,
            'df': 4,
            'p_value': 0.6624812636448205,
            'cramers_v': 0.12026095963652716}]

CHI_GOF = [{'observed': [12, 8, 5, 0],
            'expected': None,
            'chi2': 12.280000000000001,
            'df': 3,
            'p_value': 0.0064830336341513166},
           {'observed': [30, 50, 20],
            'expected': [1, 2, 1],
            'chi2': 2.0,
            'df': 2,
            'p_value': 0.36787944117144245},
           {'observed': [7, 3],
            'expected': [0.3, 0.7],
            'chi2': 7.619047619047619,
            'df': 1,
            'p_value': 0.005775498089305212}]

HOLM = [[[0.01, 0.04, 0.03, 0.005], [0.03, 0.06, 0.06, 0.02]], [[0.5, 0.2], [0.5, 0.4]],
        [[0.02], [0.02]]]

SS_PROP = [[[0.012, 0.004, 0.05, 0.8], 13543], [[0.1, 0.02, 0.05, 0.9], 5142],
           [[0.5, -0.05, 0.01, 0.8], 2329]]

SS_MEAN = [[[15.0, 5.0, 0.05, 0.8], 143, 0.802082973735455, 0.7993154370824757],
           [[1.0, 0.5, 0.05, 0.9], 86, 0.9032299799904953, 0.8998940794178057],
           [[0.2, 0.01, 0.01, 0.8], 9345, 0.8000085311025958, 0.7999573267626647],
           [[1.0, 2.0, 0.05, 0.8], 6, 0.876417771411989, 0.7905423779725715],
           [[1.0, 3.0, 0.05, 0.8], 4, 0.9389357455090199, 0.782554387058117],
           [[1.0, 4.0, 0.05, 0.8], 3, 0.9479377545400749, 0.564514289260038],
           [[2.0, 1.0, 0.1, 0.8], 51, 0.8059150189615247, 0.7989544786598332]]


def close(actual, expected, rel=1e-9, abs_=0.0):
    assert actual is not None, f"expected {expected!r}, got None"
    assert math.isfinite(actual), actual
    assert actual == pytest.approx(expected, rel=rel, abs=abs_), (actual, expected)


# -- special functions ------------------------------------------------------------


@pytest.mark.parametrize("x,expected", NORMAL_CDF)
def test_normal_cdf_matches_scipy(x, expected):
    close(stats.normal_cdf(x), expected, rel=1e-12)


@pytest.mark.parametrize("p,expected", NORMAL_PPF)
def test_normal_ppf_matches_scipy(p, expected):
    close(stats.normal_ppf(p), expected, rel=1e-13, abs_=1e-15)


def test_normal_ppf_agrees_with_the_standard_library_everywhere():
    reference = statistics.NormalDist()
    for i in range(1, 2000):
        p = i / 2000.0
        assert stats.normal_ppf(p) == pytest.approx(reference.inv_cdf(p), rel=1e-13, abs=1e-15)
    for exponent in range(-300, -1, 7):
        p = 10.0 ** exponent
        assert stats.normal_ppf(p) == pytest.approx(reference.inv_cdf(p), rel=1e-13)
    for p in (1e-9, 0.001, 0.2):
        # 1 - p carries the rounding of the subtraction, hence the looser bound.
        assert stats.normal_ppf(1 - p) == pytest.approx(-stats.normal_ppf(p), rel=1e-6)


def test_normal_ppf_inverts_normal_cdf():
    for x in (-8.0, -3.0, -0.2, 0.0, 1.0, 5.0):
        assert stats.normal_ppf(stats.normal_cdf(x)) == pytest.approx(x, abs=1e-9)


@pytest.mark.parametrize("t,df,expected", T_CDF)
def test_student_t_cdf_matches_scipy(t, df, expected):
    close(stats.student_t_cdf(t, df), expected)


def test_student_t_cdf_symmetry_and_limits():
    for df in (1, 2.5, 17, 1e4, 1e8):
        for t in (0.1, 1.7, 6.0):
            assert stats.student_t_cdf(-t, df) == pytest.approx(1 - stats.student_t_cdf(t, df), abs=1e-15)
    assert stats.student_t_cdf(0.0, 3) == 0.5
    assert stats.student_t_cdf(float("inf"), 3) == 1.0
    assert stats.student_t_cdf(float("-inf"), 3) == 0.0
    assert stats.student_t_cdf(1.3, float("inf")) == pytest.approx(stats.normal_cdf(1.3), rel=1e-15)


def test_student_t_large_df_keeps_precision_in_both_regimes():
    # mpmath at 50 digits (I_x(df/2, 1/2)/2): regimes where the plain
    # continued fraction loses up to five digits.
    close(stats.student_t_cdf(-1.97857, 8.097e10), 0.023932220056124284309, rel=1e-11)
    close(stats.student_t_cdf(2.5, 3e7), 0.99379033202671992903, rel=1e-13)
    # Far tail at large df, where the asymptotic form is not used.
    assert 0 < stats.student_t_cdf(-35.0, 1e6) < 1e-200


@pytest.mark.parametrize("p,df,expected", T_PPF)
def test_student_t_ppf_matches_scipy(p, df, expected):
    close(stats.student_t_ppf(p, df), expected, rel=1e-9)


def test_student_t_ppf_inverts_the_cdf():
    for df in (1, 2, 3, 4.5, 9.9, 60, 3e4, 2e5, 5e9):
        for p in (1e-12, 0.001, 0.025, 0.3, 0.5, 0.77, 0.975, 0.9999):
            t = stats.student_t_ppf(p, df)
            assert stats.student_t_cdf(t, df) == pytest.approx(p, rel=1e-9)


def test_student_t_ppf_near_the_median():
    # mpmath: the root of F(t) = 0.5000001 for df = 3 (scipy is off by 1e-10 here).
    close(stats.student_t_ppf(0.5000001, 3), 2.7206990449193157e-07, rel=1e-12)
    assert stats.student_t_ppf(0.5, 7) == 0.0


@pytest.mark.parametrize("x,df,expected", CHI2_SF)
def test_chi2_sf_matches_scipy(x, df, expected):
    close(stats.chi2_sf(x, df), expected)


@pytest.mark.parametrize("p,df,expected", CHI2_PPF)
def test_chi2_ppf_matches_scipy(p, df, expected):
    close(stats.chi2_ppf(p, df), expected)


def test_chi2_sf_edges():
    assert stats.chi2_sf(0.0, 3) == 1.0
    assert stats.chi2_sf(-1.0, 3) == 1.0
    assert stats.chi2_sf(1e6, 3) == 0.0


@pytest.mark.parametrize("a,b,x,expected", BETAINC)
def test_betainc_matches_scipy(a, b, x, expected):
    close(stats.betainc(a, b, x), expected, rel=1e-8)


def test_betainc_symmetry_and_edges():
    for a, b, x in ((0.5, 3.0, 0.3), (40.0, 7.0, 0.8), (2e3, 3e3, 0.41)):
        assert stats.betainc(a, b, x) == pytest.approx(1 - stats.betainc(b, a, 1 - x), abs=1e-14)
    assert stats.betainc(2.0, 3.0, 0.0) == 0.0
    assert stats.betainc(2.0, 3.0, 1.0) == 1.0


def test_beta_ppf_inverts_betainc():
    for a, b in ((0.5, 0.5), (1.0, 1.0), (130.0, 10560.0), (2.0, 9e5), (7e5, 3.0), (0.05, 40.0)):
        for p in (1e-10, 0.025, 0.5, 0.975):
            x = stats.beta_ppf(p, a, b)
            assert 0.0 <= x <= 1.0
            assert stats.betainc(a, b, x) == pytest.approx(p, rel=1e-8, abs=1e-15)


def test_inverse_incomplete_beta_keeps_both_tails_precise():
    # The root of Beta(1/2, 1/2) at 1 - 1e-9 is 1 - 2.47e-18, not representable
    # as x; the solver returns it as 1 - x, which is.
    for a, b in ((0.5, 0.5), (130.0, 10560.0), (7e5, 3.0), (3.0, 7e5)):
        for q in (1e-9, 1e-30):
            x, y = stats._ibeta_inv(a, b, 1.0 - q, q)
            assert stats._ibeta(a, b, x, y)[1] == pytest.approx(q, rel=1e-8)
    x, y = stats._ibeta_inv(0.5, 0.5, 1.0 - 1e-9, 1e-9)
    assert y == pytest.approx((math.pi / 2 * 1e-9) ** 2, rel=1e-6)


@pytest.mark.parametrize("s,x,expected", GAMMAINCC)
def test_gammaincc_matches_scipy(s, x, expected):
    close(stats.gammaincc(s, x), expected, rel=1e-9)


def test_incomplete_gamma_for_huge_shape_parameters():
    # mpmath quadrature at 40 digits; scipy's gammainc reports 9.15e-10 here.
    a, x, expected = GAMMAINC_HUGE
    lower, upper = stats._gamma_pq(a, x)
    close(lower, expected, rel=1e-10)
    assert upper == pytest.approx(1 - expected, abs=1e-15)


# -- binomial ---------------------------------------------------------------------


@pytest.mark.parametrize("k,n,p,expected", BINOM_CDF)
def test_binom_cdf_matches_scipy(k, n, p, expected):
    close(stats.binom_cdf(k, n, p), expected, rel=1e-8)


def test_binom_cdf_edges():
    assert stats.binom_cdf(-1, 10, 0.3) == 0.0
    assert stats.binom_cdf(10, 10, 0.3) == 1.0
    assert stats.binom_cdf(3, 10, 0.0) == 1.0
    assert stats.binom_cdf(3, 10, 1.0) == 0.0


@pytest.mark.parametrize("k,n,p,expected", BINOMTEST)
def test_binomial_test_matches_scipy_binomtest(k, n, p, expected):
    close(stats.binomial_test(k, n, p)["p_value"], expected, rel=1e-9)


def test_binomial_test_edges():
    assert stats.binomial_test(0, 0)["p_value"] == 1.0
    assert stats.binomial_test(5, 10)["p_value"] == 1.0
    assert stats.binomial_test(0, 5, 0.0)["p_value"] == 1.0
    assert stats.binomial_test(1, 5, 0.0)["p_value"] == 0.0
    with pytest.raises(stats.StatsInputError):
        stats.binomial_test(11, 10)


# -- intervals --------------------------------------------------------------------


@pytest.mark.parametrize("k,n,alpha,expected", WILSON)
def test_wilson_interval_matches_scipy(k, n, alpha, expected):
    lo, hi = stats.wilson_interval(k, n, alpha)
    close(lo, expected[0], rel=1e-10, abs_=1e-15)
    close(hi, expected[1], rel=1e-10)


def test_wilson_interval_edges():
    assert stats.wilson_interval(0, 0) == (None, None)
    assert stats.wilson_interval(0, 50)[0] == 0.0
    assert stats.wilson_interval(50, 50)[1] == 1.0
    with pytest.raises(stats.StatsInputError):
        stats.wilson_interval(6, 5)
    with pytest.raises(stats.StatsInputError):
        stats.wilson_interval(-1, 5)


@pytest.mark.parametrize("k,t,alpha,lo,hi", POISSON)
def test_poisson_interval_is_the_exact_chi_square_interval(k, t, alpha, lo, hi):
    got_lo, got_hi = stats.poisson_interval(k, t, alpha)
    close(got_lo, lo, rel=1e-10, abs_=1e-300)
    close(got_hi, hi, rel=1e-10)


def test_poisson_interval_edges_and_huge_counts():
    assert stats.poisson_interval(3, 0) == (None, None)
    started = time.perf_counter()
    lo, hi = stats.poisson_interval(10 ** 9, 1.0)
    assert time.perf_counter() - started < 2.0
    # Normal approximation is excellent at this size: +-1.96 sqrt(k).
    assert lo == pytest.approx(1e9 - 1.959964 * math.sqrt(1e9), rel=1e-7)
    assert hi == pytest.approx(1e9 + 1.959964 * math.sqrt(1e9), rel=1e-7)


@pytest.mark.parametrize("mean,var,n,alpha,expected", T_INTERVAL)
def test_t_interval_matches_scipy(mean, var, n, alpha, expected):
    lo, hi = stats.t_interval(mean, var, n, alpha)
    close(lo, expected[0])
    close(hi, expected[1])


def test_t_interval_needs_two_observations_and_a_variance():
    assert stats.t_interval(1.0, 2.0, 1) == (None, None)
    assert stats.t_interval(1.0, None, 10) == (None, None)
    assert stats.t_interval(None, 1.0, 10) == (None, None)


# -- tests of two groups ----------------------------------------------------------


@pytest.mark.parametrize("case", TWO_PROP, ids=lambda c: str(c["args"]))
def test_two_proportion_test_matches_scipy_and_newcombe(case):
    result = stats.two_proportion_test(*case["args"])
    for key in ("p1", "p2", "diff", "ci_low", "ci_high", "z", "p_value", "relative_lift"):
        close(result[key], case[key], rel=1e-9, abs_=1e-15)


def test_two_proportion_test_degenerate_inputs():
    same = stats.two_proportion_test(0, 50, 0, 70)
    assert same["p_value"] == 1.0 and same["z"] == 0.0 and same["diff"] == 0.0
    assert same["relative_lift"] is None
    empty = stats.two_proportion_test(0, 0, 3, 10)
    assert empty["p_value"] is None and empty["diff"] is None and empty["p2"] == 0.3
    with pytest.raises(stats.StatsInputError):
        stats.two_proportion_test(11, 10, 1, 10)


@pytest.mark.parametrize("case", BAYES, ids=lambda c: str(c["args"]))
def test_bayes_beta_binomial_matches_exact_integration(case):
    s1, n1, s2, n2, prior_a, prior_b = case["args"]
    result = stats.bayes_beta_binomial(s1, n1, s2, n2, prior_a=prior_a, prior_b=prior_b)
    # prob_better against numerical integration, the rest against 4e6 numpy draws.
    assert result["prob_better"] == pytest.approx(case["prob_better"], abs=0.01)
    assert result["diff_mean"] == pytest.approx(case["diff_mean"], abs=0.002)
    assert result["expected_loss"] == pytest.approx(case["expected_loss"], abs=0.002)
    assert result["ci_low"] == pytest.approx(case["ci_low"], abs=0.004)
    assert result["ci_high"] == pytest.approx(case["ci_high"], abs=0.004)
    assert result["ci_low"] <= result["diff_mean"] <= result["ci_high"]


def test_bayes_beta_binomial_is_deterministic():
    first = stats.bayes_beta_binomial(12, 100, 20, 100)
    second = stats.bayes_beta_binomial(12, 100, 20, 100)
    assert first == second
    assert stats.bayes_beta_binomial(12, 100, 20, 100, seed=8) != first


def test_bayes_beta_binomial_symmetry_and_validation():
    forward = stats.bayes_beta_binomial(30, 200, 45, 200)
    backward = stats.bayes_beta_binomial(45, 200, 30, 200)
    assert forward["prob_better"] + backward["prob_better"] == pytest.approx(1.0, abs=0.01)
    with pytest.raises(stats.StatsInputError):
        stats.bayes_beta_binomial(5, 4, 1, 4)
    with pytest.raises(stats.StatsInputError):
        stats.bayes_beta_binomial(1, 4, 1, 4, prior_a=0)
    with pytest.raises(stats.StatsInputError):
        stats.bayes_beta_binomial(1, 4, 1, 4, draws=10)


@pytest.mark.parametrize("case", WELCH, ids=lambda c: str(c["args"]))
def test_welch_t_test_matches_scipy(case):
    result = stats.welch_t_test(*case["args"])
    for key in ("diff", "t", "df", "p_value", "ci_low", "ci_high"):
        close(result[key], case[key], rel=1e-9)


def test_welch_t_test_without_enough_data():
    one = stats.welch_t_test(1.0, 0.5, 1, 2.0, 0.5, 10)
    assert one["diff"] == 1.0 and one["t"] is None and one["p_value"] is None
    unknown = stats.welch_t_test(1.0, None, 10, 2.0, 0.5, 10)
    assert unknown["p_value"] is None
    flat = stats.welch_t_test(1.0, 0.0, 10, 2.0, 0.0, 10)
    assert flat["p_value"] is None and flat["ci_low"] is None
    assert stats.welch_t_test(None, 1, 3, 1.0, 1, 3)["diff"] is None


@pytest.mark.parametrize("case", PAIRED, ids=lambda c: str(c["args"][0][:3]))
def test_paired_t_test_matches_scipy(case):
    diffs, alpha = case["args"]
    result = stats.paired_t_test(diffs, alpha)
    for key in ("mean_diff", "t", "p_value", "ci_low", "ci_high"):
        close(result[key], case[key], rel=1e-9)
    assert result["df"] == case["df"] and result["n_pairs"] == case["n_pairs"]


def test_paired_t_test_without_enough_pairs():
    assert stats.paired_t_test([])["mean_diff"] is None
    single = stats.paired_t_test([0.4])
    assert single["mean_diff"] == 0.4 and single["p_value"] is None and single["n_pairs"] == 1
    constant = stats.paired_t_test([0.1, 0.1, 0.1])
    assert constant["t"] is None and constant["p_value"] is None
    with pytest.raises(stats.StatsInputError):
        stats.paired_t_test([0.1, float("nan")])


@pytest.mark.parametrize("case", POISSON_RATE, ids=lambda c: str(c["args"]))
def test_poisson_rate_test_matches_the_exact_conditional_test(case):
    result = stats.poisson_rate_test(*case["args"])
    for key in ("rate1", "rate2", "ratio", "ci_low", "ci_high", "p_value"):
        if case[key] is None:
            assert result[key] is None, key
        else:
            close(result[key], case[key], rel=1e-9, abs_=1e-300)


def test_poisson_rate_test_edges_and_large_counts():
    none = stats.poisson_rate_test(0, 10, 0, 10)
    assert none["p_value"] == 1.0 and none["ratio"] is None
    assert stats.poisson_rate_test(3, 0, 3, 10)["p_value"] is None
    # Beyond ten million events: the Wald test on log(ratio).
    big = stats.poisson_rate_test(9_000_000, 1000.0, 9_030_000, 1000.0)
    se = math.sqrt(1 / 9_000_000 + 1 / 9_030_000)
    z = math.log(9_030_000 / 9_000_000) / se
    close(big["p_value"], math.erfc(z / math.sqrt(2)), rel=1e-12)
    close(big["ci_low"], math.exp(math.log(9_030_000 / 9_000_000) - 1.959963984540054 * se), rel=1e-12)


def test_ratio_delta_matches_the_delta_method():
    case = RATIO_DELTA
    result = stats.ratio_delta(*case["args"])
    for key in ("ratio1", "ratio2", "diff", "ci_low", "ci_high", "p_value"):
        close(result[key], case[key], rel=1e-9)


def test_ratio_delta_without_enough_data():
    one = stats.ratio_delta([1.0], [2.0], [3.0, 4.0], [2.0, 2.0])
    assert one["ratio1"] == 0.5 and one["diff"] == pytest.approx(1.25) and one["p_value"] is None
    zero = stats.ratio_delta([1.0, 2.0], [0.0, 0.0], [1.0, 2.0], [1.0, 1.0])
    assert zero["ratio1"] is None and zero["diff"] is None
    exact = stats.ratio_delta([2.0, 4.0], [1.0, 2.0], [3.0, 6.0], [1.0, 2.0])
    assert exact["diff"] == pytest.approx(1.0) and exact["p_value"] is None
    with pytest.raises(stats.StatsInputError):
        stats.ratio_delta([1.0, 2.0], [1.0], [1.0, 2.0], [1.0, 1.0])


# -- chi-square -------------------------------------------------------------------


@pytest.mark.parametrize("case", CHI_IND, ids=lambda c: str(c["table"]))
def test_chi_square_independence_matches_scipy(case):
    result = stats.chi_square_independence(case["table"])
    close(result["chi2"], case["chi2"])
    close(result["p_value"], case["p_value"])
    close(result["cramers_v"], case["cramers_v"])
    assert result["df"] == case["df"]


def test_chi_square_independence_with_nothing_to_test():
    assert stats.chi_square_independence([[3, 4]])["p_value"] is None
    assert stats.chi_square_independence([[3, 0], [5, 0]])["df"] == 0
    assert stats.chi_square_independence([])["p_value"] is None
    with pytest.raises(stats.StatsInputError):
        stats.chi_square_independence([[1, 2], [3]])
    with pytest.raises(stats.StatsInputError):
        stats.chi_square_independence([[1, -2], [3, 4]])


@pytest.mark.parametrize("case", CHI_GOF, ids=lambda c: str(c["observed"]))
def test_chi_square_goodness_of_fit_matches_scipy(case):
    result = stats.chi_square_goodness_of_fit(case["observed"], case["expected"])
    close(result["chi2"], case["chi2"])
    close(result["p_value"], case["p_value"])
    assert result["df"] == case["df"]


def test_chi_square_goodness_of_fit_validation():
    assert stats.chi_square_goodness_of_fit([0, 0, 0])["p_value"] is None
    with pytest.raises(stats.StatsInputError):
        stats.chi_square_goodness_of_fit([5])
    with pytest.raises(stats.StatsInputError):
        stats.chi_square_goodness_of_fit([5, 3], [1, 0])
    with pytest.raises(stats.StatsInputError):
        stats.chi_square_goodness_of_fit([5, 3], [1, 2, 3])


# -- Holm, sample sizes, variance -------------------------------------------------


@pytest.mark.parametrize("p_values,expected", HOLM)
def test_holm_matches_the_step_down_procedure(p_values, expected):
    assert stats.holm(p_values) == pytest.approx(expected, rel=1e-12)


def test_holm_keeps_order_and_skips_none():
    adjusted = stats.holm([0.04, None, 0.01])
    assert adjusted[1] is None and adjusted[0] == pytest.approx(0.04) and adjusted[2] == pytest.approx(0.02)
    assert stats.holm([]) == []
    assert all(p <= 1.0 for p in stats.holm([0.9, 0.8, 0.7]))
    with pytest.raises(stats.StatsInputError):
        stats.holm([1.5])


@pytest.mark.parametrize("args,expected", SS_PROP)
def test_sample_size_proportion_matches_fleiss(args, expected):
    assert stats.sample_size_proportion(*args) == expected


@pytest.mark.parametrize("args,expected,power_at_n,power_below", SS_MEAN)
def test_sample_size_mean_reaches_the_power_of_the_exact_t_test(args, expected, power_at_n, power_below):
    assert stats.sample_size_mean(*args) == expected
    # Pinned from scipy's noncentral t: the smallest n that reaches the power.
    assert power_at_n >= args[3] > power_below
    assert stats._t_test_power(expected, args[0], args[1], args[2]) == pytest.approx(power_at_n, abs=1e-9)


def test_sample_size_validation_is_german():
    with pytest.raises(ValidationError) as excinfo:
        stats.sample_size_proportion(1.2, 0.01)
    assert "Basisrate" in excinfo.value.message
    with pytest.raises(stats.StatsInputError):
        stats.sample_size_proportion(0.5, 0.0)
    with pytest.raises(stats.StatsInputError):
        stats.sample_size_proportion(0.95, 0.1)
    with pytest.raises(stats.StatsInputError):
        stats.sample_size_mean(0.0, 1.0)
    with pytest.raises(stats.StatsInputError):
        stats.sample_size_mean(1.0, 1.0, power=1.0)
    with pytest.raises(stats.StatsInputError):
        stats.sample_size_mean(1.0, 1.0, alpha=float("nan"))


def test_variance_from_sufficient_statistics():
    assert stats.variance(6.0, 14.0, 3) == pytest.approx(1.0)
    assert stats.variance(6.0, 14.0, 1) is None
    assert stats.variance(6.0, None, 3) is None
    assert stats.variance(None, 14.0, 3) is None
    # Rounding can push sum_sq - sum^2/n slightly below zero.
    assert stats.variance(0.3, 0.03 - 1e-18, 3) == 0.0
    # sum^2 overflows: undefined rather than infinite.
    assert stats.variance(1e308, 1e308, 3) is None


# -- the module's promises --------------------------------------------------------


def test_input_errors_are_german_validation_errors_and_value_errors():
    with pytest.raises(ValueError):
        stats.normal_ppf(1.0)
    with pytest.raises(ValidationError) as excinfo:
        stats.student_t_cdf(1.0, 0)
    assert "gr\u00f6sser als 0" in excinfo.value.message
    for bad in (float("nan"), "1", None, True):
        with pytest.raises(stats.StatsInputError):
            stats.normal_cdf(bad)
    with pytest.raises(stats.StatsInputError):
        stats.two_proportion_test(1, 10, 1, 10, alpha=1.5)
    with pytest.raises(stats.StatsInputError):
        stats.chi2_ppf(0.5, float("inf"))


def _all_numbers(result):
    values = result.values() if isinstance(result, dict) else result
    for value in values:
        if value is not None:
            assert isinstance(value, (int, float)) and not isinstance(value, bool)
            assert math.isfinite(value), result


def test_results_never_contain_nan_or_infinity_on_random_inputs():
    rng = random.Random(20260928)
    for _ in range(400):
        n1, n2 = rng.choice([0, 1, 2, 5, 40, 10 ** 6]), rng.choice([0, 1, 3, 50, 10 ** 7])
        s1, n2s = rng.randint(0, n1) if n1 else 0, rng.randint(0, n2) if n2 else 0
        _all_numbers(stats.two_proportion_test(s1, n1, n2s, n2))
        _all_numbers(stats.poisson_rate_test(s1, n1, n2s, n2) if n1 and n2 else {})
        m1, m2 = rng.uniform(-1e3, 1e3), rng.uniform(-1e3, 1e3)
        v1, v2 = rng.choice([0.0, rng.uniform(0, 1e4)]), rng.choice([0.0, rng.uniform(0, 1e4)])
        _all_numbers(stats.welch_t_test(m1, v1, n1, m2, v2, n2))
        diffs = [rng.uniform(-1, 1) for _ in range(rng.randint(0, 6))]
        _all_numbers(stats.paired_t_test(diffs))
        xs = [rng.uniform(0, 10) for _ in range(rng.randint(0, 5))]
        ys = [rng.choice([0.0, rng.uniform(0, 10)]) for _ in xs]
        _all_numbers(stats.ratio_delta(xs, ys, xs[::-1], ys[::-1]))
        table = [[rng.choice([0, rng.randint(0, 30)]) for _ in range(3)] for _ in range(rng.randint(1, 4))]
        _all_numbers(stats.chi_square_independence(table))
    for result in (stats.wilson_interval(0, 0), stats.poisson_interval(0, 0)):
        assert result == (None, None)


def test_results_are_json_ready():
    result = stats.two_proportion_test(129, 10688, 175, 10714)
    json.dumps(result, allow_nan=False)
    json.dumps(stats.bayes_beta_binomial(129, 10688, 175, 10714), allow_nan=False)


def test_large_inputs_finish_quickly():
    started = time.perf_counter()
    stats.welch_t_test(1.0, 2.0, 10 ** 9, 1.001, 2.0, 10 ** 9)
    stats.binomial_test(4_990_000, 10_000_000, 0.5)
    stats.poisson_rate_test(3, 1000.0, 5_000_000, 20_000_000.0)
    stats.student_t_ppf(0.975, 3e9)
    stats.chi2_ppf(0.975, 4e8)
    stats.binom_cdf(5, 10 ** 9, 1e-8)
    assert time.perf_counter() - started < 3.0


def test_chi_square_with_counts_near_the_float_limit():
    small = stats.chi_square_independence([[1, 2], [3, 1]])
    huge = stats.chi_square_independence([[1e300, 2e300], [3e300, 1e300]])
    assert huge["chi2"] == pytest.approx(small["chi2"] * 1e300, rel=1e-12)
    assert huge["cramers_v"] == pytest.approx(small["cramers_v"], rel=1e-12)
    assert huge["p_value"] == 0.0
    assert stats.chi_square_independence([[1.7e308, 1.7e308], [1.7e308, 1.0]])["chi2"] is not None
    gof = stats.chi_square_goodness_of_fit([1.7e308, 1.7e308, 1.0])
    assert gof["chi2"] is None or math.isfinite(gof["chi2"])
    assert stats.chi_square_goodness_of_fit([3e300, 1e300])["chi2"] == pytest.approx(
        stats.chi_square_goodness_of_fit([3, 1])["chi2"] * 1e300, rel=1e-12)


def test_absurd_parameters_end_quickly_instead_of_hanging():
    started = time.perf_counter()
    # Far tails underflow: answered without evaluating a continued fraction.
    assert stats.betainc(1e300, 1e300, 0.1) == 0.0
    assert stats.betainc(1e300, 1e300, 0.9) == 1.0
    # At the mean of astronomically large parameters the continued fraction
    # would need millions of steps: a bounded budget, then ArithmeticError.
    with pytest.raises(ArithmeticError):
        stats.binom_cdf(5 * 10 ** 17, 10 ** 18, 0.5)
    assert time.perf_counter() - started < 3.0
