"""Zone registraire/admin : saisie des résultats, forçages, horaire, grand livre.

Reproduit les actions du menu « 🏆 Tournoi Baseball » d'origine (Générer/Saisir/
Mettre à jour/Forcer 2e/Forcer rang/Grand livre/Simuler/Effacer/Exporter), mais
la mise à jour des classements est IMMÉDIATE : chaque page les recalcule depuis
la base via le moteur (aucun déclencheur à armer, aucun verrou multi-postes —
PostgreSQL sérialise les écritures).
"""

import csv
import io
import zipfile

from flask import (Blueprint, Response, flash, redirect, render_template,
                   request, send_file, url_for)
from flask_login import login_required

from .auth import admin_required
from .demo import simulate_results
from .engine import CLASSES
from .extensions import db
from .models import ForcedRank, Game, SecondOverride
from .standings import (build_admin_model, build_ledger, get_game_results,
                        get_teams)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

TYPE_OPTIONS = ['Normal', 'Mercy', 'Forfait', 'Supplémentaires']


def _int_or_none(raw):
    raw = (raw or '').strip()
    if raw == '':
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@admin_bp.route('/')
@login_required
def home():
    return redirect(url_for('admin.results'))


# ---------------------------------------------------------------------------
#  Saisie des résultats
# ---------------------------------------------------------------------------

@admin_bp.route('/results')
@login_required
def results():
    classe = request.args.get('classe', 'A').upper()
    if classe not in CLASSES:
        classe = 'A'
    games = (Game.query.filter_by(classe=classe)
             .order_by(Game.pool, Game.id).all())
    return render_template('admin/results.html', classe=classe, games=games,
                           type_options=TYPE_OPTIONS, classes=CLASSES)


@admin_bp.route('/results/<int:game_id>', methods=['POST'])
@login_required
def save_result(game_id):
    from flask_login import current_user
    g = db.session.get(Game, game_id)
    if not g:
        flash('Partie introuvable.', 'danger')
        return redirect(url_for('admin.results'))

    g.score1 = _int_or_none(request.form.get('score1'))
    g.score2 = _int_or_none(request.form.get('score2'))
    local = (request.form.get('local_team') or '').strip()
    g.local_team = local if local in (g.team1, g.team2) else None
    g.manches = _int_or_none(request.form.get('manches'))
    g.retraits = _int_or_none(request.form.get('retraits'))
    mp = _int_or_none(request.form.get('manches_prevues'))
    g.manches_prevues = mp if mp else 6
    type_fin = (request.form.get('type_fin') or 'Normal').strip()
    g.type_fin = type_fin if type_fin in TYPE_OPTIONS else 'Normal'
    g.pointage_regl = _int_or_none(request.form.get('pointage_regl'))
    g.updated_by = current_user.username

    db.session.commit()
    flash('Partie #{} enregistrée (Classe {}).'.format(g.partie, g.classe), 'success')
    return redirect(url_for('admin.results', classe=g.classe) + '#g{}'.format(g.id))


@admin_bp.route('/results/<int:game_id>/clear', methods=['POST'])
@login_required
def clear_result(game_id):
    g = db.session.get(Game, game_id)
    if not g:
        flash('Partie introuvable.', 'danger')
        return redirect(url_for('admin.results'))
    g.score1 = g.score2 = g.local_team = None
    g.manches = g.retraits = g.pointage_regl = None
    g.manches_prevues = 6
    g.type_fin = 'Normal'
    db.session.commit()
    flash('Résultat de la partie #{} effacé.'.format(g.partie), 'success')
    return redirect(url_for('admin.results', classe=g.classe) + '#g{}'.format(g.id))


# ---------------------------------------------------------------------------
#  Classements (avec bris d'égalité) + forçages
# ---------------------------------------------------------------------------

@admin_bp.route('/standings')
@login_required
def standings():
    classe = request.args.get('classe', 'A').upper()
    if classe not in CLASSES:
        classe = 'A'
    model = build_admin_model(classe)
    return render_template('admin/standings.html', model=model, classe=classe,
                           classes=CLASSES)


@admin_bp.route('/force-second', methods=['POST'])
@login_required
def force_second():
    """Coche/décoche « Forcer 2e » pour une équipe (Note 5)."""
    classe = (request.form.get('classe') or '').upper()
    team = (request.form.get('team') or '').strip()
    if classe in CLASSES and team:
        existing = SecondOverride.query.filter_by(classe=classe, team=team).first()
        if existing:
            db.session.delete(existing)
        else:
            db.session.add(SecondOverride(classe=classe, team=team))
        db.session.commit()
    return redirect(url_for('admin.standings', classe=classe) + '#pools')


@admin_bp.route('/force-rank', methods=['POST'])
@login_required
def force_rank():
    """Enregistre l'ordre « Forcer rang » (Priorité 4) d'une portée de bris."""
    classe = (request.form.get('classe') or '').upper()
    scope = (request.form.get('scope') or '').strip()
    if classe not in CLASSES or not scope:
        return redirect(url_for('admin.standings'))
    # Chaque champ « rank_<team> » porte le numéro saisi (ou vide).
    for key, value in request.form.items():
        if not key.startswith('rank_'):
            continue
        team = key[len('rank_'):]
        rank = _int_or_none(value)
        existing = ForcedRank.query.filter_by(classe=classe, scope=scope, team=team).first()
        if rank is None or rank <= 0:
            if existing:
                db.session.delete(existing)
        elif existing:
            existing.rank = rank
        else:
            db.session.add(ForcedRank(classe=classe, scope=scope, team=team, rank=rank))
    db.session.commit()
    flash('Ordre « Forcer rang » enregistré ({} {}).'.format(classe, scope), 'success')
    return redirect(url_for('admin.standings', classe=classe) + '#tb-' + scope)


# ---------------------------------------------------------------------------
#  Horaire
# ---------------------------------------------------------------------------

@admin_bp.route('/schedule')
@login_required
def schedule():
    data = {c: Game.query.filter_by(classe=c).order_by(Game.pool, Game.id).all()
            for c in CLASSES}
    teams = {c: get_teams(c) for c in CLASSES}
    return render_template('admin/schedule.html', data=data, teams=teams, classes=CLASSES)


@admin_bp.route('/schedule/import', methods=['POST'])
@admin_required
def schedule_import():
    from .seed import seed_schedule
    replace = bool(request.form.get('replace'))
    upload = request.files.get('file')
    path = None
    tmp = None
    try:
        if upload and upload.filename:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
            upload.save(tmp.name)
            tmp.close()
            path = tmp.name
        inserted, skipped = seed_schedule(path=path, replace=replace)
        flash('Horaire importé : {} parties insérées, {} déjà présentes.'
              .format(inserted, skipped), 'success')
    except Exception as exc:
        db.session.rollback()
        flash('Échec de l\'import : {}'.format(exc), 'danger')
    finally:
        if tmp:
            import os
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    return redirect(url_for('admin.schedule'))


# ---------------------------------------------------------------------------
#  Grand livre
# ---------------------------------------------------------------------------

@admin_bp.route('/ledger')
@login_required
def ledger():
    rows = build_ledger()
    return render_template('admin/ledger.html', rows=rows)


# ---------------------------------------------------------------------------
#  Simulation / effacement / export (admin)
# ---------------------------------------------------------------------------

@admin_bp.route('/simulate', methods=['POST'])
@admin_required
def simulate():
    n = simulate_results()
    flash('{} parties simulées (données de test). Effacez avant le vrai tournoi.'.format(n),
          'warning')
    return redirect(url_for('admin.standings'))


@admin_bp.route('/clear-all', methods=['POST'])
@admin_required
def clear_all():
    for g in Game.query.all():
        g.score1 = g.score2 = g.local_team = None
        g.manches = g.retraits = g.pointage_regl = None
        g.manches_prevues = 6
        g.type_fin = 'Normal'
    db.session.commit()
    flash('Tous les résultats ont été effacés (horaire conservé).', 'success')
    return redirect(url_for('admin.results'))


def _tsv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter='\t', lineterminator='\n')
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


@admin_bp.route('/export')
@login_required
def export():
    """Exporte Résultats + Grand livre en ZIP de TSV (comme ``exportSheetsToZip``)."""
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for classe in CLASSES:
            rows = [['Pool', 'Partie', 'Jour', 'Heure', 'Terrain', 'Équipe 1', 'Équipe 2',
                     'Score 1', 'Score 2', 'Locale', 'Manches', 'Retraits', 'Prévues',
                     'Type', 'Pointage régl.', 'Gagnant', 'MO Loc', 'MD Loc', 'MO Vis', 'MD Vis']]
            by_id = {g['rowId']: g for g in get_game_results(classe)}
            for g in Game.query.filter_by(classe=classe).order_by(Game.pool, Game.id).all():
                res = by_id.get(g.id)
                rows.append([
                    g.pool, g.partie, g.jour, g.heure, g.terrain, g.team1, g.team2,
                    g.score1 if g.score1 is not None else '',
                    g.score2 if g.score2 is not None else '',
                    g.local_team or '', g.manches or '', g.retraits if g.retraits is not None else '',
                    g.manches_prevues or '', g.type_fin or '',
                    g.pointage_regl if g.pointage_regl is not None else '',
                    res['winner'] if res else '',
                    res['offLocal'] if res else '', res['defLocal'] if res else '',
                    res['offVisiteur'] if res else '', res['defVisiteur'] if res else '',
                ])
            zf.writestr('Resultats_{}.tsv'.format(classe), _tsv(rows))

        ledger_rows = [['Cl', 'Pool', '#', 'Équipe', 'Adversaire', 'Résultat', 'Score',
                        'Loc/Vis', 'PC', 'Somme PC', 'MD', 'Somme MD', 'RD',
                        'PP', 'Somme PP', 'MO', 'Somme MO', 'RO']]
        for r in build_ledger():
            ledger_rows.append([r['classe'], r['pool'], r['partie'], r['equipe'],
                                r['adversaire'], r['resultat'], r['score'], r['locVis'],
                                r['pcRow'], r['cumPc'], r['mdRow'], r['cumMd'], r['rd'],
                                r['ppRow'], r['cumPp'], r['moRow'], r['cumMo'], r['ro']])
        zf.writestr('GrandLivre.tsv', _tsv(ledger_rows))

    mem.seek(0)
    return send_file(mem, mimetype='application/zip', as_attachment=True,
                     download_name='tournoi_export.zip')
