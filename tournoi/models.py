"""Modèles SQLAlchemy.

Correspondance avec le système Google Sheets d'origine :
  - ``Game``          ≈ une ligne de « Résultats A/B » (horaire + saisie du résultat).
  - ``SecondOverride``≈ une case cochée « Forcer 2e » (Note 5) d'un onglet Classements.
  - ``ForcedRank``    ≈ une valeur « Forcer rang » (Priorité 4) d'un tableau de bris.
  - ``User``          ≈ le partage de compte Google (ici : comptes individuels).
"""

from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = 'app_user'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    # Un admin peut gérer les utilisateurs, importer l'horaire, simuler, effacer.
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    # Mot de passe temporaire : oblige un changement à la première connexion.
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Game(db.Model):
    """Une partie de pool : horaire (immuable) + saisie du résultat (registraire).

    Les colonnes calculées (gagnant, manches offensives/défensives) NE SONT PAS
    stockées : elles sont recalculées à la volée par le moteur, exactement comme
    la web app publique d'origine (``computeStandingsModel``) recalculait tout.
    """

    __tablename__ = 'game'

    id = db.Column(db.Integer, primary_key=True)

    # --- Horaire (issu de la Configuration / du fichier Excel du tournoi) ---
    classe = db.Column(db.String(1), nullable=False, index=True)   # 'A' | 'B'
    pool = db.Column(db.Integer, nullable=False, index=True)        # 1 | 2 | 3
    partie = db.Column(db.String(20), nullable=False)              # « # de match »
    jour = db.Column(db.String(40), default='')
    heure = db.Column(db.String(20), default='')
    terrain = db.Column(db.String(40), default='')
    team1 = db.Column(db.String(120), nullable=False)             # Équipe 1 (pas la locale)
    team2 = db.Column(db.String(120), nullable=False)             # Équipe 2

    # --- Saisie du résultat (NULL tant que la partie n'est pas jouée) ---
    score1 = db.Column(db.Integer, nullable=True)                 # Score Équipe 1
    score2 = db.Column(db.Integer, nullable=True)                 # Score Équipe 2
    local_team = db.Column(db.String(120), nullable=True)         # Équipe Locale (team1|team2)
    manches = db.Column(db.Integer, nullable=True)                # Manches complètes (dernière jouée)
    retraits = db.Column(db.Integer, nullable=True)               # Retraits en fin (0-2)
    manches_prevues = db.Column(db.Integer, nullable=True, default=6)  # Longueur réglementaire
    type_fin = db.Column(db.String(20), nullable=True, default='Normal')  # Normal|Mercy|Forfait|Supplémentaires
    pointage_regl = db.Column(db.Integer, nullable=True)          # Pointage régl. (suppl.) — Note 4

    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    updated_by = db.Column(db.String(80), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('classe', 'partie', name='uq_game_classe_partie'),
    )

    @property
    def played(self):
        return self.score1 is not None and self.score2 is not None


class SecondOverride(db.Model):
    """Forçage admin du représentant « 2e de pool » au Meilleur 2e (Note 5).

    Présence d'une ligne = équipe marquée (équivalent case cochée « Forcer 2e »).
    """

    __tablename__ = 'second_override'

    id = db.Column(db.Integer, primary_key=True)
    classe = db.Column(db.String(1), nullable=False, index=True)
    team = db.Column(db.String(120), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('classe', 'team', name='uq_second_classe_team'),
    )


class ForcedRank(db.Model):
    """Ordre manuel « Forcer rang » (Priorité 4, Art. 42.11) pour une portée de bris.

    ``scope`` : 'A1'|'A2'|'A3' (Étape A d'un pool), 'C' (Étape C) ou 'B' (Étape B).
    """

    __tablename__ = 'forced_rank'

    id = db.Column(db.Integer, primary_key=True)
    classe = db.Column(db.String(1), nullable=False, index=True)
    scope = db.Column(db.String(4), nullable=False)
    team = db.Column(db.String(120), nullable=False)
    rank = db.Column(db.Integer, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('classe', 'scope', 'team', name='uq_forced_scope_team'),
    )
