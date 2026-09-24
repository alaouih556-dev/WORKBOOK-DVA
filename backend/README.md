# Backend Paddle Billing — Workbook DVA

FastAPI backend pour vendre le **Workbook DVA** + **Masterclass DVA** via
[Paddle Billing](https://developer.paddle.com/) (Sandbox en développement).
Livraison automatique par email de liens protégés expirants.

---

## Architecture

```
workbook-site/
├── index (6).html              ← page existante (lecture seule, jamais modifiée)
└── backend/
    ├── app/
    │   ├── __init__.py
    │   ├── main.py             ← FastAPI app + tous les endpoints
    │   ├── config.py           ← Settings (pydantic-settings, lit .env)
    │   ├── database.py         ← SQLite + schema
    │   ├── models.py           ← Requêtes DB (orders, webhook_events)
    │   ├── schemas.py          ← Pydantic request / response
    │   ├── security.py         ← tokens HMAC, liens signés
    │   ├── validation.py       ← validation côté serveur du formulaire
    │   ├── paddle.py           ← Client Paddle API (httpx, sandbox)
    │   ├── webhook.py          ← Vérification Paddle-Signature + traitement
    │   └── delivery.py         ← Envoi email + liens expirants
    ├── tests/
    │   ├── conftest.py
    │   ├── test_api.py
    │   ├── test_webhook.py
    │   └── test_delivery.py
    ├── data/                   ← créé automatiquement (SQLite)
    ├── requirements.txt
    ├── .env.example
    └── README.md
```

---

## Installation (PowerShell)

```powershell
# 1. Se placer dans le dossier backend
cd C:\Users\nitro\Desktop\workbook-site\backend

# 2. Créer l'environnement virtuel
python -m venv .venv

# 3. Activer l'environnement
.\.venv\Scripts\Activate.ps1

# 4. Installer les dépendances
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt  # pour les tests

# 5. Copier et renseigner la configuration
Copy-Item .env.example .env
notepad .env                       # remplir les valeurs réelles
```

---

## Lancement

```powershell
# Toujours depuis backend/ avec .venv activé

# Serveur de développement (reload automatique)
uvicorn app.main:app --reload --port 8000

# Ouvrir :
# http://localhost:8000        ← la page existante (lecture seule)
# http://localhost:8000/docs   ← Swagger / OpenAPI

# Entrée ASGI réservée au webhook uniquement (pas de /, /docs, admin, fichiers) :
# URL complète Paddle : <domaine-public>/api/webhooks/paddle
uvicorn app.webhook_only:app --port 8001
```

---

## Fichier .env — valeurs à renseigner

| Variable | Description | Valeur par défaut |
|---|---|---|
| `PADDLE_API_KEY` | Clé API **Sandbox** | (vide — requis) |
| `PADDLE_PRICE_ID` | Price ID du Workbook (commence par `pri_`) | (vide — requis) |
| `PADDLE_EXPECTED_CURRENCY` | Devise du prix (ex. `USD`) | `USD` |
| `PADDLE_WEBHOOK_SECRET` | Clé secrète webhook (pdl_ntfset_…) | (vide — requis) |
| `ACCESS_LINK_SECRET` | Secret pour signer les liens expirants | (vide — requis) |
| `EMAIL_HOST` | Serveur SMTP | (vide — requis pour envoi) |
| `EMAIL_FROM` | Adresse expéditeur | (vide — requis pour envoi) |
| `DELIVERY_FILE_WORKBOOK` | Chemin vers le PDF Workbook | (vide — requis pour livraison) |
| `MASTERCLASS_URL` | Lien live Masterclass (Zoom, etc.) | (vide — requis pour livraison) |
| `ADMIN_STATUS_TOKEN` | Jeton imprévisible pour `/api/status` | (vide — requis) |

> **Générer un jeton admin :**
> ```powershell
> python -c "import secrets; print(secrets.token_urlsafe(32))"
> ```

---

## Endpoints

| Méthode | Route | Description | Auth |
|---|---|---|---|
| `GET` | `/` | Sert la page HTML existante (lecture seule) | — |
| `POST` | `/api/orders` | Valide le formulaire + crée la transaction Paddle → retourne `order_id`, `order_number`, `status`, `checkout_url`, `checkout_token`, `transaction_id` | — |
| `GET` | `/api/orders/{id}/status?token=` | Statut d'une commande | checkout_token |
| `POST` | `/api/webhooks/paddle` | Réception webhook Paddle | Paddle-Signature |
| `GET` | `/api/access/workbook/{id}?token=` | Téléchargement PDF expirant | token signé |
| `GET` | `/api/access/masterclass/{id}?token=` | Redirection vers la Masterclass | token signé |
| `GET` | `/api/status?token=` | Config + stats (masqué) | admin_status_token |
| `POST` | `/api/admin/deliveries/{id}/retry?token=` | Relancer un envoi échoué | admin_status_token |

---

## Flux de paiement

```
1.  Page HTML → POST /api/orders (JSON)
2.  Backend crée une order (status: created)
3.  Backend appelle Paddle API Sandbox → transaction draft
4.  Backend retourne checkout_url au navigateur
5.  Navigateur redirige → Paddle Checkout (test card 4000 0000 0000 0002)
6.  Paiement confirmé → Paddle envoie POST /api/webhooks/paddle
7.  Backend vérifie Paddle-Signature (HMAC-SHA256 sur corps brut)
8.  Backend vérifie : order_id, transaction_id, price_id, devise, montant
9.  Backend marque la commande payée (atomique, idempotent)
10. Backend envoie email avec liens Workbook + Masterclass (expirants)
```

---

## Tests

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest -v
```

### Tests couverts

| Test | Scénario |
|---|---|
| `test_missing_signature_header_returns_400` | Header Paddle-Signature absent → 400 |
| `test_invalid_signature_returns_401` | Mauvais hash → 401, aucune traitement |
| `test_wrong_secret_returns_401` | Secret erroné → 401 |
| `test_tampered_body_returns_401` | Corps modifié après signature → 401 |
| `test_replay_old_timestamp_rejected` | Timestamp trop vieux (> 5 min) → 401 |
| `test_price_mismatch_not_paid` | Price ID incohérent → rejected, pas payé |
| `test_currency_mismatch_not_paid` | Devise ≠ USD → rejected |
| `test_unknown_order_rejected_and_recorded` | order_id inexistant → rejected |
| `test_transaction_id_mismatch_rejected` | txn_id ≠ celui créé → rejected |
| `test_valid_webhook_marks_paid_and_delivery_pending` | Happy path → payé, delivery pending |
| `test_duplicate_event_not_reprocessed` | Même event_id deux fois → 200, pas de doublon |
| `test_two_events_same_order_both_recorded` | Deux events différents même order → tous enregistrés |
| `test_transaction_created_is_ignored` | Event non `completed` → ignoré, 200 |
| `test_workbook_download_after_payment` | PDF servi uniquement si payé + token valide |
| `test_masterclass_redirect_after_payment` | 302 vers MASTERCLASS_URL |
| `test_unpaid_order_cannot_download` | Token valide mais non payé → 403 |
| `test_tampered_token_rejected` | Token falsifié → 403 |
| `test_expired_token_rejected` | Token expiré → 403 |
| `test_retry_when_email_configured_sends` | Relance envoi → sent |
| `test_file_missing_blocks_delivery` | Fichier absent → pending |
| Validation (4 tests) | Champs manquants, email invalide, téléphone invalide, challenge interdit |

---

## Ce qui ne sera pas modifié

- Le fichier `index (6).html` n'est **jamais** modifié (ni contenu, ni design, ni scripts).
- Le **Default payment link** du compte Paddle (pointant vers MinderCoach) n'est pas modifié.
- Aucun secret n'est exposé dans le HTML, les logs ou les réponses API.

---

## Modifications HTML à prévoir (étape 2)

Pour que la page existante utilise ce backend, voici les changements nécessaires
(à appliquer dans un second temps, **jamais** dans cette étape) :

1. **Cible du formulaire** : remplacer `fetch(GOOGLE_APP_SCRIPT_URL, ...)` par
   `fetch('/api/orders', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) })`.

2. **Redirection après création** : utiliser la réponse `checkout_url` pour
   rediriger le client vers le Paddle Checkout :
   ```js
   const res = await fetch('/api/orders', { ... });
   const { checkout_url, checkout_token, order_id } = await res.json();
   window.location.href = checkout_url;
   ```

3. **Polling du statut** : après retour du checkout, ouvrir un panneau
   « Merci, votre paiement est en cours de vérification » et interroger
   `GET /api/orders/{order_id}/status?token={checkout_token}` toutes les 3 secondes.

4. **Désactiver `mode: 'no-cors'`** : le navigateur doit pouvoir lire la réponse JSON
   du backend.

5. **Adapter le message de succès** : indiquer que l'email de livraison sera envoyé
   après confirmation du paiement (au lieu de « l'équipe va vous contacter »).

6. **Le prix affiché** : la valeur « 199 MAD » reste dans le HTML tel quel
   (votre choix commercial). Le prix Sandbox Paddle est un prix de test séparé.