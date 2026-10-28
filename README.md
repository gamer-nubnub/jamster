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
- **Tier 1 and Tier 2 models: cloud by default, local opt-in.** Backend is pluggable via config;
  the deterministic groove engine remains the deadline-miss fallback.
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

- Exact bar-notation and branching-score schemas.
- Concrete cloud/local model choices and cost budget (no spending cap decided yet).

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
