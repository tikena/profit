"""
Envoi d'email (utilisé pour le code de vérification à l'inscription).

Fonctionne avec n'importe quel service SMTP standard (Gmail avec mot de
passe d'application, Brevo, SendGrid en mode SMTP relay, etc.) — voir
.env.example pour la configuration.

Si SMTP_HOST n'est pas configuré, le code est simplement écrit dans les
logs du serveur au lieu d'être envoyé par email : pratique pour tester en
local sans avoir à configurer un vrai service d'envoi, mais À NE JAMAIS
laisser en l'état une fois en production (personne ne recevrait ses codes).
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger("profit.mailer")


def send_verification_code(to_email: str, code: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST")

    if not smtp_host:
        logger.warning(
            "SMTP_HOST non configuré : le code de vérification n'est PAS envoyé par email. "
            "Code pour %s : %s (voir .env.example pour configurer un vrai envoi)",
            to_email,
            code,
        )
        return

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    from_address = os.environ.get("SMTP_FROM", smtp_user or "no-reply@profit.example")

    message = MIMEText(
        f"Voici votre code de vérification Profit : {code}\n\n"
        f"Ce code expire dans 15 minutes. Si vous n'avez pas demandé ce code, ignorez cet email."
    )
    message["Subject"] = "Votre code de vérification Profit"
    message["From"] = from_address
    message["To"] = to_email

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_address, [to_email], message.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        # On ne fait jamais planter l'inscription à cause d'un souci d'envoi
        # d'email : l'utilisateur pourra toujours redemander un nouveau code.
        logger.error("Échec de l'envoi du code de vérification à %s : %s", to_email, exc)