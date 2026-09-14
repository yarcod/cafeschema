# Caféschema — webbapp för lagets vaktpass

Föräldrar loggar in utan lösenord, ser sina egna pass, ser hela lagets schema och
kan föreslå byten med varandra. Appen är också den källa som påminnelsemailen
(`duty_mailer`) läser sitt schema från.

**URL:** `https://duty-swap-webapp.fly.dev`

## Hur det fungerar

**För föräldern**

1. Skriv in din e-postadress på inloggningssidan → du får en inloggningslänk i mailen.
2. **Mina pass** — dina kommande pass, vem du är inbokad med, och knappen *Föreslå byte*.
3. **Hela laget** — kalender och lista över månadens alla pass.
4. **Byten** — förslag som väntar på dig, och dina egna skickade förslag.
5. **⚙ Inställningar** — stäng av e-post, eller växla mellan att visa föräldrarnas
   och spelarnas namn.

Sidan visar aldrig någons e-postadress. All kontakt går via appens egna mail.

**Säkerhet**

- Inloggningslänken är personlig, engångs och giltig i 20 minuter.
- Inloggningsmail skickas **bara** till adresser som finns i schemat, och sidan
  svarar exakt likadant oavsett om adressen finns eller inte — annars skulle
  vem som helst kunna gissa sig till vilka som är med i laget.
- Inloggningslänkar skickas alltid, även för den som stängt av e-post — utan dem
  går det inte att logga in.

## Dokument

Instruktioner och annat föräldrar behöver inför ett pass ligger under
**Dokument** i appen (kräver inloggning — de är inte publika). Påminnelsemailet
länkar dit i stället för att bifoga filer, så det finns ett ställe att hålla
uppdaterat.

```bash
just docs-push ~/Downloads/Kiosk_instruktioner.pdf   # laddar upp
just docs-list                                       # visar vad som ligger där
```

Filerna ligger på Fly-volymen under `/data/dokument`, så en ny fil behöver ingen
driftsättning. Tillåtna filtyper: `pdf`, `png`, `jpg`, `txt`, `md`. Filnamnet blir
rubriken i listan — `Kiosk_instruktioner.pdf` visas som "Kiosk instruktioner".

## Påminnelsemail

`duty_mailer` skickar påminnelsen inför ett pass. Mailet går ut både som HTML (i
samma formspråk som sajten) och som ren text, och innehåller vilka som står på
passet, nyckelinfo från förra gången, samt knappar till schemat och till
**Dokument**.

Länkarna styrs från `config.yaml`:

```yaml
schedule:
  schedule_link: "https://innebandy.edholm.cc/"
  documents_link: "https://innebandy.edholm.cc/dokument"
```

Den som stängt av e-post under Inställningar hoppas över.

> **OBS:** Påminnelsemailen skickas i dag inte av någonting. Workflowen
> `.github/workflows/daily-reminders.yml` finns i repot men repot har ingen
> git-remote, så det finns inget GitHub som kör den. Se *Att göra* nedan.

## Att göra

- **Ingen skickar påminnelsemailen.** Antingen: pusha repot till GitHub och sätt
  secrets (`SCHEDULE_API_KEY`, `SMTP_PASSWORD`), så kör `daily-reminders.yml`
  varje morgon. Eller: kör `duty_mailer` från en schemalagd Fly-maskin i stället,
  och slipp GitHub helt.

## Uppdatera schemat

Schemat kommer från tränarens Excel-fil och byggs om till en enklare fil som
appen importerar. Två steg:

```bash
just schedule-build data/Bemanningsschema_26_27_Hans_260913_v2.xlsx   # bygger data/seed_sasong_26_27.xlsx
just schedule-push                                                     # importerar till den driftsatta appen
```

`schedule-build` varnar om någon spelare i schemat saknar kontaktuppgifter.
Sådana pass importeras ändå, men som **Ledigt** — ingen tilldelas dem.

`schedule-push` **ersätter alla pass**. Föräldrarna själva behålls (de matchas på
e-post), så ingen blir utloggad och ingens inställningar försvinner.

### Tränarens fil

Bladet **`Säsong 26-27`**, en rad per pass:

| Aktivitet | Datum | Tid | Person 1 | Person 2 | Person 3 |
|---|---|---|---|---|---|
| Bästkustcupen | 2026-09-25 | 16:30-22:45 | Alva Exempel | Moa Exempel | |
| Arena värdskap höst | 2026-09-18 | Fre 18-21 | Nils Exempel | Signe Exempel | |
| Arena värdskap vinter | Datum ej satt (VT27) | Fre 18-21 | Alva Exempel | Bo Exempel | |

- **Aktivitet** — vad som helst. Nya aktiviteter behöver inte läggas upp någonstans;
  de får automatiskt en egen färg utifrån namnet, samma färg för alla.
- **Datum** — `ÅÅÅÅ-MM-DD`. Är datumet inte spikat ännu duger vilken text som helst
  (t.ex. `Datum ej satt (VT27)`) — passet visas då under **Datum ej satt** i stället
  för i kalendern.
- **Tid** — `18:00-21:00`, `18-21` och `Fre 18-21` funkar alla. Veckodagen sparas
  som en notering på pass utan datum, så man vet vilken dag det brukar bli.
- **Person 1–3** — spelarnas namn. Ett pass blir ett eget "pass" per person, så att
  var och en kan byta sitt eget.

### Föräldrar och barn

`data/f15_parent_mailing_list.csv` kopplar ihop spelare med förälder och e-post:

```csv
parent_name,parent_email,children_on_team
Kim Exempel,kim@exempel.se,Alva Exempel
```

> Filerna under `data/` innehåller riktiga namn och adresser och ligger utanför
> repot (`.gitignore`). Detsamma gäller `data/name_fixes.json`, som håller reda
> på spelare som stavas olika i de två filerna.

- **Lägga till en familj** — lägg till en rad. Nästa `schedule-build` + `schedule-push`
  plockar upp den.
- **Två föräldrar i samma familj** — lägg båda på varsin rad. Den som står först får
  passen; den andra kan alltid vidarebefordra inloggningslänken.
- **Namnet stavas olika i de två filerna** — lägg till stavningen i
  `data/name_fixes.json` (`{"Namn i schemat": "Namn i kontaktlistan"}`).
  Annars blir passet *Ledigt*.

## Testa bytesflödet

En slask-förälder och ett testpass kan läggas in för att prova hela kedjan —
föreslå byte, mail, acceptera. Använd en adress du själv läser mail på, så att
inloggningslänken och bytesmailen går att klicka på:

```bash
just test-fixture-add min.adress@exempel.se      # "TEST Testförälder" + passet "TEST – bytesprov"
just test-fixture-remove min.adress@exempel.se   # tar bort föräldern, passet och eventuella byten
```

Testpasset syns för alla i **Hela laget**, så ta bort det när det är avklarat.
Det försvinner också vid nästa `schedule-push`, eftersom den ersätter alla pass.

## Utveckling

```bash
just web-install     # skapar web/.venv
just web-test        # kör testsviten
just web-run         # startar appen på http://localhost:8080
just web-admin       # importsidan, endast localhost, på :8081
just web-seed        # importerar en byggd seed-fil till den lokalt körande appen
```

## Drift

- **Fly.io** kör appen i en container i Stockholm (arn). Maskinen sover när ingen
  använder sidan och vaknar vid besök.
- Databasen (SQLite) ligger på en persistent volym under `/data/duty.db`.
- Alla hemligheter ligger i 1Password-posten **`duty-swap-webapp`** (valvet Private)
  och sätts som Fly-secrets därifrån — de finns aldrig i koden.

```bash
just web-deploy      # driftsätter
just web-logs        # loggar
just web-ssh         # skal på maskinen
```

| Secret | Beskrivning |
|--------|-------------|
| `SECRET_KEY` | Signerar sessionscookies och inloggningslänkar |
| `SCHEDULE_API_KEY` | Nyckel för `/api/schedule` (används av `duty_mailer`) |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.fastmail.com` / `587` |
| `SMTP_USER` / `SMTP_PASSWORD` | Fastmail-konto och app-lösenord |
| `FROM_ADDRESS` | Avsändaradress, måste vara ett alias i Fastmail |
| `PUBLIC_HOSTS` | Kommaseparerade domäner appen svarar på. Skyddar mot att en förfalskad `Host`-header styr var inloggningslänken pekar |

> **OBS:** Byt aldrig `SECRET_KEY` i onödan — alla blir utloggade och måste begära
> en ny inloggningslänk.

## Om tjänsten behöver sättas upp från noll

1. **Installera Fly CLI och logga in:**

   ```bash
   curl -L https://fly.io/install.sh | sh
   fly auth login
   ```

2. **Skapa app och volym:**

   ```bash
   fly apps create duty-swap-webapp
   fly volumes create data --region arn --size 1 --app duty-swap-webapp
   ```

3. **Sätt hemligheterna från 1Password** (värdena passerar aldrig terminalen):

   ```bash
   fly secrets set -a duty-swap-webapp \
     SECRET_KEY="$(op read 'op://Private/duty-swap-webapp/SECRET_KEY')" \
     SCHEDULE_API_KEY="$(op read 'op://Private/duty-swap-webapp/SCHEDULE_API_KEY')" \
     SMTP_HOST="$(op read 'op://Private/duty-swap-webapp/SMTP_HOST')" \
     SMTP_PORT="$(op read 'op://Private/duty-swap-webapp/SMTP_PORT')" \
     SMTP_USER="$(op read 'op://Private/duty-swap-webapp/SMTP_USER')" \
     SMTP_PASSWORD="$(op read 'op://Private/duty-swap-webapp/SMTP_PASSWORD')" \
     FROM_ADDRESS="$(op read 'op://Private/duty-swap-webapp/FROM_ADDRESS')"
   ```

4. **Driftsätt:** `just web-deploy`

   Får appen ingen IP automatiskt:

   ```bash
   fly ips allocate-v6 -a duty-swap-webapp
   fly ips allocate-v4 -a duty-swap-webapp --shared
   ```

5. **Skapa laget** (engångsåtgärd — inget annat skapar en `Team`-rad):

   ```bash
   fly ssh console -a duty-swap-webapp -C "python -c \"
   from duty_web.db import init_db, make_engine, make_session_factory
   from duty_web.models import Team
   e = make_engine('/data/duty.db'); init_db(e)
   s = make_session_factory(e)()
   s.add(Team(name='F15', venue='Wallenstam arena')); s.commit()\""
   ```

6. **Fyll på schemat:** `just schedule-build <tränarens fil>` och `just schedule-push`

7. **Valfritt — egen domän:**

   ```bash
   fly certs create innebandy.edholm.cc -a duty-swap-webapp
   fly certs show innebandy.edholm.cc -a duty-swap-webapp   # visar vilka DNS-poster som krävs
   ```

   DNS (utan CDN framför — enklast):

   ```
   A    innebandy → 66.241.124.136
   AAAA innebandy → 2a09:8280:1::18d:2a1:0
   ```

   **Med Cloudflare-proxy påslagen (orange moln)** ser Fly bara Cloudflares
   IP-adresser och kan därför aldrig validera domänen — certifikatet utfärdas
   inte, och Cloudflare svarar `520`. Antingen slå av proxyn för posten (grått
   moln), eller lägg till ägarbeviset och sätt rätt SSL-läge:

   ```
   TXT _fly-ownership.innebandy → app-5yrj6zn
   ```

   plus SSL/TLS-läget **Full (strict)** i Cloudflare. Flexible fungerar inte —
   Fly tvingar HTTPS, vilket ger en oändlig omdirigering.

   Varje domän appen svarar på måste också stå i `PUBLIC_HOSTS`, annars svarar
   den `400`. Inget annat i koden behöver ändras: CSRF-skyddet jämför mot den
   domän anropet faktiskt kom till, och inloggningslänkarna byggs från samma
   domän som föräldern använde.

   Sessionscookies är knutna till domännamnet, så den som växlar mellan de två
   adresserna får logga in en gång per adress.

## Felsökning

| Fel | Orsak | Lösning |
|-----|-------|---------|
| Ett pass står som **Ledigt** | Spelarens namn matchar ingen rad i kontaktlistan | Lägg till familjen i CSV:n, eller stavningen i `NAME_FIXES` |
| **Mina pass** är tomt | Alla pass ligger bakåt i tiden, eller saknar datum | Kontrollera `Datum`-kolumnen i tränarens fil |
| Ingen inloggningslänk kommer fram | Adressen finns inte i schemat (sidan säger inte till — med flit) | Kontrollera e-postadressen i kontaktlistan |
| `Ingen grupp med team_id=1 finns` | `Team`-raden saknas | Se steg 5 ovan |
| `SMTPAuthenticationError` | Fel `SMTP_PASSWORD` | Uppdatera Fly-secret från 1Password |
| `SMTPDataError: 551` | Ej behörig avsändaradress | `FROM_ADDRESS` måste vara ett alias i Fastmail |
| Cloudflare `520` på egen domän | Fly har inget certifikat för domänen, för proxyn döljer DNS:en | Slå av proxyn, eller lägg in `_fly-ownership`-TXT och sätt Full (strict) |
| `400 Okänd värd` | Domänen saknas i `PUBLIC_HOSTS` | Lägg till den och driftsätt om |
