# Odido Mobile Usage voor Home Assistant

HACS-integratie voor mobiele abonnementen in Mijn Odido, waaronder voormalige
Tele2-abonnementen. Gebaseerd op de website en de bestaande eigen repository
[HACS-odido-mobile-usage](https://github.com/timdows/HACS-odido-mobile-usage).

## Status

De ingelogde pagina's zijn op 5 november 2026 onderzocht. De parser is aangepast
aan de echte structuur van `/my/tegoed` en `/my/verbruik-en-kosten`.
De formulieraanmelding is door de gebruiker met een echt account uitgevoerd.
Versie 0.2.4 is op 5 november 2026 met die opgeslagen sessie getest: bundeltegoed,
maandverbruik, belminuten, sms en extra kosten zijn succesvol uitgelezen.
Installatie en uitvoering in Home Assistant moeten nog worden getest.
Het openbare inlogformulier is ook via een losse HTTP-client gecontroleerd.
De aanmelding is vervolgens aangepast aan een lokale netwerkopname van de
werkelijke aanmelding. Deze gebruikt een viercijferige sms-code: eerst een
AJAX-controle met `pincode` en daarna een aparte POST om de sessie af te ronden.
Ook de inlogknop wordt expliciet meegestuurd. De echte formulieren en de
succesvolle landingspagina worden correct herkend bij offline controle.
De opname bevat geen cookies en geen verbruikspagina's; hiermee is geen nieuwe
live sessie gestart. De originele opname, wachtwoorden en codes staan niet in
de repository. Alleen formulierstructuren met volledig vervangen veldwaarden
zijn opgenomen als tests.

Versie 0.2.2 stuurt voor de sms-controle dezelfde JSON-`Accept`-header en
UTF-8-formulierinhoud als de opgenomen browseraanvraag. JSON met een Unicode-BOM
wordt ook herkend. Bij een onverwacht antwoord meldt de lokale test alleen
HTTP-status, antwoordformaat, paginacategorie, omleiding en lengte; geen inhoud
of geheime veldwaarden. De volledige live aanmelding moet nog slagen voordat
de integratie als werkend kan worden beschouwd.

Als Odido na aanmelden eerst het abonnementsoverzicht toont, laadt de client
het abonnementenblok en selecteert het enige mobiele abonnement met het
formulier van de website. Bij meerdere mobiele abonnementen stopt de client;
een expliciete nummerkeuze moet dan nog worden toegevoegd.

## Sensoren

| Sensor | Bron |
| --- | --- |
| Databundel, resterende data, bundelverbruik en percentage | Tegoed |
| Dataverbruik maandoverzicht | Verbruik & Kosten |
| Verbruikte belminuten, inclusief seconden als fractie van een minuut | Verbruik & Kosten |
| Belbundel (Onbeperkt wanneer expliciet vermeld) | Tegoed |
| Sms-verbruik en extra kosten deze maand | Verbruik & Kosten |

De tegoedpagina en het maandoverzicht kunnen op verschillende momenten zijn
bijgewerkt. Bundelverbruik wordt berekend als bundel minus resterend; het
maandoverzicht wordt afzonderlijk getoond. 1.000 MB is hier 1 GB, overeenkomstig
de providerweergave. `26:41 min` betekent 26 minuten en 41 seconden.
Meerdere gelijktijdige databundels worden nog niet ondersteund: de parser
meldt een fout in plaats van mogelijk overlappende bundels op te tellen.

## Installatie

1. Kopieer `custom_components/odido_mobile_usage` naar `config/custom_components`.
2. Herstart Home Assistant.
3. Voeg **Odido Mobile Usage** toe via Apparaten & diensten.
4. Vul je Mijn Odido-gebruikersnaam, wachtwoord en viercijferige sms-code in.

De vastgestelde flow is die voor sms-verificatie. Andere verificatiemethoden
zijn niet met een echte opname gevalideerd.

Voor HACS: publiceer deze repository op GitHub, voeg de repository-URL als
aangepaste repository van categorie **Integration** toe en download de integratie.
De documentatielinks gaan uit van `timdows/homeassistant-odido`; pas ze aan als
de uiteindelijke repository anders heet. Er is nog niets gepubliceerd.

De domeinnaam blijft `odido_mobile_usage`, zodat deze de bestaande integratie
kan vervangen. Installeer niet twee repositories voor hetzelfde domein.

## Sessies

Cookies zijn per account geïsoleerd van Home Assistants gedeelde HTTP-sessie.
Alleen een volledig aangemelde en met succes uitgelezen sessie wordt opgeslagen.
Vernieuwde cookies worden na succesvol uitlezen bewaard. Een verlopen sessie
start de Home Assistant-heraanmelding; er worden niet automatisch nieuwe
verificatiecodes aangevraagd. Odido bepaalt de geldigheid van de sessie.
Er is geen garantie op maandenlange geldigheid.

Er worden geen individuele gesprekken, telefoonnummers, klantnummers,
adressen, analytische identificatievelden of ruwe antwoorden als
sensorattributen gepubliceerd. Diagnostiek bevat geen verbruik of geheimen.

## Tests

Python 3.12 of hoger:

```powershell
python -m pip install aiohttp voluptuous
python -m unittest discover -s tests -v
```

Tests gebruiken synthetische gegevens en nagebootste websitesessies.
Voor een echte lokale aanmeldtest:

```powershell
python tools/local_check.py
```

Deze test bewaart vanaf versie 0.2.3 de cookies van een volledig aangemelde
sessie in `debug/session.json`. Dit bestand is uitgesloten van Git. Het bevat
geen wachtwoord of sms-code, maar geeft wel toegang tot de sessie: houd het privé.
Bij de volgende run vraagt de test je gebruikersnaam en probeert eerst de
opgeslagen sessie. Alleen wanneer Odido de sessie afwijst, wordt opnieuw om
je wachtwoord en verificatiecode gevraagd. Een uitleesfout bewaart de sessie,
zodat we de parser verder kunnen onderzoeken zonder nieuwe aanmelding.
Om de sessie lokaal te vergeten, verwijder je `debug/session.json`.

De vorige versies bewaarden geen lokale testsessie; na de update is daarom
nog één nieuwe aanmelding nodig. Home Assistant gebruikt zijn eigen opgeslagen
integratiesessie; de lokale testcache wordt daar niet automatisch geïmporteerd.

De client laadt de formulieren `BundleUsage.GetStatus` op `/my/tegoed` en
`SimUsageDetail.GetUsageTotals` op `/my/verbruik-en-kosten`. De gewone GET-pagina's
bevatten alleen de nog lege verbruiksblokken. Kolomnamen van het maandoverzicht
worden uit `data-table-header` gelezen; ze staan niet als tekst in de cellen.
De parser meldt welk onderdeel ontbreekt, met veilige structurele diagnostiek.

Op deze Windows-computer kun je ook `tools/local_check.ps1` starten. Die vindt
de meegeleverde Python, maakt een lokale `.venv` en installeert de benodigde
pakketten voordat de test begint.
Plaats HAR-bestanden, cookies, wachtwoorden of ingelogde HTML nooit in Git.

Dit is een onofficiële integratie die alleen gegevens uitleest en niets koopt,
geen bundels activeert en geen accountinstellingen verandert.
