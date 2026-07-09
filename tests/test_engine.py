"""
Port pytest de ``gsheet/tests/test_tiebreaker.js``.

Vérifie que le moteur Python (``tournoi.engine``) reproduit EXACTEMENT le
comportement des fonctions pures Apps Script : bascule de bris via ratio (P2),
régression PDF (Note 2), Étape A tête-à-tête, Priorité 4 manuelle + override
« Forcer rang », isRowComplete, gameIsSupp, calculateInnings (manches prévues +
victoire locale), Note 4 (exclusion suppl.), Note 5 (forfaits) et forçage du 2e.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tournoi.engine import (  # noqa: E402
    calculate_innings, calculate_pool_standings, compute_team_stats,
    game_is_supp, is_row_complete, order_teams, resolve_forced_ranks,
    resolve_second_representative,
)


# --- Aides de construction d'un scénario ------------------------------------

def game(pool, a, b, sa, sb):
    """Partie complète : 6 manches, fin Normal -> manches symétriques 6/6."""
    winner = a if sa > sb else (b if sb > sa else '')
    return {
        'pool': pool, 'local': a, 'visiteur': b,
        'scoreLocal': sa, 'scoreVisiteur': sb,
        'winner': winner, 'type': 'Normal',
        'offLocal': 6, 'defLocal': 6, 'offVisiteur': 6, 'defVisiteur': 6,
    }


TEAMS = ['Kamouraska', 'RiviereDuLoup', 'Temiscouata', 'Montmagny']


def build_games(temi_vs_kam):
    return [
        game(1, 'Kamouraska', 'RiviereDuLoup', 10, 0),
        game(1, 'RiviereDuLoup', 'Temiscouata', 5, 4),
        game(1, 'Temiscouata', 'Kamouraska', temi_vs_kam, 1),
        game(1, 'Kamouraska', 'Montmagny', 7, 0),
        game(1, 'RiviereDuLoup', 'Montmagny', 7, 0),
        game(1, 'Temiscouata', 'Montmagny', 7, 0),
    ]


def rank_of(stats, team):
    for s in stats:
        if s['team'] == team:
            return s['rank']
    return -1


def approx(a, b):
    return abs(a - b) < 1e-9


# --- Bascule de bris d'égalité via ratio (P2) -------------------------------

def test_tiebreaker_p2_flip():
    before = calculate_pool_standings(build_games(2), TEAMS)
    after = calculate_pool_standings(build_games(9), TEAMS)

    assert rank_of(before, 'Kamouraska') == 1
    assert rank_of(before, 'Temiscouata') == 2
    assert rank_of(after, 'Temiscouata') == 1
    assert rank_of(after, 'Kamouraska') == 2
    assert rank_of(before, 'RiviereDuLoup') == 3
    assert rank_of(after, 'RiviereDuLoup') == 3


# --- Régression PDF officiel (Note 2) : QC > CN > RS ------------------------

def test_pdf_note2_regression():
    pdf_games = [
        game(1, 'QC', 'RS', 6, 3),
        game(1, 'CN', 'QC', 6, 4),
        game(1, 'RS', 'CN', 10, 8),
    ]
    order = order_teams(['QC', 'RS', 'CN'], pdf_games, False)
    assert ' > '.join(order) == 'QC > CN > RS'


# --- Étape A restreinte au tête-à-tête : C > B > A > D -----------------------

def test_step_a_head_to_head():
    h2h = [
        game(1, 'A', 'B', 1, 5),
        game(1, 'C', 'A', 5, 1),
        game(1, 'A', 'D', 5, 1),
        game(1, 'C', 'B', 7, 2),
        game(1, 'B', 'D', 8, 0),
        game(1, 'D', 'C', 8, 0),
    ]
    order = order_teams(['A', 'B', 'C', 'D'], h2h, False)
    assert ' > '.join(order) == 'C > B > A > D'
    assert order.needs_manual_check is not True


# --- Priorité 4 (manuelle) après épuisement des ratios ----------------------

P4_GAMES = [
    game(1, 'A', 'B', 5, 3),
    game(1, 'B', 'C', 5, 3),
    game(1, 'C', 'A', 5, 3),
]


def test_priority4_manual():
    order = order_teams(['A', 'B', 'C'], P4_GAMES, False)
    assert ' > '.join(order) == 'A > B > C'
    assert order.needs_manual_check is True


# --- Override « Forcer rang » (résolution manuelle P4) ----------------------

def test_forced_ranks_override():
    o1 = order_teams(['A', 'B', 'C'], P4_GAMES, False, {'C': 1, 'B': 2})
    o2 = order_teams(['A', 'B', 'C'], P4_GAMES, False, {'A': 1, 'C': 2})
    o3 = order_teams(['A', 'B', 'C'], P4_GAMES, False, {'A': 1, 'B': 1})

    assert ' > '.join(o1) == 'C > B > A'
    assert o1.needs_manual_check is not True
    assert ' > '.join(o2) == 'A > C > B' and o2.needs_manual_check is not True
    assert o3.needs_manual_check is True

    r = resolve_forced_ranks(['B', 'C'], {'C': 1, 'B': 2})
    assert r['resolved'] is True and ''.join(r['ordered']) == 'CB'
    assert resolve_forced_ranks(['B', 'C'], {})['resolved'] is False


# --- isRowComplete (gate du recalcul live) ----------------------------------

def _rc(sa, sb, local, manches, retraits, type_, supp_tie=''):
    return is_row_complete({'scoreA': sa, 'scoreB': sb, 'local': local,
                            'manches': manches, 'retraits': retraits,
                            'type': type_, 'suppTie': supp_tie})


def test_is_row_complete():
    assert _rc(5, 3, 'Kamouraska', 6, 0, 'Normal') is True
    assert _rc(0, 0, 'Kamouraska', 6, 0, 'Normal') is True
    assert _rc(4, 2, 'Kamouraska', 5, 0, 'Normal') is True
    assert _rc(7, 0, '', '', '', 'Forfait') is True
    assert _rc(5, '', 'Kamouraska', 6, 0, 'Normal') is False
    assert _rc(5, 3, 'Kamouraska', '', 0, 'Normal') is False
    assert _rc(5, 3, '', 6, 0, 'Normal') is False
    assert _rc(5, 3, 'Kamouraska', 6, 0, '') is False
    assert _rc(7, 5, 'Kamouraska', 7, 0, 'Supplémentaires', 5) is True
    assert _rc(7, 5, 'Kamouraska', 7, 0, 'Supplémentaires', '') is False
    assert _rc(2, 1, 'Kamouraska', 7, 0, 'Supplémentaires', 0) is True


def test_game_is_supp():
    assert game_is_supp({'type': 'Supplémentaires'}) is True
    assert game_is_supp({'type': 'Normal'}) is False
    assert game_is_supp({}) is False


# --- calculateInnings (manches prévues + victoire locale) -------------------

def _inn_eq(inn, ol, dl, ov, dv):
    return (approx(inn['offLocal'], ol) and approx(inn['defLocal'], dl) and
            approx(inn['offVisiteur'], ov) and approx(inn['defVisiteur'], dv))


def test_calculate_innings():
    assert _inn_eq(calculate_innings(7, 5, 6, 0, 'Normal', True, 6), 5, 6, 6, 5)
    assert _inn_eq(calculate_innings(6, 5, 6, 2, 'Normal', True, 6),
                   5 + 2 / 3, 6, 6, 5 + 2 / 3)
    assert _inn_eq(calculate_innings(10, 0, 5, 0, 'Mercy', True, 5), 4, 5, 5, 4)
    assert _inn_eq(calculate_innings(0, 12, 5, 0, 'Mercy', True, 5), 5, 5, 5, 5)
    assert _inn_eq(calculate_innings(3, 7, 6, 0, 'Normal', True, 6), 6, 6, 6, 6)
    assert _inn_eq(calculate_innings(7, 5, 6, 0, 'Normal', False, 6), 6, 6, 6, 6)
    assert _inn_eq(calculate_innings(6, 0, 6, 0, 'Forfait', True, 6), 0, 6, 6, 0)
    assert _inn_eq(calculate_innings(0, 5, 5, 0, 'Forfait', True, 5), 5, 0, 0, 5)


# --- Note 4 : exclusion des manches supplémentaires du ratio ----------------

def _supp_game(local, vis, final_loc, final_vis, tie, reg):
    return {
        'pool': 1, 'local': local, 'visiteur': vis,
        'scoreLocal': final_loc, 'scoreVisiteur': final_vis,
        'winner': local if final_loc > final_vis else vis,
        'type': 'Supplémentaires',
        'offLocal': 7, 'defLocal': 7, 'offVisiteur': 7, 'defVisiteur': 7,
        'regRsLocal': tie, 'regRsVisiteur': tie,
        'regOffLocal': reg, 'regDefLocal': reg,
        'regOffVisiteur': reg, 'regDefVisiteur': reg,
        'suppNeedsTie': False,
    }


def test_note4_extra_innings_excluded():
    s = compute_team_stats('X', [_supp_game('X', 'Y', 7, 5, 5, 6)], False)
    assert s['rs'] == 7
    assert s['ra'] == 5
    assert approx(s['rsRatio'], 5 / 6)
    assert approx(s['raRatio'], 5 / 6)
    assert approx(s['offInn'], 6)
    assert approx(s['defInn'], 6)


# --- Note 5 : forfaits exclus des ratios du « Meilleur deuxième » -----------

def _forfait_game(local, vis):
    return {
        'pool': 1, 'local': local, 'visiteur': vis,
        'scoreLocal': 6, 'scoreVisiteur': 0, 'winner': local, 'type': 'Forfait',
        'offLocal': 0, 'defLocal': 6, 'offVisiteur': 6, 'defVisiteur': 0,
    }


def test_note5_forfait_excluded():
    f_win = compute_team_stats('W', [_forfait_game('W', 'L')], False)
    f_win_x = compute_team_stats('W', [_forfait_game('W', 'L')], True)
    f_lose = compute_team_stats('L', [_forfait_game('W', 'L')], False)

    assert f_win['rsNum'] == 6 and f_win['raNum'] == 0
    assert approx(f_win['offInn'], 0) and approx(f_win['defInn'], 6)
    assert approx(f_lose['offInn'], 6) and approx(f_lose['defInn'], 0)
    assert (f_win_x['rsNum'] == 0 and f_win_x['raNum'] == 0 and
            approx(f_win_x['offInn'], 0) and approx(f_win_x['defInn'], 0))
    assert f_win_x['v'] == 1 and f_win_x['d'] == 0
    assert f_win_x['rs'] == 6 and f_win_x['ra'] == 0


# --- Forçage admin du « 2e de pool » (resolveSecondRepresentative) ----------

def _standing(team, rank):
    return {'team': team, 'rank': rank}


POOL_STD = [_standing('A', 1), _standing('B', 2), _standing('C', 3), _standing('D', 4)]


def test_resolve_second_representative():
    rsr = resolve_second_representative
    assert rsr(POOL_STD, [])['team'] == 'B' and rsr(POOL_STD, [])['forced'] is False
    assert rsr(POOL_STD, ['C'])['team'] == 'C' and rsr(POOL_STD, ['C'])['forced'] is True
    assert rsr(POOL_STD, ['B'])['team'] == 'B' and rsr(POOL_STD, ['B'])['forced'] is True

    r = rsr(POOL_STD, ['A'])
    assert r['team'] == 'B' and r['forced'] is False and r['warning'] != ''
    r = rsr(POOL_STD, ['C', 'D'])
    assert r['team'] == 'B' and r['forced'] is False and r['warning'] != ''
    assert rsr(POOL_STD, ['Z'])['team'] == 'B' and rsr(POOL_STD, ['Z'])['forced'] is False
