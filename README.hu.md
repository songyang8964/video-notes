# video-notes

[中文](README.md) | [English](README.en.md) | **Magyar**

Egy helyi előadás-, kurzus- vagy oktatóvideóból automatikusan **megosztható, az eredeti videó képkockáival illusztrált Markdown jegyzetet** készít.

- Az eredeti magyarázat sorrendjét és gondolatmenetét követi, teljes körűen kifejti az okokat, működést, feltételeket, lépéseket és példákat — nem összefoglaló és nem leirat.
- Automatikusan kiválasztja a **kulcsfontosságú képkockákat** az eredeti videóból (fejezetenként legfeljebb 8, minden dia csak egyszer), és az általuk alátámasztott magyarázat mellé helyezi őket; a képaláírás leírja a kép tartalmát és megadja az eredeti videóbeli időpontot.
- Javítja a nyilvánvaló beszédfelismerési hibákat, kihagyja a csevegést, a szervezési részeket és a témához nem tartozó bemutatókat, és soha nem ír „az előadó megemlíti…” típusú közvetítő mondatokat.
- Minden fejezet gépi ellenőrzésen és független áttekintésen megy át, utána a teljes dokumentumot még egyszer átnézi; egy megszakított futás ott folytatódik, ahol abbamaradt.

> 0.1-es verzió. A folyamatot és az ellenőrzéseket egységtesztek és valódi videórészletek igazolják; a szöveg minősége a választott nagy nyelvi modelltől függ, és még nem készült róla rendszeres kiértékelés.

## Gyors kezdés

```bash
# 1. Telepítés (egyszer)
git clone https://github.com/songyang8964/video-notes.git
cd video-notes
powershell -ExecutionPolicy Bypass -File install.ps1 -AddToPath   # macOS/Linux: sh install.sh

# 2. Jelentkezz be egy modell-CLI-be (egyszer): `claude`, majd /login; vagy `codex login`
video-notes doctor            # a környezet ellenőrzése

# 3. Minden alkalommal: lépj be a videót (és az azonos nevű .srt-t) tartalmazó mappába
cd D:\kurzus
video-notes                   # elkészül az output\kurzus\培训笔记.md és az assets mappa
```

```mermaid
flowchart LR
    A["kurzus.mp4<br/>+ azonos nevű .srt (opcionális)"] --> B["video-notes"]
    B --> C["培训笔记.md"]
    B --> D["assets/ képkockák"]
    B --> E["report.md<br/>ellenőrzési jelentés"]
```

---

## Tartalom

1. [Gyors kezdés](#gyors-kezdés)
2. [Eredmény](#eredmény)
3. [Telepítés](#telepítés)
4. [Használat](#használat)
5. [Feliratok készítése Colab T4 GPU-n](#feliratok-készítése-colab-t4-gpu-n)
6. [Videókontextus (opcionális)](#videókontextus-opcionális)
7. [Gyakori kérdések](#gyakori-kérdések)
8. [Működés](#működés)
9. [Minőségbiztosítás](#minőségbiztosítás)
10. [Kimenet és belső naplók](#kimenet-és-belső-naplók)
11. [Beállítások](#beállítások)
12. [Időigény és erőforrások](#időigény-és-erőforrások)
13. [Fejlesztés és tesztek](#fejlesztés-és-tesztek)

---

## Eredmény

A videót tartalmazó mappában futtatott egyetlen parancs eredménye:

```
output/<videó neve>/
  培训笔记.md      ← a teljes jegyzet, szabványos Markdown, a képek relatív útvonallal
  assets/          ← a jegyzetben használt képkockák eredeti felbontásban
```

A teljes `output/<videó neve>/` mappa változtatás nélkül másolható, tömöríthető vagy feltölthető; egyetlen kép sem vész el.

Példa a jegyzet szerkezetére:

```markdown
# A kurzus címe

## 1. fejezet: … (a tartalom alapján automatikusan elnevezve)

### A témakör
Teljes magyarázat: háttér → elv → feltételek → lépések → eredmény → buktatók …

![Topológia: a három központi eszköz összekapcsolása](assets/C01F00127742.jpg)

*Topológia: a három központi eszköz összekapcsolása (eredeti videó 00:02:07.797)*

### B témakör
…
```

---

## Telepítés

### Követelmények

- Python 3.10 vagy újabb
- FFmpeg: `ffmpeg` és `ffprobe` a PATH-on
- Legalább egy bejelentkezett modell-parancssori eszköz:
  - [Claude Code](https://docs.anthropic.com/claude-code): telepítés után futtasd egyszer a `claude` parancsot, és jelentkezz be a `/login` paranccsal
  - vagy Codex CLI: telepítés után futtasd a `codex login` parancsot

### Az asztali alkalmazások használata (Claude asztali / Codex asztali)

Az eszköz parancssori programon keresztül hívja a modelleket, de **a CLI-t nem kell külön telepíteni**: az asztali alkalmazások ugyanezt a programot tartalmazzák, és az eszköz automatikusan megtalálja. Keresési sorrend:

1. a `video-notes setup --claude-path <útvonal>` / `--codex-path <útvonal>` beállítással megadott program;
2. a rendszer PATH-ján lévő `claude` / `codex`;
3. az asztali alkalmazáshoz mellékelt példány:
   - Claude asztali: `%APPDATA%\Claude\claude-code\<verzió>\claude.exe` (a legújabb verziót választja)
   - Codex asztali: `%LOCALAPPDATA%\OpenAI\Codex\bin\codex.exe`

Ha nincs megadott útvonal, és több verzió is található (pl. egy a PATH-on és egy az asztali alkalmazásban), a **legújabb verziót** használja, mert a régebbiek nem feltétlenül ismerik az aktuális modellneveket. A `video-notes doctor` megmutatja, melyik programot használja és honnan származik.

Megjegyzés: az asztali alkalmazásban történt bejelentkezés csak az alkalmazáson belül érvényes. Ha a mellékelt programot terminálból hívod, saját bejelentkezés kell hozzá, egyszer:

```bash
"%APPDATA%\Claude\claude-code\<verzió>\claude.exe"        # utána írd be: /login
"%LOCALAPPDATA%\OpenAI\Codex\bin\codex.exe" login
```

#### Használat az asztali alkalmazás beszélgetéséből (ügynökmód, ajánlott)

A Claude asztali vagy a Codex asztali alkalmazás beszélgetésében a beszélgetés MI-je **maga** olvassa a feliratokat, nézi a képeket és ír; a `video-notes` csak a modellt nem igénylő lépéseket végzi (felismerés, fejezetek, képkockák, gépi ellenőrzés, összeállítás). Nem indul `claude` / `codex` CLI, nem kell külön bejelentkezni, és ugyanazok a feliratok és képek nem kerülnek újra meg újra a modellhez.

1. Nyisd meg a **Claude asztali → Code** lapot vagy a **Codex asztali alkalmazást**, és válaszd munkamappának a videót tartalmazó mappát.
2. Írd be a beszélgetésbe például:

   ```text
   Készíts jegyzetet a mappában lévő videóból a video-notes ügynökmódjával: futtasd a video-notes prepare parancsot, minden fejezethez írd meg a brief.md alapján a topics.csv, knowledge.csv, chapter.md és review.md fájlt, majd futtasd a video-notes assemble parancsot, amíg minden ellenőrzés sikeres.
   ```

3. Az MI egymás után elkészíti a fejezeteket, és megadja a jegyzet útvonalát.

Megjegyzések:

- Ne kérd a beszélgetés MI-jét, hogy futtassa a `video-notes` parancsot (automatikus mód) és figyelje a haladását: így a beszélgetés és a CLI is fogyasztja a keretet, és ugyanazt a tartalmat kétszer dolgozza fel. Felügyelet nélküli futtatáshoz használd a saját terminálodat.
- Hosszú videó több beszélgetésben is elkészülhet: a csomagok és a már megírt fejezetek a munkamappában maradnak, az `assemble` megnevezi a hiányzó vagy hibás fejezeteket.

Opcionális:

- Tesseract OCR: javítja a jelöltek rangsorolását (terminálok, kódok és táblázatok esetén a leghasznosabb)
- Helyi beszédfelismerés (csak felirat és Colab nélkül kell): `pip install faster-whisper`, vagy a WhisperX telepítése

### Telepítési lépések

```bash
git clone https://github.com/songyang8964/video-notes.git
cd video-notes
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -AddToPath
```

Az `-AddToPath` hozzáadja az eszközt a felhasználói PATH-hoz, így a `video-notes` bármelyik mappában működik (utána nyiss új terminált). Nélküle az eszköz csak ennek a mappának a `.venv` környezetébe települ.

macOS / Linux:

```bash
sh install.sh
```

Az utasítás szerint add hozzá a `.venv/bin` mappát a PATH-hoz.

### Első beállítás

```bash
video-notes setup --backend claude       # vagy codex
video-notes setup --model claude-opus-5-5 --effort medium   # opcionális: az aktuális háttérrendszer modellje és gondolkodási szintje
video-notes setup --language Magyar      # a jegyzet nyelve, alapértelmezés: 中文 (kínai)
video-notes setup --tesseract "C:\Program Files\Tesseract-OCR\tesseract.exe" --ocr-langs eng+hun   # opcionális
video-notes doctor                        # a környezet ellenőrzése, valódi bejelentkezési próbával
```

Alapértelmezett modellek (ha nincs mást beállítva):

| Háttérrendszer | Modell | Gondolkodási szint |
| --- | --- | --- |
| `claude` (Claude Code / Claude asztali) | `claude-opus-5-5` (Claude Opus 5.5) | `medium` |
| `codex` (Codex CLI / Codex asztali) | `gpt-6.1-sol` (GPT sol 6.1) | `medium` |

Mindkét háttérrendszer megjegyzi a saját modelljét és szintjét, így váltáskor egyik beállítás sem vész el. A modell vagy a szint módosítása után a korábban gyorsítótárazott modelleredmények nem kerülnek újrahasználatra (a gyorsítótár kulcsa tartalmazza a modellt és a szintet).

A `doctor` jelenti: FFmpeg, Python-függőségek, telepítve és bejelentkezve van-e a modell-CLI, OCR, beszédfelismerő háttérrendszerek és a beállítófájl helye. Ha valami kötelező hiányzik, nem nulla kóddal lép ki, és leírja a javítás módját.

---

## Használat

1. Tedd a videót egy mappába (ha van felirat, az azonos nevű `.srt` mellé).
2. Nyiss terminált abban a mappában, és futtasd:

```bash
video-notes                         # a mappában lévő egyetlen .mp4 feldolgozása
video-notes "kurzus.mp4"            # több videó esetén válassz egyet
video-notes "kurzus.mp4" --srt "felirat.srt"
video-notes "kurzus.mp4" --backend codex          # most a másik modellel
video-notes "kurzus.mp4" --output D:\jegyzetek    # kimeneti gyökérmappa (alapértelmezés ./output)
video-notes "kurzus.mp4" --context hatter.md      # videókontextus fájl
video-notes "kurzus.mp4" --fallback-backend codex  # a Claude keretének kimerülésekor a Codex folytatja
```

3. Az előrehaladás a terminálban látszik (stderr); az utolsó sor (stdout) a jegyzet útvonala.
4. Megszakítás után (Ctrl+C, hálózati hiba, keretkimerülés, leállítás) **futtasd újra ugyanazt a parancsot**: a képernyőfelismerés, a képkockák és minden modellhívás gyorsítótárban van, semmi nem fut és nem számlázódik kétszer.

Hogyan működik a folytatás:

```mermaid
flowchart LR
    R1["Első futás"] --> CA[("Gyorsítótár<br/>.work/video-notes")]
    CA --> R2["Újrafuttatás megszakítás után<br/>csak a maradék fut"]
    CA --> R3["Módosított kontextus<br/>csak az érintett rész fut"]
```

Minden modellhívás a „prompt szövege + képek tartalma” hash-e szerint kerül gyorsítótárba: változatlan bemenetnél az előző eredmény jön; megváltozott bemenetnél (pl. módosított videókontextus) új hívás történik.

Szabályok:

- Videó megadása nélkül csak az aktuális mappa legfelső szintjén lévő `.mp4` fájlokat veszi figyelembe (kis- és nagybetű mindegy), az almappákat nem; ha nincs videó vagy több is van, tájékoztat és kilép, nem találgat.
- Online hivatkozások (http/https) még nem támogatottak; előbb töltsd le a videót.
- Az eredeti videó és a felirat csak olvasható, soha nem módosul.
- Ha kézzel szerkesztetted a korábban elkészült `培训笔记.md` fájlt, az új futás **nem írja felül**; az új eredmény `培训笔记.<idő>.md` néven kerül mentésre.

### Ügynökmód: prepare / assemble

```bash
video-notes prepare "kurzus.mp4"    # ellenőrzés, fejezetek, jelölt képkockák; fejezetenként egy brief.md (modellhívás nélkül)
video-notes assemble "kurzus.mp4"   # ugyanazok a gépi ellenőrzések, mint automatikus módban; csak hibátlanul állít össze (modellhívás nélkül)
```

- A `prepare` minden fejezet csomagját a munkamappa `agent/Cnn/brief.md` fájljába írja: az általános írási szabályok, a fejezet feliratai, a jelöltek táblázata (idő, közben látható feliratok, eredeti kép útvonala) és az áttekintő lapok.
- Az író (a beszélgetés MI-je vagy egy ember) ugyanabba a mappába írja a `topics.csv`, `knowledge.csv` és `chapter.md` fájlt (az automatikus móddal azonos formátumban), valamint a `review.md` önellenőrzési naplót: mit ellenőrzött az eredetin minden beszúrt képnél, és hol van kifejtve minden fontos tudáselem.
- Ha az `assemble` sikertelen, a fejezetek hibáit az `agent/check.md` fájlba írja, és nem ad ki jegyzetet; javítás után futtasd újra.

### Kilépési kódok

| Kilépési kód | Jelentés |
| --- | --- |
| 0 | Siker, minden fejezet átment az ellenőrzésen és az áttekintésen |
| 1 | Függőségi, modell- vagy feldolgozási hiba; vagy a jegyzet elkészült, de egyes fejezetek nem mentek át az áttekintésen (lásd a jelentést) |
| 2 | Kétértelmű vagy érvénytelen bemenet: nincs videó, több videó, hiányzó fájl, online hivatkozás, elutasított felirat |
| 130 | A felhasználó megszakította (az előrehaladás mentve) |

---

## Feliratok készítése Colab T4 GPU-n

Helyi NVIDIA GPU nélkül egy 2 órás videó helyi beszédfelismerése órákig tarthat. A Google Colab ingyenes T4 GPU-ja ajánlott:

```mermaid
sequenceDiagram
    participant PC as A számítógéped
    participant CO as Colab T4 GPU
    PC->>PC: Hang kinyerése (ffmpeg)
    PC->>CO: Hang feltöltése
    CO->>CO: WhisperX átírás + igazítás
    CO-->>PC: .srt letöltése
    PC->>PC: video-notes futtatása
```

1. (Ajánlott) Helyben csak a hangot nyerd ki — kicsi, gyorsan feltölthető —, a videó fájlnevét megtartva:

   ```bash
   ffmpeg -i "kurzus.mp4" -vn -ac 1 -c:a aac -b:a 64k "kurzus.m4a"
   ```

2. Nyisd meg a tároló [`colab/transcribe.ipynb`](colab/transcribe.ipynb) jegyzetfüzetét a Colabban (Colab → Fájl → Jegyzetfüzet megnyitása → GitHub, vagy töltsd fel a fájlt).
3. Menü: **Futtatókörnyezet → Futtatókörnyezet típusának módosítása → T4 GPU**.
4. Válaszd ki a forrást a „paraméterek” cellában:
   - `SOURCE = 'upload'`: a cella futásakor a számítógépedről választasz fájlt; a végén az SRT automatikusan letöltődik;
   - `SOURCE = 'drive'`: fájl a Google Drive-ról; az SRT ugyanabba a mappába kerül (a Google Drive asztali kliensével egyenesen a helyi videó mellé szinkronizálható).
   Opcionálisan töltsd ki az `INITIAL_PROMPT` (szakkifejezések) és a `LANGUAGE` értéket.
5. Futtasd az összes cellát fentről lefelé. A jegyzetfüzet WhisperX-et használ: először átírás (alapértelmezés: large-v3), majd szószintű igazítás, végül a bemenettel azonos nevű `.srt`.
6. Tedd a `kurzus.srt` fájlt a helyi `kurzus.mp4` mellé, és futtasd a `video-notes` parancsot.

Megjegyzés: a WhisperX NVIDIA GPU-t használ; a TPU futtatókörnyezet nem gyorsítja, mert ott valójában a CPU-n fut.

---

## Videókontextus (opcionális)

Tegyél a videó mellé egy `<videó neve>.context.md` fájlt (vagy `video-notes.context.md` fájlt az aktuális mappába, vagy add meg `--context` kapcsolóval), amely leírja a kurzust. Minden modelllépés használja. Nélküle a modell a feliratokból következtet a témára, a terjedelemre és a szakkifejezésekre.

```markdown
---
title: Bevezetés az elosztott rendszerekbe (3. előadás)
language: hu
include_times: 00:12:30, 00:41:05.500
forbid: az előadó, ebben a videóban
asr_preset: networking
---
Az előadás témája a konszenzusalgoritmusok. A fogalmak írásmódja: Raft, Paxos, Leader.
A feliratban szereplő „rafting” a Raft felismerési hibája.
Az első tíz perc eszközbeállítása és az óra utáni jelentkezési kérdések nem tartoznak a témához.
```

| Mező | Szerep |
| --- | --- |
| `title` | A jegyzet címe (alapértelmezés: a videó fájlneve) |
| `language` | Feliratnyelv-preferencia (az azonos nevű feliratok közüli választáshoz; a beszédfelismerés is megkapja) |
| `include_times` | Képernyő-időpontok, amelyeknek jelöltté kell válniuk, vesszővel elválasztva, `HH:MM:SS(.mmm)` vagy másodperc |
| `forbid` | Szavak, amelyek nem szerepelhetnek a szövegben, vesszővel elválasztva (gépileg ellenőrizve) |
| `asr_preset` | Szókincs-előbeállítás a helyi átíráshoz (jelenleg `networking`); vagy írd meg közvetlenül az `asr_prompt` mezőben |

A törzs (a `---` után) szabad szöveg: téma, terjedelem, megerősített szakkifejezések és gyakori felismerési hibák, kizárandó tartalom.

---

## Gyakori kérdések

**Az asztali alkalmazást szeretném használni, nem akarok CLI-t telepíteni**: futtasd a `video-notes doctor` parancsot; az eszköz megtalálja az asztali alkalmazáshoz mellékelt programot. Ha azt írja, hogy nem vagy bejelentkezve, jelentkezz be egyszer az [Az asztali alkalmazások használata](#az-asztali-alkalmazások-használata-claude-asztali--codex-asztali) részben leírt parancsokkal.

**A `doctor` szerint a modell „not usable” / lejárt a bejelentkezés**: futtasd a `claude` és `/login` (vagy `codex login`) parancsot egy terminálban, majd ismét a `video-notes` parancsot; ott folytatja, ahol abbahagyta.

**„Several videos found”**: a mappában egynél több `.mp4` van; nevezz meg egyet: `video-notes "fajl.mp4"`.

**Elutasított felirat**: olvasd el az okot a terminálban (túl kevés sor, alacsony lefedettség, rossz hossz, gördülő felirat). Használd a megfelelő feliratot, vagy töröld, hogy az eszköz maga írja át.

**Egy fejezet áttekintése REVISE eredményt mutat**: a jegyzet így is elkészül. Olvasd el a `.work/video-notes/…/<fejezet>/review.md` fájlt, és ha kell, javítsd kézzel a jegyzetet; egy új futás nem írja felül a módosításaidat.

**Egy képkockán nehezen olvasható az apró szöveg**: az `assets/` eredeti felbontású képeket tartalmaz. Ha egy kulcsfontosságú kép nem került kiválasztásra, add hozzá az időpontját a videókontextus `include_times` mezőjéhez, és futtasd újra (csak az érintett részek futnak újra).

**A jegyzet nem a kívánt nyelven készült**: `video-notes setup --language Magyar`, vagy állítsd be az `output_language` értékét a beállítófájlban.

---

## Működés

A teljes folyamatot helyben vezérli a program. Csak „a tartalom megértése, a képek kiválasztása, az írás és az ellenőrzés” hív nagy nyelvi modellt (Claude Code CLI vagy Codex CLI, a saját, bejelentkezett fiókoddal).

```mermaid
flowchart TD
    V["kurzus.mp4"] --> S{"Van felirat?"}
    SUB["azonos nevű vagy<br/>beágyazott felirat"] --> S
    S -- igen --> CUES["Felirat"]
    S -- nem --> ASR["Beszédfelismerés<br/>helyben vagy Colab"] --> CUES
    V --> DET["② Képernyő-felismerés<br/>videónként egyszer"]
    CUES --> CH["③ Fejezetek<br/>kb. 10 perc"]
    DET --> CH
    CH --> P
    subgraph LOOP["Minden fejezet"]
        P["④ Témakörök<br/>tudásleltár"] --> C["⑤ Jelölt képkockák"]
        C --> SEL["⑥ Kulcsképek<br/>legfeljebb 8"]
        SEL --> W["⑦ Írás"]
        W --> CHK{"⑧ Ellenőrzés<br/>áttekintés"}
        CHK -- sikertelen --> W
    end
    CHK -- sikeres --> G["⑨ Teljes áttekintés"]
    G --> OUT["⑩ Kész jegyzet"]
```

A „modell” jelölésű lépések nagy nyelvi modellt hívnak; minden más helyi feldolgozás (FFmpeg, PyAV, képfeldolgozás).

### 1. Feliratválasztás és átírás

Az eszköz megkeresi „a videóhoz valóban tartozó szöveget”:

- A jelölt források sorrendben: a `--srt` kapcsolóval megadott fájl; a videóval azonos nevű `.srt` / `.vtt` fájlok (pl. `kurzus.srt`, `kurzus.en.vtt`); a videóba ágyazott szöveges feliratsávok. Az azonos nevű feliratok nyelv szerint rangsorolódnak: kért nyelv → nyelvjelölés nélküli → egyéb nyelvek.
- Ezeket a feliratokat elutasítja, és az okot rögzíti: 5-nél kevesebb sor; a játékidő kevesebb mint 30%-át fedi le (általában csak az idegen nyelvű részeket fordító „kényszerített” sáv); az időpontok messze túlnyúlnak a videón (másik vágáshoz készült); a sorok több mint 30%-a az előző sor szövegével kezdődik (duplikátumszűrés nélküli, gördülő automatikus felirat).
- A WebVTT gördülő feliratait már beolvasáskor megtisztítja (minden sor megismétli az előzőt).
- A `--srt` kapcsolóval megadott felirat kifejezett kérés: ha nem felel meg, a futás leáll, ahelyett hogy csendben mást használna.
- Ha nincs használható felirat, helyben átírja a beszédet (WhisperX → faster-whisper → openai-whisper, ami telepítve van). NVIDIA GPU nélkül a helyi átírás lassú; használd inkább a [Colab T4 GPU](#feliratok-készítése-colab-t4-gpu-n) lehetőséget.

### 2. Képernyőváltások felismerése

Egy előadás fő vizuális elemei a diák, ábrák, kódok és terminálok. A felismerés egyetlen kérdésre felel: **mikor változik a kép?**

- **Az átfedő sávok kizárása**: először minden képpontsor változási gyakoriságát méri. A kép felső vagy alsó szélén lévő, a tartalomnál sokkal gyakrabban változó sávokat (beégetett feliratok, futó szalagcímek) kizárja; különben minden új feliratsor új diának tűnne.
- **Három jel** (képkockánként, kis felbontású szürkeárnyalatos képen mérve):
  - *Horgonyeltolódás*: az aktuális kocka átlagos eltérése „az utolsó nyugalmi képtől” — a fokozatosan betelő táblát vagy a soronként megjelenő kódot fogja meg;
  - *Vágási terület*: a szomszédos kockák között erősen megváltozott képpontok aránya — diaváltásokat és vágásokat fog meg;
  - *Pillanatnyi változási ráta*: a szomszédos kockák átlagos eltérése — mozgást érzékel, és eldönti, hogy „a kép megnyugodott, rögzíthető”.
- **Kiegészítő érzékelő**: egy opcionális PySceneDetect adaptív menet további vágási időpontokat ad.
- **Események és rögzítési pontok**: a 0,5 másodpercen belüli csúcsok egy „képernyőváltássá” olvadnak össze. Minden váltáshoz két nyugalmi kockát keres: a váltás előttit (a régi kép **kész állapota**) és utánit (az új kép **kezdete**). Ha egy kép kezdete és kész állapota szinte azonos (SSIM ≥ 0,93), csak a kezdetet tartja meg — pl. statikus dia; ha jól láthatóan eltérnek, mindkettőt — pl. lépésenként felépülő ábra vagy begépelt parancs.
- **Képkockasebesség a valódi időbélyegekből**: a képernyőfelvételek gyakran változó képkockasebességűek (névlegesen 60 fps, valójában kb. 15 fps). Minden időpont a valódi időbélyeget használja.
A felismerés menete:

```mermaid
flowchart TD
    F["Minden kocka<br/>kis szürke képként"] --> B["Felirat- és<br/>szalagcímsáv ki"]
    B --> S1["Horgonyeltolódás"]
    B --> S2["Vágási terület"]
    B --> S3["Pillanatnyi változás"]
    S1 & S2 & S3 --> M["Összevonás<br/>egy képernyőváltássá"]
    AD["Kiegészítő érzékelő"] --> M
    M --> E["Nyugalmi kockák<br/>előtte és utána"]
    E --> K{"Szinte azonos?"}
    K -- igen --> ONE["Csak a kezdet"]
    K -- nem --> TWO["Kezdet + kész"]
```

Milyen képkockák keletkeznek egy diaváltáskor (egy dia, amelynek pontjai egyenként jelennek meg):

| Pillanat | A képernyőn | Képkocka |
| --- | --- | --- |
| Megjelenik az A dia | csak a cím | ① A kezdete |
| A pontok egyenként jelennek meg | lassan gyűlő változás, nem új dia | — |
| Közvetlenül a váltás előtt | minden pont látszik | ② A kész |
| A váltás után, nyugalmi állapotban | B dia | ③ B kezdete |

- ① és ② jól láthatóan eltér, így mindkettő jelölt lesz; a modell általában a teljesebb ②-t választja.
- Ha A statikus dia, ① és ② szinte azonos, és csak ① marad meg.
- Ha A sokáig marad a képernyőn, 20 másodpercenként egy „szívverés” kocka is készül, hogy apró változások se maradjanak ki.

- **Teljesítmény**: a teljes videót kétszer dekódolja a méréshez és egyszer a kiegészítő érzékelőhöz; minden kezdet/kész összehasonlítás **egyetlen soros dekódolással** történik, nem több ezer véletlenszerű ugrással. Az eredmények gyorsítótárba kerülnek, és fejezetenként újrahasznosulnak.

### 3. Fejezetek

A cél fejezetenként kb. 10 perc (állítható). Minden ablak utolsó negyedében a **leghosszabb szünetnél**, lehetőleg **képernyőváltás közelében** lévő felirathatáron vág, hogy egy témakört ne vágjon ketté.

### 4. Témakörök és tudásleltár

A modell elolvassa a fejezet feliratait (előtte és utána néhány sor kontextussal), és két dolgot végez el:

- minden feliratsort egy **témakörhöz** rendel, vagy indoklással témán kívülinek jelöl; semmi nem hiányozhat, fedhet át vagy állhat rossz sorrendben (a program ellenőrzi; hibás kimenetet újra kell írni);
- részletes **tudásleltárt** készít: definíciók, működés, feltételek, okok, összehasonlítások, példák, parancsok, konfigurációs lépések, ellenőrzési eredmények, kockázatok, visszaállítás és értékes kérdések-válaszok, mindegyik fontos (important) vagy kiegészítő jelöléssel.

### 5. Jelölt képkockák

- A jelölt időpontok forrásai: a képernyőváltások kezdő és kész kockái; hosszan változatlan képeken belül 20 másodpercenként egy minta (hogy az olyan apró változás se vesszen el, mint egy új sor a terminálban); a videókontextusban megadott kötelező időpontok; a modell által kért további időpontok.
- Minden jelöltet **az eredeti videóból, teljes felbontásban, a valódi időbélyeg alapján** rögzít, és feljegyzi a tényleges kocka időpontját.
- **Minőségszűrés**: a túl sötét, túl világos, elmosódott (alacsony Laplace-variancia, pl. áttűnés) vagy szinte üres kockákat kiszűri, és az okot rögzíti.
- **OCR (opcionális, Tesseract kell hozzá)**: a tartalmi területet és a feliratsávot külön ismeri fel, és „szövegújdonságot” számol: a megmaradó új tartalmi szöveg sokat ér; a felvillanó vagy feliratbeli változás keveset.
- **Ismétlődő képek összevonása**: a jelölteket 320×180-as szürkeárnyalatos képként veti össze; ha a képpontok kevesebb mint 1%-a változott érdemben, ugyanaz a kép, és csak egy marad meg (elsőként a kötelező időpontok, aztán a kész állapot). A nagyjából egy másodperc alatt átlapozott diák is megmaradnak. Minden különböző kép a modell elé kerül; csak ha egy fejezetben 32-nél több van, akkor szűkíti 32-re a változatosság szerinti válogatás (változás erőssége, szövegújdonság, élesség, hasonlóság a már kiválasztottakhoz, időbeli szórás). A modell által kért kockák a meglévő jelöltekhez adódnak hozzá.
- Minden jelölthöz csatolja, **mi hangzott el, amíg az a kép a képernyőn volt** (feliratsorszám-tartomány), hogy a modell megítélhesse, illik-e a kép a szöveghez.
- A modell 1280 képpontos olvasási másolatokat lát a keret kímélése érdekében; az írás és az ellenőrzés az eredetiket használja, hogy a parancsok és számok olvashatók maradjanak.

```mermaid
flowchart TD
    A["Jelölt időpontok"] --> B["Eredeti kockák"]
    B --> Q{"Jó minőségű?"}
    Q -- nem --> R1["Kiesik"]
    Q -- igen --> D{"Azonos egy<br/>meglévővel?"}
    D -- igen --> R2["Összevonva"]
    D -- nem --> SH["Szűkített lista<br/>fejezetenként legfeljebb 32"]
    SH --> M["A modell választ<br/>legfeljebb 8 kulcsképet"]
    M --> N["A szövegbe kerül"]
```

### 6. Kulcsképek kiválasztása

A modell minden jelöltet megnéz a témakörökkel és a hozzájuk tartozó szöveggel együtt, és a **teljes fejezethez** választ kulcsképeket:

- csak az olvasó számára szükséges szerkezeti ábrákat, folyamatábrákat, összehasonlításokat, táblázatokat, kulcsparancsokat vagy eredményeket;
- diánként egyet, a legteljesebbet (általában a kész állapotot); címlap, tartalomjegyzék, csak szöveges dia és csak az előadót mutató kép nem kerül be;
- fejezetenként legfeljebb 8, egy fejezetnek lehet kép nélkül is; minden kép ahhoz a témakörhöz tartozik, amelyet magyaráz;
- a ki nem választott képek fontos információit (számok, konfiguráció, kapcsolatok) az írási lépés szövegben fejezi ki;
- ha a szöveg egyértelműen egy olyan kulcsfontosságú képre utal, amely hiányzik a jelöltek közül, a modell legfeljebb 3 további időpontot kérhet; ezeket rögzíti és még egyszer elbírálja.

### 7. Írás

A modell a feliratok, a tudásleltár és a kiválasztott eredeti képek alapján, az eredeti sorrendben írja meg a fejezetet: témakörönként egy szakasz, a képek a megfelelő magyarázat mellett; a témán kívüli anyag csak belső megjegyzésben, indoklással szerepel.

### 8. Ellenőrzés, áttekintés és javítás

Lásd: [Minőségbiztosítás](#minőségbiztosítás). Az áttekintés a hibákat „blokkoló” (hibás tény, szám vagy parancs, hiányzó fontos tudás, kép és szöveg eltérése, megalapozatlan következtetés, közvetítés) és „javaslat” (megfogalmazás) csoportba sorolja. Csak a blokkoló hibák vagy a sikertelen gépi ellenőrzés indít javítást; javítás után csak az előző kör blokkoló hibáit és a javítás által okozott új hibákat ellenőrzi újra, legfeljebb 2 körben.

### 9. A teljes dokumentum áttekintése

Miután minden fejezet elkészült, a modell végigolvassa a teljes dokumentumot, és ellenőrzi a fejezetek közötti átmeneteket, a fogalmak egységességét és a fontos tudás lefedettségét. Ez a kör nem lát képeket, ezért nem kérheti a számok „egységesítését”: különböző rendelkezésre állási zónák, eszközök vagy példák jogosan használhatnak eltérő értékeket. Az ismétlődő képeket már írás közben kezeli: minden fejezet írása és áttekintése megkapja a korábbi fejezetekben beszúrt képek listáját. A hibákat fejezetenként javítja; a javított fejezetnek is át kell mennie a gépi ellenőrzésen, különben az előző változat marad meg, és ez bekerül a jelentésbe.

### 10. Kimenet

Minden egyetlen Markdown fájlba kerül, a kiválasztott eredeti képek az `assets/` mappába másolódnak, a képaláírások megkapják az eredeti videóbeli időpontot, és elkészül a belső `report.md` jelentés.

---

## Minőségbiztosítás

### Gépi ellenőrzések (minden fejezet után; a hibás fejezet visszamegy javításra)

| Ellenőrzés | Szabály |
| --- | --- |
| Teljes feliratlefedettség | Minden feliratsor pontosan egy szakaszhoz tartozik, sorrendben, vagy indoklással kizártként jelölt; semmi nem hiányzik, ismétlődik vagy áll rossz helyen |
| Képek száma | Fejezetenként legfeljebb 8 (állítható), egy kép sem szerepel kétszer |
| Képek helye | Minden kép csak a saját témakörének szakaszában jelenhet meg; a képazonosítóknak létezniük kell |
| Tudáslefedettség | Minden fontos tudáselemnek egy létező szakaszra kell mutatnia a „tudástérképen”, vagy kizárási indoklással kell rendelkeznie |
| Részletesség | Szöveges karakterek (címek, kódblokkok és képek nélkül) ≥ 100 a magyarázat minden percére (állítható); a legalább 1 perces szakaszokban ≥ 50 percenként. Megállítja az összefoglalóként megírt fejezeteket |
| Írásmód | Nem lehet benne „az előadó / a beszélő / a videó megemlíti / a felirat szerint” típusú közvetítés, sem a kontextus `forbid` szavai |
| Belső azonosítók | A szövegben nem lehet C01, Chapter 8 vagy képkocka-azonosító típusú belső azonosító; a korábbi részekre a címükkel kell hivatkozni |
| Formátum | Minden fejezetnek van címe; nincs nyers képútvonal, nincs páratlan kódblokk; az összeállítás után nem marad helyőrző |

A fejezetenkénti ellenőrzési és javítási kör:

```mermaid
flowchart TD
    W["Írás"] --> C["Gépi ellenőrzés"]
    C --> R["Független áttekintés<br/>blokkoló / javaslat"]
    R --> J{"Van blokkoló hiba?"}
    J -- nem --> OK["A fejezet kész"]
    J -- igen --> F["Javítás, csak a blokkolók újraellenőrzése"]
    F --> C
    F -. 2 kör után is sikertelen .-> REP["Elkészül így is<br/>hibák a jelentésben"]
```

### Modellalapú áttekintés

- **Független fejezetenkénti áttekintés**: az áttekintő megkapja a feliratokat, a tudásleltárt, a kiválasztott eredeti képeket, a korábbi fejezetekben beszúrt képek listáját és a vázlatot, és a hibákat blokkoló és javaslat csoportba sorolja: teljesen ki van-e fejtve a fontos tudás (egy kulcsszó nem elég), egyeznek-e a tények és számok a feliratokkal és a képekkel, alátámasztja-e minden kép a mellette lévő szöveget, sérülnek-e a képszabályok (a fejezetek közötti ismétlést is beleértve), van-e közvetítő vagy témán kívüli tartalom.
- **Újraellenőrzés**: javítás után csak az előző kör blokkoló hibáit és a javítás által okozott hibákat nézi; nincs újabb teljes áttekintés, amely korábban a végtelenségig új megfogalmazási megjegyzéseket hozott.
- **A teljes dokumentum áttekintése**: fejezetek közötti átmenetek, fogalmak egységessége, a fontos tudás lefedettsége.

### Korlátok

- Az áttekintést is nagy nyelvi modell végzi. A nyilvánvaló hiányokat, hibákat és formai problémákat észreveszi, de nem helyettesíti az emberi ellenőrzést.
- Az, hogy „tényleg kulcsfontosságú-e ez a kép” és „jól javította-e ki ezt a felismerési hibát”, végső soron a modellen múlik; fontos anyagoknál szúrópróbaszerűen nézd át a `report.md` által jelzett fejezeteket.
- Az ügynökmódban nincs független áttekintő: az `assemble` csak a gépileg ellenőrizhető dolgokat és azt tudja ellenőrizni, hogy a `review.md` minden képhez és minden fontos tudáselemhez tartalmaz-e ellenőrzési bejegyzést; hogy ezek valóban megtörténtek-e, az írón múlik.

---

## Kimenet és belső naplók

```
<aktuális mappa>/
  output/<videó neve>/培训笔记.md  + assets/      ← megosztásra
  .work/video-notes/<hash>/                       ← belső naplók, törölhetők (a folytatási gyorsítótár elvész)
    source.json            bemeneti hash-ek, a felirat forrása és a választás oka, modell
    detect/                képernyőfelismerési jelek és eredmény-gyorsítótár
    C01/ C02/ ...          fejezetenként: témakörök, tudásleltár, jelöltek és indoklás, képválasztás, vázlat, áttekintés, ellenőrzőpont
    agent/                 ügynökmód: fejezetenkénti brief.md, az ügynök által írt fájlok, check.md
    model-calls/           minden modellhívás teljes promptja, válasza, naplója és valós tokenhasználata
    global-review.csv      a teljes dokumentum áttekintésének megállapításai
    report.md              futási jelentés: fejezetenkénti eredmények, megoldatlan problémák, korlátozások, hívások, tokenek, időigény
```

Windowson, ha a videó mappájának útvonala nagyon mély (például egy értekezlet-alkalmazás felvételi mappája), a belső naplók a `%USERPROFILE%\.video-notes\work\<hash>` mappába kerülnek, hogy az útvonalak 260 karakter alatt maradjanak. Egy munkamappát egyszerre csak egy futás használhat.

A `report.md` kifejezetten felsorolja a korlátozott működést, pl. hiányzó OCR, automatikus átírás használata, feliratproblémák, a kép csak egy adott időpontig dekódolható, a gépi ellenőrzés által elutasított teljes dokumentumbeli javítás.

---

## Beállítások

A beállítófájl: `~/.video-notes/config.json` (Windows: `%USERPROFILE%\.video-notes\config.json`; a régi `%APPDATA%\video-notes\config.json` helyet továbbra is beolvassa). Nem az AppData alatt van, mert az olyan asztali alkalmazások, mint a Claude és a Codex, mind saját privát mappába irányítják át az AppData-t, így a különböző alkalmazásokból indított futások más beállításokat látnának. A gyakori elemek a `video-notes setup` paranccsal módosíthatók; a többihez szerkeszd a JSON-t.

| Kulcs | Alapértelmezés | Leírás |
| --- | --- | --- |
| `backend` | `claude` | Modell-háttérrendszer: `claude` vagy `codex` |
| `claude_model` / `claude_effort` | `claude-opus-5-5` / `medium` | A Claude háttérrendszer modellje és gondolkodási szintje (low, medium, high, xhigh, max) |
| `codex_model` / `codex_effort` | `gpt-6.1-sol` / `medium` | A Codex háttérrendszer modellje és gondolkodási szintje (minimal, low, medium, high) |
| `claude_path` / `codex_path` | üres | A CLI útvonala (üres = előbb a PATH, aztán az asztali alkalmazás mellékelt példánya) |
| `output_language` | `中文` | A jegyzet nyelve |
| `max_images` | 8 | Képek maximális száma fejezetenként |
| `shortlist` | 32 | A modellnek fejezetenként mutatott különböző képek legnagyobb száma (csak fölötte szűkít) |
| `chapter_minutes` | 10 | Célzott fejezethossz (perc) |
| `use_adaptive` | true | A PySceneDetect kiegészítő érzékelő használata (jobb lefedés, kicsit lassabb) |
| `review_cycles` | 2 | „Javítás → újraellenőrzés” körök maximális száma az első áttekintés után |
| `parallel_chapters` | 3 | Egyszerre feldolgozott fejezetek száma |
| `fallback_backend` | üres | A keret kimerülésekor átvevő háttérrendszer (`claude` vagy `codex`); a futás elején ellenőrzi a használhatóságát |
| `min_chars_per_minute` | 100 | A részletesség alsó határa |
| `tesseract` / `ocr_langs` | üres / `eng` | Az OCR program útvonala és nyelvei |
| `asr_backend` / `asr_model` / `whisperx` | automatikus / `large-v3` / üres | Helyi átírási beállítások |

---

## Időigény és erőforrások

Példa: egy kb. 2 óra 40 perces, 1080p felbontású, felirattal rendelkező képernyőfelvétel (átlagos laptop CPU):

| Szakasz | Idő |
| --- | --- |
| Jelmérés (két dekódolás) | kb. 10 perc |
| Kiegészítő érzékelő (egy dekódolás) | kb. 20 perc |
| Kezdet/kész összehasonlítás (egy soros dekódolás) | kb. 10 perc |
| Jelöltek rögzítése és szűrése | negyedórától félóráig, a képernyőváltások számától függően |
| Modellhívások (fejezetenként kb. 5–8, plusz egy teljes dokumentum-áttekintés) | több óra, a modell sebességétől és a keretektől függően |

- A képernyőfelismerés csak az első futáskor történik meg; utána a gyorsítótár teljesen újrahasznosul.
- Alapértelmezés szerint 3 fejezet fut párhuzamosan; minden modellhívás csak a képolvasó eszközt és egy rövid rendszerpromptot tölt be, a `report.md` pedig rögzíti a valós tokenhasználatot. Megszakítás után a kész hívások nem ismétlődnek (a két háttérrendszer eredményei kölcsönösen újrahasznosulnak).
- Memória: a felismerés kockánként, folyamatosan dolgozik, így a memóriahasználat nem nő a videó hosszával. Zárd be a sok memóriát használó programokat, hogy elkerüld a memóriahiány miatti dekódolási hibákat (az eszköz újrapróbálkozik, és rögzíti őket a jelentésben).

---

## Fejlesztés és tesztek

```bash
python -m unittest tests.test_video_notes -v      # egységtesztek: feliratok beolvasása és választása, fejezetek, ellenőrzési szabályok, algoritmusok, kimenetvédelem
python tests/integration_scripted.py <mappa>       # a teljes folyamat futtatása valódi videórészleten, szkriptelt modellel (valódi modellhívás nélkül)
python tests/integration_agent_mode.py <mappa>     # ügynökmód: prepare → szkriptelt fejezetek → assemble (modellhívás nélkül)
```

A forráskód szerkezete:

```
src/video_notes/
  cli.py          parancssor: videó keresése, kapcsolók, setup, doctor, kilépési kódok
  pipeline.py     vezérlés, fejezetek, képválasztás, írás, áttekintés, teljes dokumentum-áttekintés, kimenetvédelem
  subtitles.py    feliratjelöltek és elutasítási szabályok, VTT beolvasás
  transcribe.py   helyi beszédfelismerés (WhisperX / faster-whisper / openai-whisper)
  detect/         képernyőváltás-felismerés (átfedő sáv, három jel, események, kezdet/kész párok)
  vision/         minőségszűrés, OCR és szövegújdonság, változatosság szerinti válogatás
  candidates.py   fejezetenkénti jelöltek, olvasási másolatok, kontaktlapok
  llm.py          modell-háttérrendszerek (Claude Code CLI / Codex CLI), asztali alkalmazás keresése, gyorsítótár, újrapróbálás, bejelentkezés-ellenőrzés
  render.py       gépi ellenőrzések és összeállítás
  config.py       beállítások és videókontextus
  checkpoint.py   fejezetenkénti ellenőrzőpontok (tartalom-hash) a biztonságos folytatáshoz és átadáshoz
  prompts/        promptok minden szakaszhoz (általános szabályok, témakörök, képválasztás, írás, áttekintés, újraellenőrzés, javítás, teljes áttekintés, ügynökcsomag)
colab/transcribe.ipynb   Colab T4 GPU feliratkészítő jegyzetfüzet
```

A harmadik féltől származó licencnyilatkozatok a [NOTICE](NOTICE) fájlban vannak.
