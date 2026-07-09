"""Affichage public (lecture seule) — port de ``doGet`` / ``renderPublicHtml_``.

Page unique auto-suffisante (HTML/CSS/JS embarqué) réutilisant le MÊME moteur de
calcul que la vue admin (``compute_standings_model``) : l'affichage public ne peut
donc pas diverger des classements. Rafraîchissement auto toutes les 60 s.
"""

import json

from flask import Blueprint, Response, current_app, jsonify

from .public_template import PUBLIC_HTML_TEMPLATE, RULES_HTML
from .standings import compute_standings_model, get_match_rows

public_bp = Blueprint('public', __name__)


def _build_data():
    matches = get_match_rows('A') + get_match_rows('B')

    def partie_key(m):
        try:
            return (0, int(m['partie']))
        except (TypeError, ValueError):
            return (1, str(m['partie']))

    matches.sort(key=partie_key)
    return {
        'A': compute_standings_model('A'),
        'B': compute_standings_model('B'),
        'matches': matches,
        'version': current_app.config.get('APP_VERSION', ''),
    }


def render_public_html(data):
    # Échappe « < » comme l'original pour qu'aucun nom d'équipe ne ferme </script>.
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    title = current_app.config.get('TOURNAMENT_TITLE', 'Tournoi Baseball 13U')
    html = (PUBLIC_HTML_TEMPLATE
            .replace('/*__DATA__*/', 'window.DATA = ' + payload + ';')
            .replace('/*__RULES__*/', RULES_HTML)
            .replace('/*__TITLE__*/', title))
    return html


@public_bp.route('/')
def index():
    return Response(render_public_html(_build_data()), mimetype='text/html')


@public_bp.route('/api/standings')
def api_standings():
    """Données JSON (mêmes que la page) — pour un rafraîchissement sans rechargement."""
    return jsonify(_build_data())
