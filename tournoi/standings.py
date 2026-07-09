"""Couche « classements » : pont entre la base de données et le moteur pur.

Reproduit fidèlement les fonctions Apps Script qui lisaient les feuilles et
appelaient le moteur :
  - ``get_game_results``       ≈ getGameResults (forfait, calculateInnings, Note 4).
  - ``get_match_rows``         ≈ getMatchRows (vue publique « Résultats »).
  - ``compute_standings_model``≈ computeStandingsModel (modèle public épuré).
  - ``build_admin_model``      ≈ buildStandingsSheet + writePoolSection +
                                 writeTiebreakTable + writeAdvancementSection.
  - ``build_ledger``           ≈ buildLedgerRows + makeLedgerTx (grand livre).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .engine import (
    CLASSES, POOLS, TOTAL_INNINGS, INF,
    approx_equal, calculate_innings, calculate_pool_standings, calculate_step,
    compute_team_stats, decisive_criterion, format_fraction, game_is_supp,
    head_to_head_games, resolve_forced_ranks, resolve_second_representative,
    round3, P4_LABEL,
)
from .models import ForcedRank, Game, SecondOverride

MONTHS_FR = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juill.',
             'août', 'sept.', 'oct.', 'nov.', 'déc.']


# ---------------------------------------------------------------------------
#  Lecture des saisies -> game dicts du moteur
# ---------------------------------------------------------------------------

def _as_int(x, default=None):
    try:
        if x is None or x == '':
            return default
        return int(x)
    except (TypeError, ValueError):
        return default


def get_game_results(classe):
    """Lit les parties JOUÉES d'une classe et produit les game dicts du moteur.

    Port de ``getGameResults`` : normalisation des forfaits, calcul des manches
    (fractions), champs « base régulière » de la Note 4.
    """
    rows = (Game.query
            .filter_by(classe=classe)
            .order_by(Game.pool, Game.id)
            .all())
    games = []
    for g in rows:
        team_a = (g.team1 or '').strip()
        team_b = (g.team2 or '').strip()
        score_a = g.score1
        score_b = g.score2
        if score_a is None or score_b is None or team_a == '' or team_b == '':
            continue

        local_sel = (g.local_team or '').strip()
        manches = _as_int(g.manches)
        retraits = _as_int(g.retraits, 0)
        manches_prevues = _as_int(g.manches_prevues)
        type_ = (g.type_fin or 'Normal').strip() or 'Normal'
        supp_tie_raw = g.pointage_regl

        if manches is None or manches < 1:
            manches = TOTAL_INNINGS
        if retraits is None or retraits < 0:
            retraits = 0
        if manches_prevues is None or manches_prevues < 1:
            manches_prevues = TOTAL_INNINGS

        if local_sel == team_a:
            local, visiteur = team_a, team_b
            score_local, score_visiteur = score_a, score_b
            home_known = True
        elif local_sel == team_b:
            local, visiteur = team_b, team_a
            score_local, score_visiteur = score_b, score_a
            home_known = True
        else:
            local, visiteur = team_a, team_b
            score_local, score_visiteur = score_a, score_b
            home_known = False

        if type_ == 'Forfait':
            locale_gagne = (score_local >= score_visiteur)
            score_local = manches_prevues if locale_gagne else 0
            score_visiteur = 0 if locale_gagne else manches_prevues

        inn = calculate_innings(score_local, score_visiteur, manches, retraits,
                                type_, home_known, manches_prevues)

        if type_ == 'Forfait':
            winner = local if score_local >= score_visiteur else visiteur
        elif score_local > score_visiteur:
            winner = local
        elif score_visiteur > score_local:
            winner = visiteur
        else:
            winner = ''

        # ---- Note 4 : base régulière (manches supplémentaires exclues) ----
        is_supp = (type_ == 'Supplémentaires')
        supp_tie = _as_int(supp_tie_raw)
        supp_needs_tie = is_supp and (supp_tie is None or supp_tie < 0)
        if is_supp and not supp_needs_tie:
            reg_rs_local = reg_rs_visiteur = supp_tie
            reg_off_local = reg_def_local = manches_prevues
            reg_off_visiteur = reg_def_visiteur = manches_prevues
        else:
            reg_rs_local, reg_rs_visiteur = score_local, score_visiteur
            reg_off_local, reg_def_local = inn['offLocal'], inn['defLocal']
            reg_off_visiteur, reg_def_visiteur = inn['offVisiteur'], inn['defVisiteur']

        games.append({
            'pool': g.pool,
            'partie': g.partie,
            'rowId': g.id,
            'jour': g.jour or '',
            'heure': g.heure or '',
            'terrain': g.terrain or '',
            'local': local,
            'visiteur': visiteur,
            'homeKnown': home_known,
            'scoreLocal': score_local,
            'scoreVisiteur': score_visiteur,
            'manches': manches,
            'retraits': retraits,
            'manchesPrevues': manches_prevues,
            'type': type_,
            'winner': winner,
            'offLocal': inn['offLocal'],
            'defLocal': inn['defLocal'],
            'offVisiteur': inn['offVisiteur'],
            'defVisiteur': inn['defVisiteur'],
            'regRsLocal': reg_rs_local,
            'regRsVisiteur': reg_rs_visiteur,
            'regOffLocal': reg_off_local,
            'regDefLocal': reg_def_local,
            'regOffVisiteur': reg_off_visiteur,
            'regDefVisiteur': reg_def_visiteur,
            'suppNeedsTie': supp_needs_tie,
        })
    return games


def get_teams(classe):
    """Équipes d'une classe regroupées par pool, déduites de l'horaire (``getTeams``)."""
    result = {1: [], 2: [], 3: []}
    rows = Game.query.filter_by(classe=classe).order_by(Game.pool, Game.id).all()
    for g in rows:
        lst = result.get(g.pool)
        if lst is None:
            continue
        for nm in ((g.team1 or '').strip(), (g.team2 or '').strip()):
            if nm and nm not in lst:
                lst.append(nm)
    return result


def get_match_rows(classe):
    """Toutes les parties générées (jouées ou non) pour la vue publique « Résultats »."""
    rows = Game.query.filter_by(classe=classe).order_by(Game.pool, Game.id).all()
    out = []
    for g in rows:
        eq1 = (g.team1 or '').strip()
        eq2 = (g.team2 or '').strip()
        if eq1 == '' or eq2 == '':
            continue
        type_ = (g.type_fin or '').strip()
        has_score = g.score1 is not None and g.score2 is not None
        score_a = g.score1 if has_score else None
        score_b = g.score2 if has_score else None
        if has_score and type_ == 'Forfait':
            prevues = _as_int(g.manches_prevues, TOTAL_INNINGS) or TOTAL_INNINGS
            if score_a >= score_b:
                score_a, score_b = prevues, 0
            else:
                score_a, score_b = 0, prevues
        out.append({
            'partie': g.partie,
            'classe': classe,
            'pool': g.pool,
            'terrain': g.terrain or '',
            'eq1': eq1, 'eq2': eq2,
            'scoreA': score_a, 'scoreB': score_b,
            'lastInn': g.manches if g.manches not in (None, '') else None,
            'type': (type_ or 'Normal') if has_score else '',
            'played': has_score,
        })
    return out


# ---------------------------------------------------------------------------
#  Forçages admin (Note 5 / Priorité 4)
# ---------------------------------------------------------------------------

def read_second_overrides(classe):
    """{ nomÉquipe: True } pour les équipes marquées « Forcer 2e » (``readSecondOverrides``)."""
    return {o.team: True for o in SecondOverride.query.filter_by(classe=classe).all()}


def read_forced_ranks(classe):
    """{ scope: { nomÉquipe: rang } } pour « Forcer rang » (``readForcedRanks``)."""
    out = {}
    for fr in ForcedRank.query.filter_by(classe=classe).all():
        out.setdefault(fr.scope, {})[fr.team] = fr.rank
    return out


# ---------------------------------------------------------------------------
#  Horodatage d'affichage (« Mis à jour : … »)
# ---------------------------------------------------------------------------

def _updated_at_label():
    try:
        from zoneinfo import ZoneInfo
        from flask import current_app
        tz = ZoneInfo(current_app.config.get('DISPLAY_TZ', 'America/Montreal'))
        now = datetime.now(tz)
    except Exception:
        now = datetime.now(timezone.utc)
    return '{} {} {} à {:02d}h{:02d}'.format(
        now.day, MONTHS_FR[now.month - 1], now.year, now.hour, now.minute)


# ---------------------------------------------------------------------------
#  Modèle PUBLIC épuré — ``computeStandingsModel``
# ---------------------------------------------------------------------------

def compute_standings_model(classe):
    override_by_team = read_second_overrides(classe)
    forced_ranks = read_forced_ranks(classe)

    games = get_game_results(classe)
    teams_by_pool = get_teams(classe)

    firsts = []            # { team, pool }
    seconds = []           # { team, pool, forced }
    pool_stats_by_team = {}
    pools = []

    for p in POOLS:
        teams = teams_by_pool.get(p, [])
        pool_games = [g for g in games if g['pool'] == p]
        forced_pool = forced_ranks.get('A' + str(p), {})
        standings = calculate_pool_standings(pool_games, teams, forced_pool)
        marked = [t for t in teams if override_by_team.get(t)]
        second_rep = resolve_second_representative(standings, marked)

        supp_games = [g for g in pool_games if game_is_supp(g)]
        supp_resolved = [g for g in supp_games if not g['suppNeedsTie']]
        supp_missing = [g for g in supp_games if g['suppNeedsTie']]

        pools.append({
            'pool': p,
            'standings': [{
                'rank': s['rank'], 'team': s['team'], 'pj': s['pj'],
                'v': s['v'], 'd': s['d'], 'rs': s['rs'], 'ra': s['ra'],
                'mo': format_fraction(s['offInnFull']),
                'md': format_fraction(s['defInnFull']),
                'rd': (s['raRatioFull'] if (s['defInnFull'] > 0 and s['raRatioFull'] != INF) else None),
                'ro': (s['rsRatioFull'] if s['offInnFull'] > 0 else None),
            } for s in standings],
            'banners': {
                'note4': len(supp_resolved) > 0,
                'suppMissing': len(supp_missing) > 0,
                'forcedSecond': second_rep['forced'],
                'secondTeam': second_rep['team'],
                'secondWarning': second_rep['warning'] or '',
            },
        })

        for s in standings:
            pool_stats_by_team[s['team']] = s
            if s['rank'] == 1:
                firsts.append({'team': s['team'], 'pool': p})
        seconds.append({'team': second_rep['team'], 'pool': p, 'forced': second_rep['forced']})

    ordered_firsts = calculate_step([f['team'] for f in firsts], games, True,
                                    forced_ranks.get('C', {}))
    ordered_seconds = calculate_step([s['team'] for s in seconds], games, True,
                                     forced_ranks.get('B', {}))

    pool_of = {}
    for f in firsts:
        pool_of[f['team']] = f['pool']
    for s in seconds:
        pool_of[s['team']] = s['pool']

    def with_pool(name):
        return {'team': name, 'pool': pool_of.get(name, '')}

    seed_by_team = {}
    for i, name in enumerate(ordered_firsts):
        seed_by_team[name] = i + 1
    if len(ordered_seconds) > 0:
        seed_by_team[ordered_seconds[0]] = 4
    for pc in pools:
        for s in pc['standings']:
            s['seed'] = seed_by_team.get(s['team'])

    seconds_card = []
    for i, name in enumerate(ordered_seconds):
        st = pool_stats_by_team.get(name, {})
        seconds_card.append({
            'team': name,
            'pool': pool_of.get(name, ''),
            'v': st.get('v', 0),
            'd': st.get('d', 0),
            'rd': (st['raRatioFull'] if (st.get('defInnFull', 0) > 0 and st.get('raRatioFull') != INF) else None),
            'seed': 4 if i == 0 else None,
        })

    p1 = ordered_firsts[0] if len(ordered_firsts) > 0 else ''
    p2 = ordered_firsts[1] if len(ordered_firsts) > 1 else ''
    p3 = ordered_firsts[2] if len(ordered_firsts) > 2 else ''
    p4 = ordered_seconds[0] if len(ordered_seconds) > 0 else ''

    return {
        'classe': classe,
        'pools': pools,
        'firsts': [with_pool(n) for n in ordered_firsts],
        'seconds': seconds_card,
        'semifinals': {
            'positions': [with_pool(p1), with_pool(p2), with_pool(p3), with_pool(p4)],
            'demi1': {'a': p1, 'b': p4},
            'demi2': {'a': p2, 'b': p3},
        },
        'updatedAt': _updated_at_label(),
    }


# ---------------------------------------------------------------------------
#  Tableau de bris d'égalité (admin) — ``writeTiebreakTable``
# ---------------------------------------------------------------------------

def _tiebreak_table(teams, games, ordered_names, use_all_games, forced):
    """Reproduit la donnée du bloc « BRIS D'ÉGALITÉ » (base régulière, Note 4)."""
    forced = forced or {}
    if use_all_games:
        group_scope = [g for g in games
                       if g['local'] in teams or g['visiteur'] in teams]
    else:
        group_scope = games

    group_stat = {t: compute_team_stats(t, group_scope, use_all_games) for t in ordered_names}

    def vd(t):
        return group_stat[t]['v'] - group_stat[t]['d']

    runs = []
    for t in ordered_names:
        if runs and vd(runs[-1][0]) == vd(t):
            runs[-1].append(t)
        else:
            runs.append([t])
    tie_groups = [r for r in runs if len(r) >= 2]

    any_supp = False
    groups_out = []

    for grp in tie_groups:
        scope = group_scope if use_all_games else head_to_head_games(grp, games)
        if any(game_is_supp(g) for g in scope):
            any_supp = True
        stat_by_team = {t: compute_team_stats(t, scope, use_all_games) for t in grp}

        # Sous-groupes Priorité 4 (couples consécutifs non départagés).
        run_id_of = {}
        p4runs = []
        for i, t in enumerate(grp):
            if i == 0:
                continue
            if decisive_criterion(stat_by_team[grp[i - 1]], stat_by_team[t]) == P4_LABEL:
                prev = grp[i - 1]
                if prev not in run_id_of:
                    run_id_of[prev] = len(p4runs)
                    p4runs.append([prev])
                run_id_of[t] = run_id_of[prev]
                p4runs[run_id_of[t]].append(t)
        run_resolved = [resolve_forced_ranks(rn, forced)['resolved'] for rn in p4runs]

        rows = []
        for i, t in enumerate(grp):
            s = stat_by_team[t]
            rd = ('{:.3f}'.format(s['raRatio']) if (s['defInn'] > 0 and s['raRatio'] != INF) else '—')
            ro = ('{:.3f}'.format(s['rsRatio']) if s['offInn'] > 0 else '—')
            crit = '—' if i == 0 else decisive_criterion(stat_by_team[grp[i - 1]], s)
            in_p4 = t in run_id_of
            if crit == P4_LABEL and in_p4 and run_resolved[run_id_of[t]]:
                crit = '🔒 Forcé (P4)'
            rows.append({
                'team': t,
                'vd': '{}-{}'.format(s['v'], s['d']),
                'pp': s['rsNum'],
                'pc': s['raNum'],
                'mo': format_fraction(s['offInn']),
                'md': format_fraction(s['defInn']),
                'rd': rd,
                'ro': ro,
                'crit': crit,
                'inP4': in_p4,
                'forcedValue': forced.get(t, ''),
            })
        groups_out.append(rows)

    return {'groups': groups_out, 'anySupp': any_supp, 'hasTies': len(tie_groups) > 0,
            'scopeLabel': 'toutes parties de pool' if use_all_games else 'tête-à-tête'}


# ---------------------------------------------------------------------------
#  Modèle ADMIN complet — ``buildStandingsSheet`` & sections
# ---------------------------------------------------------------------------

def build_admin_model(classe):
    override_by_team = read_second_overrides(classe)
    forced_ranks = read_forced_ranks(classe)
    games = get_game_results(classe)
    teams_by_pool = get_teams(classe)

    firsts = []
    seconds = []
    pool_stats_by_team = {}
    pool_data = []

    for p in POOLS:
        teams = teams_by_pool.get(p, [])
        pool_games = [g for g in games if g['pool'] == p]
        forced_pool = forced_ranks.get('A' + str(p), {})
        standings = calculate_pool_standings(pool_games, teams, forced_pool)
        marked = [t for t in teams if override_by_team.get(t)]
        second_rep = resolve_second_representative(standings, marked)
        pool_data.append({'pool': p, 'standings': standings, 'poolGames': pool_games,
                          'marked': marked, 'secondRep': second_rep, 'forcedPool': forced_pool})
        for s in standings:
            pool_stats_by_team[s['team']] = s
            if s['rank'] == 1:
                firsts.append({'team': s['team'], 'pool': p})
        seconds.append({'team': second_rep['team'], 'pool': p, 'forced': second_rep['forced']})

    forced_c = forced_ranks.get('C', {})
    ordered_firsts = calculate_step([f['team'] for f in firsts], games, True, forced_c)
    forced_b = forced_ranks.get('B', {})
    ordered_seconds = calculate_step([s['team'] for s in seconds], games, True, forced_b)

    seed_by_team = {}
    for i, team in enumerate(ordered_firsts[:3]):
        seed_by_team[team] = i + 1
    if ordered_seconds:
        seed_by_team[ordered_seconds[0]] = 4

    # -------- Sections de pool --------
    pool_sections = []
    for pd in pool_data:
        standings = pd['standings']
        pool_games = pd['poolGames']
        supp_games = [g for g in pool_games if game_is_supp(g)]
        supp_resolved = [g for g in supp_games if not g['suppNeedsTie']]
        supp_missing = [g for g in supp_games if g['suppNeedsTie']]

        def game_label(g):
            return '{} vs {}{}'.format(
                g['local'], g['visiteur'],
                ' (partie #{})'.format(g['partie']) if g['partie'] else '')

        rows = []
        for s in standings:
            rows.append({
                'rank': s['rank'], 'team': s['team'], 'pj': s['pj'],
                'v': s['v'], 'd': s['d'], 'pp': s['rs'], 'pc': s['ra'],
                'mo': format_fraction(s['offInnFull']),
                'md': format_fraction(s['defInnFull']),
                'rd': ('{:.3f}'.format(s['raRatioFull']) if s['defInnFull'] > 0 else '—'),
                'ro': ('{:.3f}'.format(s['rsRatioFull']) if s['offInnFull'] > 0 else '—'),
                'seed': seed_by_team.get(s['team'], ''),
                'forced2e': s['team'] in pd['marked'],
            })

        ordered_names = [s['team'] for s in standings]
        tb = _tiebreak_table(ordered_names, pool_games, ordered_names, False, pd['forcedPool'])

        pool_sections.append({
            'pool': pd['pool'],
            'rows': rows,
            'secondRep': pd['secondRep'],
            'suppResolved': [game_label(g) for g in supp_resolved],
            'suppMissing': [game_label(g) for g in supp_missing],
            'tiebreak': tb,
            'scope': 'A' + str(pd['pool']),
        })

    # -------- Sections d'avancement (Étape C puis B) --------
    def advancement_section(title, ordered_teams, pool_info, base_position, scope, forced):
        pool_of = {}
        forced_team = {}
        for info in pool_info:
            pool_of[info['team']] = info['pool']
            if info.get('forced'):
                forced_team[info['team']] = True
        needs_manual = getattr(ordered_teams, 'needs_manual_check', False)

        rows = []
        for idx, team in enumerate(ordered_teams):
            team_games = [g for g in games if g['local'] == team or g['visiteur'] == team]
            st = compute_team_stats(team, team_games, True)
            note_parts = []
            if forced_team.get(team):
                note_parts.append('🔒 2e forcé par le registraire (Note 5)')
            if needs_manual:
                note_parts.append('⚠ Vérif. manuelle (P4) — saisir « Forcer rang »')
            elif isinstance(forced.get(team), (int, float)):
                note_parts.append('🔒 Rang forcé (P4)')
            if any(g['suppNeedsTie'] for g in team_games):
                note_parts.append('⚠ Suppl. : pointage régl. manquant')
            elif any(game_is_supp(g) for g in team_games):
                note_parts.append('ℹ Note 4 appliquée (suppl.)')
            rows.append({
                'position': base_position + idx,
                'team': team,
                'pool': pool_of.get(team, ''),
                'v': st['v'], 'd': st['d'],
                'rd': (round3(st['raRatio']) if st['raRatio'] != INF else '—'),
                'ro': round3(st['rsRatio']),
                'pp': st['rs'], 'pc': st['ra'],
                'note': ' '.join(note_parts),
                'qualifies': (base_position == 1) or (idx == 0),
            })
        tb = _tiebreak_table(list(ordered_teams), games, list(ordered_teams), True, forced)
        return {'title': title, 'rows': rows, 'tiebreak': tb, 'scope': scope,
                'basePosition': base_position}

    step_c = advancement_section(
        'SECTION 4 — CLASSEMENT DES 1ers (Positions 1-2-3) — Étape C',
        ordered_firsts, firsts, 1, 'C', forced_c)
    step_b = advancement_section(
        'SECTION 5 — MEILLEUR 2e (Position 4) — Étape B',
        ordered_seconds, seconds, 4, 'B', forced_b)

    p1 = ordered_firsts[0] if len(ordered_firsts) > 0 else '—'
    p2 = ordered_firsts[1] if len(ordered_firsts) > 1 else '—'
    p3 = ordered_firsts[2] if len(ordered_firsts) > 2 else '—'
    p4 = ordered_seconds[0] if len(ordered_seconds) > 0 else '—'

    return {
        'classe': classe,
        'pools': pool_sections,
        'stepC': step_c,
        'stepB': step_b,
        'semifinals': {
            'positions': [p1, p2, p3, p4],
            'demi1': '{}  vs  {}'.format(p1, p4),
            'demi2': '{}  vs  {}'.format(p2, p3),
        },
        'updatedAt': _updated_at_label(),
    }


# ---------------------------------------------------------------------------
#  Grand livre des matchs — ``buildLedgerRows`` / ``makeLedgerTx``
# ---------------------------------------------------------------------------

def _make_ledger_tx(classe, g, is_local):
    equipe = g['local'] if is_local else g['visiteur']
    adversaire = g['visiteur'] if is_local else g['local']
    pp_row = g['scoreLocal'] if is_local else g['scoreVisiteur']
    pc_row = g['scoreVisiteur'] if is_local else g['scoreLocal']
    mo_row = g['offLocal'] if is_local else g['offVisiteur']
    md_row = g['defLocal'] if is_local else g['defVisiteur']
    if g['winner'] == equipe:
        resultat = 'Victoire'
    elif g['winner'] == '':
        resultat = 'Nul'
    else:
        resultat = 'Défaite'
    loc_vis = 'Inconnu' if g['homeKnown'] is False else ('Local' if is_local else 'Visiteur')
    pointage_regl = (g['regRsLocal'] if (g['type'] == 'Supplémentaires' and not g['suppNeedsTie']) else '')
    return {
        'classe': classe, 'pool': g['pool'], 'partie': g['partie'],
        'jour': g['jour'], 'heure': g['heure'], 'terrain': g['terrain'],
        'equipe': equipe, 'adversaire': adversaire, 'resultat': resultat,
        'score': '{}-{}'.format(pp_row, pc_row), 'locVis': loc_vis,
        'manches': g['manches'], 'retraits': g['retraits'],
        'manchesPrevues': g['manchesPrevues'], 'type': g['type'],
        'pointageRegl': pointage_regl,
        'ppRow': pp_row, 'pcRow': pc_row, 'moRow': mo_row, 'mdRow': md_row,
    }


def build_ledger():
    """Grand livre : une ligne = une équipe dans un match, cumuls progressifs."""
    tx = []
    for classe in CLASSES:
        for g in get_game_results(classe):
            tx.append(_make_ledger_tx(classe, g, True))
            tx.append(_make_ledger_tx(classe, g, False))

    def sort_key(t):
        pa = _as_int(t['partie'])
        return (t['classe'], t['pool'], t['equipe'].lower(),
                0 if pa is not None else 1, pa if pa is not None else 0, str(t['partie']))

    tx.sort(key=sort_key)

    rows = []
    cum = None
    cur_key = None
    for t in tx:
        key = '{}|{}|{}'.format(t['classe'], t['pool'], t['equipe'])
        if key != cur_key:
            cum = {'pp': 0, 'pc': 0, 'mo': 0, 'md': 0}
            cur_key = key
        cum['pp'] += t['ppRow']
        cum['pc'] += t['pcRow']
        cum['mo'] += t['moRow']
        cum['md'] += t['mdRow']
        rd = '{:.3f}'.format(cum['pc'] / cum['md']) if cum['md'] > 0 else '—'
        ro = '{:.3f}'.format(cum['pp'] / cum['mo']) if cum['mo'] > 0 else '—'
        rows.append({
            'classe': t['classe'], 'pool': t['pool'], 'partie': t['partie'],
            'jour': t['jour'], 'heure': t['heure'], 'terrain': t['terrain'],
            'equipe': t['equipe'], 'adversaire': t['adversaire'],
            'resultat': t['resultat'], 'score': t['score'], 'locVis': t['locVis'],
            'manches': t['manches'], 'retraits': t['retraits'],
            'manchesPrevues': t['manchesPrevues'], 'type': t['type'],
            'pointageRegl': t['pointageRegl'],
            'pcRow': t['pcRow'], 'cumPc': cum['pc'],
            'mdRow': format_fraction(t['mdRow']), 'cumMd': format_fraction(cum['md']), 'rd': rd,
            'ppRow': t['ppRow'], 'cumPp': cum['pp'],
            'moRow': format_fraction(t['moRow']), 'cumMo': format_fraction(cum['mo']), 'ro': ro,
        })
    return rows
