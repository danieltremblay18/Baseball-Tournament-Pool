"""Point d'entrée WSGI (gunicorn sur Render ; `flask run` en local).

Charge un fichier .env s'il existe (développement), puis expose ``app``.
"""

import os

from dotenv import load_dotenv

# override=True : le .env local a priorité sur une variable DATABASE_URL déjà
# présente dans l'environnement du poste (sur Render, il n'y a pas de .env, donc
# ce sont bien les variables du dashboard qui s'appliquent).
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=True)

from tournoi import create_app  # noqa: E402

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))
