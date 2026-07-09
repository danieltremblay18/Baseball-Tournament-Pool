"""Application factory du Tournoi Baseball 13U (Flask + PostgreSQL/Neon).

Version Flask du système de gestion de tournoi auparavant hébergé dans Google
Sheets (voir ``gsheet/``). Le moteur de bris d'égalité (Art. 42.11) est un port
fidèle, prouvé par ``tests/test_engine.py``.
"""

import click
from flask import Flask
from flask.cli import with_appcontext

from .config import Config
from .extensions import csrf, db, login_manager

APP_VERSION = '1.0.5-flask'


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    app.config['APP_VERSION'] = APP_VERSION

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401  (enregistre les tables)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))

    # Blueprints.
    from .public import public_bp
    from .auth import auth_bp
    from .admin import admin_bp
    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    # Création idempotente des tables + admin initial (utile au 1er démarrage sur Neon).
    with app.app_context():
        try:
            db.create_all()
            ensure_initial_admin(app)
        except Exception as exc:  # pragma: no cover - dépend de la connexion DB
            app.logger.warning('Initialisation DB/admin ignorée : %s', exc)

    _register_cli(app)
    return app


def ensure_initial_admin(app):
    """Crée un compte admin initial si AUCUN utilisateur n'existe encore.

    Mot de passe temporaire : ``INITIAL_ADMIN_PASSWORD`` (env) s'il est défini,
    sinon un mot de passe aléatoire écrit dans les logs de démarrage. Dans les deux
    cas, le compte est marqué ``must_change_password`` -> changement forcé à la
    première connexion. Identifiant : ``INITIAL_ADMIN_USERNAME`` (défaut « admin »).
    """
    import os
    import secrets

    from .models import User

    if User.query.count() > 0:
        return  # des comptes existent déjà : on ne touche à rien.

    username = os.environ.get('INITIAL_ADMIN_USERNAME', 'admin')
    password = os.environ.get('INITIAL_ADMIN_PASSWORD') or secrets.token_urlsafe(9)
    generated = 'INITIAL_ADMIN_PASSWORD' not in os.environ

    admin = User(username=username, is_admin=True, must_change_password=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()

    banner = (
        '\n' + '=' * 68 +
        '\n  COMPTE ADMIN INITIAL CRÉÉ'
        '\n  Identifiant       : {}'.format(username) +
        ('\n  Mot de passe TEMP : {}   (généré — À CHANGER)'.format(password)
         if generated else
         '\n  Mot de passe TEMP : (celui de INITIAL_ADMIN_PASSWORD — À CHANGER)') +
        '\n  Changement OBLIGATOIRE à la première connexion.'
        '\n' + '=' * 68)
    app.logger.warning(banner)


def _register_cli(app):
    @app.cli.command('init-db')
    @with_appcontext
    def init_db():
        """Crée les tables (idempotent)."""
        db.create_all()
        click.echo('Tables créées.')

    @app.cli.command('seed-schedule')
    @click.option('--path', default=None, help='Chemin du fichier .xlsx (défaut : horaire ABMR du dépôt).')
    @click.option('--replace', is_flag=True, help='Efface toutes les parties avant import.')
    @with_appcontext
    def seed_schedule_cmd(path, replace):
        """Importe l'horaire 2026 (onglet « Horaire globalArbitre »)."""
        from .seed import seed_schedule
        inserted, skipped = seed_schedule(path=path, replace=replace)
        click.echo('Horaire importé : {} parties insérées, {} déjà présentes.'.format(inserted, skipped))

    @app.cli.command('create-user')
    @click.argument('username')
    @click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option('--admin', is_flag=True, help='Donne les droits admin.')
    @with_appcontext
    def create_user(username, password, admin):
        """Crée un compte registraire (ou admin)."""
        from .models import User
        if User.query.filter_by(username=username).first():
            click.echo('Utilisateur « {} » déjà existant.'.format(username))
            return
        u = User(username=username, is_admin=admin)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo('Utilisateur « {} » créé{}.'.format(username, ' (admin)' if admin else ''))

    @app.cli.command('seed-demo')
    @with_appcontext
    def seed_demo():
        """Injecte des résultats fictifs (test) sur l'horaire existant."""
        from .demo import simulate_results
        n = simulate_results()
        click.echo('{} parties simulées.'.format(n))
