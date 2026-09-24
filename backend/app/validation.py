from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^[0-9+().\- ]{8,40}$")

CHALLENGES = frozenset(
    {
        "Trop d'opérationnel",
        "Croissance stagnante",
        "Manque d'autonomie",
        "Autre",
    }
)

FIELD_LABELS = {
    "firstName": "Prénom",
    "lastName": "Nom",
    "email": "Email",
    "phone": "Téléphone",
    "company": "Entreprise",
    "challenge": "Défi",
}


def validate_order_payload(data: dict) -> dict[str, str]:
    """Return {field: message_fr} for each invalid field (empty = valid)."""
    errors: dict[str, str] = {}

    def has(label: str):
        return (data.get(label) or "").strip()

    if not has("firstName"):
        errors["firstName"] = "Prénom obligatoire."
    elif len(data["firstName"]) > 80:
        errors["firstName"] = "Prénom trop long."

    if not has("lastName"):
        errors["lastName"] = "Nom obligatoire."
    elif len(data["lastName"]) > 80:
        errors["lastName"] = "Nom trop long."

    email = has("email")
    if not email:
        errors["email"] = "Email obligatoire."
    elif not EMAIL_RE.match(email):
        errors["email"] = "Email invalide."

    phone = has("phone")
    if not phone:
        errors["phone"] = "Téléphone obligatoire."
    elif not PHONE_RE.match(phone):
        errors["phone"] = "Numéro de téléphone invalide."

    if not has("company"):
        errors["company"] = "Entreprise obligatoire."
    elif len(data["company"]) > 200:
        errors["company"] = "Entreprise trop longue."

    challenge = has("challenge")
    if not challenge:
        errors["challenge"] = "Défi obligatoire."
    elif challenge not in CHALLENGES:
        errors["challenge"] = "Valeur de défi non autorisée."

    return errors