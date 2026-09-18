import os
import io
import json
import time
import uuid
from datetime import datetime, date, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import Markup
from PIL import Image
from pywebpush import webpush, WebPushException

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-moi-en-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', f"sqlite:///{os.path.join(basedir, 'couple.db')}"
).replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024  # 8 Mo max par photo envoyée
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)  # reste connecté 1 an

# Verrouillage par mot de passe : une fois débloqué, on reste débloqué ce temps-ci
# d'inactivité avant de redemander le mot de passe.
LOCK_GRACE_SECONDS = 5 * 60


def _horodatage_deverrouillage():
    return time.time() + LOCK_GRACE_SECONDS

db = SQLAlchemy(app)

# --- Notifications push (Web Push) ---
# Clés VAPID par défaut générées pour cette appli (fonctionnent tout de suite).
# Pour plus de robustesse, tu peux les figer via des variables d'environnement
# sur Render (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY) — sinon ces valeurs par
# défaut sont utilisées à chaque démarrage.
_DEFAULT_VAPID_PRIVATE = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgTKYhz9AhtwAfuM6v
B9FLAjQ9r7MhuPyjeGYllhK2fVGhRANCAATVOD9tIdelKybcHMZ35GK9RX4LLLN/
0ddbCQAuPZ6AsjHD5jzo/Korb/wmWg0XQ6xt00e7YF4dOcdE4/cAxG6L
-----END PRIVATE KEY-----
"""
_DEFAULT_VAPID_PUBLIC = "BNU4P20h16UrJtwcxnfkYr1Ffgsss3_R11sJAC49noCyMcPmPOj8qitv_CZaDRdDrG3TR7tgXh05x0Tj9wDEbos"

VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', _DEFAULT_VAPID_PRIVATE)
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY', _DEFAULT_VAPID_PUBLIC)
VAPID_CLAIMS_SUB = os.environ.get('VAPID_SUBJECT', 'mailto:contact@nousdeux.app')


def compresser_image(fichier, max_dim=1280, qualite=80):
    """Redimensionne et compresse une image envoyée avant de la stocker en base
    de données (toujours en JPEG), pour qu'elle reste petite et permanente."""
    try:
        img = Image.open(fichier)
        img = img.convert('RGB') if img.mode in ('RGBA', 'P', 'LA') else img.convert('RGB')
        img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=qualite, optimize=True)
        return buf.getvalue(), 'image/jpeg'
    except Exception:
        return None, None


JOURS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']

DEFAULT_CHALLENGES = [
    ('action', 'romantique', "Écris un mot doux et envoie-le maintenant 💌"),
    ('action', 'drole', "Envoie un selfie avec la grimace la plus bizarre 🤪"),
    ('action', 'couple', "Planifie notre prochaine soirée en 3 phrases 🎬"),
    ('action', 'defi', "Ne dis pas 'je t'aime' pendant 1h (facile non ?) 😏"),
    ('verite', 'romantique', "Quel a été notre plus beau souvenir ensemble ?"),
    ('verite', 'drole', "Quelle est la chose la plus gênante que tu aies faite devant moi ?"),
    ('verite', 'couple', "Qu'est-ce que tu voudrais qu'on fasse plus souvent ?"),
    ('verite', 'defi', "Quel est ton plus grand rêve qu'on pourrait réaliser ensemble ?"),
]

SOUVENIR_SUGGESTIONS = [
    "Regardez ensemble une photo ou vidéo de ce souvenir 📸",
    "Écrivez-vous un petit mot sur ce moment dans 💌 À découvrir",
    "Recréez un mini moment similaire aujourd'hui 🎉",
    "Racontez ce souvenir à voix haute ce soir, comme une histoire 🕯️",
    "Préparez le même repas ou la même activité qu'à l'époque 🍽️",
]


# ---------------------- MODELES ----------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    prenom = db.Column(db.String(80), nullable=False)
    avatar = db.Column(db.String(10), default='🙂')
    photo_data = db.Column(db.LargeBinary)
    photo_mimetype = db.Column(db.String(50))
    mot_de_passe_hash = db.Column(db.String(255))
    verrouillage_actif = db.Column(db.Boolean, default=False)
    en_ligne = db.Column(db.Boolean, default=False)
    derniere_activite = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, mdp):
        self.mot_de_passe_hash = generate_password_hash(mdp)

    def check_password(self, mdp):
        if not self.mot_de_passe_hash:
            return False
        return check_password_hash(self.mot_de_passe_hash, mdp)

    def to_dict(self):
        return {
            'id': self.id, 'prenom': self.prenom, 'avatar': self.avatar,
            'en_ligne': self.en_ligne,
        }


class Challenge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(10), nullable=False)  # action | verite
    categorie = db.Column(db.String(30), default='couple')
    texte = db.Column(db.String(500), nullable=False)
    personnalise = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    def to_dict(self):
        return {
            'id': self.id, 'type': self.type, 'categorie': self.categorie,
            'texte': self.texte, 'personnalise': self.personnalise,
        }


class Defi(db.Model):
    """Un duo Action/Vérité écrit par l'expéditeur, envoyé en aveugle au/à la partenaire."""
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    texte_action = db.Column(db.String(500), nullable=False)
    texte_verite = db.Column(db.String(500), nullable=False)
    choix = db.Column(db.String(10))  # None | 'action' | 'verite'
    statut = db.Column(db.String(15), default='envoye')  # envoye | choisi | termine
    preuve_texte = db.Column(db.Text)
    preuve_photo_data = db.Column(db.LargeBinary)
    preuve_photo_mimetype = db.Column(db.String(50))
    date_envoi = db.Column(db.DateTime, default=datetime.utcnow)
    date_choix = db.Column(db.DateTime)
    date_preuve = db.Column(db.DateTime)

    def texte_choisi(self):
        if self.choix == 'action':
            return self.texte_action
        if self.choix == 'verite':
            return self.texte_verite
        return None

    def to_dict_pour(self, user_id):
        base = {
            'id': self.id, 'statut': self.statut, 'choix': self.choix,
            'sender_id': self.sender_id, 'receiver_id': self.receiver_id,
            'date_envoi': self.date_envoi.strftime('%d/%m %H:%M') if self.date_envoi else '',
            'preuve_texte': self.preuve_texte,
            'a_photo': bool(self.preuve_photo_data),
        }
        if user_id == self.sender_id:
            base['texte_action'] = self.texte_action
            base['texte_verite'] = self.texte_verite
        else:
            base['texte_choisi'] = self.texte_choisi()
        return base


class DefiMessage(db.Model):
    """Discussion qui continue une fois qu'un défi a été choisi (et prouvé)."""
    id = db.Column(db.Integer, primary_key=True)
    defi_id = db.Column(db.Integer, db.ForeignKey('defi.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    texte = db.Column(db.Text, nullable=False)
    lu = db.Column(db.Boolean, default=False)
    modifie = db.Column(db.Boolean, default=False)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('defi_message.id'), nullable=True)
    date_envoi = db.Column(db.DateTime, default=datetime.utcnow)

    reply_to = db.relationship('DefiMessage', remote_side=[id])

    def to_dict(self):
        return {
            'id': self.id, 'defi_id': self.defi_id, 'sender_id': self.sender_id,
            'texte': self.texte, 'lu': self.lu, 'modifie': self.modifie,
            'reply_to_id': self.reply_to_id,
            'date_envoi': self.date_envoi.strftime('%d/%m %H:%M') if self.date_envoi else '',
        }


class Activite(db.Model):
    """Une tâche du plan du jour."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date = db.Column(db.String(10), default=lambda: date.today().isoformat())
    heure = db.Column(db.String(5), default='08:00')
    titre = db.Column(db.String(200), nullable=False)
    emoji = db.Column(db.String(10), default='📌')
    statut = db.Column(db.String(15), default='a_faire')  # a_faire | en_cours | termine
    ordre = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id, 'heure': self.heure, 'titre': self.titre,
            'emoji': self.emoji, 'statut': self.statut, 'date': self.date,
        }


class ActiviteSemaine(db.Model):
    """Une case de l'emploi du temps hebdomadaire."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    jour = db.Column(db.Integer, nullable=False)  # 0=Lundi ... 6=Dimanche
    heure_debut = db.Column(db.String(5), nullable=False)
    heure_fin = db.Column(db.String(5), nullable=False)
    titre = db.Column(db.String(200), nullable=False)
    couleur = db.Column(db.String(20), default='#ff8fab')

    def to_dict(self):
        return {
            'id': self.id, 'jour': self.jour, 'heure_debut': self.heure_debut,
            'heure_fin': self.heure_fin, 'titre': self.titre, 'couleur': self.couleur,
        }


class Message(db.Model):
    """Un message de la messagerie 'À découvrir'."""
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    texte = db.Column(db.Text, nullable=False)
    lu = db.Column(db.Boolean, default=False)
    modifie = db.Column(db.Boolean, default=False)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)
    date_envoi = db.Column(db.DateTime, default=datetime.utcnow)

    reply_to = db.relationship('Message', remote_side=[id])

    def to_dict(self):
        return {
            'id': self.id, 'texte': self.texte, 'lu': self.lu, 'modifie': self.modifie,
            'sender_id': self.sender_id, 'receiver_id': self.receiver_id,
            'reply_to_id': self.reply_to_id,
            'date_envoi': self.date_envoi.strftime('%d/%m/%Y %H:%M') if self.date_envoi else '',
        }


class Objectif(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    texte = db.Column(db.String(300), nullable=False)
    termine = db.Column(db.Boolean, default=False)
    date_realise = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))

    def to_dict(self):
        return {
            'id': self.id, 'texte': self.texte, 'termine': self.termine,
            'date_realise': self.date_realise.strftime('%d/%m/%Y') if self.date_realise else None,
        }


class Souvenir(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titre = db.Column(db.String(150), nullable=False)
    texte = db.Column(db.Text, default='')
    date_souvenir = db.Column(db.String(10), default=lambda: date.today().isoformat())
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))

    def to_dict(self):
        return {
            'id': self.id, 'titre': self.titre, 'texte': self.texte,
            'date': self.date_souvenir,
        }


class PushSubscription(db.Model):
    """Abonnement aux notifications push d'un appareil (téléphone/navigateur)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint = db.Column(db.String(500), nullable=False)
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------- HELPERS ----------------------

def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return User.query.get(uid)


def comptes_valides():
    """Seuls les comptes avec un mot de passe défini comptent comme de 'vrais' comptes."""
    return User.query.filter(User.mot_de_passe_hash.isnot(None))


def partner_user():
    u = current_user()
    if not u:
        return None
    return comptes_valides().filter(User.id != u.id).first()


def ensure_default_challenges():
    if Challenge.query.count() == 0:
        for t, cat, txt in DEFAULT_CHALLENGES:
            db.session.add(Challenge(type=t, categorie=cat, texte=txt, personnalise=False))
        db.session.commit()


def envoyer_push(user, titre, corps, url='/', message_id=None):
    """Envoie une notification push à tous les appareils abonnés de user."""
    if not user:
        return
    subs = PushSubscription.query.filter_by(user_id=user.id).all()
    for s in subs:
        try:
            webpush(
                subscription_info={
                    'endpoint': s.endpoint,
                    'keys': {'p256dh': s.p256dh, 'auth': s.auth},
                },
                data=json.dumps({'title': titre, 'body': corps, 'url': url, 'message_id': message_id}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={'sub': VAPID_CLAIMS_SUB},
            )
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):
                # Abonnement expiré ou invalide côté navigateur : on le supprime proprement.
                db.session.delete(s)
                db.session.commit()
            else:
                app.logger.error(
                    "Échec envoi push (endpoint=%s, status=%s): %s",
                    s.endpoint[:60], status, e
                )
        except Exception as e:
            app.logger.error("Erreur inattendue lors de l'envoi push: %s", e)


def ensure_schema():
    """Ajoute les colonnes des nouvelles fonctionnalités sur une base déjà existante,
    sans perdre les données déjà en place."""
    blob_type = 'BYTEA' if db.engine.dialect.name == 'postgresql' else 'BLOB'
    statements = [
        "ALTER TABLE user ADD COLUMN mot_de_passe_hash VARCHAR(255)",
        f"ALTER TABLE user ADD COLUMN photo_data {blob_type}",
        "ALTER TABLE user ADD COLUMN photo_mimetype VARCHAR(50)",
        "ALTER TABLE message ADD COLUMN reply_to_id INTEGER",
        "ALTER TABLE message ADD COLUMN modifie BOOLEAN DEFAULT FALSE",
        "ALTER TABLE objectif ADD COLUMN date_realise TIMESTAMP",
        f"ALTER TABLE defi ADD COLUMN preuve_photo_data {blob_type}",
        "ALTER TABLE defi ADD COLUMN preuve_photo_mimetype VARCHAR(50)",
        "ALTER TABLE defi_message ADD COLUMN reply_to_id INTEGER",
        "ALTER TABLE defi_message ADD COLUMN modifie BOOLEAN DEFAULT FALSE",
        "ALTER TABLE user ADD COLUMN verrouillage_actif BOOLEAN DEFAULT FALSE",
    ]
    for stmt in statements:
        try:
            db.session.execute(db.text(stmt))
            db.session.commit()
        except Exception:
            db.session.rollback()


with app.app_context():
    db.create_all()
    ensure_schema()
    ensure_default_challenges()


_ROUTES_SANS_VERROU = {'verrouillage', 'logout', 'static'}


@app.before_request
def setup():
    u = current_user()
    if u:
        u.en_ligne = True
        u.derniere_activite = datetime.utcnow()
        db.session.commit()
        if u.verrouillage_actif and request.endpoint not in _ROUTES_SANS_VERROU:
            unlocked_until = session.get('unlocked_until')
            if not unlocked_until or time.time() > unlocked_until:
                return redirect(url_for('verrouillage', next=request.path))
            # Activité en cours : on prolonge la fenêtre de déverrouillage.
            session['unlocked_until'] = _horodatage_deverrouillage()


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            return redirect(url_for('login'))
        return view(*a, **kw)
    return wrapped


@app.route('/verrouillage', methods=['GET', 'POST'])
@login_required
def verrouillage():
    u = current_user()
    error = None
    next_url = request.args.get('next') or request.form.get('next') or url_for('accueil')
    if request.method == 'POST':
        mdp = request.form.get('mot_de_passe', '')
        if u.check_password(mdp):
            session['unlocked_until'] = _horodatage_deverrouillage()
            return redirect(next_url)
        error = "Mot de passe incorrect."
    return render_template('verrouillage.html', user=u, error=error, next=next_url)


@app.template_global()
def avatar_html(u, taille='big'):
    """Affiche la photo de profil (stockée en base de données) si elle existe,
    sinon l'avatar emoji."""
    if not u:
        return Markup(f'<span class="avatar-{taille}">❔</span>')
    if u.photo_mimetype:
        return Markup(
            f'<img src="{url_for("foto_profil", uid=u.id)}" '
            f'class="avatar-photo avatar-{taille}" alt="{u.prenom}">'
        )
    return Markup(f'<span class="avatar-{taille}">{u.avatar}</span>')


@app.context_processor
def inject_notifications():
    u = current_user()
    if not u:
        return {}

    a_choisir_n = Defi.query.filter_by(receiver_id=u.id, choix=None).count()
    preuve_n = Defi.query.filter_by(receiver_id=u.id, statut='choisi').count()
    mes_defis_ids = [d.id for d in Defi.query.filter(
        or_(Defi.sender_id == u.id, Defi.receiver_id == u.id)
    ).all()]
    discussion_n = 0
    if mes_defis_ids:
        discussion_n = DefiMessage.query.filter(
            DefiMessage.defi_id.in_(mes_defis_ids),
            DefiMessage.sender_id != u.id,
            DefiMessage.lu.is_(False),
        ).count()
    notif_action_verite = a_choisir_n + preuve_n + discussion_n

    today_iso = date.today().isoformat()
    notif_plan_jour = Activite.query.filter_by(user_id=u.id, date=today_iso).filter(
        Activite.statut != 'termine'
    ).count()

    notif_decouvrir = Message.query.filter_by(receiver_id=u.id, lu=False).count()

    notif_espace_couple = Souvenir.query.filter_by(date_souvenir=today_iso).count()

    return dict(
        notif_action_verite=notif_action_verite,
        notif_plan_jour=notif_plan_jour,
        notif_decouvrir=notif_decouvrir,
        notif_espace_couple=notif_espace_couple,
    )


# ---------------------- AUTH ----------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    users = comptes_valides().all()
    error = None
    if request.method == 'POST':
        uid = request.form.get('user_id')
        mdp = request.form.get('mot_de_passe', '')
        u = User.query.get(int(uid)) if uid else None
        if u and u.check_password(mdp):
            session.permanent = True
            session['user_id'] = u.id
            session['unlocked_until'] = _horodatage_deverrouillage()
            return redirect(url_for('accueil'))
        error = "Profil ou mot de passe incorrect."
    return render_template('login.html', users=users, error=error)


@app.route('/creer-compte', methods=['GET', 'POST'])
def creer_compte():
    nb_comptes = comptes_valides().count()
    if nb_comptes >= 2:
        return redirect(url_for('login'))
    error = None
    if request.method == 'POST':
        prenom = request.form.get('prenom', '').strip()
        avatar = request.form.get('avatar', '').strip() or '🙂'
        mdp = request.form.get('mot_de_passe', '')
        mdp2 = request.form.get('mot_de_passe2', '')
        if not prenom or not mdp:
            error = "Le prénom et le mot de passe sont obligatoires."
        elif len(mdp) < 4:
            error = "Le mot de passe doit faire au moins 4 caractères."
        elif mdp != mdp2:
            error = "Les deux mots de passe ne correspondent pas."
        else:
            u = User(prenom=prenom, avatar=avatar)
            u.set_password(mdp)
            db.session.add(u)
            db.session.commit()
            session.permanent = True
            session['user_id'] = u.id
            session['unlocked_until'] = _horodatage_deverrouillage()
            return redirect(url_for('accueil'))
    return render_template('creer_compte.html', error=error, nb_comptes=nb_comptes)


@app.route('/logout')
def logout():
    u = current_user()
    if u:
        u.en_ligne = False
        db.session.commit()
    session.pop('user_id', None)
    session.pop('unlocked_until', None)
    return redirect(url_for('login'))


@app.route('/foto/profil/<int:uid>')
def foto_profil(uid):
    u = User.query.get_or_404(uid)
    if not u.photo_data:
        abort(404)
    return Response(u.photo_data, mimetype=u.photo_mimetype or 'image/jpeg')


@app.route('/foto/preuve/<int:did>')
@login_required
def foto_preuve(did):
    d = Defi.query.get_or_404(did)
    u = current_user()
    if u.id not in (d.sender_id, d.receiver_id):
        abort(403)
    if not d.preuve_photo_data:
        abort(404)
    return Response(d.preuve_photo_data, mimetype=d.preuve_photo_mimetype or 'image/jpeg')


# ---------------------- 1. ACCUEIL ----------------------

@app.route('/')
@login_required
def accueil():
    u, p = current_user(), partner_user()
    today = date.today().isoformat()
    activites = Activite.query.filter_by(user_id=u.id, date=today).all()
    total = len(activites)
    faites = len([a for a in activites if a.statut == 'termine'])
    progression = round((faites / total) * 100) if total else 0
    return render_template('accueil.html', user=u, partner=p,
                            total=total, faites=faites, progression=progression)


# ---------------------- 2. ACTION / VERITE ----------------------

@app.route('/action-verite')
@login_required
def action_verite():
    u, p = current_user(), partner_user()
    idees = Challenge.query.all()
    categories = sorted({c.categorie for c in idees})

    a_choisir = Defi.query.filter_by(receiver_id=u.id, choix=None).order_by(Defi.date_envoi.desc()).all()
    en_attente_preuve = Defi.query.filter_by(receiver_id=u.id).filter(
        Defi.choix.isnot(None), Defi.statut == 'choisi'
    ).order_by(Defi.date_choix.desc()).all()

    reponses = Defi.query.filter(
        or_(Defi.sender_id == u.id, Defi.receiver_id == u.id)
    ).filter(Defi.choix.isnot(None)).order_by(Defi.date_envoi.desc()).limit(30).all()

    reponses_dict = {}
    a_marquer = []
    for d in reponses:
        msgs = DefiMessage.query.filter_by(defi_id=d.id).order_by(DefiMessage.date_envoi.asc()).all()
        for m in msgs:
            if m.sender_id != u.id and not m.lu:
                m.lu = True
                a_marquer.append(m)
        reponses_dict[d.id] = msgs
    if a_marquer:
        db.session.commit()

    return render_template('action_verite.html', user=u, partner=p,
                            idees=idees, categories=categories,
                            idees_json=[c.to_dict() for c in idees],
                            a_choisir=a_choisir, en_attente_preuve=en_attente_preuve,
                            reponses=reponses, reponses_dict=reponses_dict)


@app.route('/api/idees', methods=['POST'])
@login_required
def api_add_idee():
    data = request.get_json()
    c = Challenge(type=data['type'], categorie=data.get('categorie', 'couple'),
                  texte=data['texte'], personnalise=True, created_by=current_user().id)
    db.session.add(c)
    db.session.commit()
    return jsonify(c.to_dict()), 201


@app.route('/api/idees/<int:cid>', methods=['DELETE'])
@login_required
def api_delete_idee(cid):
    c = Challenge.query.get_or_404(cid)
    db.session.delete(c)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/defis', methods=['POST'])
@login_required
def api_create_defi():
    """L'utilisateur écrit lui-même l'Action ET la Vérité, puis les envoie ensemble."""
    data = request.get_json()
    p = partner_user()
    if not p:
        return jsonify({'error': 'Aucun-e partenaire trouvé-e'}), 400
    d = Defi(sender_id=current_user().id, receiver_id=p.id,
             texte_action=data['texte_action'], texte_verite=data['texte_verite'])
    db.session.add(d)
    db.session.commit()
    envoyer_push(p, f"🎲 {current_user().prenom} t'a envoyé un défi",
                 "Action ou Vérité : à toi de choisir...", url_for('action_verite'))
    return jsonify(d.to_dict_pour(current_user().id)), 201


@app.route('/api/defis/<int:did>/choisir', methods=['POST'])
@login_required
def api_choisir_defi(did):
    """Le/la destinataire choisit ACTION ou VÉRITÉ, sans jamais voir l'autre."""
    d = Defi.query.get_or_404(did)
    if d.receiver_id != current_user().id:
        return jsonify({'error': 'non autorise'}), 403
    if d.choix is not None:
        return jsonify({'error': 'deja choisi'}), 400
    choix = request.get_json().get('choix')
    if choix not in ('action', 'verite'):
        return jsonify({'error': 'choix invalide'}), 400
    d.choix = choix
    d.statut = 'choisi'
    d.date_choix = datetime.utcnow()
    db.session.commit()
    sender = User.query.get(d.sender_id)
    envoyer_push(sender, f"👀 {current_user().prenom} a fait son choix",
                 "Une preuve arrive bientôt...", url_for('action_verite'))
    return jsonify(d.to_dict_pour(current_user().id))


@app.route('/api/defis/<int:did>/preuve', methods=['POST'])
@login_required
def api_preuve_defi(did):
    """Le/la destinataire envoie sa preuve : une phrase, une photo, ou les deux."""
    d = Defi.query.get_or_404(did)
    if d.receiver_id != current_user().id:
        return jsonify({'error': 'non autorise'}), 403
    if d.choix is None:
        return jsonify({'error': 'pas encore choisi'}), 400

    texte = request.form.get('texte', '').strip()
    fichier = request.files.get('photo')
    photo_data, photo_mime = (None, None)
    if fichier and fichier.filename:
        photo_data, photo_mime = compresser_image(fichier)

    if not texte and not photo_data:
        return jsonify({'error': 'preuve vide'}), 400

    d.preuve_texte = texte or None
    d.preuve_photo_data = photo_data
    d.preuve_photo_mimetype = photo_mime
    d.statut = 'termine'
    d.date_preuve = datetime.utcnow()
    db.session.commit()
    sender = User.query.get(d.sender_id)
    envoyer_push(sender, f"✅ {current_user().prenom} a envoyé sa preuve",
                 "Va voir ce qu'elle/il a fait !", url_for('action_verite'))
    return jsonify(d.to_dict_pour(current_user().id))


@app.route('/api/defis/<int:did>', methods=['DELETE'])
@login_required
def api_delete_defi(did):
    d = Defi.query.get_or_404(did)
    u = current_user()
    if u.id not in (d.sender_id, d.receiver_id):
        return jsonify({'error': 'non autorise'}), 403
    DefiMessage.query.filter_by(defi_id=d.id).delete()
    db.session.delete(d)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/defis/<int:did>/messages', methods=['GET', 'POST'])
@login_required
def api_defi_messages(did):
    """Discussion qui continue une fois qu'un défi a été choisi. On peut
    répondre à un message précis via reply_to."""
    d = Defi.query.get_or_404(did)
    u = current_user()
    if u.id not in (d.sender_id, d.receiver_id):
        return jsonify({'error': 'non autorise'}), 403
    if request.method == 'POST':
        data = request.get_json()
        texte = (data.get('texte') or '').strip()
        if not texte:
            return jsonify({'error': 'message vide'}), 400
        m = DefiMessage(defi_id=d.id, sender_id=u.id, texte=texte,
                       reply_to_id=data.get('reply_to') or None)
        db.session.add(m)
        db.session.commit()
        autre_id = d.receiver_id if u.id == d.sender_id else d.sender_id
        envoyer_push(User.query.get(autre_id), f"💬 {u.prenom} a répondu",
                     texte[:80], url_for('action_verite'))
        return jsonify(m.to_dict()), 201
    msgs = DefiMessage.query.filter_by(defi_id=d.id).order_by(DefiMessage.date_envoi.asc()).all()
    return jsonify([m.to_dict() for m in msgs])


@app.route('/api/defis/messages/<int:mid>', methods=['PUT', 'DELETE'])
@login_required
def api_defi_message_item(mid):
    m = DefiMessage.query.get_or_404(mid)
    if m.sender_id != current_user().id:
        return jsonify({'error': 'non autorise'}), 403
    if request.method == 'DELETE':
        db.session.delete(m)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json()
    texte = (data.get('texte') or '').strip()
    if not texte:
        return jsonify({'error': 'message vide'}), 400
    m.texte = texte
    m.modifie = True
    db.session.commit()
    return jsonify(m.to_dict())


# ---------------------- 3. PLAN DU JOUR ----------------------


@app.route('/plan-jour')
@login_required
def plan_jour():
    u, p = current_user(), partner_user()
    today = date.today().isoformat()
    mes_activites = Activite.query.filter_by(user_id=u.id, date=today).order_by(Activite.heure).all()
    ses_activites = Activite.query.filter_by(user_id=p.id, date=today).order_by(Activite.heure).all() if p else []
    return render_template('plan_jour.html', user=u, partner=p,
                            mes_activites=mes_activites, ses_activites=ses_activites,
                            today=today)


@app.route('/api/plan', methods=['GET', 'POST'])
@login_required
def api_plan():
    if request.method == 'POST':
        data = request.get_json()
        a = Activite(user_id=current_user().id, date=data.get('date', date.today().isoformat()),
                     heure=data.get('heure', '08:00'), titre=data['titre'],
                     emoji=data.get('emoji', '📌'))
        db.session.add(a)
        db.session.commit()
        return jsonify(a.to_dict()), 201
    today = request.args.get('date', date.today().isoformat())
    items = Activite.query.filter_by(user_id=current_user().id, date=today).order_by(Activite.heure).all()
    return jsonify([i.to_dict() for i in items])


@app.route('/api/plan/<int:aid>', methods=['PUT', 'DELETE'])
@login_required
def api_plan_item(aid):
    a = Activite.query.get_or_404(aid)
    if a.user_id != current_user().id:
        return jsonify({'error': 'non autorise'}), 403
    if request.method == 'DELETE':
        db.session.delete(a)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json()
    for field in ('heure', 'titre', 'emoji', 'statut'):
        if field in data:
            setattr(a, field, data[field])
    db.session.commit()
    return jsonify(a.to_dict())


# ---------------------- 4. EMPLOI DU TEMPS ----------------------

@app.route('/emploi-temps')
@login_required
def emploi_temps():
    u, p = current_user(), partner_user()
    mes_creneaux = ActiviteSemaine.query.filter_by(user_id=u.id).all()
    return render_template('emploi_temps.html', user=u, partner=p,
                            jours=JOURS, creneaux=mes_creneaux)


@app.route('/api/semaine', methods=['GET', 'POST'])
@login_required
def api_semaine():
    if request.method == 'POST':
        data = request.get_json()
        a = ActiviteSemaine(user_id=current_user().id, jour=int(data['jour']),
                            heure_debut=data['heure_debut'], heure_fin=data['heure_fin'],
                            titre=data['titre'], couleur=data.get('couleur', '#ff8fab'))
        db.session.add(a)
        db.session.commit()
        return jsonify(a.to_dict()), 201
    items = ActiviteSemaine.query.filter_by(user_id=current_user().id).all()
    return jsonify([i.to_dict() for i in items])


@app.route('/api/semaine/<int:aid>', methods=['PUT', 'DELETE'])
@login_required
def api_semaine_item(aid):
    a = ActiviteSemaine.query.get_or_404(aid)
    if a.user_id != current_user().id:
        return jsonify({'error': 'non autorise'}), 403
    if request.method == 'DELETE':
        db.session.delete(a)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json()
    for field in ('jour', 'heure_debut', 'heure_fin', 'titre', 'couleur'):
        if field in data:
            setattr(a, field, data[field])
    db.session.commit()
    return jsonify(a.to_dict())


# ---------------------- 5. A DECOUVRIR (messagerie) ----------------------

@app.route('/decouvrir')
@login_required
def decouvrir():
    u, p = current_user(), partner_user()
    messages = []
    if p:
        # "Vu" simple et automatique dès l'ouverture, pas besoin de confirmer soi-même.
        non_lus = Message.query.filter_by(sender_id=p.id, receiver_id=u.id, lu=False).all()
        for m in non_lus:
            m.lu = True
        if non_lus:
            db.session.commit()
        messages = Message.query.filter(
            or_(
                and_(Message.sender_id == u.id, Message.receiver_id == p.id),
                and_(Message.sender_id == p.id, Message.receiver_id == u.id),
            )
        ).order_by(Message.date_envoi.asc()).all()
    return render_template('decouvrir.html', user=u, partner=p, messages=messages)


@app.route('/api/messages', methods=['POST'])
@login_required
def api_add_message():
    data = request.get_json()
    p = partner_user()
    if not p:
        return jsonify({'error': "Aucun-e partenaire trouvé-e"}), 400
    texte = (data.get('texte') or '').strip()
    if not texte:
        return jsonify({'error': 'message vide'}), 400
    reply_to = data.get('reply_to') or None
    m = Message(sender_id=current_user().id, receiver_id=p.id, texte=texte, reply_to_id=reply_to)
    db.session.add(m)
    db.session.commit()
    envoyer_push(p, f"💌 {current_user().prenom}", texte[:120], url_for('decouvrir', reply=m.id), message_id=m.id)
    return jsonify(m.to_dict()), 201


@app.route('/api/messages/<int:mid>', methods=['PUT', 'DELETE'])
@login_required
def api_message_item(mid):
    m = Message.query.get_or_404(mid)
    if m.sender_id != current_user().id:
        return jsonify({'error': 'non autorise'}), 403
    if request.method == 'DELETE':
        db.session.delete(m)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json()
    texte = (data.get('texte') or '').strip()
    if not texte:
        return jsonify({'error': 'message vide'}), 400
    m.texte = texte
    m.modifie = True
    db.session.commit()
    return jsonify(m.to_dict())


# ---------------------- 6. ESPACE COUPLE ----------------------

@app.route('/espace-couple')
@login_required
def espace_couple():
    u, p = current_user(), partner_user()
    objectifs_en_cours = Objectif.query.filter_by(termine=False).order_by(Objectif.id.desc()).all()
    objectifs_realises = Objectif.query.filter_by(termine=True).order_by(Objectif.date_realise.desc()).all()
    souvenirs = Souvenir.query.order_by(Souvenir.date_souvenir.desc()).all()
    today_iso = date.today().isoformat()
    rappels = [s for s in souvenirs if s.date_souvenir == today_iso]
    return render_template('espace_couple.html', user=u, partner=p,
                            objectifs_en_cours=objectifs_en_cours,
                            objectifs_realises=objectifs_realises,
                            souvenirs=souvenirs, rappels=rappels,
                            suggestions=SOUVENIR_SUGGESTIONS)


@app.route('/api/objectifs', methods=['POST'])
@login_required
def api_add_objectif():
    data = request.get_json()
    o = Objectif(texte=data['texte'], created_by=current_user().id)
    db.session.add(o)
    db.session.commit()
    return jsonify(o.to_dict()), 201


@app.route('/api/objectifs/<int:oid>', methods=['PUT', 'DELETE'])
@login_required
def api_objectif_item(oid):
    o = Objectif.query.get_or_404(oid)
    if request.method == 'DELETE':
        if o.termine:
            return jsonify({'error': "Ce rêve est gravé pour toujours, il ne peut plus être supprimé."}), 403
        db.session.delete(o)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json()
    if o.termine and ('termine' in data and not data['termine']):
        return jsonify({'error': "Ce rêve est déjà gravé pour toujours, impossible de revenir en arrière."}), 403
    if 'termine' in data and data['termine'] and not o.termine:
        o.termine = True
        o.date_realise = datetime.utcnow()
    if 'texte' in data and not o.termine:
        o.texte = data['texte']
    db.session.commit()
    return jsonify(o.to_dict())


@app.route('/api/souvenirs', methods=['POST'])
@login_required
def api_add_souvenir():
    data = request.get_json()
    s = Souvenir(titre=data['titre'], texte=data.get('texte', ''),
                date_souvenir=data.get('date', date.today().isoformat()),
                created_by=current_user().id)
    db.session.add(s)
    db.session.commit()
    return jsonify(s.to_dict()), 201


@app.route('/api/souvenirs/<int:sid>', methods=['PUT', 'DELETE'])
@login_required
def api_souvenir_item(sid):
    s = Souvenir.query.get_or_404(sid)
    if request.method == 'DELETE':
        db.session.delete(s)
        db.session.commit()
        return jsonify({'ok': True})
    data = request.get_json()
    for field in ('titre', 'texte', 'date'):
        key = 'date_souvenir' if field == 'date' else field
        if field in data:
            setattr(s, key, data[field])
    db.session.commit()
    return jsonify(s.to_dict())


# ---------------------- 7. PROFIL ----------------------

@app.route('/profil', methods=['GET', 'POST'])
@login_required
def profil():
    u = current_user()
    if request.method == 'POST':
        u.prenom = request.form.get('prenom', u.prenom).strip() or u.prenom
        avatar_val = request.form.get('avatar', '').strip()
        if avatar_val:
            u.avatar = avatar_val

        fichier = request.files.get('photo')
        if fichier and fichier.filename:
            data, mime = compresser_image(fichier)
            if data:
                u.photo_data = data
                u.photo_mimetype = mime

        nouveau_mdp = request.form.get('nouveau_mdp', '').strip()
        if nouveau_mdp:
            u.set_password(nouveau_mdp)

        db.session.commit()
        return redirect(url_for('profil'))
    return render_template('profil.html', user=u, partner=partner_user(), vapid_public_key=VAPID_PUBLIC_KEY)


@app.route('/profil/verrouillage', methods=['POST'])
@login_required
def toggle_verrouillage():
    u = current_user()
    u.verrouillage_actif = not u.verrouillage_actif
    if u.verrouillage_actif:
        session['unlocked_until'] = _horodatage_deverrouillage()
    db.session.commit()
    return redirect(url_for('profil'))


# ---------------------- NOTIFICATIONS PUSH ----------------------

@app.route('/api/push/vapid-public-key')
@login_required
def push_public_key():
    return jsonify({'key': VAPID_PUBLIC_KEY})


@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    data = request.get_json()
    endpoint = data.get('endpoint')
    keys = data.get('keys', {})
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'error': 'invalide'}), 400
    existing = PushSubscription.query.filter_by(user_id=current_user().id, endpoint=endpoint).first()
    if not existing:
        db.session.add(PushSubscription(user_id=current_user().id, endpoint=endpoint,
                                        p256dh=keys['p256dh'], auth=keys['auth']))
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    data = request.get_json()
    endpoint = data.get('endpoint')
    PushSubscription.query.filter_by(user_id=current_user().id, endpoint=endpoint).delete()
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/sw.js')
def service_worker():
    from flask import send_from_directory, make_response
    resp = make_response(send_from_directory(app.static_folder, 'sw.js'))
    resp.headers['Content-Type'] = 'application/javascript'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_schema()
        ensure_default_challenges()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
