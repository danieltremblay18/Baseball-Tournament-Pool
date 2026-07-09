"""Configuration Flask, pilotée par variables d'environnement.

Sur Render.com, on définit ``DATABASE_URL`` (chaîne Neon) et ``SECRET_KEY``.
En local, un fichier ``.env`` (chargé par python-dotenv dans ``wsgi.py``) suffit.
Sans ``DATABASE_URL``, on retombe sur un fichier SQLite local pour développer/tester.
"""

import os


def _pick_pg_driver() -> str:
    """Choisit le dialecte PostgreSQL selon le pilote disponible.

    Render installe psycopg 3 (``psycopg[binary]`` dans requirements.txt) ; un
    poste de dev peut n'avoir que psycopg 2. On s'adapte pour marcher partout.
    """
    try:
        import psycopg  # noqa: F401  (psycopg 3)
        return 'postgresql+psycopg'
    except ImportError:
        try:
            import psycopg2  # noqa: F401
            return 'postgresql+psycopg2'
        except ImportError:
            return 'postgresql'  # laisse SQLAlchemy choisir son pilote par défaut


def _normalize_db_url(url: str) -> str:
    """Adapte l'URL Neon (``postgresql://…?sslmode=require``) au bon pilote."""
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        url = _pick_pg_driver() + '://' + url[len('postgresql://'):]
    return url


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

    _raw_db = os.environ.get('DATABASE_URL', '')
    if _raw_db:
        SQLALCHEMY_DATABASE_URI = _normalize_db_url(_raw_db)
    else:
        # Repli local : SQLite dans le dossier de l'instance.
        _here = os.path.dirname(os.path.abspath(__file__))
        SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_here, '..', 'tournoi_local.db')

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,      # Neon coupe les connexions inactives : on revalide.
        'pool_recycle': 300,
    }

    # Fuseau horaire d'affichage pour "Mis à jour : …" (l'app formate elle-même).
    DISPLAY_TZ = os.environ.get('DISPLAY_TZ', 'America/Montreal')

    # Nom affiché dans l'en-tête public.
    TOURNAMENT_TITLE = os.environ.get(
        'TOURNAMENT_TITLE',
        'TOURNOI PROVINCIAL DE BASEBALL 13U 2026 DE RIMOUSKI')
