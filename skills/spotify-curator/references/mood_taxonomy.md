# Mood taxonomy

The weekly digest and `spotify_curator_generate_mood` tool classify
artists into 6 moods. The classification is **rule-based for v0.2.0**
(using Spotify audio features + heuristics), with an LLM-enhanced
path planned for v0.3.0+.

---

## The 6 moods

### 1. **cheerful** — uplifting, positive, feel-good

**Audio profile:**
- High valence (0.6-0.9)
- Moderate-high energy (0.5-0.8)
- Major keys (inferred from audio)
- Low acousticness (0.0-0.4)

**Genre signals:** indie pop, power pop, surf rock, feel-good folk, disco,
afrobeats (filter out generic pop and EDM)

**Source emphasis:** Last.fm `tag.getTopArtists(period='3month')` weighted
toward recent scrobble growth (suggests rising feel-good hits).

**Example picks:** Alvvays, girl in red, The Beths, Wet Leg, The Regrettes

---

### 2. **calming** — peaceful, ambient, slow

**Audio profile:**
- Low tempo (60-100 BPM)
- Low-to-moderate energy (0.2-0.5)
- Moderate acousticness (0.4-0.8)
- Low speechiness (0.0-0.1)

**Genre signals:** ambient, post-rock, slowcore, shoegaze (slow),
neoclassical, drone, field recordings

**Source emphasis:** Bandcamp is **king** here — these genres release
to Bandcamp first. Also ListenBrainz ML-similar (catches the slow niche).

**Example picks:** Stars of the Lid, Grouper, Low, Alcest, Hammock,
Slowdive (slower tracks)

---

### 3. **stimulating** — complex, intellectual, challenging

**Audio profile:**
- Variable tempo (but often with time changes)
- Moderate-to-high energy
- Low-to-moderate valence (0.2-0.6)
- High instrumentalness (0.5+)
- Moderate acousticness (varies)

**Genre signals:** math rock, post-rock (energetic), progressive metal,
IDM, experimental electronic, jazz fusion, krautrock

**Source emphasis:** ListenBrainz ML-similar (algorithmic depth) +
Last.fm `tag.getTopArtists(period='12month')` (signals that reward
endurance, not just viral hits).

**Example picks:** Tortoise, Black Midi, King Gizzard, Battles,
Tosin Abasi, Daughters

---

### 4. **focus** — minimal, instrumental, work-friendly

**Audio profile:**
- Steady tempo (no wild changes)
- Moderate energy (0.4-0.6)
- High instrumentalness (0.7+)
- Low valence (0.3-0.5, neutral)
- No vocals or very quiet vocals

**Genre signals:** minimal electronic, modern classical, ambient piano,
drone, post-rock (instrumental sections), video game OSTs

**Source emphasis:** Bandcamp + MusicBrainz genre search. Avoid artists
who are "popular for vocals" (low instrumentalness in their discography).

**Example picks:** Ólafur Arnalds, Nils Frahm, Brian Eno, Ryuichi Sakamoto,
Tim Hecker

---

### 5. **melancholic** — sad, reflective, emotional

**Audio profile:**
- Low-to-moderate tempo (70-110)
- Low-to-moderate energy (0.2-0.5)
- Low valence (0.0-0.3)
- Moderate acousticness (0.4-0.7)
- Often minor keys

**Genre signals:** sadcore, slowcore, emo, indie folk, dream pop,
chamber pop, singer-songwriter (introspective)

**Source emphasis:** Last.fm tag `sad`, `melancholy`. Reddit's
r/indieheads "FRESH" threads surface these frequently.

**Example picks:** Phoebe Bridgers, Big Thief, Julien Baker, Elliott Smith,
Adrianne Lenker, Mitski

---

### 6. **energetic** — high-tempo, driving, intense

**Audio profile:**
- High tempo (130+ BPM)
- High energy (0.7+)
- Variable valence
- High danceability (0.5+)
- Often loud

**Genre signals:** punk, post-hardcore, garage rock, electronic (high-BPM),
breakcore, drum & bass, hardcore techno

**Source emphasis:** Last.fm `tag.getTopArtists(period='1month')` for
freshness. Cross-reference with Bandcamp for underground hardcore.

**Example picks:** IDLES, Fontaines D.C., Speed, JPEGMAFIA, SOPHIE,
100 gecs, Lingua Ignota

---

## How classification works (v0.2.0, rule-based)

For each candidate artist:
1. Get the top 3 tracks' audio features (from Spotify, if available)
2. Average each feature across the 3 tracks
3. Map to mood via hardcoded thresholds (see the audio profile columns
   above — each mood is a 5D region in audio feature space)
4. Edge cases (artist with no audio features): fall back to genre tag
   matching against each mood's preferred tags
5. If still ambiguous: assign to the mood with the highest Last.fm tag
   overlap

This is imperfect. The v0.3.0+ LLM-based path will use a small local
model (Ollama) to do richer semantic classification with commentary.

---

## Tuning

The rule-based classifier lives in
`src/discovery/mood_classifier.py` (planned for FP-9). The thresholds
above are starting values — adjust per user feedback.

If a user says "the melancholic playlist keeps giving me uplifting songs",
we adjust the valence threshold for that mood downward.
