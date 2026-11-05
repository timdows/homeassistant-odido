"""Live test of Odido's app route, reusing a locally saved app token."""

import asyncio
import getpass
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from custom_components.odido_mobile_usage.api import OdidoError, OdidoInvalidAuth
from custom_components.odido_mobile_usage.app_api import OdidoAppClient

CACHE = ROOT / "debug/app-session.json"
BOOTSTRAP_CACHE = ROOT / "debug/app-bootstrap.json"


def load_session(username):
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        if (
            data.get("account")
            == hashlib.sha256(username.casefold().encode()).hexdigest()
        ):
            return data.get("session")
    except (OSError, ValueError, AttributeError):
        pass
    return None


def save_session(username, client):
    session = client.export_session()
    if not session:
        return
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".app-session-", dir=CACHE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "account": hashlib.sha256(username.casefold().encode()).hexdigest(),
                    "session": session,
                },
                stream,
            )
        os.replace(temporary, CACHE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_bootstrap(username):
    try:
        data = json.loads(BOOTSTRAP_CACHE.read_text(encoding="utf-8"))
        if (
            data.get("account")
            == hashlib.sha256(username.casefold().encode()).hexdigest()
        ):
            from custom_components.odido_mobile_usage.cookie_store import valid_cookies

            if valid_cookies(data.get("cookies")):
                return data["cookies"]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def save_bootstrap(username, client):
    if client.export_session():
        BOOTSTRAP_CACHE.unlink(missing_ok=True)
        return
    cookies = client.export_bootstrap()
    if not cookies:
        return
    BOOTSTRAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".app-bootstrap-", dir=BOOTSTRAP_CACHE.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "account": hashlib.sha256(username.casefold().encode()).hexdigest(),
                    "cookies": cookies,
                },
                stream,
            )
        os.replace(temporary, BOOTSTRAP_CACHE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def main():
    username = input("Mijn Odido-gebruikersnaam: ").strip()
    saved = load_session(username)
    bootstrap = load_bootstrap(username)
    client = OdidoAppClient(None, username, "", app_session=saved, bootstrap=bootstrap)
    usage = None
    try:
        if saved:
            print("Opgeslagen appsessie hergebruiken...")
            try:
                usage = await client.async_get_usage()
            except OdidoInvalidAuth:
                saved = None
                CACHE.unlink(missing_ok=True)
                print("Appsessie afgewezen; opnieuw aanmelden is nodig.")
        if not saved and bootstrap:
            print("Aangemelde browsersessie hervatten, zonder wachtwoord of sms...")
            try:
                await client.async_resume_bootstrap()
            except OdidoInvalidAuth:
                BOOTSTRAP_CACHE.unlink(missing_ok=True)
            else:
                saved = client.export_session()
                save_session(username, client)
                save_bootstrap(username, client)
                usage = await client.async_get_usage()
        if not saved:
            password = getpass.getpass("Wachtwoord (verborgen): ")
            client = OdidoAppClient(None, username, password)
            if await client.async_start_login():
                await client.async_submit_code(
                    getpass.getpass("Verificatiecode (verborgen): ").strip()
                )
            save_session(username, client)
            save_bootstrap(username, client)
            print("Appsessie opgeslagen; wachtwoord en code zijn niet opgeslagen.")
            usage = await client.async_get_usage()
        save_session(username, client)
        print(json.dumps(usage.as_dict(), indent=2))
        restored = OdidoAppClient(
            None, username, "", app_session=client.export_session()
        )
        await restored.async_get_usage()
        save_session(username, restored)
        print(
            "Appgegevens ophalen en sessiehergebruik geslaagd, zonder nieuwe aanmelding."
        )
        return 0
    except OdidoError as error:
        if isinstance(error, OdidoInvalidAuth) and not client.export_bootstrap():
            CACHE.unlink(missing_ok=True)
            BOOTSTRAP_CACHE.unlink(missing_ok=True)
        else:
            save_session(username, client)
            save_bootstrap(username, client)
        print(f"Mislukt: {type(error).__name__}: {error}")
        print("Veilige diagnostiek: " + json.dumps(client.debug_summary()))
        if CACHE.exists():
            print("De appsessie blijft bewaard voor de volgende poging.")
        elif BOOTSTRAP_CACHE.exists():
            print(
                "De voltooide browsersessie blijft bewaard; de volgende poging probeert eerst de app-overdracht zonder nieuwe aanmelding."
            )
        return 1


if __name__ == "__main__":
    if not sys.stdin.isatty():
        raise SystemExit("Start in een interactieve terminal; pipe geen inloggegevens.")
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Geannuleerd.")
    except Exception as error:  # noqa: BLE001 -- redact unexpected HTTP exception data
        print(f"Onverwachte fout ({type(error).__name__}); details afgeschermd.")
        raise SystemExit(1) from None
