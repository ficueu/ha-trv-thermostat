# HA TRV Thermostat

Integracja Home Assistant do sterowania głowicami Zigbee (TRV) na podstawie **zewnętrznego czujnika temperatury w pomieszczeniu**. Działa jak Better Thermostat, ale:

- **na głowicę idzie zapis tylko przy zmianie poziomu grzania albo temperatury docelowej**, a przed każdym zapisem integracja porównuje wartość ze stanem głowicy (jeśli głowica już ją ma, nic nie wysyła),
- poziom może się zmienić najwyżej raz na `min_hold` (domyślnie 5 min), więc szum czujnika nie wywołuje zapisów,
- w spoczynku ustawia **dodatni** offset, żeby głowica na pewno zamknęła zawór,
- działa z Z2M i z ZHA, bo steruje zwykłymi encjami `climate` i `number`.

## Zasada działania

Głowica zawsze dostaje jako setpoint **prawdziwą** temperaturę docelową. O tym, czy grzeje, decyduje offset (kalibracja temperatury lokalnej). Poziom grzania jest wspólny dla pokoju, ale **offset dla każdego poziomu ustawiasz osobno dla każdej głowicy**, np. mocniej dla głowicy przy dużym grzejniku pod oknem. Domyślne wartości:

| Poziom | Kiedy | Offset (domyślnie) |
|---|---|---|
| 0 – spoczynek | nie trzeba grzać | **+3.0** (głowica „myśli”, że jest ciepło → zawór zamknięty) |
| 1 | niedogrzanie ≤ 0.6 °C | **−2.0** |
| 2 | niedogrzanie > 0.6 °C | **−5.0** |
| 3 (opcja) | niedogrzanie > próg L3 | −8.0 (domyślnie wyłączony, próg = 0) |

Histereza (T = czujnik pokojowy):

- **start** grzania: `T ≤ zadana − hyst_on` (domyślnie 0.3)
- **stop** grzania: `T ≥ zadana + hyst_off` (domyślnie 0.1)
- **zmiana poziomu w górę**: niedogrzanie > próg; **w dół**: niedogrzanie < próg − `level_hyst` (domyślnie 0.2). Przy progu 0.6 poziom 2 włącza się powyżej 0.6, a wraca do 1 poniżej 0.4.
- **min_hold**: minimalny czas między dowolnymi zmianami poziomu (domyślnie 5 min).
- **wyprzedzenie trendu** (opcja, domyślnie 0 = wyłączone): grzanie kończy się wcześniej, jeśli `T + trend × wyprzedzenie ≥ zadana + hyst_off`. Pomaga przy żeliwnych grzejnikach, które oddają ciepło jeszcze długo po zamknięciu zaworu. Na początek warto spróbować 10–15 min.

Sytuacje szczególne:

| Sytuacja | Co się dzieje |
|---|---|
| Okno otwarte (po opóźnieniu, domyślnie 60 s) | głowica → `off`; jeśli nie ma trybu off → setpoint = `min_temp` |
| Termostat pokoju `off` | jak wyżej, offset bez zmian |
| Temperatura zewnętrzna ≥ próg (domyślnie 15 °C) | jak wyżej (status „ciepło na zewnątrz”); grzanie wraca dopiero przy próg − histereza (domyślnie 1 °C, czyli 14 °C) |
| **Boost** | głowice dostają swój maksymalny setpoint (`max_temp`), więc zawór otwiera się na pełno na „Czas boostu” (domyślnie 15 min). Boost pomija harmonogram, histerezę, poza domem, próg zewnętrzny i wyłączony termostat; wygrywa z nim tylko otwarte okno. Offsetów nie zmienia, więc to 2 zapisy na głowicę |
| Pokój grzeje i brakuje ≥ próg zasłon (domyślnie 1,0 °C) | odsłania całkiem zasunięte zasłony przed grzejnikami, po zakończeniu grzania zasuwa te, które sama odsłoniła (szczegóły niżej) |
| **Poza domem** włączone | zadana = temperatura poza domem (domyślnie 20 °C), niezależnie od harmonogramu, presetu i nadpisań; pokrętło głowicy jest cofane |
| Czujnik `unavailable` < 10 min | ignorowane, zostaje ostatnia wartość |
| Czujnik zgubiony (unavailable ≥ 10 min albo brak raportu przez `sensor_timeout`, domyślnie 120 min) | offset awaryjny (0) → głowica reguluje sama |
| Restart HA, czujnik jeszcze nie zaraportował | przez 10 min nic nie jest zapisywane |
| Ktoś pokręci głowicą | nowa wartość = tymczasowe nadpisanie do najbliższej zmiany dzień/noc, pozostałe głowice w pokoju się dostosowują (można wyłączyć w opcjach, wtedy integracja przywraca swoją wartość) |

## Ruch w sieci Zigbee

- Pętla decyzyjna liczy się przy każdym odczycie czujnika i co 60 s, ale **nic nie wysyła**, dopóki nie zmieni się wartość docelowa.
- Setpoint głowicy w granicach ±0.25 °C od zadanej uznawany jest za poprawny (przy offsetach to bez znaczenia, a oszczędza zapisy).
- Jeśli głowica nie potwierdzi zapisu w ciągu 90 s → ponowienie, maks. 3 próby, potem najwyżej co 30 min (w logu pojawi się warning).
- Ten sam mechanizm naprawia głowicę po restarcie albo po ręcznej zmianie offsetu.
- Encja **Zapisy Zigbee** zlicza wszystkie zapisy od startu HA, więc ruch można obejrzeć na wykresie.

W teście symulującym kilka godzin pracy dwóch głowic (start/stop grzania, zmiany poziomów, okno, przejście w noc, kręcenie pokrętłem, głowica gubiąca ramki, utrata czujnika) wysłano łącznie **30 zapisów**.

## Instalacja

### HACS (zalecane)

[![Otwórz w HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ficueu&repository=ha-trv-thermostat&category=integration)

albo ręcznie: **HACS → ⋮ → Custom repositories** → URL `https://github.com/ficueu/ha-trv-thermostat`, typ **Integration** → **Add**. Następnie na liście znajdź **HA TRV Thermostat** → **Download** i zrestartuj HA. Nowe wersje pojawiają się w HA jako zwykłe aktualizacje (encja `update`).

### Ręcznie

Skopiuj katalog `custom_components/ha_trv_thermostat` do `/config/custom_components/ha_trv_thermostat` i zrestartuj HA.

### Konfiguracja

1. **Ustawienia → Urządzenia i usługi → Dodaj integrację → HA TRV Thermostat**. Jedno pomieszczenie = jeden wpis:
   - nazwa, czujnik temperatury, głowice (może być kilka),
   - encje offsetu: wykrywane automatycznie na urządzeniu głowicy (`local_temperature_calibration` w Z2M, `local_temperature_offset` w ZHA); sprawdź, czy wybrały się właściwe,
   - opcjonalnie czujniki okna.
2. Opcje zaawansowane (czujnik zewnętrzny i jego histereza, zasłony, opóźnienia okna, timeout czujnika, offset awaryjny, przejmowanie zmian z głowicy): **Konfiguruj** przy wpisie.

Wymagany HA ≥ 2024.11 (testowane na 2026.2).

## Encje (na każde pomieszczenie)

| Encja | Opis |
|---|---|
| `climate.<pokój>` | termostat; presety: **Harmonogram** (dzień/noc wg godzin), **Dzień**, **Noc** |
| `number.<pokój>_temperatura_dzien` / `_noc` | temperatury zadane |
| `time.<pokój>_poczatek_dnia` / `_nocy` | granice harmonogramu |
| `switch.<pokój>_poza_domem` | tryb poza domem |
| `number.<pokój>_temperatura_poza_domem` | zadana w trybie poza domem (domyślnie 20 °C) |
| `number.<pokój>_wylacz_grzanie_przy_temperaturze_zewnetrznej` | próg wyłączenia grzania (domyślnie 15 °C); tylko gdy wybrano czujnik zewnętrzny |
| `switch.<pokój>_sterowanie_zaslonami` | włącza sterowanie zasłonami (wyłączenie zasuwa to, co integracja odsłoniła); tylko gdy wybrano zasłony |
| `number.<pokój>_odslon_zaslony_przy_niedogrzaniu` | próg niedogrzania do odsłonięcia (domyślnie 1,0 °C); tylko gdy wybrano zasłony |
| `button.<pokój>_boost` / `_zakoncz_boost` | start (ponowne naciśnięcie liczy czas od nowa) / przerwanie boostu |
| `number.<pokój>_czas_boostu` | czas boostu (domyślnie 15 min) |
| `number.<pokój>_<głowica>_offset_spoczynek_poziom_0` … `_offset_poziom_3` | offsety tej głowicy dla poziomów 0–3 (kategoria konfiguracja); zakres i krok jak w encji kalibracji głowicy |
| `number.*` (kategoria konfiguracja) | histerezy, progi poziomów, min_hold, wyprzedzenie |
| `sensor.<pokój>_poziom_grzania` | 0–3 |
| `sensor.<pokój>_status` | wyłączone / bezczynny / grzanie / okno otwarte / boost / ciepło na zewnątrz / brak czujnika |
| `sensor.<pokój>_<głowica>_zadany_offset`, `sensor.<pokój>_zapisy_zigbee` | diagnostyka |

Zmiana temperatury na karcie termostatu:
- w presecie **Harmonogram** ustawia nadpisanie do najbliższej zmiany dzień/noc (atrybut `override_until`); ustawienie z powrotem wartości z harmonogramu kasuje nadpisanie,
- w presecie **Dzień** / **Noc** zmienia na stałe temperaturę dzienną / nocną,
- przy włączonym **Poza domem** zmienia temperaturę poza domem.

Atrybuty `climate` do debugowania: `status`, `level`, `deficit`, `trend` (°C/h), `reason`, `target_source` (`day`/`night`/`comfort`/`eco`/`override`/`away`), `override_until`, `last_level_change`, `away`, `boost_until`, `outdoor_temperature`, `outdoor_warm`, `covers_opened`, `offsets` (zadany offset każdej głowicy). Atrybuty `deficit`, `trend` i `reason` nie trafiają do recordera.

### Aktualizacja z wcześniejszych wersji

Wspólne offsety pokoju z wersji 1.0 zostają skopiowane do każdej głowicy, a stare encje offsetów pokoju znikają same. Encje głowic usuniętych z pokoju w **Konfiguruj** też są sprzątane automatycznie.

### Poza domem dla całego mieszkania

Przełącznik jest w każdym pomieszczeniu osobno. Żeby mieć jeden dla całego mieszkania, dodaj pomocnika **Ustawienia → Urządzenia i usługi → Pomocnicy → Utwórz pomocnika → Grupa → Grupa przełączników** i dodaj do niej wszystkie `switch.*_poza_domem`. Przełączenie grupy przełącza wszystkie pokoje.

### Temperatura zewnętrzna

Czujnik wybierasz przy dodawaniu pomieszczenia albo w **Konfiguruj**. Może to być czujnik temperatury albo encja pogody (`weather.*`, brany jest jej atrybut `temperature`). Bez czujnika funkcja jest wyłączona i encja progu nie powstaje. Gdy czujnik jest niedostępny, zostaje ostatni stan, więc chwilowy brak odczytu nie przełącza głowic.

### Zasłony

Zasłona przed grzejnikiem zatrzymuje ciepło między grzejnikiem a oknem, więc przy dużym niedogrzaniu warto ją odsłonić. Zasłony (encje `cover`) wybierasz przy dodawaniu pomieszczenia albo w **Konfiguruj**.

- **Odsłanianie:** gdy pokój grzeje i niedogrzanie ≥ próg, integracja odsłania zasłony, które są **całkiem zasunięte**. Te już odsłonięte zostawia i potem ich nie zasuwa.
- **Zasuwanie:** gdy grzanie się kończy (bezczynny, wyłączony, ciepło na zewnątrz), integracja zasuwa tylko zasłony, które sama odsłoniła.
- **Ręczna zmiana wygrywa:** jeśli ktoś zasunie albo przesunie zasłonę (pozycja < 90), integracja odpuszcza i do końca tego cyklu grzania jej nie rusza.
- **Tylko za dnia** (domyślnie): odsłania tylko przy słońcu nad horyzontem (`sun.sun`, bez niego według harmonogramu dzień/noc). O zachodzie zasuwa to, co sama odsłoniła, bo w nocy zasłona izoluje okno i daje prywatność.
- **Bez reakcji:** przy otwartym oknie, boostcie i utracie czujnika zasłony zostają, jak są.
- Zasłony odsłonięte przez integrację są pamiętane po restarcie HA (atrybut `covers_opened`).

## Wskazówki

- **Wyłącz wbudowaną detekcję okna w głowicach** (`window_detection` w Z2M). Skok offsetu z +3 na −5 to dla głowicy nagły spadek temperatury o 8 °C i część modeli (np. Tuya) uzna to za otwarte okno i zamknie zawór na 30–60 min.
- Wyłącz w głowicach tryb `auto` i wewnętrzne harmonogramy. Integracja i tak wymusza `heat`, ale harmonogram głowicy potrafi nadpisać setpoint.
- **Czujnik pokojowy** powinien raportować z rozdzielczością 0.1 °C. W Z2M sprawdź `temperature_precision` / reporting change, bo przy raportowaniu co 0.5 °C histereza 0.3 traci sens.
- Zakres offsetu bywa różny (np. ±7, ±9, −12.8…12.7). Wartość spoza zakresu zostanie przycięta i w logu pojawi się warning, więc dobierz offsety do swoich głowic.
- Jeśli czujnik pokojowy nie wysyła okresowych raportów (tylko przy zmianie), zwiększ `sensor_timeout`.
- Logi szczegółowe:
  ```yaml
  logger:
    logs:
      custom_components.ha_trv_thermostat: debug
  ```
  Na poziomie `info` logowana jest każda zmiana poziomu z powodem, na `debug` każdy zapis do głowicy.

## Przykładowa karta

```yaml
type: vertical-stack
cards:
  - type: thermostat
    entity: climate.salon
  - type: entities
    entities:
      - sensor.salon_status
      - sensor.salon_poziom_grzania
      - number.salon_temperatura_dzien
      - number.salon_temperatura_noc
      - time.salon_poczatek_dnia
      - time.salon_poczatek_nocy
      - number.salon_histereza_start_ponizej_zadanej
      - number.salon_histereza_stop_powyzej_zadanej
```

(Identyfikatory encji zależą od nazwy pomieszczenia i języka HA w momencie dodania; sprawdź je w urządzeniu.)

## Testy

```bash
pip install -r requirements_test.txt
pytest
```

`tests/test_logic.py` testuje czystą logikę, a `tests/test_integration.py` scenariusze end-to-end w prawdziwej instancji HA z symulowanymi głowicami i zlicza zapisy. GitHub Actions przy każdym pushu uruchamia testy oraz walidację HACS i hassfest.

## Wydawanie wersji

1. Podbij `version` w `custom_components/ha_trv_thermostat/manifest.json`.
2. Commit, a potem `git tag vX.Y.Z && git push --tags`.
3. Workflow `Release` sprawdzi zgodność tagu z manifestem i utworzy release, a HACS pokaże aktualizację.

Zamiast punktu 2 możesz na GitHubie wejść w **Releases → Draft a new release**, wpisać nowy tag `vX.Y.Z` (target `main`) i kliknąć **Publish**. Workflow uzupełni wtedy opis release'u.
