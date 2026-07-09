"""Import de l'horaire du tournoi.

Lit l'onglet « Horaire globalArbitre » du fichier Excel du tournoi (produit par
le comité de l'ABMR) SANS dépendance externe (analyse .xlsx via la stdlib :
zipfile + ElementTree), et insère les 36 parties de pool valides (« # pool »
comme « 3A ») dans la table ``Game``. Les parties éliminatoires (DF1A, etc.) et
les lignes mal formées sont ignorées, exactement comme ``readScheduleRows``/
``parsePoolClasse`` du script d'origine.
"""

import os
import re
import xml.etree.ElementTree as ET
import zipfile

from .engine import parse_pool_classe
from .extensions import db
from .models import Game

_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_RNS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_ODR = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

_FLASK_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_DIR = os.path.dirname(_FLASK_APP_DIR)

# Copie autonome dans flask-app/data (déploiement du seul sous-dossier), avec
# repli sur l'horaire du dépôt d'origine (gsheet/).
_LOCAL_XLSX = os.path.join(_FLASK_APP_DIR, 'data', 'Horaire-tournoi-2026.xlsx')
_REPO_XLSX = os.path.join(_REPO_DIR, 'gsheet', 'Horaire-tournoi-2026.xlsx')
DEFAULT_XLSX = _LOCAL_XLSX if os.path.exists(_LOCAL_XLSX) else _REPO_XLSX

SCHEDULE_SHEET = 'Horaire globalArbitre'


def _col_num(ref):
    m = re.match(r'([A-Z]+)(\d+)', ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n, int(m.group(2))


def read_schedule_from_xlsx(path=None, sheet_name=SCHEDULE_SHEET):
    """Retourne la liste des lignes de l'onglet horaire : dict par numéro de colonne."""
    path = path or DEFAULT_XLSX
    z = zipfile.ZipFile(path)

    # Chaînes partagées.
    sst = []
    if 'xl/sharedStrings.xml' in z.namelist():
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in root.findall(_NS + 'si'):
            sst.append(''.join(t.text or '' for t in si.iter(_NS + 't')))

    # Onglet -> fichier.
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    relmap = {r.get('Id'): r.get('Target') for r in rels.findall(_RNS + 'Relationship')}
    target = None
    for s in wb.find(_NS + 'sheets'):
        if s.get('name') == sheet_name:
            target = relmap[s.get(_ODR + 'id')]
    if not target:
        raise ValueError('Onglet « {} » introuvable dans {}'.format(sheet_name, path))
    if not target.startswith('xl/'):
        target = 'xl/' + target

    sheet = ET.fromstring(z.read(target))
    rows = {}
    for c in sheet.iter(_NS + 'c'):
        ref = c.get('r')
        t = c.get('t')
        v = c.find(_NS + 'v')
        if v is not None:
            val = v.text
            if t == 's':
                val = sst[int(val)]
        else:
            iss = c.find(_NS + 'is')
            val = ''.join(x.text or '' for x in iss.iter(_NS + 't')) if iss is not None else None
        col, rw = _col_num(ref)
        rows.setdefault(rw, {})[col] = val

    ordered = []
    for rw in sorted(rows):
        if rw == 1:
            continue  # en-tête
        ordered.append(rows[rw])
    return ordered


def _clean(x):
    return ('' if x is None else str(x)).strip()


def parse_schedule_games(rows):
    """Filtre/normalise les lignes en parties de pool valides (comme readScheduleRows)."""
    games = []
    for r in rows:
        partie = _clean(r.get(1))
        pool_classe = _clean(r.get(2))
        jour = _clean(r.get(3))
        heure = _clean(r.get(4))
        terrain = _clean(r.get(5))
        team_a = _clean(r.get(6))
        team_b = _clean(r.get(7))
        if pool_classe == '' or team_a == '' or team_b == '':
            continue
        pc = parse_pool_classe(pool_classe)
        if not pc:
            continue
        # « 1.0 » (nombre Excel) -> « 1 »
        if re.match(r'^\d+\.0$', partie):
            partie = partie[:-2]
        games.append({
            'partie': partie, 'pool': pc['pool'], 'classe': pc['classe'],
            'jour': jour, 'heure': heure, 'terrain': terrain,
            'team1': team_a, 'team2': team_b,
        })
    return games


def seed_schedule(path=None, replace=False):
    """Insère l'horaire dans la table Game. ``replace`` efface d'abord tout Game.

    Retourne (inséré, ignoré_existants).
    """
    rows = read_schedule_from_xlsx(path)
    games = parse_schedule_games(rows)

    if replace:
        Game.query.delete()
        db.session.commit()

    inserted = 0
    skipped = 0
    for gd in games:
        exists = Game.query.filter_by(classe=gd['classe'], partie=gd['partie']).first()
        if exists:
            skipped += 1
            continue
        db.session.add(Game(
            classe=gd['classe'], pool=gd['pool'], partie=gd['partie'],
            jour=gd['jour'], heure=gd['heure'], terrain=gd['terrain'],
            team1=gd['team1'], team2=gd['team2'],
            manches_prevues=6, type_fin='Normal',
        ))
        inserted += 1
    db.session.commit()
    return inserted, skipped
