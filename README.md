# 💞 Nous Deux

Application de couple : plan du jour, emploi du temps, action/vérité avec
discussion, messagerie "à découvrir", objectifs & souvenirs partagés.

## Lancer en local

```bash
pip install -r requirements.txt
python app.py
```

Puis ouvre http://localhost:5000 — aucun compte n'existe au premier
lancement. Clique sur "➕ Créer un compte" pour créer ton profil (prénom,
avatar, mot de passe), puis fais de même pour ton/ta partenaire (2 comptes
maximum). Ensuite, connexion par mot de passe à chaque fois.

## Déployer sur Render

1. Pousse ce dossier sur un dépôt GitHub.
2. Sur [render.com](https://render.com), "New +" → "Blueprint", connecte
   le dépôt : `render.yaml` configure tout automatiquement.
3. Pour une base de données persistante en production, ajoute une base
   Postgres sur Render et branche la variable `DATABASE_URL` (le code
   la détecte automatiquement) — sinon l'app utilise SQLite en local.
4. Au démarrage, l'app crée/ajuste automatiquement les tables et
   colonnes nécessaires (`db.create_all()` + petites migrations
   automatiques), y compris si tu redéploies par-dessus une base qui
   existait déjà avec l'ancienne version sans mot de passe.

## Installer l'appli sur votre téléphone (PWA)

Une fois déployée sur Render (donc en HTTPS), l'appli s'installe comme une
vraie application, chacun·e sur son téléphone :

- **Android (Chrome)** : ouvrir le site → menu ⋮ → "Installer l'application"
  (ou un bandeau propose "Ajouter à l'écran d'accueil"). Une icône apparaît
  sur l'écran d'accueil, l'appli s'ouvre en plein écran sans barre de
  navigateur.
- **iPhone (Safari)** : ouvrir le site → bouton Partager (carré avec une
  flèche) → "Sur l'écran d'accueil" → Ajouter.

Aucun store, aucun compte développeur nécessaire — c'est le même site,
juste installable.



## Structure

- `app.py` — modèles (SQLAlchemy) + routes pages/API
- `templates/` — pages Jinja2
- `static/style.css` — thème romantique, responsive, cartes arrondies
- `static/script.js` — utilitaires JS partagés
- `Procfile`, `render.yaml` — déploiement

## Fonctionnalités

- **Comptes & connexion** : création de compte (prénom, avatar, mot de
  passe), connexion par mot de passe, photo de profil modifiable pour
  chacun·e.
- **Accueil** : vue d'ensemble, statut du/de la partenaire.
- **Action/Vérité** : tu écris toi-même l'action ET la vérité, l'autre
  choisit à l'aveugle puis fournit une preuve. Une fois choisi, la
  discussion continue directement sous chaque réponse (comme un petit
  chat). Les réponses/discussions sont mises en avant ; proposer un
  nouveau défi se fait via un panneau à ouvrir.
- **Plan du jour** & **Emploi du temps** : organisation personnelle et
  partagée, modifiable et supprimable.
- **À découvrir** : une vraie messagerie — bulles de discussion, "vu"
  automatique dès l'ouverture (pas de confirmation manuelle), possibilité
  de répondre à un message précis en cliquant dessus.
- **Espace couple** :
  - *Objectifs à deux* : on écrit un rêve, et une fois réalisé on le
    marque comme tel — il est alors gravé pour toujours (non modifiable,
    non supprimable) dans la liste des rêves réalisés 🏆.
  - *Souvenirs* : ajoute une date à un souvenir — le jour venu, l'appli
    l'affiche en rappel en haut de la page, façon agenda, avec quelques
    idées pour fêter ça.
- **Notifications** : un badge numéroté apparaît dans le menu sur
  Action/Vérité, Plan du jour, À découvrir et Espace couple dès qu'il y a
  quelque chose de nouveau à voir. En plus, de vraies **notifications
  push** peuvent être activées (bouton dans le Profil) : vous serez
  alerté·e sur votre téléphone même quand l'appli est fermée, dès qu'un
  message, un défi ou une réponse arrive. La première activation demande
  l'autorisation du navigateur.
- **Déconnexion, modification, réponse ciblée** : on peut se déconnecter
  aussi bien sur mobile que sur ordinateur, modifier un message déjà
  envoyé (À découvrir et discussions Action/Vérité), et cliquer sur un
  message précis pour lui répondre directement (les deux zones de chat).
- **Toutes les données sont permanentes**, y compris les photos : elles
  sont stockées dans la base de données (comme tout le reste) et non plus
  sur le disque du serveur — donc rien n'est perdu, même après une
  déconnexion ou un redéploiement sur Render.

Toutes les données restent **ajoutables, modifiables et supprimables**
directement depuis l'interface (sauf les rêves réalisés, volontairement
permanents).

## Notifications push (optionnel)

Des clés VAPID par défaut sont déjà intégrées dans le code : les
notifications fonctionnent dès le déploiement, sans rien configurer. Si
tu veux les rendre indépendantes du code (par exemple pour ne jamais les
perdre même si tu changes ce fichier), tu peux définir sur Render :
`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (une adresse
mail ou une URL). Sinon, les valeurs par défaut suffisent.
