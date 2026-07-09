"""
Moteur de classement — port fidèle des fonctions PURES de
``gsheet/TournoiBaseball_Script.gs`` (Baseball Québec, Art. 42.11).

Ce module ne touche NI la base de données NI Flask : il opère uniquement sur des
« game dicts » et des « stats dicts » en mémoire, exactement comme les fonctions
Apps Script d'origine (celles qui n'appelaient jamais SpreadsheetApp). Les clés
des dictionnaires reprennent volontairement les noms d'origine (camelCase :
``scoreLocal``, ``offVisiteur``, ``raRatio`` …) pour que la correspondance 1:1
avec le .gs reste évidente et vérifiable par la suite de tests portée.

Voir ``gsheet/CLAUDE.md`` pour la logique métier détaillée (fractions de manches,
Notes 4/5, bris d'égalité récursif à trois niveaux, Priorité 4 manuelle).
"""

from __future__ import annotations

import math
import re
from typing import Callable, Optional

# ---------------------------------------------------------------------------
#  Constantes
# ---------------------------------------------------------------------------

CLASSES = ['A', 'B']
POOLS = [1, 2, 3]
TEAMS_PER_POOL = 4
TOTAL_INNINGS = 6  # 13U = 6 manches réglementaires
INF = float('inf')


# ---------------------------------------------------------------------------
#  Résultat ordonné : liste + drapeau __needsManualCheck (comme en JS)
# ---------------------------------------------------------------------------

class OrderedResult(list):
    """Liste ordonnée d'équipes portant le drapeau ``needs_manual_check``.

    Réplique le motif JS ``ordered.__needsManualCheck = true`` : on renvoie une
    liste normale (itérable, indexable, ``join``-able via ``' > '.join(...)``) à
    laquelle on peut accrocher un attribut.
    """

    needs_manual_check: bool = False


# ---------------------------------------------------------------------------
#  Utilitaires numériques
# ---------------------------------------------------------------------------

def round3(x) -> float:
    """Arrondit à 3 décimales (comme ``round3`` du .gs)."""
    if x is None or x == '' or (isinstance(x, float) and math.isinf(x)):
        return x
    return round(float(x) + 0.0, 3)


def approx_equal(a, b) -> bool:
    """Comparaison de flottants avec tolérance (``approxEqual``)."""
    if a == INF and b == INF:
        return True
    if a == INF or b == INF:
        return False
    return abs(a - b) < 1e-9


def format_fraction(decimal) -> str:
    """Convertit un décimal de manches en format lisible (4.333 -> "4 1/3").

    Gère les tiers (1/3 et 2/3) ; arrondit au tiers le plus proche. Port de
    ``formatFraction``.
    """
    if decimal == '' or decimal is None:
        return ''
    num = float(decimal)
    whole = int(math.floor(num + 1e-9))
    frac = num - whole
    third = round(frac * 3)
    if third == 3:
        whole += 1
        third = 0
    if third == 0:
        return str(whole)
    frac_str = '1/3' if third == 1 else '2/3'
    if whole == 0:
        return frac_str
    return '{} {}'.format(whole, frac_str)


# ---------------------------------------------------------------------------
#  Interprétation de la Configuration / des saisies
# ---------------------------------------------------------------------------

def parse_pool_classe(raw) -> Optional[dict]:
    """Interprète "3A" -> {'pool': 3, 'classe': 'A'} (``parsePoolClasse``)."""
    s = str(raw).strip().upper()
    m = re.match(r'^(\d+)\s*([AB])$', s)
    if not m:
        return None
    return {'pool': int(m.group(1)), 'classe': m.group(2)}


def is_row_complete(v: dict) -> bool:
    """Détermine si une saisie de partie est COMPLÈTE (``isRowComplete``).

    ``v`` : dict avec clés scoreA, scoreB, local, manches, retraits, type, suppTie.
    On teste le VIDE (``''``/``None``), jamais la fausseté — un 0 est valide.
    """
    def filled(x):
        return x != '' and x is not None

    if not filled(v.get('scoreA')) or not filled(v.get('scoreB')):
        return False

    type_ = str(v.get('type', '')).strip()
    if not filled(type_):
        return False

    if type_ == 'Forfait':
        return True

    if not filled(v.get('local')) or not filled(v.get('manches')) or not filled(v.get('retraits')):
        return False

    if type_ == 'Supplémentaires':
        return filled(v.get('suppTie'))

    return True


def game_is_supp(g: dict) -> bool:
    """Une partie est-elle allée en manches supplémentaires ? (``gameIsSupp``)."""
    return bool(g) and g.get('type') == 'Supplémentaires'


# ---------------------------------------------------------------------------
#  Calcul des manches (fractions) — ``calculateInnings``
# ---------------------------------------------------------------------------

def calculate_innings(score_local, score_visiteur, manches_completes, retraits_en_fin,
                      type_fin, home_known=True, regulation=None) -> dict:
    """Calcule les manches offensives/défensives de la locale et de la visiteuse.

    Port fidèle de ``calculateInnings``. Retourne un dict
    {offLocal, defLocal, offVisiteur, defVisiteur}.
    """
    if home_known is None:
        home_known = True
    if not regulation:
        regulation = TOTAL_INNINGS

    N = manches_completes
    H = retraits_en_fin
    frac = H / 3.0
    partial = (N - 1) + frac

    # -------- FORFAIT --------
    if type_fin == 'Forfait':
        if score_local >= score_visiteur:
            return {'offLocal': 0, 'defLocal': regulation,
                    'offVisiteur': regulation, 'defVisiteur': 0}
        return {'offLocal': regulation, 'defLocal': 0,
                'offVisiteur': 0, 'defVisiteur': regulation}

    locale_wins_bottom = home_known and (score_local > score_visiteur)

    # -------- MERCY --------
    if type_fin == 'Mercy':
        if score_local > score_visiteur:
            if locale_wins_bottom:
                return {'offLocal': partial, 'defLocal': regulation,
                        'offVisiteur': N, 'defVisiteur': partial}
            return {'offLocal': N, 'defLocal': regulation,
                    'offVisiteur': N, 'defVisiteur': N}
        return {'offLocal': N, 'defLocal': N,
                'offVisiteur': N, 'defVisiteur': regulation}

    # -------- NORMAL --------
    if locale_wins_bottom:
        return {'offLocal': partial, 'defLocal': N,
                'offVisiteur': N, 'defVisiteur': partial}

    return {'offLocal': N, 'defLocal': N,
            'offVisiteur': N, 'defVisiteur': N}


# ---------------------------------------------------------------------------
#  Statistiques d'équipe — ``computeTeamStats``
# ---------------------------------------------------------------------------

def _val(x, fallback):
    return fallback if x is None else x


def compute_team_stats(team, games, exclude_forfait_ratios) -> dict:
    """Statistiques cumulées d'une équipe (``computeTeamStats``).

    Deux familles d'accumulateurs : base RÉGULIÈRE (ratios de bris, Note 4) et
    base RÉELLE (tableau de pool, suppl. incluses). ``exclude_forfait_ratios``
    (Note 5) exclut entièrement les forfaits des ratios de bris.
    """
    s = {
        'team': team, 'pj': 0, 'v': 0, 'd': 0,
        'rs': 0, 'ra': 0,
        'rsNum': 0, 'raNum': 0,
        'offInn': 0, 'defInn': 0,
        'rsRatio': 0, 'raRatio': 0,
        'offInnFull': 0, 'defInnFull': 0,
        'rsRatioFull': 0, 'raRatioFull': 0,
    }

    for g in games:
        is_local = (g['local'] == team)
        is_vis = (g['visiteur'] == team)
        if not is_local and not is_vis:
            continue

        s['pj'] += 1
        if g['winner'] == team:
            s['v'] += 1
        elif g['winner'] != '':
            s['d'] += 1

        team_score = g['scoreLocal'] if is_local else g['scoreVisiteur']
        opp_score = g['scoreVisiteur'] if is_local else g['scoreLocal']
        s['rs'] += team_score
        s['ra'] += opp_score

        if is_local:
            reg_team_runs = _val(g.get('regRsLocal'), g['scoreLocal'])
            reg_opp_runs = _val(g.get('regRsVisiteur'), g['scoreVisiteur'])
        else:
            reg_team_runs = _val(g.get('regRsVisiteur'), g['scoreVisiteur'])
            reg_opp_runs = _val(g.get('regRsLocal'), g['scoreLocal'])

        skip_ratio = (exclude_forfait_ratios and g['type'] == 'Forfait')
        if not skip_ratio:
            s['rsNum'] += reg_team_runs
            s['raNum'] += reg_opp_runs
            if is_local:
                s['offInn'] += _val(g.get('regOffLocal'), g['offLocal'])
                s['defInn'] += _val(g.get('regDefLocal'), g['defLocal'])
                s['offInnFull'] += g['offLocal']
                s['defInnFull'] += g['defLocal']
            else:
                s['offInn'] += _val(g.get('regOffVisiteur'), g['offVisiteur'])
                s['defInn'] += _val(g.get('regDefVisiteur'), g['defVisiteur'])
                s['offInnFull'] += g['offVisiteur']
                s['defInnFull'] += g['defVisiteur']

    s['raRatio'] = (s['raNum'] / s['defInn']) if s['defInn'] > 0 else INF
    s['rsRatio'] = (s['rsNum'] / s['offInn']) if s['offInn'] > 0 else 0
    s['raRatioFull'] = (s['ra'] / s['defInnFull']) if s['defInnFull'] > 0 else INF
    s['rsRatioFull'] = (s['rs'] / s['offInnFull']) if s['offInnFull'] > 0 else 0
    return s


# ---------------------------------------------------------------------------
#  Regroupement / portées
# ---------------------------------------------------------------------------

def group_by_metric(teams, metric_fn: Callable, descending: bool):
    """Groupe les équipes par valeur d'une métrique, du meilleur au moins bon.

    Retourne une liste de groupes (chaque groupe = équipes à égalité). Port de
    ``groupByMetric`` : tri STABLE, égalité via ``approx_equal``.
    """
    entries = [{'team': t, 'value': metric_fn(t)} for t in teams]

    # Tri stable, décroissant/croissant. Les infinis sont gérés par une clé
    # qui préserve l'ordre attendu (INF > tout fini).
    def sort_key(e):
        v = e['value']
        return -v if descending else v

    # Python trie de façon stable ; on convertit INF proprement.
    entries.sort(key=lambda e: (float('-inf') if (descending and e['value'] == INF)
                                else (float('inf') if (not descending and e['value'] == INF)
                                      else (-e['value'] if descending else e['value']))))

    groups = []
    current = []
    last_val = None
    for i, e in enumerate(entries):
        if i == 0 or approx_equal(e['value'], last_val):
            current.append(e['team'])
        else:
            groups.append(current)
            current = [e['team']]
        last_val = e['value']
    if current:
        groups.append(current)
    return groups


def head_to_head_games(teams, games):
    """Parties jouées strictement entre les équipes du groupe (``headToHeadGames``)."""
    return [g for g in games
            if g['local'] in teams and g['visiteur'] in teams]


# ---------------------------------------------------------------------------
#  Priorité 4 — override « Forcer rang » (``resolveForcedRanks``)
# ---------------------------------------------------------------------------

def resolve_forced_ranks(teams, forced):
    """Applique l'override « Forcer rang » (Priorité 4) à un sous-groupe.

    Retourne {'ordered': [...], 'resolved': bool}. Seul l'ordre RELATIF compte.
    Résolu ssi aucun doublon ET au plus une équipe sans numéro.
    """
    forced = forced or {}

    def sort_key(t):
        fa = forced.get(t)
        has = isinstance(fa, (int, float)) and not isinstance(fa, bool)
        # (0, num) pour les numérotées (triées par num) ; (1, nom) pour les autres.
        return (0, fa) if has else (1, t)

    ordered = sorted(teams, key=sort_key)

    nums = [forced[t] for t in teams
            if isinstance(forced.get(t), (int, float)) and not isinstance(forced.get(t), bool)]
    seen = {}
    has_dup = False
    for x in nums:
        if x in seen:
            has_dup = True
        seen[x] = True
    blanks = len(teams) - len(nums)
    resolved = (not has_dup) and blanks <= 1
    return {'ordered': ordered, 'resolved': resolved}


# ---------------------------------------------------------------------------
#  Application des priorités (Note 2) — ``applyPriorities``
# ---------------------------------------------------------------------------

def apply_priorities(group, relevant_games, use_all_games, start_p, forced):
    """Applique les priorités à partir de ``start_p`` sur une portée FIXÉE.

    Cœur de la Note 2 : une séparation partielle fait CONTINUER chaque sous-groupe
    à la priorité suivante sur la MÊME portée (pas de retour à P1, pas de
    re-restriction). P1/P2/P3 épuisées -> Priorité 4 (manuelle / « Forcer rang »).
    """
    if len(group) <= 1:
        return OrderedResult(group)
    forced = forced or {}

    def metric_p1(team):
        st = compute_team_stats(team, relevant_games, use_all_games)
        return st['v'] - st['d']

    def metric_p2(team):
        return compute_team_stats(team, relevant_games, use_all_games)['raRatio']

    def metric_p3(team):
        return compute_team_stats(team, relevant_games, use_all_games)['rsRatio']

    priorities = [
        (metric_p1, True),
        (metric_p2, False),
        (metric_p3, True),
    ]

    for i in range(start_p, 4):
        metric_fn, descending = priorities[i - 1]
        groups = group_by_metric(group, metric_fn, descending)
        if len(groups) > 1:
            ordered = OrderedResult()
            manual = False
            for sg in groups:
                sub = apply_priorities(sg, relevant_games, use_all_games, i + 1, forced)
                if getattr(sub, 'needs_manual_check', False):
                    manual = True
                ordered.extend(sub)
            if manual:
                ordered.needs_manual_check = True
            return ordered

    # P1..P3 épuisées : Priorité 4 (manuelle) -> override « Forcer rang ».
    res = resolve_forced_ranks(group, forced)
    ordered = OrderedResult(res['ordered'])
    if not res['resolved']:
        ordered.needs_manual_check = True
    return ordered


def tiebreaker(tied_teams, games, use_all_games, forced):
    """Démarre une passe de bris d'égalité à la Priorité 1 (``tiebreaker``).

    Fixe la PORTÉE : tête-à-tête (Étape A) ou toutes les parties « impliquant »
    (Étapes B/C), puis délègue à ``apply_priorities``.
    """
    if len(tied_teams) <= 1:
        return OrderedResult(tied_teams)

    if use_all_games:
        relevant_games = [g for g in games
                          if g['local'] in tied_teams or g['visiteur'] in tied_teams]
    else:
        relevant_games = head_to_head_games(tied_teams, games)

    return apply_priorities(list(tied_teams), relevant_games, use_all_games, 1, forced or {})


def order_teams(teams, games, use_all_games, forced=None):
    """Ordonne une liste d'équipes avec le bris d'égalité récursif (``orderTeams``).

    Étape A : structure à DEUX niveaux (regroupement par fiche GLOBALE, puis
    tête-à-tête par groupe). Étapes B/C : un seul groupe « impliquant ».
    """
    forced = forced or {}
    if len(teams) <= 1:
        return OrderedResult(teams)

    if use_all_games:
        return tiebreaker(list(teams), games, True, forced)

    # Étape A : regrouper par fiche globale (V-D sur tout le pool).
    def metric(t):
        st = compute_team_stats(t, games, False)
        return st['v'] - st['d']

    groups = group_by_metric(list(teams), metric, True)

    ordered = OrderedResult()
    manual = False
    for grp in groups:
        sub = tiebreaker(grp, games, False, forced)
        if getattr(sub, 'needs_manual_check', False):
            manual = True
        ordered.extend(sub)
    if manual:
        ordered.needs_manual_check = True
    return ordered


# ---------------------------------------------------------------------------
#  Classements de pool / étapes — ``calculatePoolStandings`` / ``calculateStep``
# ---------------------------------------------------------------------------

def calculate_pool_standings(games, teams, forced=None):
    """Classement d'un pool (Étape A). Retourne les stats triées avec ``rank``."""
    stats = [compute_team_stats(t, games, False) for t in teams]
    ordered = order_teams(teams, games, False, forced or {})

    rank_by_team = {t: i + 1 for i, t in enumerate(ordered)}
    for s in stats:
        s['rank'] = rank_by_team.get(s['team'], 99)
    stats.sort(key=lambda s: s['rank'])
    return stats


def calculate_step(teams, games, use_all_games, forced=None):
    """Étape B (meilleur 2e) ou C (classement des 1ers) — ``calculateStep``."""
    return order_teams(teams, games, use_all_games, forced or {})


# ---------------------------------------------------------------------------
#  Représentant du « Meilleur 2e » (Note 5) — ``resolveSecondRepresentative``
# ---------------------------------------------------------------------------

def resolve_second_representative(standings, marked_teams):
    """Détermine le représentant d'un pool au « Meilleur 2e » (Note 5).

    Retourne {'team', 'forced', 'warning'}. Le forçage a préséance sauf s'il vise
    la 1re équipe (déjà qualifiée Étape C) ou est ambigu -> repli sur le rang 2.
    """
    def team_at_rank(r):
        for s in standings:
            if s['rank'] == r:
                return s['team']
        return ''

    rank2 = team_at_rank(2)

    marks = [t for t in (marked_teams or [])
             if any(s['team'] == t for s in standings)]
    if not marks:
        return {'team': rank2, 'forced': False, 'warning': ''}

    rank1 = team_at_rank(1)
    invalid_first = [t for t in marks if t == rank1]
    valid = [t for t in marks if t != rank1]

    if len(valid) == 1:
        w = ('Marque « 2 » ignorée sur la 1re équipe (' + ', '.join(invalid_first) + ').'
             if invalid_first else '')
        return {'team': valid[0], 'forced': True, 'warning': w}
    if len(valid) > 1:
        return {'team': rank2, 'forced': False,
                'warning': 'Forçage du 2e ambigu (' + ', '.join(valid) +
                           ') — ignoré, 2e automatique conservé.'}
    return {'team': rank2, 'forced': False,
            'warning': 'Forçage du 2e sur la 1re équipe (' + ', '.join(invalid_first) +
                       ') — ignoré : elle est déjà qualifiée comme 1re (Étape C).'}


# ---------------------------------------------------------------------------
#  Critère décisif d'un bris d'égalité — ``decisiveCriterion``
# ---------------------------------------------------------------------------

P4_LABEL = '⚠ Manuel (P4)'


def decisive_criterion(hi, lo) -> str:
    """Critère (Art. 42.11) qui classe ``hi`` devant ``lo`` (``decisiveCriterion``)."""
    if (hi['v'] - hi['d']) != (lo['v'] - lo['d']):
        return 'Fiche {}-{} > {}-{}'.format(hi['v'], hi['d'], lo['v'], lo['d'])
    if not approx_equal(hi['raRatio'], lo['raRatio']):
        return 'RD {} < {}'.format(round3(hi['raRatio']), round3(lo['raRatio']))
    if not approx_equal(hi['rsRatio'], lo['rsRatio']):
        return 'RO {} > {}'.format(round3(hi['rsRatio']), round3(lo['rsRatio']))
    return P4_LABEL
