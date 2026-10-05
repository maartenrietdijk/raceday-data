# Betrouwbaarheidscontrole sessievoorstellen — 5 oktober 2026

## Resultaat en reikwijdte

59 kalenderbestanden, 718 events en 3.758 sessies geïnventariseerd. De herkenbare sessies in alle 31 geregistreerde series zijn gecontroleerd: een sessie moet zichzelf terugvinden of een conflict opleveren, nooit stilzwijgend een andere sessie. Geen dubbele sessie-ID’s binnen events aangetroffen.

54 Python-tests en drie JavaScript-testsuites slagen. Dit zijn reproduceerbare lokale controles, geen volledige live verificatie van alle officiële websites. Bestaande bronfixtures dekken F1, F1 Academy, Super Formula en SUPER GT; de overige series hebben gedeelde matching-/tijdzonetests, maar nog geen volledige vastgelegde brontests per website. Die ontbrekende brondekking blijft een beperking.

## Aangepast

- Alleen een andere naam bij dezelfde betrouwbaar gekoppelde sessietijd levert geen tijdcorrectie meer op.
- Warm-ups, shootouts, Superpole Race, nachttrainingen, klassen en kwalificatiegroepen blijven onderscheiden.
- Een klassenummer (GT3) of racelengte (6 Hour Race) geldt niet als sessienummer.
- Dubbele kalenderkandidaten en tegenstrijdige bronregels leveren handmatige beoordeling op. Exact herhaalde bronregels maken geen extra sessie aan.
- Sessiedatums worden gekoppeld in de editorzone, ook bij een datumwisseling door tijdzoneconversie.
- Niet-bestaande en dubbelzinnige zomertijden, ongeldige tijden en tegenstrijdig UTC-bewijs worden tegengehouden. Een kolom “My Time” geldt niet als circuittijd.
- Een geannuleerde sessie wordt niet automatisch opnieuw aangemaakt; bestaande voorstellen mogen geen sessiesubtype/klasse overschrijven.
- Sinds de scan gewijzigde conceptsessies, TBC-status en dubbele IDs worden bij accepteren beschermd.
- Een gerichte scan bewaart voorstellen én bronstatussen van andere events/series.
- Alle tests draaien voortaan vóór de officiële GitHub-scan. Een bestaande test voor annuleren is bijgewerkt voor de huidige editorinterface.

## Bestaande data die beoordeling nodig hebben

Geen kalenderinhoud verwijderd of tijden aangepast.

| Bestand / event | Mogelijk dubbel |
| --- | --- |
| supercars_2026.json / supercars-2026-04 | Practice 1 en Practice 1 (copy): 18 april 2026, 11:00 |
| supercars_2026.json / supercars-2026-11 | Practice 7 en Warm Up: 11 oktober 2026, 08:30 |

De Supercars-scanner nummert een warm-up voortaan niet meer mee als gewone training. Dat verwijdert een eerder aangemaakte dubbele kalenderregel niet automatisch.

## Series met scanner

sf, supergt, btcc, f1, wsbk, motogp, moto2, moto3, wrc, erc, f2, f3, f1academy, formulae, supercars, nascar, nascar_oreilly, nascar_trucks, nascareuro, gtwce, gtwca_am, gtwca_asia, gtwca_aus, british_gt, dtm, wec, alms, lemanscup, indycar, indynxt, imsa.

## Series zonder ingestelde bronscanner

24hseries, bsb, elms, ewc, fd, igtc, nls, porsche_supercup, rx, tcr.

Hun kalenderbestanden zijn geïnventariseerd, maar de scanner haalt voor deze series geen officiële tijden op. Voor volledige automatische dekking zijn afzonderlijke bronlezers en bronfixtures nodig.

## Herhalen

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/test_editor_proposals.cjs
node tests/test_editor_reliability.cjs
node tests/test_editor_cancelled.cjs
```

Na upload naar GitHub: vernieuw de editor en voer opnieuw een gerichte broncontrole uit voor de gewenste events. De wijzigingen zijn lokaal aangebracht, niet gepubliceerd.
