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

`just docs-push fil.pdf` delas av hela klubben, medan `just docs-push fil.pdf p13`
bara gäller P13. Ett lags egen fil går före en delad fil med samma namn.

Flyttar man en instruktion mellan ett lags mapp och den gemensamma mappen
slutar länkarna i redan skickade påminnelsemail att fungera. Länken pekar på den
gamla platsen, och mailen går inte att ändra i efterhand.

Filerna ligger på Fly-volymen under `/data/dokument`, så en ny fil behöver ingen
driftsättning. Tillåtna filtyper: `pdf`, `png`, `jpg`, `txt`, `md`. Filnamnet blir
rubriken i listan — `Kiosk_instruktioner.pdf` visas som "Kiosk instruktioner".

## Admin

Ansvariga i ett lag och klubbadmin ser en **✎** bredvid ⚙. Den leder till
arbetsbänken på `/admin/<lag>/`:

- **Pass**: lagets pass plats för plats, och där man börjar. **+ Lägg till pass**
  för en cup eller ett enstaka pass: välj dagar (eller *Den här helgen*), skriv
  aktiviteten och upp till fyra rader med tid, station och antal platser.
  Bocka i lediga platser och tryck **Bemanna valda platser**: den med minst
  pass den här säsongen går först, syskon hamnar gärna på samma pass, och
  slumpen avgör bara mellan lika. Ledarnas barn får aldrig pass. **Ändra** och
  **Ta bort** gäller ett pass i taget. Flyttar du ett pass med datum till en
  annan dag, tid eller station får familjen ett mejl; granskningen säger vilka. **Importera Excel** tar coachens fil
  som den är (se nedan).
- **Lag & ansvariga**: laglistan från 360Player, ledarna och vilka som är
  ansvariga. Varje ansvarig kan göra en annan förälder i laget till ansvarig.
  Bara klubbadmin kan ta bort lagets sista ansvariga.
- **Platser**: platser, stationer och vilken instruktion varje station
  länkar. Delas av hela klubben. En station väljs alltid ur listan, så att
  "cafe" och "Café" aldrig blir två ställen. Bara delade instruktioner, de som
  ligger direkt i `DOCUMENTS_DIR`, kan kopplas till en station. En fil med
  samma namn i lagets mapp går före den delade för just det laget.

Allt som ändrar något visas först i **Granska** (+ nytt, ~ ändrat, − borttaget).
Ingenting händer förrän du trycker **Genomför**.

### Laglistan från 360Player

1. Öppna laget i 360Player → **Truppen** → **Exportera** (en `…-members.csv`).
2. ✎ → **Lag & ansvariga** → **Uppdatera från 360Player** → välj filen → Granska → Genomför.

Spelare läses bara från spelarrader. Ledare känns igen automatiskt: i exporten
står en ledare en gång till, utan barn och utan föräldrar, med samma e-post som
när hen står som förälder. Ledarnas barn markeras som undantagna och får
aldrig pass när du bemannar. Står de i coachens Excel varnar granskningen. Är
någon felaktigt ledare rättas det i 360Player, inte här. Den som redan finns i ett annat lag
kopplas till det här laget men byter aldrig namn. Ingen tas bort av en import:
den som saknas i filen listas som en varning. Telefonnummer läses aldrig in.
Filen läses i minnet och sparas inte; uppladdningar är begränsade till 2 MB per
anrop och 1 MB per fil.

### Coachens Excel

✎ → **Pass** → **Importera Excel** → välj coachens bemanningsschema (.xlsx) →
Granska → Genomför. Filen läses i minnet och sparas inte.

Importen är en ändringslista, inte en ersättning:

- Ett pass som är oförändrat behåller sitt id och sina byten, också byten som
  redan är gjorda: importen jämför med namnet som stod i schemat, inte med vem
  som har passet nu.
- Byter filen namn på en plats ställs väntande byten på den in, och båda
  familjerna får ett mejl.
- Ett pass som får sitt datum behåller sina byten.
- Har du ändrat datum, tid eller station på ett importerat pass under Pass
  sätter nästa import tillbaka det filen säger. Passet behåller sitt id och
  sin spelare, men väntande byten ställs in. Familjen får ett mejl om att
  passet ändrats.
- Ett pass som inte längre finns i filen tas bort.
- Bara aktiviteter som står i filen, och bara säsongerna filen gäller, rörs.
  Pass före i dag och pass ni lagt till under Pass ändras aldrig.

Står ett namn som inte finns i laglistan frågar granskningen *Vem är 'X'?*;
utan svar blir passet ledigt med namnet kvar. Har passet redan en spelare
behåller det sin spelare tills du svarar. Har en aktivitet ingen känd plats
frågar den *Var hålls 'X'?*. Svaren sparas när importen genomförs med
ändringar, så nästa import av samma fil frågar inget. Ett sparat svar visas
förvalt och kan ändras där. En plats som filen namnger men som saknas under
Platser stoppar importen, liksom ett datum som inte är ett datum (skriv *Datum
ej satt* för pass utan datum). Det här ersätter `data/name_fixes.json`.

### För agenter

Varje knapp har en tvilling i API:t. Nycklar skapas bara i en inloggad
webbläsare, under ⚙ → **Inställningar** → **API-nyckel för agent**. Den visas
en gång, så spara den i 1Password. Nyckeln har samma behörighet som du och
skickas bara som header. En nyckel i URL, formulär eller JSON avvisas med `400`
och maskas i åtkomstloggen:

```bash
curl -H "Authorization: Bearer $NYCKEL" https://innebandy.edholm.cc/admin/api
```

`GET /admin/api` listar dina lag och alla åtgärder med fält. Varje åtgärd
körs två gånger: först `"dry_run": true`, som ger exakt Granska-listan och en
`plan_token`. Sedan `"dry_run": false` med samma `plan_token`, som genomför
just det som granskades. Har något ändrats emellan blir svaret `409` och du
granskar igen. Logga aldrig `plan_token` (agentägare): den bär den planerade
indatan.

```bash
curl -X POST -H "Authorization: Bearer $NYCKEL" -F team=f15 -F file=@F15-members.csv \
  https://innebandy.edholm.cc/admin/api/roster.import
```

`just roster-push <slug> <fil>` och `just schedule-push <slug> <fil>` gör
samma två anrop från terminalen: de visar Granska och frågar innan de
genomför. Nyckeln läses ur 1Password vid anropet (`ADMIN_KEY_REF`, standard
`op://Private/pjss2h7w7ec7amvsfbni4cdcvu/credential`) och skrivs aldrig ut
eller till disk. En import med en obesvarad fråga genomförs aldrig från
terminalen: svara i webben.

Läsning: `GET /admin/api/slots?team=f15` (valfritt `from`/`to`),
`GET /admin/api/roster?team=f15`, `GET /admin/api/venues`, `GET /admin/api/documents`.
Bemanna kräver inget `seed`: utelämnat väljs ett, och `plan_token` bär det, så
det som genomförs är det som granskades.

### Migrering

`entrypoint.sh` kör `python -m duty_web.migrate` vid varje start, före appen.
Den är idempotent:

- Stationer och instruktionskopplingar läggs in från `duties.py` bara första
  gången, när tabellen `stations` är tom. Senare körningar kopplar bara
  passen som saknar station till redan befintliga stationer.
- `CLUB_ADMINS` fungerar bara för adresser som redan finns bland personerna och
  bara när ingen är klubbadmin. Hoppades en adress över: importera laglistan
  och starta om.

Repetera mot en kopia innan en driftsättning som ändrar den:

```bash
just db-pull /tmp/duty-copy.db
cd web && uv run python -m duty_web.migrate /tmp/duty-copy.db
cd web && uv run python ../scripts/verify_admin_migration.py /tmp/duty-copy.db
```

### Vilken instruktion hör till vilket pass

Både **Att göra på passet** och påminnelsemailet länkar till den enda instruktion
passet behöver, inte till listan. Kopplingen sås
från `web/src/duty_web/duties.py` första gången migreringen körs, och ändras
sedan under ✎ → Platser:

| Pass | Var | Vad | Instruktion |
|---|---|---|---|
| Bästkustcupen | Mölnlycke idrottshall | Café | `Cafeteria_Instruktion_MIH.pdf` |
| Cafépass | Wallenstam arena | Café Arena B, nedre plan | `Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf` |
| Cafépass Arena A | Wallenstam arena | Café Arena A, övre plan | `Cafeteria_Instruktion_W_Arena_A_övre_plan.pdf` |

Ett pass utan koppling — eller vars fil inte är uppladdad — länkar till listan i
stället, så knappen aldrig leder till en 404. Tabellen i `duties.py` sås in vid första
migreringen och gäller sedan bara som reserv för pass och aktiviteter utan
station. Nya platser och instruktioner läggs in under Platser, och
`just duties-push` sätter plats och café på de pass som redan är importerade.

## Påminnelsemail

`duty_mailer` skickar påminnelsen inför ett pass. Mailet går ut både som HTML (i
samma formspråk som sajten) och som ren text, och innehåller vilka som står på
passet, var det är, nyckelinfo från förra gången, samt knappar till schemat och
till **Dokument**.

Ett mail per pass, inte per dag — samma gruppering som **Hela laget** använder
(`_shifts()` i `schedule_routes.py`: datum, tid, syssla och arena). En lördag
med tre pass ger tre mail, och inget av dem nämner de andra passens föräldrar.
`/api/schedule` skickar tiden och platsen som avgör det, så en ändring i hur
appen grupperar pass måste följas av samma ändring i `Shift` i
`src/duty_mailer/sources/web_api.py`.

Knappen **Läs instruktionerna** går till passets egen instruktion när appen vet
vilken det är (`/api/schedule` skickar med `document_url`); annars till listan.

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

Schemat kommer från tränarens Excel-fil, som importeras som den är. Två vägar:
✎ → Pass → Importera Excel (se *Coachens Excel*), eller från terminalen:

```bash
just roster-push f15 data/F15-members.csv   # 360Players medlemsexport (.csv), ingen standardfil
just schedule-push f15 data/Bemanningsschema_26_27.xlsx   # tränarens .xlsx
```

Båda visar Granska och frågar innan de genomför. Kör `roster-push` före
`schedule-push`: importen kräver att spelarna finns. Ingen av dem har någon
standardfil, så att ett annat lags körning aldrig importerar F15:s fil.

Importen är en ändringslista, inte en ersättning (se *Coachens Excel*): pass
som inte längre finns i filen tas bort för det laget, andra lags pass rörs inte.
Föräldrarna själva behålls, så ingen blir utloggad och ingens inställningar
försvinner.

### Tränarens fil

Bladet **`Säsong 26-27`**, en rad per pass:

| Aktivitet | Datum | Tid | Person 1 | Person 2 | Person 3 |
|---|---|---|---|---|---|
| Bästkustcupen | 2026-09-25 | 16:30-22:45 | Alva Exempel | Moa Exempel | |
| Arena värdskap höst | 2026-09-18 | Fre 18-21 | Nils Exempel | Signe Exempel | |
| Arena värdskap vinter | Datum ej satt (VT27) | Fre 18-21 | Alva Exempel | Bo Exempel | |

- **Aktivitet** — vad som helst. Nya aktiviteter behöver inte läggas upp någonstans;
  de får automatiskt en egen färg utifrån namnet, samma färg för alla.
  Båda "Arena värdskap"-raderna heter **Cafépass** i appen — importen
  döper om dem, och fyller samtidigt i arena och café (se *Vilken
  instruktion hör till vilket pass*).
- **Datum** — `ÅÅÅÅ-MM-DD`. Är datumet inte spikat ännu duger vilken text som helst
  (t.ex. `Datum ej satt (VT27)`) — passet visas då under **Datum ej satt** i stället
  för i kalendern.
- **Tid** — `18:00-21:00`, `18-21` och `Fre 18-21` funkar alla. Veckodagen sparas
  som en notering på pass utan datum, så man vet vilken dag det brukar bli.
- **Person 1–3** — spelarnas namn. Ett pass blir ett eget "pass" per person, så att
  var och en kan byta sitt eget.

### Föräldrar och barn

Laglistan kommer från 360Players medlemsexport (.csv), som `roster-push` eller
✎ → Lag & ansvariga importerar (se *Laglistan från 360Player*). Den kopplar ihop spelare med föräldrar och e-post.

> Exportfilerna under `data/` innehåller riktiga namn och adresser och ligger
> utanför repot (`.gitignore`).

- **Lägga till en familj** — lägg till barnet och föräldern i 360Player och kör
  `just roster-push <slug> <fil>` med en ny medlemsexport.
- **Två föräldrar i samma familj** — den som står först får passen; den andra
  kan alltid vidarebefordra inloggningslänken.
- **Namnet stavas olika i schemat och laglistan** — granskningen frågar *Vem är
  'X'?* och sparar svaret. Utan svar blir passet *Ledigt*.

## Lägga till ett lag

```bash
just team-add p13 P13 "Mölnlycke idrottshall"                      # skapar laget (slug, namn, hall)
just roster-push p13 <fil>                                         # spelare och föräldrar; 360Players medlemsexport (.csv)
just schedule-push p13 <fil>                                       # tränarens .xlsx
just docs-push <fil> p13                                           # lagets egna instruktioner (utan lag: delade av klubben)
just docs-list                                                     # kontrollera vad som ligger var
```

Kör `roster-push` före `schedule-push`: importen kräver att spelarna finns.

## Testa bytesflödet

En slask-förälder och ett testpass kan läggas in för att prova hela kedjan —
föreslå byte, mail, acceptera. Använd en adress du själv läser mail på, så att
inloggningslänken och bytesmailen går att klicka på:

```bash
just test-fixture-add min.adress@exempel.se      # "TEST Testförälder" + passet "TEST – bytesprov"
just test-fixture-remove min.adress@exempel.se   # tar bort föräldern, passet och eventuella byten
```

Testpasset syns för alla i **Hela laget**, så ta bort det när det är avklarat.
En import rör det inte (bara aktiviteter som står i filen ändras); ta bort det med `just test-fixture-remove`.

## Utveckling

```bash
just web-install     # skapar web/.venv
just web-test        # kör testsviten
just web-run         # startar appen på http://localhost:8080
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
| `CLUB_ADMINS` | Kommaseparerade e-postadresser som blir klubbadmin, **bara** så länge ingen är det och adressen redan finns bland personerna. Därefter avgör databasen. |

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

5. **Skapa laget** (en gång per lag — inget annat skapar en `Team`-rad):

   ```bash
   just team-add f15 F15
   ```

   Slugen (`f15`) är lagets korta namn i adresser, dokumentmappen och i
   `just roster-push <slug> <fil>` / `just schedule-push <slug> <fil>`. Bara a–z, 0–9
   och bindestreck.

6. **Fyll på schemat:** `just roster-push f15 <medlemsexport>` och sedan `just schedule-push f15 <tränarens fil>`

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
