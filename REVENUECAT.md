# RevenueCat-statistieken voor RaceDay Studio

Het zijmenu van `raceday-editor.html` bevat **Statistieken**. Na het instellen van de koppeling haalt de editor bij iedere paginalaad de gegevens op. Je kunt ook **Verversen** gebruiken. De kerncijfers zijn Active Trials, Active Subscriptions, MRR, Revenue en New Customers. De omzetperiode is 7, 30 of 90 dagen; kerncijfers houden hun eigen periode uit RevenueCat. Er is geen automatische polling. Een verversing doet maximaal drie RevenueCat-verzoeken; tussen verversingen zit minimaal 15 seconden.

GitHub Pages serveert alleen statische bestanden. Daarom draait de RevenueCat-koppeling in een aparte Cloudflare Worker. De geheime RevenueCat-sleutel komt nooit in de HTML, browseropslag, repository of API-antwoorden terecht. De Worker beschermt de statistieken met een afzonderlijke toegangscode en accepteert browserverzoeken alleen vanaf `https://maartenrietdijk.github.io`.

## Jouw gepubliceerde koppeling

De backend is gepubliceerd op `https://raceday-statistics.gentle-meadow-2bdd.workers.dev` en met echte RevenueCat-antwoorden gecontroleerd. De vijf kerncijfers en de dagelijkse omzet zijn beschikbaar. De backend-URL is alvast ingevuld in `statistics.js`.

Upload de gewijzigde `raceday-editor.html`, `statistics.js`, `statistics.css` en dit document zelf naar GitHub. Open daarna **Statistieken → Koppeling instellen**, vul de waarde van `DASHBOARD_TOKEN` uit je lokale privébestand `/tmp/raceday-revenuecat-secrets.json` in en klik **Koppelen en ophalen**. Het privébestand blijft buiten de GitHub-uploadmap.

De instellingen en RevenueCat-sleutel zijn al als secrets in Cloudflare opgeslagen. Onderstaande eenmalige installatie is alleen nodig als je de koppeling opnieuw wilt opzetten.

## Eenmalig instellen

1. Maak in RevenueCat een **geheime API v2-sleutel** met uitsluitend de leesrechten `charts_metrics:overview:read` en `charts_metrics:charts:read`. Noteer ook het RevenueCat-project-ID. Gebruik geen publieke iOS SDK-sleutel.
2. Maak in Cloudflare een Worker, bijvoorbeeld `raceday-statistics`. Neem de inhoud van `backend/revenuecat-worker.mjs` over in de code-editor. Stel de volgende variabelen in bij de Worker:

   | Naam | Type | Waarde |
   | --- | --- | --- |
   | `ALLOWED_ORIGIN` | Tekst | `https://maartenrietdijk.github.io` |
   | `REVENUECAT_PROJECT_ID` | Secret | Het project-ID uit RevenueCat |
   | `REVENUECAT_SECRET_KEY` | Secret | De geheime RevenueCat API v2-sleutel |
   | `DASHBOARD_TOKEN` | Secret | Een zelf gegenereerde, willekeurige toegangscode van minimaal 32 tekens |

   De toegangscode geeft alleen toegang tot deze statistieken-backend. Gebruik hiervoor een nieuwe code, niet je RevenueCat-sleutel of GitHub-token.

3. Publiceer de Worker en kopieer zijn HTTPS-URL. Publiceer de gewijzigde editor samen met `statistics.js`, `statistics.css` en deze instructies op GitHub Pages.
4. Open **Statistieken → Koppeling instellen**. Vul de Worker-URL en de waarde van `DASHBOARD_TOKEN` in en klik **Koppelen en ophalen**.

De URL, periode en valuta worden op dit apparaat onthouden. De toegangscode wordt alleen in `sessionStorage` opgeslagen: verversen binnen hetzelfde browsertabblad haalt de cijfers automatisch opnieuw op; na het sluiten van het tabblad voer je de code opnieuw in. De statistieken zelf worden niet opgeslagen in de repository of browseropslag.

### Deployen met Wrangler

Als je Node.js en een Cloudflare-account hebt, kun je de Worker ook vanuit de projectmap deployen:

```sh
cd backend
npx wrangler login
npx wrangler secret put REVENUECAT_SECRET_KEY
npx wrangler secret put REVENUECAT_PROJECT_ID
npx wrangler secret put DASHBOARD_TOKEN
npx wrangler deploy
```

De commando's vragen de geheime waarden interactief; zet ze niet in `wrangler.toml`. `backend/wrangler.toml` bevat de juiste dashboard-origin. Bij een ander dashboarddomein moet `ALLOWED_ORIGIN` exact die origin bevatten, zonder pad of afsluitende slash.

## Gedrag bij fouten

- Bij een onbereikbare backend of mislukte verversing blijven eerder opgehaalde cijfers zichtbaar, met een foutmelding.
- Als alleen de kerncijfers of de omzetgrafiek beschikbaar zijn, toont de editor het beschikbare gedeelte en een melding voor het ontbrekende gedeelte.
- Bij een RevenueCat-limiet wacht de editor minimaal een minuut voordat een nieuw verzoek mogelijk is.
- De dagbedragen zijn ook als tabel beschikbaar. Ontbrekende bedragen worden niet als nul weergegeven; onvolledige dagen worden gemarkeerd.
- Het opgehaalde tijdstip is de tijd van de verversing. RevenueCat kan de onderliggende cijfers met vertraging bijwerken; de bron-tijdstempel staat waar beschikbaar bij het kerncijfer (hover op de periode).

API-documentatie: [RevenueCat Charts & Metrics](https://www.revenuecat.com/docs/api-v2/charts-and-metrics). Backend: [Cloudflare Workers](https://developers.cloudflare.com/workers/).

## Controleren

```sh
node tests/test_statistics.cjs
node --test tests/test_revenuecat_worker.mjs
```

De tests gebruiken nagebootste antwoorden; ze doen geen verzoeken met echte sleutels. Voor een echte end-to-end-controle moeten de Worker en de RevenueCat-secrets zijn ingesteld.
