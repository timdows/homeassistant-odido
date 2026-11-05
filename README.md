# Odido Mobile Usage voor Home Assistant

Onofficiële HACS-integratie voor mobiele Mijn Odido-abonnementen, waaronder
voormalige Tele2-abonnementen. Leest bundeltegoed en verbruik; wijzigt geen
abonnementen en activeert geen bundels.

## Installatie en overstappen

1. Voeg in HACS `https://github.com/timdows/homeassistant-odido` toe als aangepaste repository van categorie **Integratie**.
2. Download **Odido Mobile Usage** en herstart Home Assistant.
3. Ga naar **Instellingen → Apparaten & diensten → Integratie toevoegen → Odido Mobile Usage**.
4. Kies **App-route** en voer je Mijn Odido-gegevens en de gevraagde viercijferige sms-code in.

Bij een bestaande websiteconfiguratie kies je bij die integratie
**Herconfigureren** om over te stappen naar de app-route. Dit behoudt de
configuratie en sensor-ID's. Een update alleen zet de bestaande websiteflow
niet automatisch om. De lokale testsessie wordt niet automatisch naar Home
Assistant gekopieerd; de eerste app-aanmelding daar kan opnieuw MFA vragen.
Installeer één repository voor het domein `odido_mobile_usage`; verwijder een
oude HACS-bron voor hetzelfde domein voordat je deze gebruikt.

Handmatige installatie kan ook: kopieer `custom_components/odido_mobile_usage`
naar `config/custom_components/` en herstart Home Assistant.

## Hoe de officiële app aanmeldt

De Android-APK Odido 9.10.0 is statisch onderzocht. De knop **Gebruiker toevoegen**
opent een browservenster. De app gebruikt dus een website voor de aanmelding,
waarna gegevens via de app-API worden opgehaald:

1. Open `https://www.odido.nl/login?returnSystem=app&nav=off&token=…`.
   De parameter `token` bevat de met Fernet versleutelde openbare client-ID
   uit de APK. Dezelfde waarde blijft behouden tijdens de formulieraanmelding.
2. Doorloop het wachtwoordformulier, de AJAX-controle van de sms-code en de
   afsluitende formulier-POST. De code wordt niet als sessie opgeslagen.
3. Ontvang de terugkeer op `/loginappresult?token=…`. De integratie ontsleutelt
   deze, controleert account en tijdclaims en haalt de autorisatiecode uit de
   geneste versleutelde waarde. Deze code is tijdelijk en wordt niet bewaard.
4. De client probeert de ontvangen waarde op de accountresource. Bij een
   vereiste uitwisseling volgt eenmaal `POST https://capi.odido.nl/createtoken`
   met JSON `AuthorizationCode`, Basic-clientauthenticatie en de header
   `grant_type: authorization_code`. De Android-app leest het uiteindelijke
   token uit de **AccessToken-responseheader**. HTTP **202** is hierbij geldig;
   de antwoordbody hoeft geen JSON-object te bevatten. Een ErrorCode-header
   wordt gecontroleerd. Een verbruikte code wordt niet opnieuw aangeboden.
5. Bewaar het bevestigde app-token en gebruik dit als bearer voor het uitlezen.

De client-ID en Fernet-sleutel zijn publieke appconstanten; ze vervangen
geen accountaanmelding. Het oude rechtstreekse CAPI-wachtwoordendpoint gaf
tijdens onderzoek HTTP 503 en wordt niet gebruikt. De Android-app hoeft niet
op Home Assistant geïnstalleerd of uitgevoerd te worden.

## Verbruik ophalen via de app-API

De API biedt resource-URL's aan. De integratie volgt deze in plaats van
klantnummers of abonnementspaden zelf samen te stellen. De Accept-header is
`application/json,application/vnd.capi.tmobile.nl.<label>.v<versie>+json`,
met het label in kleine letters.

| Stap | Resource en versie | Gegevens |
| --- | --- | --- |
| Account | Account v1 met `resourcelabel=LinkedSubscriptions` | Verwijzing naar gekoppelde abonnementen |
| Gekoppelde abonnementen | LinkedSubscriptions v1 | Eén actief mobiel abonnement selecteren |
| Abonnement | Subscription v2 | Beschikbare verbruiksresources |
| Bundelstatus | PostpaidBundleStatus v5 | Belbundel en extra kosten |
| Databundel | PostpaidDataBundleStatus v9 | Limiet, gebruikt en resterend |
| Maandoverzicht | PostpaidBundleStatusDetails v4 | Geaggregeerde belduur, sms en maanddata |

`account/current` kan HTTP 302 naar een accountpad geven. Alleen GET-omleidingen
naar HTTPS op dezelfde CAPI-host worden gevolgd, met maximaal vijf hops.
Het bearer-token wordt niet naar een andere host doorgestuurd. Subscription
v1 gaf bij het onderzochte voormalige Tele2-account HTTP 403; v2 werkt wel.
Een 403 op een resource verwijdert het opgeslagen token niet. Een 401 betekent
dat de sessie is afgewezen en heraanmelding nodig is.

De parser gebruikt de expliciete bundelpresentatie om ruwe datatellers te
kalibreren: in het waargenomen antwoord is 10.240.000 KB gelijk aan 10.000 MB,
ofwel 10 GB. Hij controleert dat gebruikt plus resterend overeenkomt met de
limiet. Belduur in seconden wordt omgerekend naar minuten; sms wordt geteld.
Individuele gespreksregels worden uitsluitend tijdens verwerking geaggregeerd.
Meerdere mobiele abonnementen of databundels worden nog niet ondersteund;
de integratie meldt een fout in plaats van een abonnement of overlap te raden.

## Implementatie en sessiebehoud

| Bestand | Verantwoordelijkheid |
| --- | --- |
| `app_const.py` | Publieke constanten uit de APK |
| `app_api.py` | Browserbootstrap, callbackcontrole, tokenuitwisseling, CAPI en normalisatie |
| `api.py` | Bestaande formulier- en sms-aanmelding; gedeeld met de websiteflow |
| `config_flow.py` | App-keuze, verificatie, heraanmelding en overstappen |
| `__init__.py` | Client kiezen en opgeslagen sessie herstellen |
| `coordinator.py` | Iedere 30 minuten uitlezen en appsessie bewaren |
| `sensor.py` | Genormaliseerde meetwaarden met bestaande sensor-ID's |

De appsessie staat in `app_session` van de Home Assistant-configuratie.
Browsercookies worden niet als app-credentials bewaard. Een geslaagde
app-aanmelding blijft behouden als daarna een parser- of resourcefout optreedt.
Achtergrondverversingen sturen geen nieuwe sms-verzoeken. Er is geen bevestigd
refresh-tokenendpoint; Odido bepaalt de geldigheidsduur. Maandenlange of
onbeperkte geldigheid kan niet worden gegarandeerd. Na echte tokenafwijzing
moet je via Home Assistant opnieuw aanmelden.

De websiteflow blijft beschikbaar voor bestaande configuraties. Die bewaart
accountgebonden sessie- en apparaatcookies en probeert na sessieafwijzing
hoogstens eenmaal automatisch herstel. Verplichte MFA of mislukt herstel
blokkeert verdere automatische aanmeldpogingen, ook na een herstart, tot een
succesvolle handmatige aanmelding. Dit gedrag geldt afzonderlijk van de app-route.

Home Assistant kan de ingevoerde inloggegevens in de integratieconfiguratie
bewaren. Behandel configuratie en back-ups als vertrouwelijk. Sms-codes,
callbackcodes, ruwe API-antwoorden en individuele gesprekken worden niet
opgeslagen of als sensorattributen gepubliceerd. Diagnostiek bevat alleen
veilige structurele gegevens, zonder tokens of account-URL's.

## Sensoren en beperkingen

- Databundel, bundelverbruik, resterende data en percentage.
- Afzonderlijk dataverbruik van het maandoverzicht.
- Verbruikte belminuten, met seconden als fractie van een minuut, en belbundel.
- Sms-verbruik en extra kosten deze maand.

Bundelstatus en maandoverzicht kunnen op verschillende momenten zijn bijgewerkt.
Niet-nulkosten blijven onbekend zolang de schaal van die API-waarde niet is
gevalideerd. Nulkosten worden alleen getoond wanneer de API expliciet nul meldt.

## Testen en onderzoek

```powershell
python -m pip install aiohttp voluptuous cryptography
python -m unittest discover -s tests -v
python tools/app_check.py
```

De app-route is op 5 november 2026 met een voormalig Tele2-account getest:
aanmelding met sms, bundel- en maandverbruik ophalen en tweemaal uitlezen met
een nieuw clientobject en hetzelfde bewaarde token zijn geslaagd. Langdurige
servergeldigheid volgt niet uit deze test. De regressietests gebruiken
synthetische tokens en antwoorden, inclusief headeruitwisseling, veilige
omleidingen, sessiebehoud en aggregatie zonder privégespreksgegevens.

`tools/app_check.py` vraagt wachtwoord en code verborgen. De bevestigde sessie
staat accountgebonden in `debug/app-session.json`, ook na een uitleesfout.
Een volgende poging gebruikt eerst dat token, zonder nieuwe aanmelding.
Een voltooide browserbootstrap kan tijdelijk in `debug/app-bootstrap.json`
worden bewaard om een mislukte app-overdracht opnieuw te proberen; na verkrijging
van het app-token wordt die cache verwijderd. Deze bestanden bevatten geen
wachtwoord of sms-code, maar geven wel toegang tot de sessie: houd ze privé.
Verwijder de cachebestanden als je lokaal opnieuw wilt beginnen.

Voor de oudere websiteflow bestaat `python tools/local_check.py`, met de
aparte cache `debug/session.json`. De APK, analysebestanden en `debug/` zijn
uitgesloten van Git. Commit nooit HAR-bestanden, cookies, tokens, wachtwoorden
of ingelogde HTML. Verwerk nieuwe API-inzichten in synthetische fixtures en
controleer daarna met een bestaande sessie voordat je opnieuw MFA vraagt.

## Afbeeldingen

`custom_components/odido_mobile_usage/brand/` bevat pictogrammen en woordmerken
voor lichte en donkere modus, inclusief hoge resolutie. Bron:
[officieel appicoon](https://www.odido.nl/Assets/Lfwd/Favicons/apple-touch-icon-1024x1024.png)
en [officieel woordmerk](https://www.odido.nl/Assets/Lfwd/Static/odido-logo.svg).
Odido behoudt de merkrechten. Deze integratie is niet verbonden aan Odido.
