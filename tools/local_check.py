"""Test login and usage, reusing completed sessions from an ignored local cache."""
import asyncio
import getpass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aiohttp import ClientSession
from custom_components.odido_mobile_usage.api import OdidoClient, OdidoError, OdidoInvalidAuth
from session_store import load_session, save_session, clear_session

SESSION_PATH = ROOT / "debug" / "session.json"


async def main():
    username = input("Mijn Odido-gebruikersnaam: ").strip()
    async with ClientSession() as session:
        cookies = load_session(SESSION_PATH, username)
        client = OdidoClient(session, username, "", cookies=cookies)
        usage = None
        try:
            if cookies:
                print("Opgeslagen sessie hergebruiken...")
                try:
                    usage = await client.async_get_usage()
                except OdidoInvalidAuth:
                    clear_session(SESSION_PATH)
                    print("De opgeslagen sessie is verlopen; opnieuw aanmelden is nodig.")
                    cookies = None
            if not cookies:
                password = getpass.getpass("Wachtwoord (verborgen): ")
                client = OdidoClient(session, username, password)
                if await client.async_start_login():
                    await client.async_submit_code(getpass.getpass("Verificatiecode (verborgen): ").strip())
                # Keep completed authentication even if parsing usage fails.
                save_session(SESSION_PATH, username, client.export_cookies())
                print("Aangemelde sessie lokaal opgeslagen; wachtwoord en code zijn niet opgeslagen.")
                usage = await client.async_get_usage()
            save_session(SESSION_PATH, username, client.export_cookies())
            restored = OdidoClient(session, username, "", cookies=client.export_cookies())
            await restored.async_get_usage()
            save_session(SESSION_PATH, username, restored.export_cookies())
        except OdidoError as err:
            if isinstance(err, OdidoInvalidAuth):
                clear_session(SESSION_PATH)
            else:
                save_session(SESSION_PATH, username, client.export_cookies())
            print(f"Mislukt: {type(err).__name__}: {err}")
            print("Veilige diagnostiek: " + json.dumps(client.debug_summary()))
            if SESSION_PATH.exists():
                print("De sessie blijft bewaard voor een volgende poging; opnieuw inloggen is dan meestal niet nodig.")
            return 1
        print(json.dumps(usage.as_dict(), indent=2))
        print("Sessieverificatie met nieuwe client geslaagd. De sessie is lokaal bewaard.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
