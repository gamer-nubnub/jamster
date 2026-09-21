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
