# Edit Director - Cinematic Pipeline

## When To Use

This stage turns the beat map into a paced cinematic timeline. Rhythm and restraint matter more than effect count.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["assets"]["asset_manifest"]`, `state.artifacts["scene_plan"]["scene_plan"]`, `state.artifacts["script"]["script"]` | Assets, hero frames, beat map |
| Playbook | Active style playbook | Typography and transition consistency |

## Process

### 1. Cut By Emotion First

Cuts should follow:

- emotional emphasis,
- reveal timing,
- musical turns,
- visual contrast.

Do not optimize only for information density.

### 2. Protect Strong Moments

If a look, line, or gesture is doing the work, let it live. Do not over-cover it with extra inserts.

### 3. Use Sound To Push The Edit

Ambience, impacts, dropouts, and music changes should help create momentum between scenes.

### 4. Use Metadata For Timing Logic

Recommended metadata keys:

- `beat_timing`
- `audio_turns`
- `title_card_windows`
- `reframe_notes`

### 4b. Check Raccords at Every Cut (Narrative Fiction)

Watch each cut point, not just each clip. Flag any cut where a character swaps side, both speakers look toward the same edge, the screen direction reverses, light or weather jumps, or a state (wet, injured, object broken) resets — see `skills/creative/shot-continuity.md` §4. Fix in the edit when possible (trim to a cleaner frame, reorder, recrop a tight framing, grade a light mismatch); otherwise send the take back to assets with the defect named. Lay one continuous ambience bed per location under its takes so the sound does not jump at cuts (`skills/creative/fiction-shot-direction.md` §5).

### 5. Quality Gate

- the emotional arc is intact,
- reveals land clearly,
- title cards are sparse and timed with intent,
- strong moments are not buried under coverage.

## Common Pitfalls

- Overcutting emotional material.
- Using speed ramps or flashy transitions by default.
- Letting title cards replace editorial clarity.
