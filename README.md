# Spotify Curator

[![CI](https://github.com/petekaik/spotify-curator/actions/workflows/ci.yml/badge.svg)](https://github.com/petekaik/spotify-curator/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-31%20passing-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Aidosti älykäs soittolistojen suunnittelija** joka löytää pieniä ja nousevia
artisteja käyttäjän kuuntelumieltymysten perusteella — ilman suosittelu-"kupla"-efektiä.

> **Status:** v0.1.0-dev — FP-0/1/1b/2 ✅ | FP-3 seuraavaksi

## 🎯 Visio

Spotifyn oma "Discover Weekly" ja "Release Radar" suosittelevat pääasiassa
artisteja joiden tiedetään toimivan — eli suosittuja. Tämä projekti kääntää
logiikan ympäri: **mitä vähemmän kuultu, sitä kiinnostavampi löytö** — kunhan
se sopii käyttäjän genreprofiiliin.

## 🧩 Ominaisuudet (FP-2 valmis)

- ✅ **OAuth-autentikointi** — Authorization Code Flow, token-välimuisti
- ✅ **Spotify API v2 -wrapper** — tukee helmikuu 2026 -muutoksia
  (`/me/library`, `/playlists/{id}/items`, workaround poistetulle
  `/artists/{id}/top-tracks`)
- ✅ **Käyttäjäprofiilin rakennus** — top-artistit + top-tracks kolmesta
  aikaikkunasta (short/medium/long term), painotettu genre- ja artisti-analyysi,
  Parquet-välimuisti
- ✅ **31/31 testiä PASS** (pytest)
- 🚧 **Discovery-moduulit** (FP-3) — Last.fm, ListenBrainz, MusicBrainz, Bandcamp, Reddit
- 📋 **Pisteytysalgoritmi** (FP-4) — genre + emerging + features + mainstream penalty
- 📋 **Soittolistan rakennus** (FP-5) — N parasta artistia, 2-3 kappaletta per artisti
- 📋 **Typer-CLI** (FP-6)
- 📋 **Web-UI** (FP-8, backlog)

## ⚠️ Kriittinen: Spotifyn helmikuu 2026 API-muutokset

Spotify muutti Web API:a merkittävästi 2026-02-06
([migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)).
Projektin on otettu tämä huomioon alusta lähtien.

### Poistetut kentät (tärkeimmät)
- ❌ `artist.popularity` (0-100) — ei enää saatavilla artist-haussa
- ❌ `album.available_markets` — ei maakoodauksia
- ❌ `album.album_group` — artistin ja albumin suhdetta ei enää palauteta
- ❌ Track-level `album_group`
- ❌ Genre-dataa ollaan hiljalleen poistamassa

### Poistetut endpointit
- ❌ `GET /artists/{id}/top-tracks` — **artistin top-tracks ei saatavilla!**
- ❌ `GET /browse/new-releases` — uutuuslistat poistettu
- ❌ `GET /users/{id}/playlists` — käyttäjän soittolistat
- ❌ `GET /me/following` — seuratut artistit
- ⚠️ Search `limit` rajoitettu 50→10

### Uudet endpointit (käytetään projektissa)
- ✅ `PUT /me/library` — kirjastoon tallennus
- ✅ `POST /playlists/{id}/items` — kappaleiden lisäys

### Vaikutus projektiin

**Spotipy 2.26 EI OLE vielä päivitetty** helmikuu 2026 -muutoksiin.
Toteutimme oman `api_v2.py` -wrapperin joka kutsuu `sp._get/_post/_put/_delete`
suoraan uusille poluille. Top tracks -workaround: hae artistin albumit →
albumien raidat → järjestä album-popularityn mukaan (joka on yhä
saatavilla albumeissa).

**Popularity-kentän poisto** on isompi ongelma. Hybrididata useasta lähteestä:

| Signaali | Lähde | Käyttö |
|---|---|---|
| Followers + aika | Spotify | Kasvuvauhti = nouseva |
| Albumi.popularity | Spotify (vielä!) | Apusignaali |
| Last.fm scrobble-kasvu | Last.fm API | Suora emerging-signaali |
| ListenBrainz ML-similar | ListenBrainz Labs | Spotify-populariteettikuplaton |
| MusicBrainz genre | MusicBrainz API | Genretarkkuus |
| Bandcamp followers-low | Web-scrape | Indie/pienlevy-yhteisöt |
| r/indieheads-maininnat | Reddit JSON | Ihmisten suositukset |

## 🏗️ Arkkitehtuuri

Katso [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 4C-malli (Context,
Container, Component, Code) ja 6 datalähteen kuvaus.

## 📋 FP-suunnitelma

Katso [docs/FP_PLAN.md](docs/FP_PLAN.md) — WSJF-pisteytys, FP-2 detaljit,
hyväksymiskriteerit.

## 🚀 Pika-aloitus

```bash
# 1. Kloonaa
git clone https://github.com/petekaik/spotify-curator.git
cd spotify-curator

# 2. Asenna riippuvuudet
python3 -m pip install -r requirements.txt

# 3. Spotify-tunnukset
#    Luo: https://developer.spotify.com/dashboard
cp .env.example .env
#    Muokkaa .env: SPOTIPY_CLIENT_ID ja SPOTIPY_CLIENT_SECRET

# 4. Aja testit
pytest tests/ -v

# 5. (Tulossa) OAuth-kirjautuminen
# spotify-curator auth
```

## 📊 Testikattavuus

```
tests/test_api_v2.py    — 15/15 PASS  (Spotify API v2 wrapper)
tests/test_profile.py   — 16/16 PASS  (FP-2: User profile builder)
```

Yhteensä **31/31 PASS** kahdessa sekunnissa.

## 📚 Tärkeät linkit

- [Spotify Web API Feb 2026 Migration](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Spotify Web API Reference](https://developer.spotify.com/documentation/web-api)
- [Last.fm API](https://www.last.fm/api)
- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
- [ListenBrainz Labs](https://labs.api.listenbrainz.org/)
- [spotipy GitHub](https://github.com/spotipy-dev/spotipy)

## Lisenssi

MIT — katso [LICENSE](LICENSE).
