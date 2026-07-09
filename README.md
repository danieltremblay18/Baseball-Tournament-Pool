# 🏆 Tournoi Baseball 13U — Application Flask

Version **Flask + PostgreSQL** du système de gestion de tournoi 13U (Baseball
Québec, Art. 42.11), auparavant hébergé dans Google Sheets (voir `../gsheet/`).
Le moteur de bris d'égalité est un **port fidèle** du script d'origine, prouvé
par `tests/test_engine.py` (mêmes scénarios que `gsheet/tests/test_tiebreaker.js`).

- **Base de données** : PostgreSQL (Neon en production).
- **Hébergement** : Render.com.
- **Affichage public** (`/`) : classements + résultats + règles, lecture seule,
  rafraîchi toutes les 60 s — identique à l'ancien lien Apps Script `/exec`.
- **Zone registraire** (`/admin`, connexion requise) : saisie des résultats,
  classements avec bris d'égalité transparents, forçages « 2e » (Note 5) et
  « Forcer rang » (Priorité 4), grand livre, simulateur, export, import horaire.

## Ce qui change par rapport à Google Sheets

| GSheet | Flask |
|--------|-------|
| Onglet `Configuration` (horaire collé) | Table `game` (import Excel via `seed-schedule`) |
| Onglets `Résultats A/B` | Saisie web (`/admin/results`) → table `game` |
| Onglets `Classements A/B` | `/admin/standings` (recalcul à chaque affichage) |
| Cases « Forcer 2e » / « Forcer rang » | Tables `second_override` / `forced_rank` |
| Déclencheur `onEdit` + verrou multi-postes | Recalcul serveur à chaque page (PostgreSQL sérialise) |
| Web app `doGet` (`/exec`) | Route publique `/` (même moteur, `compute_standings_model`) |
| Partage de compte Google | Comptes individuels (`app_user`) |

## Développement local

```bash
cd flask-app
python -m venv .venv && .venv/Scripts/activate       # Windows
pip install -r requirements.txt
cp .env.example .env      # y mettre DATABASE_URL (ou laisser vide → SQLite local)

flask --app wsgi init-db            # crée les tables
flask --app wsgi seed-schedule      # importe l'horaire 2026 (36 parties)
flask --app wsgi create-user dan --admin   # crée un compte admin
flask --app wsgi run --debug        # http://127.0.0.1:5000/
```

Sans `DATABASE_URL`, l'app utilise un fichier SQLite local (`tournoi_local.db`) —
pratique pour développer hors ligne.

### Commandes CLI

| Commande | Rôle |
|----------|------|
| `flask --app wsgi init-db` | Crée les tables (idempotent) |
| `flask --app wsgi seed-schedule [--replace]` | Importe l'horaire Excel |
| `flask --app wsgi create-user <nom> [--admin]` | Crée un compte |
| `flask --app wsgi seed-demo` | Injecte des résultats fictifs (test) |

### Tests

```bash
pytest -q          # port de gsheet/tests/test_tiebreaker.js
```

## Déploiement sur Render.com (avec Neon)

1. **Neon** : créer une base ; copier la chaîne `postgresql://…?sslmode=require`.
2. **Render** : *New → Blueprint* pointé sur ce dépôt (le `render.yaml` est à la
   racine du dépôt). Ou *New → Web Service* manuel :
   - Root Directory : `.` (racine du dépôt)
   - Build : `pip install -r requirements.txt`
   - Start : `gunicorn wsgi:app --workers 2 --timeout 60`
3. **Environment** (dashboard Render) :
   - `DATABASE_URL` = la chaîne Neon
   - `SECRET_KEY` = une longue chaîne aléatoire (ou `generateValue`)
   - `DISPLAY_TZ` = `America/Montreal`
4. (Optionnel) définir `INITIAL_ADMIN_PASSWORD` et `INITIAL_ADMIN_USERNAME`
   dans l'onglet Environment pour choisir le compte admin initial.
5. Après le 1er déploiement, ouvrir le **Shell** du service :
   ```bash
   flask --app wsgi seed-schedule       # importe l'horaire 2026
   ```
6. La page publique est à la racine `/` ; la zone registraire à `/admin`.

### Compte admin initial (mot de passe temporaire)

Au **tout premier démarrage** (base sans aucun utilisateur), l'app crée
automatiquement un compte **admin** :

- Identifiant : `INITIAL_ADMIN_USERNAME` (défaut **`admin`**).
- Mot de passe : `INITIAL_ADMIN_PASSWORD` s'il est défini, sinon un mot de passe
  **aléatoire imprimé dans les logs de démarrage** (chercher « COMPTE ADMIN
  INITIAL CRÉÉ » dans les logs Render).
- Ce mot de passe est **temporaire** : l'app **force son changement** à la
  première connexion avant de donner accès à `/admin`.

Ensuite, cet admin crée les comptes registraires via **Utilisateurs** (chaque
compte reçoit aussi un mot de passe temporaire à changer). Les tables sont créées
automatiquement au démarrage (`db.create_all()`) ; `init-db` n'est donc utile que
pour les créer manuellement avant le seed.

## Utilisation annuelle

1. `flask --app wsgi seed-schedule --replace` (ou *Horaire → Importer* en admin)
   pour charger le nouvel horaire.
2. Saisir les résultats dans **Saisie** ; le classement se met à jour tout seul.
3. Consulter **Classements** pour le détail des bris d'égalité (Art. 42.11) et,
   au besoin, cocher **Forcer 2e** ou saisir **Forcer rang** (Priorité 4).

## Règles métier

Toute la logique (fractions de manches, Notes 4/5, bris d'égalité récursif à
trois niveaux, Priorité 4 manuelle, forçages) est documentée dans
`../gsheet/CLAUDE.md` et implémentée à l'identique dans `tournoi/engine.py`.
