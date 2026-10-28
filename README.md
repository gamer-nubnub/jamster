# jamster

Virtual agentic band-practice / jamming environment. AI session musicians play along
with you in Ableton Live: they listen to your MIDI playing, a leader audio track, and
the project's MIDI clips, and respond musically in near real time. You talk to the
band via mic (push-to-talk or always-listening); they answer with music only.

## Architecture

- **Conductor** — asyncio core. Ableton Link beat clock, lookahead event scheduler,
  MIDI out (notes + expression CC) via one loopMIDI virtual port per agent, mapped to
  one Ableton track per agent.
- **Tier 1 — Player loop (near-real-time)** — per-bar symbolic MIDI decisions: at bar
  N's midpoint, generate bar N+1 per agent in a compact bar-notation schema. Backed by
  a fast/local language model with a deterministic groove-engine fallback so the band
  never drops a bar.
- **Tier 2 — Producer (strategic, lag-tolerant)** — high-reasoning cloud LLM. Runs
  every N bars or on voice command; consumes transcript, user-MIDI summary,
  leader-track audio features, project MIDI, session prompt, and agent personalities;
  emits per-agent charts/intents that condition Tier 1.
- **Listening** — live user MIDI (rtmidi), leader-track audio features
  (chroma/onset/RMS → chord + energy), project MIDI via AbletonOSC.
- **Voice** — local faster-whisper ASR, dual mode (push-to-talk / always-listening),
  transcript surfaced in the UI and fed to Tier 2.
- **Config** — YAML agent personalities (`config/agents/`) and session context
  prompts (`config/sessions/`).
- **UI** — bare-bones single-page web UI (FastAPI + websocket): transcript, per-agent
  intent, beat/bar/chord display.

## Design decisions

- **Anticipation via branching scores.** To hide LLM latency, the Tier 2 producer
  pre-generates several candidate continuations ("options"), each with trigger
  conditions (e.g. "user plays Am → option B"). The conductor evaluates triggers
  deterministically at each bar boundary and plays the matching option; Tier 1 fills
  in detail. No LLM is in the bar-boundary hot path.
- **Global setting: trigger vocabulary** — `minimal` (chord + energy), `rich` (chords,
  key, energy, density, tempo feel, section markers, voice intents), or `free-text`
  (conditions interpreted by an LLM on a slower, lag-tolerant path). The option schema
  is versioned per level.
- **Global setting: option granularity** — `per-bar` (a short window of 2–4 bars of
  named options with per-agent patterns), `section` (whole grooves with transition
  triggers, Tier 1 fills bars), or `intent` (high-level energy/density/feel only,
  Tier 1 writes all notes). Lets fast and slow LLM APIs use different modes. Default:
  `section`.
- **Staleness → hold and loop.** Options are stale when their TTL (default 8 bars)
  expires *or* no trigger matches for N bars (default 2); both are configurable. The band then holds and loops its current
  pattern until the producer recovers, and the UI shows this state prominently.
- **Band can write back, in text only.** Agents still reply with music, plus short
  on-screen text messages in the UI (e.g. clarifying questions). No TTS.
- **Tier 1 and Tier 2 models: cloud by default, local opt-in.** Default classes are a top
  reasoning model for Tier 2 and a small fast model for Tier 1; both are set in config
  and the backend is pluggable. The deterministic groove engine remains the
  deadline-miss fallback.
- **Spending cap: configurable hard cap.** Default hard cap is 2 USD per session, with
  a warning at 80 % of the cap; both values are configurable. When the cap is hit the
  band falls back to the groove engine and the UI says so.
- **Leader-track chords (Phase 4): MIDI-first.** Read chords from project MIDI via
  AbletonOSC when available; fall back to classic DSP chroma + template matching on
  the audio.
- **Testing without Ableton.** Mock MIDI ports and a fake clock, with pytest unit
  tests runnable on Linux CI. Hardware/Ableton checks stay manual (Windows).

## Status

Phases 0–1 are scaffolded (internal/Link clock, scheduler, MIDI out, chord tracker,
deterministic bass + drums, timing probe) but not yet verified, and there are no tests.
Everything else is not started.

## Open questions

- Per-bar note format used by Tier 1 (the section-level schema is drafted above, v0).

## Schema draft (v0, `section` mode, `minimal` triggers)

Tier 2 emits one **branching score**. Tier 1 and the groove engine turn the active
section into per-bar `NoteEvent`/`ControlEvent` lists (see `events.py`).

```yaml
version: 0
score_id: s-0042
issued_at_bar: 17          # bar the producer call was based on
ttl_bars: 8                # stale after bar 25 (default 8)
current: verse             # section playing now
sections:
  verse:
    agents:
      bassist:  {intent: "roots and fifths, lock to kick", density: 0.4, energy: 0.4}
      drummer:  {intent: "straight pop-rock, closed hats", density: 0.4, energy: 0.4}
    transitions:           # evaluated in order each bar; first match wins
      - to: chorus
        when: {energy_above: 0.7, for_bars: 2}
      - to: verse_am
        when: {chord: Amin}
  verse_am:
    agents:
      bassist:  {intent: "stay on A root, sparse", density: 0.3, energy: 0.4}
      drummer:  {intent: "drop the hats", density: 0.3, energy: 0.3}
    transitions:
      - to: verse
        when: {chord: Cmaj}
  chorus:
    agents:
      bassist:  {intent: "eighth-note drive", density: 0.7, energy: 0.8}
      drummer:  {intent: "open hats, big fills at bar 4", density: 0.8, energy: 0.8}
    transitions:
      - to: verse
        when: {energy_below: 0.4, for_bars: 2}
```

Rules:

- **Triggers:** `minimal` supports `chord` and `energy_above`/`energy_below` (with
  optional `for_bars`). `rich` adds key, density, tempo feel, section markers, and
  voice intents under the same `when` object. `free-text` adds `when: {text: "..."}`,
  evaluated off the hot path.
- **Hot path:** the conductor evaluates `transitions` deterministically at each bar
  boundary. If none match, the current section keeps playing; after N unmatched
  bars (default 2) or when the TTL expires, the band holds and loops.
- **Per-agent entries:** `intent` is free text for Tier 1; `density` and `energy`
  (0–1) feed the groove engine fallback directly.
- **Other modes:** `per-bar` replaces a section's `intent` with explicit bar patterns
  for 2–4 bars; `intent` mode drops `transitions` and keeps only per-agent
  `density`/`energy`/`intent`.
- **Text messages:** an optional top-level `say: {bassist: "Want a key change?"}` is
  shown in the UI.

## Phases

| Phase | Slice | Verification |
| ----- | ----- | ------------ |
| 0 | Timing probe: Link sync + scheduled metronome into Ableton | jitter histogram, < 10 ms |
| 1 | Deterministic band: user chords → bass + drums follow | manual jam, follows within 1 bar |
| 2 | Tier 1 model in loop: bar notation, deadline + fallback, CC | deadline-miss rate < 20 % |
| 3 | Voice + Tier 2 producer + personalities + UI v1 | voice command changes groove |
| 4 | Project MIDI ingest + leader-track analysis | chord estimate matches known progression |

## Requirements

- Windows, Ableton Live (Link enabled)
- [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) with one port per agent
- Python 3.11+, local `.venv`
