"""Simulation de résultats de test — port de ``simulateMatchResults``.

Injecte des scores fictifs (Normal / Mercy / Forfait / Supplémentaires / walk-off,
+ égalités à chaque niveau de priorité) par POSITION sur l'horaire déjà présent
(1re partie du pool, 2e, …), sans toucher aux équipes/dates. Réservé aux tests —
à effacer avant le vrai tournoi.
"""

from .extensions import db
from .models import Game

# [scoreÉquipe1, scoreÉquipe2, manchesComplètes, retraits, type, (pointageRégl suppl.)]
# Équipe 1 = équipe locale pour ces données fictives.
DATA = {
    'A': [
        [5, 4, 6, 0, 'Normal'],
        [3, 7, 6, 0, 'Normal'],
        [6, 2, 6, 0, 'Normal'],
        [8, 1, 6, 0, 'Normal'],
        [9, 0, 5, 0, 'Mercy'],
        [5, 3, 6, 0, 'Normal'],
        [4, 3, 6, 2, 'Normal'],
        [2, 8, 6, 0, 'Normal'],
        [7, 1, 6, 0, 'Normal'],
        [3, 4, 7, 0, 'Supplémentaires', 3],
        [11, 1, 5, 0, 'Mercy'],
        [6, 2, 6, 0, 'Normal'],
        [7, 3, 6, 0, 'Normal'],
        [4, 9, 6, 0, 'Normal'],
        [1, 0, 6, 1, 'Normal'],
        [5, 8, 6, 0, 'Normal'],
        [0, 7, 6, 0, 'Forfait'],
        [6, 1, 6, 0, 'Normal'],
    ],
    'B': [
        [4, 7, 6, 0, 'Normal'],
        [16, 0, 5, 0, 'Mercy'],
        [6, 4, 6, 1, 'Normal'],
        [8, 2, 6, 0, 'Normal'],
        [5, 8, 6, 0, 'Normal'],
        [3, 5, 6, 0, 'Normal'],
        [6, 8, 6, 0, 'Normal'],
        [5, 4, 7, 1, 'Supplémentaires', 4],
        [1, 2, 8, 0, 'Supplémentaires', 1],
        [9, 0, 4, 0, 'Mercy'],
        [4, 5, 6, 0, 'Normal'],
        [3, 8, 6, 0, 'Normal'],
        [8, 3, 6, 0, 'Normal'],
        [2, 6, 6, 0, 'Normal'],
        [4, 3, 6, 2, 'Normal'],
        [7, 3, 6, 0, 'Normal'],
        [6, 0, 6, 0, 'Normal'],
        [0, 7, 6, 0, 'Forfait'],
    ],
}


def simulate_results():
    """Écrase les scores par position. Retourne le nombre de parties remplies."""
    total = 0
    for classe, rows in DATA.items():
        games = (Game.query.filter_by(classe=classe)
                 .order_by(Game.pool, Game.id).all())
        for g, r in zip(games, rows):
            g.score1 = r[0]
            g.score2 = r[1]
            g.manches = r[2]
            g.retraits = r[3]
            g.manches_prevues = 6
            g.type_fin = r[4]
            g.pointage_regl = r[5] if (r[4] == 'Supplémentaires' and len(r) > 5) else None
            g.local_team = g.team1  # Équipe 1 = locale (scénario des données fictives)
            total += 1
    db.session.commit()
    return total
