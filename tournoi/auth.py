"""Authentification et gestion des comptes (comptes individuels)."""

from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db
from .models import User

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def admin_required(view):
    """Décorateur : réserve une vue aux administrateurs."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin.results'))
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Connecté en tant que {}.'.format(user.username), 'success')
            nxt = request.args.get('next')
            return redirect(nxt or url_for('admin.results'))
        flash('Identifiant ou mot de passe invalide.', 'danger')
    return render_template('auth/login.html')


@auth_bp.before_app_request
def force_password_change():
    """Redirige vers le changement de mot de passe tant qu'il est temporaire.

    N'entrave ni la page publique, ni la déconnexion, ni les fichiers statiques,
    ni la page de changement elle-même.
    """
    if not current_user.is_authenticated:
        return None
    if not getattr(current_user, 'must_change_password', False):
        return None
    endpoint = request.endpoint or ''
    allowed = {'auth.change_password', 'auth.logout', 'static'}
    if endpoint in allowed or endpoint.startswith('public.'):
        return None
    return redirect(url_for('auth.change_password'))


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    forced = getattr(current_user, 'must_change_password', False)
    if request.method == 'POST':
        current = request.form.get('current_password') or ''
        new = request.form.get('new_password') or ''
        confirm = request.form.get('confirm_password') or ''
        if not current_user.check_password(current):
            flash('Mot de passe actuel incorrect.', 'danger')
        elif len(new) < 6:
            flash('Le nouveau mot de passe doit faire au moins 6 caractères.', 'danger')
        elif new != confirm:
            flash('La confirmation ne correspond pas.', 'danger')
        else:
            current_user.set_password(new)
            current_user.must_change_password = False
            db.session.commit()
            flash('Mot de passe modifié.', 'success')
            return redirect(url_for('admin.results'))
    return render_template('auth/change_password.html', forced=forced)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Déconnecté.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/users')
@admin_required
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template('auth/users.html', users=all_users)


@auth_bp.route('/users/create', methods=['POST'])
@admin_required
def create_user():
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    is_admin = bool(request.form.get('is_admin'))
    if not username or not password:
        flash('Nom d\'utilisateur et mot de passe requis.', 'danger')
    elif User.query.filter_by(username=username).first():
        flash('Utilisateur « {} » déjà existant.'.format(username), 'danger')
    else:
        u = User(username=username, is_admin=is_admin, must_change_password=True)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        flash('Utilisateur « {} » créé (mot de passe temporaire à changer à la 1re connexion).'
              .format(username), 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/password', methods=['POST'])
@admin_required
def reset_password(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    password = request.form.get('password') or ''
    if not password:
        flash('Mot de passe requis.', 'danger')
    else:
        user.set_password(password)
        user.must_change_password = True
        db.session.commit()
        flash('Mot de passe de « {} » réinitialisé (temporaire, à changer par l\'utilisateur).'
              .format(user.username), 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if user.id == current_user.id:
        flash('Vous ne pouvez pas supprimer votre propre compte.', 'danger')
    else:
        db.session.delete(user)
        db.session.commit()
        flash('Utilisateur « {} » supprimé.'.format(user.username), 'success')
    return redirect(url_for('auth.users'))
