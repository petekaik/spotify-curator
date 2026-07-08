# Spotify Curator

**Aidosti älykäs soittolistojen suunnittelija** joka löytää pieniä ja nousevia
artisteja käyttäjän kuuntelumieltymysten perusteella — ilman suosittelu-"kupla"-efektiä.

> **Status:** FP-0 (alustus) — hakemistorakenne luotu, API-muutokset dokumentoitu.

## 🎯 Visio

Spotifyn oma "Discover Weekly" ja "Release Radar" suosittelevat pääasiassa
artisteja joiden tiedetään toimivan — eli suosittuja. Tämä projekti kääntää
logiikan ympäri: **mitä vähemmän kuultu, sitä kiinnostavampi löytö** — kunhan
se sopii käyttäjän genreprofiiliin.

## ⚠️ Kriittinen API-muutos: helmikuu 2026

Spotify muutti Web API:a merkittävästi 2026-02-06 ([migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)).
**Projektin on otettava tämä huomioon alusta lähtien.**

### Poistetut kentät (tärkeimmät)
- ❌ `artist.popularity` (0-100) — **ei enää saatavilla artist-haussa**
- ❌ `album.available_markets` — ei maakoodauksia
- ❌ `album.album_group` — artistin ja albumin suhdetta ei enää palauteta
- ❌ `track.album.album_group`
- ❌ Genre-dataa ollaan hiljalleen poistamassa artist-haussa

### Poistetut endpointit (tärkeimmät)
- ❌ `GET /artists/{id}/top-tracks` — **artistin top-tracks ei enää saatavilla!**
- ❌ `GET /browse/new-releases` — uutuuslistat poistettu
- ❌ `GET /users/{id}/playlists` — käyttäjän soittolistat
- ❌ `GET /me/following` — seuratut artistit
- ❌ Search `limit` maksimi 50→10

### Uudet endpointit
- ✅ `PUT /me/library` — kirjastoon tallennus
- ✅ `POST /playlists/{id}/items` — kappaleiden lisäys (oli `/tracks`)

### Vaikutus projektiin

Tämä on iso muutos. "Top tracks" -endpointin puute tarkoittaa että **emme voi
kysyä "mitkä ovat artistin X parhaat kappaleet" suoraan**. Pitää tehdä näin:

1. Hae artistin kaikki albumit `Get Artist's Albums` -endpointilla
2. Hae albumien kaikki raidat `Get Album Tracks` -endpointilla
3. Järjestä Followers-määrän (vielä saatavilla!) perusteella

### Spotipy 2.26 -tilanne (2026-07-08)

**Spotipy EI OLE vielä päivitetty helmikuu 2026 -muutoksiin.** Tämä todettiin
empiirisesti tänään:

- ❌ `sp.me_library_*` -metodeja ei ole (pitäisi olla uusi `/me/library`)
- ❌ `sp.artist_top_tracks(artist_id)` on vielä vanhalla `artists/{id}/top-tracks` -polulla (joka on poistettu)
- ⚠️ `sp.playlist_tracks(playlist_id)` on vielä vanhalla `/playlists/{id}/tracks` -polulla
- ⚠️ `sp.playlist_add_items` todennäköisesti vielä vanhalla polulla

**Strategia:** ÄLÄ luota spotipyn korkean tason metodeihin näissä kriittisissä
kohdissa. Käytä `sp._get`, `sp._post`, `sp._put`, `sp._delete` -rajapintaa
suoraan uusien polkujen kanssa:

```python
# Uusi polku - toimii nyt, vaikka spotipy ei vielä tue
sp._get("me/library/contains", ids="track_id1,track_id2")

# Top tracks - korvataan artistin albumeilla
albums = sp._get(f"artists/{artist_id}/albums", limit=10)
all_tracks = []
for album in albums['items']:
    tracks = sp._get(f"albums/{album['id']}/tracks", limit=50)
    all_tracks.extend(tracks['items'])
# Järjestä albumi.popularity -kentällä (vielä albumeilla!)
```

**Albumi.popularity** on yhä saatavilla albumeissa (vain artistin popularity
poistettiin). Tätä voidaan käyttää "nouseva artisti" -signaalina.

**Popularity-kentän poisto** on vielä isompi ongelma. Vaihtoehtoiset keinot
löytää "pieniä ja nousevia" artisteja:

| Signaali | Saatavilla? | Käyttö |
|---|---|---|
| Followers count | ✅ Kyllä | Pieni määrä = pieni artisti (mutta vanhat fanit seuraavat) |
| Followers + aika | ✅ Laskettavissa | `followers / months_since_first_release` = kasvuvauhti |
| Albumien määrä | ✅ Kyllä | Vähän albumeita = uudempi artisti |
| Spotify-editorin playlistat | ⚠️ Monitoroitava | Editorial-curation = Spotify "löysi" heidät |
| Track-formaatit (single vs album) | ✅ Kyllä | Pelkät singlet = vielä nousussa |
| **Last.fm scrobbles** | ✅ Erillinen API | Korvaa popularity-kentän kokonaan |
| **MusicBrainz** | ✅ Erillinen API | Artisti- ja genre-data |
| **ListenBrainz** | ✅ Erillinen API | Suosittelualgoritmit |
| **Bandcamp/Discogs** | ✅ Erillinen API | Indie- ja pienlevy-yhteisöt |

### Strategia: hybrididata

Käytetään **Spotify API:a autentikointiin ja kirjoittamiseen** (soittolistojen
luonti) sekä **Last.fm API:a signaalidatana** (scrobbles, genre, "emerging"
-tagit). Tämä kiertää popularity-kentän poiston kokonaan.

## 🏗️ Arkkitehtuuri (4C-malli)

### Context
- **Käyttäjä:** Pomo, kuuntelee pääasiassa indietä, post-rockia, dream popia,
  electronicaa, vähän klassista. Ei pidä valtavirran popista.
- **Ympäristö:** macOS 26.5.1, Apple M1, Homebrew, Python 3.12, spotipy 2.26 asennettu
- **Integraatiot:** Spotify Web API, Last.fm API, valinnaisesti MusicBrainz
- **Vastuut:** CLI'dä käytetään Mac Terminalista; tulokset kirjoitetaan
  oikeaksi Spotify-soittolistaksi

### Container
```
spotify-curator/
├── src/
│   ├── spotify/           # API-wrapper, auth, rate-limiting
│   ├── analyzer/          # Käyttäjäprofiilin rakennus
│   ├── discovery/         # Artistien etsintä (hybrididata)
│   ├── playlist/          # Soittolistojen rakennus
│   ├── cli/               # Typer-komennot
│   └── web/               # Valinnainen FastAPI-UI
├── tests/                 # pytest + vcrpy (HTTP-mockit)
├── data/
│   ├── cache/             # Parquet/JSON välimuisti
│   └── exports/           # Viedyt soittolistat
├── docs/                  # 4C-dokumentaatio
├── .env.example           # API-avainten malli
└── requirements.txt
```

### Component

#### 1. `src/spotify/auth.py` — autentikointi
- OAuth Authorization Code Flow (Spotipy)
- Refresh-token-välimuisti `~/.spotify-curator/.cache`
- Scopes: `playlist-modify-public`, `playlist-modify-private`,
  `user-top-read`, `user-library-read`, `user-follow-read`

#### 2. `src/analyzer/profile.py` — käyttäjäprofiili
- Hakee `me/top/artists` (short, medium, long term) → genret, esiintyjät
- Hakee `me/top/tracks` → audiosoft, tempo, mood
- Hakee `me/player/recently-played` (vaatii eri scope)
- Laskee painotetun genreprofiilin (paino: medium-term 0.5, short-term 0.3, long-term 0.2)
- Tallentaa Parquet-muotoon

#### 3. `src/discovery/sources/` — datalähteet
- `spotify_search.py` — Search API:lla genrellä + tagilla haku
- `lastfm.py` — Last.fm geo+tag+time-period haut (nousevat artistit)
- `musicbrainz.py` — Genre- ja relaatiotiedot
- `similar_artists.py` — Artistien samankaltaisuusverkko (Last.fm)

#### 4. `src/discovery/ranking.py` — pisteytys
```
artist_score = (
    0.4 * genre_match_score      # käyttäjän profiiliin sopivuus
  + 0.3 * emerging_score         # Last.fm scrobble-kasvu vs Spotify followers
  + 0.2 * discovery_potential   # montako albumia, kuinka vanha
  - 0.1 * mainstream_penalty    # rankaisee yli 10k monthly listeneria
)
```

#### 5. `src/playlist/builder.py` — soittolistan rakennus
- Valitsee N parasta artistia yllä olevalla pisteytyksellä
- Jokaiselta artistilta 2-3 parasta kappaletta (Followers-sort)
- Sekoittaa, välttää yhtäjaksoisia saman artistin kappaleita
- Luo soittolistan `POST /me/playlists` (uusi endpoint)
- Lisää kappaleet `POST /playlists/{id}/items` (uusi polku)

#### 6. `src/cli/main.py` — Typer-CLI
```
spotify-curator auth                  # OAuth-kirjautuminen
spotify-curator profile build         # Päivitä kuunteluprofiili
spotify-curator discover --genre=indie --emerging  # Etsi nousevia
spotify-curator playlist create "Indie Rising" --size=30
spotify-curator playlist sync <id>     # Päivitä olemassa oleva
```

### Code

#### Teknologiavalinnat
- **Kieli:** Python 3.12 (sama ekosysteemi kuin Clairvoyant-Optics)
- **Spotify:** spotipy 2.26 (uusin, tukee helmikuu 2026 -muutoksia)
- **Last.fm:** requests (suorat REST-kutsut, ei kirjastoa)
- **CLI:** Typer (moderni, type-safe, Rich-yhteensopiva)
- **Välimuisti:** Parquet (pyarrow) — nopea, skeematon
- **Testit:** pytest + vcrpy (nauhoittaa HTTP-kutsut)

#### Ettei toisteta Clairvoyant-Optics-kokemuksia:
- ✅ Hybrididata (EI pelkkä Spotify-API)
- ✅ Parquet-välimuisti EI JSON (nopeampi, ei korruptoidu)
- ✅ Threading-fix valmiiksi (`threading.RLock`)
- ✅ INV-LAZY-LOAD-periaate: spotipy+kirjastot pip-asennetaan, EI bundled
- ✅ Testit nauhoittavat HTTP-kutsut (vcrpy) — testit eivät tarvitse API-avaimia
- ✅ CI: GitHub Actions, macOS-runner

## 🚀 Roadmap (aloitus)

| ID | Tehtävä | BV | TC | Tila |
|---|---|---|---|---|
| **FP-0** | Projektin alustus (hakemistot, README, dokumentaatio) | 8 | 1 | ✅ Tehty |
| **FP-1** | `src/spotify/auth.py` — OAuth + token-välimuisti | 13 | 2 | ✅ Tehty |
| **FP-1b** | `src/spotify/api_v2.py` — uudet (Feb 2026) API-endpointit | 13 | 3 | ✅ Tehty, 15/15 testiä PASS |
| **FP-2** | `src/analyzer/profile.py` — top-artistit + genreprofiili | 13 | 3 | Open |
| **FP-3** | `src/discovery/lastfm.py` — Last.fm top-tag-artistit geoittain | 8 | 5 | Open |
| **FP-4** | `src/discovery/ranking.py` — pisteytysalgoritmi | 13 | 5 | Open |
| **FP-5** | `src/playlist/builder.py` — soittolistan luonti | 13 | 3 | Open |
| **FP-6** | `src/cli/main.py` — Typer-CLI | 8 | 2 | Open |
| **FP-7** | Integraatiotestit vcrpy:llä (CI) | 8 | 3 | Open |
| **FP-8** | Web-UI (FastAPI, kevyt) | 5 | 5 | Backlog |

## 📚 Tärkeät linkit

- [Spotify Web API Feb 2026 Migration](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Spotify Web API Reference](https://developer.spotify.com/documentation/web-api)
- [Last.fm API](https://www.last.fm/api)
- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
- [spotipy GitHub](https://github.com/spotipy-dev/spotipy)

## Lisenssi

TBD — ehdotus: MIT (FOSS, yhteensopiva muiden Pomo-projektien kanssa).
