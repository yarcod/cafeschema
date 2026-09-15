# Påminnelser om sysslor

Skickar automatiskt e-postpåminnelser till de som står på tur enligt schemat.

## Hur det fungerar

Varje morgon kör ett schemalagt jobb på GitHub Actions:

1. Schemat läses (Excel-fil, Google Kalkylark, eller duty-swap-webbappens
   `/api/schedule`)
2. Jobbet räknar ut vilka tillfällen som är på gång
3. Ett mejl skickas till hela gruppen som står på tur

Två sorters mejl kan skickas per tillfälle:

- **Förhandsbesked** (t.ex. 7 dagar innan) — vilka som står på tur, och vem
  man hämtar nycklar hos om förra gruppen var veckan innan.
- **Påminnelse** (1 dag innan) — kort påminnelse om att det är er tur i morgon.

Hur många dagar innan styrs per rad i schemat (kolumnen `dagar_innan`, t.ex.
`7,1`). Är den tom används `default_lead_days` från `config.yaml`.

## Schemats format

| Kolumn | Krävs | Beskrivning |
|---|---|---|
| `datum` | ja | ÅÅÅÅ-MM-DD |
| `epost1`, `epost2`, … | ja | En kolumn per person, eller en `epost`-kolumn med en rad per person |
| `namn1`, `namn2`, … | nej | Namn som visas i mejlet |
| `dagar_innan` | nej | T.ex. `7,1`. Max två värden. |
| `nyckelplats` | nej | Var nyckeln finns efter passet |

Både "brett" format (en rad per datum, flera e-postkolumner) och "högt" format
(en rad per person, samma datum upprepat) fungerar.

## Köra manuellt

```bash
python -m duty_mailer --dry-run                  # visa, skicka inget
python -m duty_mailer --date 2026-09-18          # kör som ett visst datum
python -m duty_mailer --date 2026-09-18 --dry-run  # vanligaste kombinationen
python -m duty_mailer                            # skarpt läge
```

`--date` tillsammans med `--dry-run` är hur man kontrollerar vad som kommer att
skickas längre fram, utan att skicka något.

Man kan också köra jobbet från GitHub: **Actions → Dagliga påminnelser → Run
workflow** (torrkörning är förvald).

## Konfiguration

Kopiera `config.example.yaml` till `config.yaml` och fyll i. Filen innehåller
inga lösenord.

`schedule.source` kan vara:

- `xlsx` — läser en Excel-fil (`schedule.path`)
- `google_csv` — läser ett publicerat Google Kalkylark som CSV (`schedule.url`)
- `web_api` — läser duty-swap-webbappens `/api/schedule` (`schedule.url`),
  autentiserat med miljövariabeln `SCHEDULE_API_KEY`. Svaret säger också vilken
  instruktion passet har (`document_url`), och mailet länkar dit i stället för
  till `documents_link`, som då bara är reserv.

## Hemligheter

| Secret | Beskrivning |
|---|---|
| `SMTP_PASSWORD` | App-lösenord för e-postkontot |
| `SCHEDULE_API_KEY` | Krävs endast när `schedule.source: web_api`. Måste matcha webbappens `SCHEDULE_API_KEY`. |

Lagras som GitHub Secret (Settings → Secrets and variables → Actions). För lokal
körning, se `.env.example`.

## Att känna till

- **Inget skickas i efterhand.** Jobbet håller ingen historik; det jämför bara
  dagens datum mot schemat. Om en körning missas skickas det mejlet aldrig.
- **GitHub stänger av schemalagda jobb efter 60 dagars inaktivitet** i
  repot. En commit eller en manuell körning återaktiverar dem.
- **Schemat läses bara, aldrig skrivs till.**
