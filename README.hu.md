# video-notes

[中文](README.md) | [English](README.en.md) | **Magyar**

Helyi előadás-, kurzus- vagy képzésvideóból **megosztható tanulási jegyzetet készít az eredeti videó képkockáival** (Markdown és Word változatban).

- A jegyzetet a Claude vagy Codex alkalmazásban veled beszélgető MI írja: az eredeti magyarázat sorrendjében fejti ki az okokat, működést, feltételeket, lépéseket és példákat. Nem összefoglaló és nem leirat.
- A video-notes végzi a többit: ellenőrzi a feliratot, megkeresi a képernyőváltásokat, fejezetekre bont, jelölt képkockákat választ, ellenőrzi az MI által megírt fejezeteket, és elkészíti a Markdown és Word fájlt.
- Fejezetenként legfeljebb 8 kulcskép, a képaláírás leírja a tartalmukat és az eredeti videóbeli időt; a jegyzet a videóval azonos nevű, és a videó mellé kerül.

```mermaid
flowchart LR
    V["Videó + felirat"] --> P["video-notes prepare<br/>fejezetek, képkockák"]
    P --> A["MI a beszélgetésben<br/>fejezetenként ír"]
    A --> S["video-notes assemble<br/>ellenőrzés, jegyzet"]
    S --> O["videó neve.md<br/>videó neve.docx"]
```

> 0.2-es verzió. A folyamatot és az ellenőrzéseket egységtesztek és valódi videórészletek igazolják; a szöveg minősége az író MI-től és az önellenőrzésétől függ.

## Tartalom

1. [Példa az eredményre](#példa-az-eredményre)
2. [Előkészületek (egyszer)](#előkészületek-egyszer)
3. [1. lépés: Felirat készítése](#1-lépés-felirat-készítése)
4. [2. lépés: Az MI megírja a jegyzetet](#2-lépés-az-mi-megírja-a-jegyzetet)
5. [3. lépés: A kész jegyzet](#3-lépés-a-kész-jegyzet)
6. [Gyakori kérdések](#gyakori-kérdések)
7. [Haladó](#haladó)
8. [Fejlesztés és tesztek](#fejlesztés-és-tesztek)

---

## Példa az eredményre

A jegyzet az eredeti magyarázat sorrendjét követi fejezetenként és témakörönként, és minden témakört teljesen kifejt; a képkockák az általuk alátámasztott magyarázat mellé kerülnek, a képaláírás után az eredeti videóbeli idővel. Szerkezet:

```markdown
# A kurzus címe

## 1. fejezet: … (a tartalma alapján elnevezve)

### A témakör
Teljes magyarázat: háttér → működés → feltételek → lépések → eredmény → figyelmeztetések …

![Topológia: a három maghálózati eszköz összeköttetése](kurzus_assets/C01F00127742.jpg)

*Topológia: a három maghálózati eszköz összeköttetése (eredeti videó 00:02:07.742)*

### B témakör
…
```

A `kurzus.md` fájlt a `kurzus_assets/` mappával együtt másold, tömörítsd vagy töltsd fel, így a képek megmaradnak; a `kurzus.docx` beágyazva tartalmazza a képeket, önmagában is elküldhető.

---

## Előkészületek (egyszer)

1. Telepítsd a Python 3.10+, az FFmpeg és a git programot. Windowson futtasd: `winget install Python.Python.3.12`, `winget install Gyan.FFmpeg`, `winget install Git.Git` (git nélkül a GitHub-oldalról letöltheted és kicsomagolhatod a ZIP-et).
2. Telepítsd a video-notes eszközt:

   ```bash
   git clone https://github.com/songyang8964/video-notes.git
   cd video-notes
   powershell -ExecutionPolicy Bypass -File install.ps1 -AddToPath   # macOS/Linux: sh install.sh
   ```

3. **Lépj ki teljesen és nyisd meg újra** a Claude vagy Codex alkalmazást, hogy megtalálja az újonnan telepített `video-notes` parancsot.
4. Opcionális: futtasd terminálban a `video-notes doctor` parancsot, amely ellenőrzi az FFmpeg, a pandoc (a Wordhöz) és a többi összetevő meglétét.

---

## 1. lépés: Felirat készítése

Ha már van a videóval **azonos nevű** felirat (például `kurzus.srt` a `kurzus.mp4` mellett), ugorj a 2. lépésre.

Felirat nélkül a Google Colab ingyenes T4 GPU-ja ajánlott (NVIDIA GPU nélkül egy 2 órás videó helyi felismerése órákig tarthat):

1. (Ajánlott) Helyben csak a hangot bontsd ki; a fájl kicsi, gyorsan feltölthető. Tartsd meg a videó fájlnevét:

   ```bash
   ffmpeg -i "kurzus.mp4" -vn -ac 1 -c:a aac -b:a 64k "kurzus.m4a"
   ```

2. Nyisd meg a [Colab feliratkészítő jegyzetfüzetet](https://colab.research.google.com/github/songyang8964/video-notes/blob/main/colab/whisperx_for_uploading_file.ipynb). Colabban kézzel is megnyithatod: **File → Open notebook → GitHub**:

   ![Jegyzetfüzet megnyitása GitHubról Colabban](docs/images/colab-open-from-github.png)

   A keresőmezőbe írd be: `songyang8964`, válaszd a `songyang8964/video-notes` tárolót, és nyisd meg a `colab/whisperx_for_uploading_file.ipynb` fájlt:

   ![Keresés: songyang8964](docs/images/colab-search-account.png)

3. Válaszd a **Runtime → Change runtime type** menüpontot, jelöld ki a **T4 GPU**-t, majd kattints a **Save** gombra:

   ![A T4 GPU kiválasztása](docs/images/colab-t4-gpu.png)

4. Kattints a bal oldali mappa ikonra, és húzd oda a hang- (vagy videó)fájlt a feltöltéshez.
5. Opcionális: az első cella **Initial prompt** mezőjébe írd be a kurzus szakkifejezéseit; ez javítja a felismerést.
6. Válaszd a **Runtime → Run all** menüpontot. A végén a böngésző letölti a feltöltött fájllal azonos nevű `.srt` fájlt (ha a böngésző rákérdez, engedélyezd a több fájl letöltését).
7. Tedd a `kurzus.srt` fájlt a `kurzus.mp4` mellé.

Colab nélkül is megy: telepíts helyi beszédfelismerést (`pip install faster-whisper` vagy WhisperX), és a 2. lépés automatikusan átírja, de GPU nélkül lassú.

---

## 2. lépés: Az MI megírja a jegyzetet

1. Nyisd meg a **Claude asztali → Code** lapot vagy a **Codex asztali alkalmazást**, és válaszd ki a videót tartalmazó mappát.
2. Küldd el az MI-nek változtatás nélkül ezt a szöveget:

   ```text
   Készíts jegyzetet a mappában lévő videóból a video-notes segítségével: futtasd a video-notes prepare parancsot, olvasd el a kiírt csomagleírást, minden fejezethez írd meg a brief.md alapján a topics.csv, knowledge.csv, chapter.md és review.md fájlt, majd futtasd a video-notes assemble parancsot, amíg minden ellenőrzés sikeres.
   ```

3. Az MI először fejezetekre bont és képkockákat rögzít, majd fejezetenként ír és önellenőriz, végül mindent ellenőriz és elkészíti a jegyzetet. Hosszú videó több beszélgetésben is elkészülhet: a már megírt fejezetek megmaradnak; új beszélgetésben mondd: „folytasd a még kész nem lévő fejezeteket, majd futtasd a video-notes assemble parancsot”.

---

## 3. lépés: A kész jegyzet

```
<a videó mappája>/
  <videó neve>.md          Markdown jegyzet
  <videó neve>.docx        Word jegyzet (beágyazott képekkel, önállóan elküldhető)
  <videó neve>_assets/     a Markdown által használt képkockák
```

- Az eredeti videó és felirat soha nem módosul.
- A videó mellett egy `.work` mappa (gyorsítótár és köztes fájlok) is megjelenik; a jegyzet elkészülte után törölhető.
- Ha kézzel módosítottad a korábban készült jegyzetet, az újbóli elkészítés **nem írja felül**; az új eredmény időbélyeges néven kerül mentésre.

---

## Gyakori kérdések

- **A `video-notes` parancs nem található**: a telepítés `-AddToPath` nélkül történt, vagy a terminált / alkalmazást a telepítés előtt nyitottad meg. Nyiss új terminált, vagy lépj ki teljesen a Claude / Codex alkalmazásból, és nyisd meg újra.
- **Az `assemble` nem sikerült**: az okok a csomagmappa `check.md` fájljában vannak (például egy feliratsor nem tartozik szakaszhoz, hiányzik egy fontos tudáselem, túl rövid a szöveg). Kérd meg az MI-t, hogy javítsa azokat a fejezeteket, és futtasd újra az `assemble` parancsot.
- **Javítottam a feliratot vagy a videókontextust**: előbb futtasd újra a `video-notes prepare` parancsot. A már megírt fejezetek megmaradnak; az érintett fejezeteket a program megjelöli, ezeket az MI-nek újra ellenőriznie kell.
- **Megszakadt**: futtasd újra ugyanazt a parancsot; a már elkészült munka nem vész el.
- **Mennyi ideig tart**: az első `prepare` nagyjából a videó hosszának ötöde-harmada; a későbbi futások gyorsítótárat használnak. Az írás a beszélgetésben történik, és a beszélgetés saját keretét fogyasztja, nagyjából a videó hosszával arányosan; két óránál hosszabb videóknál használj több beszélgetést.
- **Angol vagy más nyelvű jegyzetet szeretnék**: `video-notes setup --language English`, vagy add meg a `note_language` értéket a videókontextusban (lásd: „Haladó”).

---

## Haladó

### Videókontextus (opcionális)

Tegyél a videó mellé egy `<videó neve>.context.md` fájlt a kurzus hátterével, szakkifejezéseivel és a kihagyandó részekkel; az MI minden fejezetnél figyelembe veszi:

```markdown
---
title: Bevezetés az elosztott rendszerekbe (3. előadás)
note_language: Magyar
include_times: 00:12:30, 00:41:05.500
forbid: az előadó, ez a videó
---
Az előadás témája a konszenzus-algoritmusok. A fogalmak írásmódja: Raft, Paxos, Leader.
A feliratban szereplő "rafting" a Raft felismerési hibája. Az előadás előtti eszközpróba nem tartozik a témához.
```

| Mező | Szerep |
| --- | --- |
| `title` | A jegyzet címe (alapértelmezés: a videó fájlneve) |
| `note_language` | A jegyzet nyelve ehhez a videóhoz (felülírja a `setup --language` beállítást) |
| `language` | Előnyben részesített feliratnyelv; felirat hiányában a beszédfelismerés is megkapja |
| `include_times` | Képernyőidők, amelyekből jelöltnek kell lennie, vesszővel elválasztva, `HH:MM:SS(.mmm)` vagy másodperc |
| `forbid` | A szövegben tiltott szavak, vesszővel elválasztva (a program ellenőrzi) |
| `asr_preset` | Szakkifejezés-tippek a helyi átíráshoz (jelenleg `networking`) |

### Futtatás terminálból

```bash
video-notes                          # ugyanaz, mint a prepare: a mappában lévő egyetlen .mp4 feldolgozása
video-notes prepare "kurzus.mp4" --srt "felirat.srt" --context hatter.md
video-notes assemble "kurzus.mp4"    # miután minden fejezet elkészült
video-notes assemble "kurzus.mp4" --output D:\jegyzetek   # kimenet másik mappába
```

A `prepare` a végén kiírja a csomagmappa helyét (`briefs/` a `.work` mappán belül): az ottani `README.md` felsorolja a fejezeteket, és minden fejezetnek van egy almappája, amelynek `brief.md` fájlja tartalmazza az írási szabályokat, a fejezet feliratait és a jelölt képkockákat. Az író ugyanabba az almappába négy fájlt ír:

| Fájl | Tartalom |
| --- | --- |
| `topics.csv` | Jelentés szerinti, egymást követő témakörök, a fejezet minden feliratsorát lefedve; a témán kívüli részek indoklással kizártként jelölve |
| `knowledge.csv` | Részletes tudásleltár: definíciók, működés, feltételek, okok, példák, parancsok, lépések, kockázatok, kérdések és válaszok |
| `chapter.md` | A fejezet szövege; a képek `[[frame:képkocka-azonosító\|aláírás]]` helyőrzők, a végén a tudástérkép |
| `review.md` | Önellenőrzési napló: mit ellenőrzött az eredetin minden képnél, hol van kifejtve minden fontos tudáselem |

### Minőségellenőrzés

Az `assemble` minden fejezeten lefuttatja az alábbi ellenőrzéseket; ha bármelyik sikertelen, nem készül jegyzet:

| Ellenőrzés | Szabály |
| --- | --- |
| Teljes feliratlefedettség | Minden feliratsor pontosan egy szakaszhoz tartozik, sorrendben, vagy indoklással kizártként jelölt |
| Képek | Fejezetenként legfeljebb 8, ismétlés nélkül; minden kép csak a saját témakörének szakaszában |
| Tudáslefedettség | Minden fontos tudáselem a tudástérképen egy létező szakaszra mutat, vagy kizárási indoklással rendelkezik |
| Részletesség | Legalább 100 karakter a magyarázat minden percére (angolnál szavakban számolva); a legalább 1 perces szakaszokban legalább 50 percenként |
| Írásmód | Nincs „az előadó megemlíti”, „a videóban” típusú közvetítés, nincsenek `forbid` szavak, sem belső azonosítók, például C01 vagy képkocka-azonosító |
| Önellenőrzési napló | A `review.md` minden képnél leírja, mit ellenőrzött az eredetin, és lefed minden fontos tudáselemet |
| Formátum | Minden fejezetnek van címe; nincs nyers képútvonal, nincs páratlan kódblokk; a jegyzetben nem marad helyőrző |

A program ellenőrzi a formátumot, a lefedettséget és az önellenőrzési napló teljességét, de azt nem tudja megítélni, hogy a szakmai tartalom helyes-e; ez azon múlik, hogy az író MI gondosan összevetette-e a feliratokkal és az eredeti képekkel. Fontos anyagoknál nézd át magad a kulcsfejezeteket, különösen a parancsokat, címeket és számokat.

### Kilépési kódok

| Kilépési kód | Jelentés |
| --- | --- |
| 0 | Sikeres: a csomagok elkészültek, vagy minden fejezet átment az ellenőrzésen és a jegyzet elkészült |
| 1 | Függőségi vagy feldolgozási hiba; vagy fejezetek nem mentek át az ellenőrzésen (okok a `briefs/check.md` fájlban, jegyzet nem készült) |
| 2 | Kétértelmű vagy érvénytelen bemenet: nincs videó, több videó, nem létező fájl, online hivatkozás, használhatatlan felirat |
| 130 | Felhasználói megszakítás (a haladás megmarad) |

### Beállítások

A beállítófájl: `~/.video-notes/config.json` (Windows: `%USERPROFILE%\.video-notes\config.json`); a gyakori elemek a `video-notes setup` paranccsal módosíthatók.

| Kulcs | Alapértelmezés | Leírás |
| --- | --- | --- |
| `output_language` | `中文` | A jegyzet nyelve |
| `max_images` | 8 | Képek maximális száma fejezetenként |
| `shortlist` | 32 | Egy fejezetcsomagban felsorolt különböző képernyők legnagyobb száma |
| `chapter_minutes` | 10 | Célzott fejezethossz (perc) |
| `use_adaptive` | true | A PySceneDetect segédérzékelő használata (teljesebb, kicsit lassabb) |
| `parallel_chapters` | 3 | Egyszerre rögzített jelöltképű fejezetek száma |
| `min_chars_per_minute` | 100 | A részletesség alsó határa |
| `tesseract` / `ocr_langs` | üres / `eng` | Az OCR program útvonala és nyelvei (opcionális, jobb jelöltrangsor) |
| `asr_backend` / `asr_model` / `whisperx` | automatikus / `large-v3` / üres | Helyi átírás beállításai |

### Működés

A `prepare` és az `assemble` helyi program (FFmpeg, PyAV, képfeldolgozás), és nem hív meg modellt; a tartalom megértését, a képválasztást, az írást és az önellenőrzést a beszélgetésben részt vevő MI végzi.

```mermaid
flowchart TD
    V["kurzus.mp4"] --> S{"Van felirat?"}
    SUB["azonos nevű felirat<br/>vagy beágyazott sáv"] --> S
    S -- igen --> CUES["Felirat"]
    S -- nem --> ASR["Beszédfelismerés<br/>helyben vagy Colabban"] --> CUES
    V --> DET["① Képernyőfigyelés<br/>videónként egyszer"]
    CUES --> CH["② Fejezetek<br/>kb. 10 perc"]
    DET --> CH
    CH --> C["③ Jelölt képkockák<br/>fejezetcsomagok"]
    subgraph AI["MI a beszélgetésben, fejezetenként"]
        W["④ Témakörök, tudásleltár<br/>képek, szöveg, önellenőrzés"]
    end
    C --> W
    W --> CHK{"⑤ assemble<br/>ellenőrzés"}
    CHK -- sikertelen --> W
    CHK -- sikeres --> OUT[".md és .docx"]
```

#### Feliratválasztás és átírás

Az eszköz megkeresi azt a beszédszöveget, amely valóban ehhez a videóhoz tartozik:

- A jelöltek sorrendben: a `--srt` kapcsolóval megadott fájl; a videóval azonos nevű `.srt` / `.vtt` fájlok (például `kurzus.srt`, `kurzus.en.vtt`); a videóba ágyazott szöveges feliratsávok. Az azonos nevű feliratok nyelv szerint rangsorolódnak: kért nyelv → nyelvjelölés nélküli → egyéb nyelvek.
- Az alábbi feliratokat a program elutasítja, és rögzíti az okát: 5-nél kevesebb sor; a játékidő kevesebb mint 30%-át fedi le (gyakran „kényszerített” felirat, amely csak az idegen nyelvű részeket fordítja); az időpontok messze túlnyúlnak a videó hosszán (más vágáshoz készült); a sorok több mint 30%-a az előző sor szövegével kezdődik (duplikációmentesítés nélküli, gördülő automatikus felirat).
- A WebVTT gördülő feliratait a program már beolvasáskor duplikációmentesíti (minden sor megismétli az előzőt).
- A `--srt` kapcsolóval megadott felirat kifejezett kérés: ha használhatatlan, a futás leáll, és nem vált csendben más forrásra. Ha csak túlnyúlik a képen (sérült felvétel, amelynek hangja hosszabb a képnél), akkor is használja.
- Használható felirat nélkül a program helyben átírja a videót (WhisperX → faster-whisper → openai-whisper, amelyik telepítve van). NVIDIA GPU nélkül a helyi átírás lassú; a Colab ajánlott (lásd: 1. lépés).

#### ① Képernyőváltás-figyelés

Az előadások fő képei a diák, topológiai ábrák, kód és parancssor. A figyelés egyetlen kérdésre felel: **mikor változott a képernyő**.

- **Takaró sávok kizárása**: először a program minden képpontsor változási gyakoriságát méri. A kép felső vagy alsó szélén lévő, a tartalomnál jóval gyakrabban változó sávokat (beégetett felirat, gördülő szalag) kizárja, különben minden új feliratsor diaváltásnak tűnne.
- **Három jel** (képkockánként mérve, kis szürkeárnyalatos képen):
  - *horgonyeltolódás*: az aktuális képkocka átlagos eltérése az utolsó stabil képernyőtől; a fokozatosan teleírt táblát vagy a soronként megjelenő kódot fogja meg;
  - *hirtelen terület*: a szomszédos képkockák között egyértelműen változó képpontok aránya; a lapozást és a váltásokat fogja meg;
  - *pillanatnyi változás*: a szomszédos képkockák átlagos eltérése; a mozgás észlelésére és annak eldöntésére szolgál, hogy a képernyő megnyugodott-e és rögzíthető-e.
- **Segédérzékelő**: a PySceneDetect opcionális adaptív érzékelője további váltási időpontokat ad.
- **Események és rögzítési pontok**: a jelek 0,5 másodpercen belüli csúcsai egy képernyőváltási eseménnyé olvadnak össze. Minden eseményhez két stabil képkocka tartozik: a váltás előtti (a régi képernyő **kész állapota**) és utáni (az új képernyő **kezdete**). Ha egy képernyő kezdete és kész állapota szinte azonos (SSIM ≥ 0,93), csak a kezdet marad meg, mint egy statikus diánál; ha egyértelműen eltérnek, mindkettő megmarad, mint egy fokozatosan felépülő ábránál vagy egy begépelt parancsnál.
- **Képkockasebesség a valódi időbélyegekből**: a képernyőfelvételek képkockasebessége gyakran változó, névlegesen 60 fps, valójában kb. 15 fps. Minden időpont a valódi időbélyegeket követi.
- **Sérült felvételek**: ha a kép csak egy adott pontig dekódolható, a program rögzíti ezt a végpontot, és utána nem készít képkockát; a feliratot továbbra is használja.

A figyelés menete:

```mermaid
flowchart TD
    F["Minden képkocka dekódolása<br/>kis szürke kép"] --> B["Felirat- és<br/>szalagsávok kizárása"]
    B --> S1["Horgonyeltolódás"]
    B --> S2["Hirtelen terület"]
    B --> S3["Pillanatnyi változás"]
    S1 & S2 & S3 --> M["Összevonás<br/>egy képernyőváltássá"]
    AD["Segédérzékelő"] --> M
    M --> E["Stabil képkockák<br/>előtte és utána"]
    E --> K{"Szinte azonos?"}
    K -- igen --> ONE["Csak a kezdet"]
    K -- nem --> TWO["Kezdet + kész"]
```

Milyen képkockákat ad egy diaváltás (egy dia, amelynek pontjai egyenként jelennek meg):

| Pillanat | A képernyőn | Képkocka |
| --- | --- | --- |
| Az A dia épp megjelent | csak a cím | ① A kezdete |
| A pontok egyenként megjelennek | a változás lassan gyűlik, nem lapozás | — |
| Lapozás előtt | minden pont látható | ② A kész |
| Lapozás után, stabilan | B dia | ③ B kezdete |

- ① és ② egyértelműen eltér, így mindkettő jelölt lesz; az MI általában a legtöbb információt hordozó ②-t választja.
- Ha A statikus dia, ① és ② szinte azonos, csak ① marad meg.
- Ha A sokáig a képernyőn marad, 20 másodpercenként egy „szívverés” képkocka is készül, hogy apró változások se maradjanak ki.

A teljes videót a program csak kétszer dekódolja a méréshez és egyszer a segédérzékelőhöz; az összes kezdet/kész összehasonlítás **egyetlen soros dekódolásban** történik, nem több ezer véletlenszerű ugrással. Az eredmények gyorsítótárba kerülnek, és fejezetenként használja fel őket.

#### ② Fejezetek

A cél fejezetenként kb. 10 perc (beállítható). Minden ablak utolsó negyedében a program annál a felirathatárnál vág, ahol **a leghosszabb a szünet**, lehetőleg **egy képernyőváltás közelében**, hogy egy témakör ne szakadjon ketté.

#### ③ Jelölt képkockák

- A jelölt időpontok forrásai: a képernyőváltások kezdő és kész képkockái; a sokáig változatlan képernyőkön 20 másodpercenként egy képkocka (apró változásokhoz, például egy új sorhoz a terminálban); a videókontextus `include_times` mezőjében megadott időpontok.
- Minden jelölt **az eredeti videóból, teljes felbontásban, a valódi időbélyeg alapján készül**, és a tényleges képkockaidő rögzítésre kerül.
- **Minőségszűrés**: a túl sötét, túl világos, elmosódott (alacsony Laplace-variancia, például áttűnésnél) vagy szinte üres képkockák kiesnek, az ok rögzítésével.
- **OCR (opcionális, Tesseract szükséges)**: a középső tartalmi és az alsó feliratterületet külön olvassa be, és kiszámítja a „szövegújdonságot”: az újonnan megjelenő és megmaradó tartalmi szöveg számít a legtöbbet; a villanásnyi változások és a feliratváltások keveset.
- **Ismétlődő képernyők összevonása**: a jelöltek 320×180-as szürke képként kerülnek összehasonlításra; ha a képpontok kevesebb mint 1%-a változik egyértelműen, ugyanaz a képernyő, és csak egy marad meg (előbb a kért időpontok, aztán a kész állapotok); a csak kb. egy másodpercig látható, gyorsan átlapozott dia is megmarad. Minden különböző képernyő bekerül a csomagba; csak ha egy fejezetben 32-nél több van, akkor szűkíti 32-re egy változatossági válogatás a változás erőssége, a szövegújdonság, az élesség, a már kiválasztottakhoz való hasonlóság és az időbeli eloszlás alapján.
- Minden jelölthöz tartozik, **mi hangzott el, amíg az a képernyő látható volt** (feliratszám-tartomány), így az MI meg tudja ítélni, hogy kép és szöveg összetartozik-e.
- A csomag megadja az eredetit, egy 1280 képpont hosszú oldalú olvasási másolatot és áttekintő lapokat (bélyegképek); parancsoknál, paramétereknél és számoknál az MI megnyitja az eredetit.

```mermaid
flowchart TD
    A["Jelölt időpontok"] --> B["Eredeti rögzítése"]
    B --> Q{"Jó minőség?"}
    Q -- nem --> R1["Kiesik"]
    Q -- igen --> D{"Azonos egy már<br/>megtartott képpel?"}
    D -- igen --> R2["Összevonva"]
    D -- nem --> SH["Szűkített lista<br/>fejezetenként max. 32"]
    SH --> M["Az MI kulcsképeket választ<br/>max. 8"]
    M --> N["A szövegbe kerül"]
```

#### ④ Írás és önellenőrzés (az MI a beszélgetésben)

Az egyes fejezetek `brief.md` fájlja alapján az MI:

- minden feliratsort egy **témakörhöz** rendel, vagy indoklással témán kívülinek jelöl (`topics.csv`);
- részletes **tudásleltárt** készít: definíciók, működés, feltételek, okok, összehasonlítások, példák, parancsok, konfigurációs lépések, ellenőrzési eredmények, kockázatok, visszaállítás és értékes kérdések-válaszok, mindegyik fontosként vagy kiegészítőként jelölve (`knowledge.csv`);
- **kulcsképeket választ**: csak a megértéshez szükséges szerkezeti ábrákat, folyamatábrákat, összehasonlításokat, táblázatokat, kulcsparancsokat vagy eredményeket; diánként egyet, a legteljesebbet; címlap, napirend, csak szöveges vagy csak előadót mutató képernyő nem kerül be; fejezetenként legfeljebb 8, és egy fejezet kép nélkül is maradhat; a nem választott képernyők fontos információi a szövegbe kerülnek;
- **megírja a szöveget** az eredeti magyarázat sorrendjében, témakörönként egy szakasszal, a képeket a magyarázatuk mellé helyezve (`chapter.md`);
- **ellenőrzi magát**: minden képnél mit ellenőrzött az eredetin, és hol van kifejtve minden fontos elem (`review.md`).

#### ⑤ Ellenőrzés és kimenet

Az `assemble` először megerősíti, hogy a felirat és a videókontextus ugyanaz, mint a `prepare` idején, és hogy minden fejezet önellenőrzése újabb a csomagjánál, majd minden fejezeten lefuttatja a [minőségellenőrzést](#minőségellenőrzés). Ha minden sikeres, a fejezetek egy jegyzetté állnak össze, a képhelyőrzők az eredeti képkockákra mutató relatív hivatkozásokká válnak, minden képaláírás után az eredeti videóbeli idővel, a kiválasztott eredetik a `<videó neve>_assets/` mappába kerülnek, a pandoc pedig beágyazott képekkel Word fájlt készít. Ha az előző kimenetet kézzel módosították, nem íródik felül; az új eredmény külön kerül mentésre.

### Belső fájlok

```
<a parancs futtatási mappája>/.work/video-notes/<hash>/    ← törölhető (a gyorsítótár elvész)
  source.json        bemeneti hash-ek, a felirat forrása és választásának oka, médiaadatok
  detect/            képernyőfigyelési jelek és gyorsítótárazott eredmények
  C01/ C02/ ...      jelölt képkockák fejezetenként: eredetik, olvasási másolatok, áttekintő lapok, candidates.csv
  briefs/
    README.md        fejezetlista és írási útmutató
    chapters.json    fejezettartományok és bemeneti ujjlenyomatok
    C01/ C02/ ...    brief.md, valamint az MI által írt topics.csv, knowledge.csv, chapter.md, review.md
    check.md         az assemble ellenőrzési eredményei
  output.sha256      az utolsó kimenet ujjlenyomatai, annak megállapítására, hogy kézzel módosították-e
```

Windowson, ha a videó mappája nagyon mélyen van (például egy értekezletalkalmazás felvételi mappája), a belső fájlok a `%USERPROFILE%\.video-notes\work\<hash>` mappába kerülnek, hogy a 260 karakteres útvonalkorláton belül maradjanak. Munkamappánként egyszerre csak egy futás engedélyezett.

### Idő és erőforrások

Egy kb. 2 óra 40 perces, 1080p-s, felirattal rendelkező felvétel esetén (átlagos laptop-CPU):

| Szakasz | Idő |
| --- | --- |
| Képernyőjelek mérése (két dekódolás) | kb. 10 perc |
| Segédérzékelő (egy dekódolás) | kb. 20 perc |
| Kezdet/kész összehasonlítás (egy soros dekódolás) | kb. 10 perc |
| Jelölt képkockák és szűrés | 10–30 perc, a képernyőváltások számától függően |
| Fejezetenkénti írás a beszélgetésben | az MI sebességétől és a beszélgetés keretétől függ |

- A képernyőfigyelés csak az első `prepare` során fut; a későbbi futások a gyorsítótárat használják. A `use_adaptive` kikapcsolása kihagyja a segédérzékelőt, de néhány váltás elmaradhat.
- Alapértelmezés szerint 3 fejezet rögzít egyszerre jelölteket; a `prepare` futása alatt a számítógép nem alszik el.
- Memória: a figyelés képkockánként, folyamatosan dolgozik, így a memóriahasználat nem nő a videó hosszával.

---

## Fejlesztés és tesztek

```bash
python -m unittest discover -s tests -v                    # egységtesztek
python tests/integration_prepare_assemble.py <mappa>       # prepare → megírt fejezetek → assemble valódi videórészleten
```

```
src/video_notes/
  cli.py          parancssor: videókeresés, prepare / assemble, setup, doctor, kilépési kódok
  pipeline.py     felismerés, fejezetek, csomagok, ellenőrzés és kimenet
  subtitles.py    feliratjelöltek és elutasítási szabályok, VTT-feldolgozás
  transcribe.py   helyi beszédfelismerés (WhisperX / faster-whisper / openai-whisper)
  detect/         képernyőváltások felismerése és a kép dekódolható vége
  vision/         minőségszűrés, OCR és szövegújdonság, változatos kiválasztás
  candidates.py   fejezetenkénti jelölt képkockák, olvasási másolatok, áttekintő lapok
  render.py       ellenőrzés, Markdown és Word kimenet
  prompts/        írási szabályok (rules.md) és a fejezetcsomag sablonja (brief.md)
colab/whisperx_for_uploading_file.ipynb   Colab T4 GPU feliratkészítő jegyzetfüzet
```
