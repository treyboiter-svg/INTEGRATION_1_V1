"""odds_calc.py — OverlineEdge v8 (Python 3.14 compatible)"""
from __future__ import annotations
import math


def american_to_decimal(american) -> float | None:
    if american is None: return None
    try: a = float(american)
    except (TypeError, ValueError): return None
    if a == 0: return None
    return round((a/100)+1.0,6) if a>0 else round((100/abs(a))+1.0,6)

def american_to_implied(american) -> float | None:
    dec = american_to_decimal(american)
    if dec is None or dec <= 1.0: return None
    return round((1.0/dec)*100.0,4)

def implied_to_american(implied) -> int | None:
    if implied is None: return None
    try: p = float(implied)/100.0
    except (TypeError, ValueError): return None
    if p<=0.0 or p>=1.0: return None
    odds = -100.0*p/(1.0-p) if p>=0.5 else 100.0*(1.0-p)/p
    return int(round(odds))

def remove_vig_proportional(p1, p2):
    if p1 is None or p2 is None: return p1, p2
    total = p1+p2
    if total<=0: return p1, p2
    return round(p1/total*100.0,4), round(p2/total*100.0,4)

def vig_percent(p1, p2) -> float | None:
    if p1 is None or p2 is None: return None
    return round((p1+p2)-100.0,4)

def _power_k_solver(r1: float, r2: float, tol=1e-9, max_iter=500) -> float:
    """Newton-Raphson: find k where r1^k + r2^k = 1"""
    if r1<=0 or r2<=0: return 1.0
    k = 1.0
    for _ in range(max_iter):
        f  = r1**k + r2**k - 1.0
        fp = r1**k*math.log(r1) + r2**k*math.log(r2)
        if abs(fp)<1e-15: break
        k_new = k - f/fp
        if abs(k_new-k)<tol: k=k_new; break
        k = k_new
    return max(k, 0.01)

def remove_vig_power(p1, p2):
    if p1 is None or p2 is None: return p1, p2
    r1, r2 = p1/100.0, p2/100.0
    k = _power_k_solver(r1, r2)
    return round(r1**k*100.0,4), round(r2**k*100.0,4)

def power_odds(no_vig_list: list) -> float | None:
    valid = [v for v in no_vig_list if v is not None]
    return round(sum(valid)/len(valid),4) if valid else None

def ev_percent(true_prob, american_odds) -> float | None:
    if true_prob is None or american_odds is None: return None
    dec = american_to_decimal(american_odds)
    if dec is None: return None
    p = true_prob/100.0
    return round((p*(dec-1)-(1-p))*100.0,4)

def disparity_pct(book_implied, pred_implied) -> float | None:
    if book_implied is None or pred_implied is None: return None
    return round(abs(book_implied-pred_implied),4)

def build_odds_package(home_american: int, away_american: int) -> dict:
    h_raw = american_to_implied(home_american)
    a_raw = american_to_implied(away_american)
    h_prop, a_prop = remove_vig_proportional(h_raw, a_raw)
    h_pow,  a_pow  = remove_vig_power(h_raw, a_raw)
    return {
        "home": {"american":home_american,"raw_implied":h_raw,
                 "true_prob_prop":h_prop,"true_prob_power":h_pow,
                 "true_odds_prop":implied_to_american(h_prop),
                 "true_odds_power":implied_to_american(h_pow)},
        "away": {"american":away_american,"raw_implied":a_raw,
                 "true_prob_prop":a_prop,"true_prob_power":a_pow,
                 "true_odds_prop":implied_to_american(a_prop),
                 "true_odds_power":implied_to_american(a_pow)},
        "vig_pct": vig_percent(h_raw, a_raw),
    }

def build_implied_matrix(consensus_nv, kalshi, poly, pwr=None) -> dict:
    cons_am = implied_to_american(consensus_nv)
    return {
        "consensus_bookmaker": consensus_nv,
        "kalshi":              kalshi,
        "polymarket":          poly,
        "power_odds":          pwr if pwr is not None else consensus_nv,
        "kalshi_disparity":    disparity_pct(consensus_nv, kalshi),
        "poly_disparity":      disparity_pct(consensus_nv, poly),
        "kalshi_ev":           ev_percent(kalshi, cons_am) if kalshi and cons_am else None,
        "poly_ev":             ev_percent(poly,   cons_am) if poly   and cons_am else None,
    }
