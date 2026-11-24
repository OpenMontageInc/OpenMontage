# Shot Continuity (Raccords) — Generated Narrative Video

## When to Use

Read this before the scene plan, before every take prompt, and after every take of any narrative piece built from several generated video takes with recurring characters, locations, or props: cinematic fiction, micro-drama episodes, series, trailers with a story. It applies to every pipeline and every video provider (Seedance, Kling, Veo…). It covers both cuts between generations and cuts inside a multi-shot generation.

The root problem: **a video model remembers nothing from one generation to the next.** Each take reinvents positions, wardrobe, wetness, injuries, light and prop states from the references and the text. If a fact is not shown by a reference image or written in the prompt, it resets. Typical breaks this skill prevents:

- a character comes out soaked although the previous take showed no rain or water;
- characters swap sides of the frame, or both look toward the same edge in a shot/reverse shot;
- a broken or dropped object is whole again two shots later;
- light changes direction or color inside one scene with no cause shown;
- a character "enters from the background" and is simply missing, or pops in.

Rules distilled from the operator's motion-tools-agent project (its raccordeur, storyboarder and take-QC agent instructions and its Seedance continuity findings), validated on real Seedance 2.5 takes. Sources they condense (MIT): Nagacash/narrative-film-direction, nolanx-ai/nolanx.ai, Emily2040/seedance-2.0, OSideMedia/higgsfield-ai-prompt-skill. For camera, acting, dialogue and sound direction see `skills/creative/fiction-shot-direction.md`.

## Prerequisites

| Input | What to establish |
|---|---|
| Script / beat map | The order of takes and shots, which scene each belongs to |
| Canon | Character sheets (identity anchor, outfit, asymmetric traits with their side), location plates, prop sheets |
| Scene plan | Per shot: cast, location, framing, camera, start frame, end state, transition |

## Process

### 1. Keep a Continuity Ledger (the script supervisor's job)

Before writing any take prompt, write `scene_plan.metadata.continuity_ledger`: the state of the world at the start of the piece, then the **state at the end of every shot** in order. Per character:

- location and side of frame, depth layer, body orientation, gaze (frame edge or who they look at);
- outfit and its variant (hood up/down, jacket open, tie removed);
- physical state: **dry / damp / soaked**, dirt, blood, injuries, hair state, breath (out of breath carries);
- props in hand, and **which hand** (from the character's point of view);
- emotional carry-over (fear or anger does not switch off at the cut).

Per prop: who holds it or where it sits, and its state (whole, dropped, face down, broken). Per location: weather, time of day, light source.

Ledger rules:

- **Every state change has a cause shown on screen**, in this shot or an earlier one. Off-screen equals nonexistent: a character cannot be soaked unless the viewer saw rain, a splash, a fall into water. If the script says "she arrives soaked", either show the cause in an earlier take or open on the cause.
- **States are monotonic.** Wetness only accumulates until a shown or implied time skip dries it; dirt and blood only accumulate; a cookie only shrinks; a broken object stays broken. Write the state in every shot from the cause onward — including the shots in between, where the text "forgot" it and the model will render the character clean and dry.
- A state that must change back (dried off, changed clothes) needs a visible bridge: a time card, a towel, a cut to a new day.
- **Audit the ledger shot by shot before the scene-plan gate**: for every state word in any prompt ("soaked", "bloody", "hood down", "holding the cable"), find the shot that caused it and check every shot between cause and use carries it.

### 2. One Location Map per Location

For each location write a GEO map (no people), stored in `scene_plan.metadata.location_maps` and **pasted verbatim into every take shot there**:

- distinctive, immovable landmarks (a second matching chair is an ambiguity, not a landmark), with rough distances;
- what is frame-left and frame-right from the camera side (models do not understand "to the left of the character" — sides exist only from the camera);
- the 180° axis: which side the camera stays on, the line it never crosses;
- the light: visible source, direction in frame, hardness, color ("warm practical lamp frame left, hard light"). One source, one shadow direction, never two suns.

The screen-direction lock comes from the plates, not from prose: a reverse angle needs its own location plate (`<location>-reverse`). A shot and its reverse never show the same background.

**Check every plate's scale before the first take.** The model copies a plate's proportions into every shot made there, so a wrong scale is a defect in every take of that location. Look at each plate on its own and compare the sizes: a door is about 2.1 m high, roughly 2.5 times an armchair's height and taller than any adult standing next to it; a ceiling is above the doors; steps and windows match a person's height. Regenerate any plate where a door, opening or piece of furniture would come out smaller than a character (edit mode from the old plate), and write the sizes into the GEO map ("a full-height bedroom door, taller than an adult, on the LEFT"). In one series, a penthouse plate with a child's door no taller than the armchair shrank the door in six takes.

### 3. Stage Every Shot Against the Map

Per shot, per character: position relative to a landmark ("by the counter, facing the window"), frame side and x/y position, depth (foreground / midground / background), body orientation, gaze with its frame edge, contact points ("right hand on the railing"), state lock (soaked, out of breath). When the scene continues, **repeat the previous shot's positions and light word for word**, changing only what a shown movement changed.

- At most 3 tracked characters per shot; state the count ("exactly two people in frame").
- A character already present is **in frame from the first frame**. Models ignore entries from the background, an alcove, or off-screen: they never arrive, or pop in.
- Crossing a room is shown (walks toward camera or crosses in front), never cut from the door to the table; the end state names the new place.
- Never exit frame and re-enter in the same shot (an exit is an implicit cut).
- A framing written only in text drifts back to a medium shot; do not write an action that depends on what stays off-frame ("hand below the frame edge").

### 4. Check Every Cut

For each pair of consecutive shots (inside a multi-shot generation and across generations):

- **30° rule.** A plain cut (not chained) on the same subject changes both shot size and angle by at least 30°, with a camera move different from the previous shot's. A slightly tighter reframe reads as an editing mistake.
- **Eyelines.** In shot/reverse shot the two speakers look toward opposite frame edges; a reaction shot looks toward where its cause stands. Never both toward the same edge, never into the lens.
- **Screen direction.** Movement left-to-right before the cut stays left-to-right after; write it "locked".
- **Foreground shoulders (amorces).** A character seen from behind or blurred in the foreground is in the cast, on the same side and in the same outfit as in the neighbouring shot. Changing side without a declared reverse shot is an axis jump.
- **Light.** Same source, direction and color for every shot of a scene. Light changes only at the scene's turn, through a cause shown in the action (door opened, lamp switched off), and the next shot inherits it.
- **Action match.** The interrupted gesture at the end of shot N resumes at the same point and in the same direction at the start of shot N+1 (often tighter). A gesture that restarts, or is already finished, is a broken match.
- **End frame.** The final frame is reachable from the starting frame or by the declared camera move, and visible from the shot's camera (a close-up does not end on the far door). Chained: motion and gaze still turned toward the next shot. Cut: gesture finished, frame settled. Shock or reveal: the peak lands on the last frame.
- **Object state.** Once a prop changes state, every later shot cites the new variant (`<prop>-<state>`), never the original sheet.

### 5. Assets Carry Continuity, Text Does Not

- **One asset per state**: clean jacket, soaked, bloodied are separate character sheets; an object that breaks or falls gets its own variant sheet. Splitting is cheaper than arguing with the model in text.
- The model does not make what no image shows: a cast shadow from an off-screen character, an object in a state its sheet does not show, the other side of a set.
- **A missing reference is fixed by a new asset, created before the take** — never by rewriting the shot into a workaround or writing the text louder. Flag it at the scene-plan or prompt review. Before asking for a new asset, check whether an existing one already covers the need (a variant made for a neighbour shot, a reverse plate).
- A wrong character sheet (inverted side, outfit from another scene) passes straight into the take: fix the sheet, not the prompt.

### 6. Feed the Previous Take Back

When a take follows another take **in the same location and the same view**:

- Extract the **real last frame of the retained previous take** (`ffmpeg -sseof -0.1 -i prev.mp4 -frames:v 1 last.png`) and look at it. Write the next take's opening from what was actually delivered (positions, sides, wetness, props, light), not from the planned end state.
- Attach that frame as the **last reference image**, captioned in the prompt: "@ImageN is the last image of the previous take of this same scene: take the positions, orientation, light and set from it, not the framing; the take does not start on it." It is not a start frame.
- Do not send it for a chained shot (that one uses the frame as a real start image), a reverse shot, an insert or an extreme close-up, and never take it from an insert or extreme close-up.
- Write the new framing precisely (shot size, angle, lens), otherwise the model copies the framing of that image.
- The text must not contradict the image (a character on the other side, an object moved without a shown gesture): the model follows the image and the take comes out wrong. A side change goes through a declared reverse shot and its reverse plate.
- Cap chains at 2–3 takes before re-anchoring on the canon assets: the previous frame drags its own drift. Continuity is carried primarily by the assets.

**Start frame or reference image: decide per shot, before the prompt.** On Seedance (fal 2.0 and 2.5, Higgsfield) the two modes are exclusive. `image_to_video` takes a start frame (and optionally an end frame) but no character sheets and no voice references. `reference_to_video` takes up to 9 (2.0) or 30 (2.5) images plus voices, but nothing pins its first frame.

| Situation | Mode | Why |
|---|---|---|
| Default: any shot with a recurring character, dialogue or a voice | reference (sheets + plate + voices) | identity and voices come only from references; a start frame alone drifts the face and loses the voices |
| Same scene, same view as the previous take | reference + previous take's last frame as the LAST reference, captioned "positions, light, set, not the framing" | carries positions without copying the framing |
| A true continuation of the same movement across two takes (the cut is invisible: same camera, same action) | start frame = real last frame of the previous take, and only one shot in that take | the only way to get a seamless match; accept the identity risk, keep it short, no dialogue |
| Splicing a replacement for one shot inside a block | start frame = last frame of the kept shot before it, short segment | point fix (`.agents/skills/seedance-2-5/SKILL.md`), not a continuity method |
| A composed opening the model can't reach from text (exact staging, a prop state, a set seen from an unusual angle) | generated keyframe still (Nano Banana, edit mode from sheets + plate), then decide: start frame if no dialogue or recurring face, otherwise keyframe as the LAST reference | the still carries the composition; for dialogue it carries it as a reference so voices stay |
| Reverse shot, insert, extreme close-up | reference, never a previous frame | a previous frame copies the wrong wall or framing |

Rules: never chain more than 2–3 start frames in a row; re-anchor on the sheets. Always look at the frame you will send as a start frame, because its defects (hood, scar, tiny door) become the opening of the new take. Log the mode per shot in the scene plan (`start_frame` / `reference`) so the take review can check it.

### 7. Write a CONTINUITY LOCKS Block in Every Take Prompt

Generated from the ledger, positive phrasing, after the shot breakdown:

```
LOCATION MAP: <GEO map of the location, verbatim>
CONTINUITY LOCKS: Mara is soaked through, hair plastered, dark jacket dripping, in every shot.
  The extraction cable lies on the floor at frame right where Reyes dropped it.
  Exactly two people in frame. Mara stays frame left, Reyes frame right, throughout.
  Light: cold white daylight from the bay window behind Reyes, frame right.
FINAL FRAME: <who is where, looking where, holding what>
```

Prefer positive locks ("ring finger empty") to negations ("no ring"): a "no X" list calls X into the frame. Keep short negatives only where the model's habit is the problem (subtitles, duplicates, music).

### 8. Review Before and After Every Take

**Prompt review (before spending):** each character's state matches the ledger; the location map is pasted; sides and eyelines match the previous shot; no entry from off-screen; final frame reachable; no beat already played is replayed and no beat reserved for the next shot is anticipated; every needed reference exists. When the previous take already has a review, start from the defects it really showed.

**Prompt preflight (mandatory, before the first paid take and after any prompt edit).** Run `scripts/prompt_preflight.py` on the scene plan's clips, in story order, with `--trait` for each asymmetric trait and `--plate-glob` for the location plates. It reports ERRORs, which block the take, and WARNs:
- missing LOCATION MAP / CONTINUITY LOCKS / FINAL FRAME, or shot time ranges with gaps;
- `@ImageN` or `@AudioN` cited but not attached (or attached but never cited), and provider limits;
- block sizing (§ 9);
- people count and frame sides not stated;
- entries, freezes and pop-ins;
- a trait repeated in a clip whose shots never show it (it drifts onto the face or neck);
- start-frame/reference mode not declared, or a same-location take without the previous take's last frame (§ 6);
- location plates not scale-checked;
- a FINAL FRAME that names no position.

It also prints a handoff table (previous FINAL FRAME → next opening). Read every row: the opening must start where the previous take left each character. "Theo's face, waiting" followed by "Theo runs across the room" is exactly the jump that cost a retake. Fix every ERROR, and answer every WARN (fix it or justify it in the report), before spending.

**Take review (after each take, before the next prompt):** sample frames at each shot boundary and the last frame, look at them one by one, and log in `asset_manifest.metadata.take_reviews`:

- state reset (dry after soaked, wound gone, object whole again) — **major**;
- character or object moved versus the previous take with no motive in the shot — **major staging**;
- framing, angle or pose copied from the previous-take reference instead of the planned framing — **major camera**;
- swapped sides, eyeline toward the wrong edge, axis jump — **major**;
- light source or color changed inside the scene — major;
- identity drift (asymmetric trait on the wrong side, outfit) — major.
- **proportions**: a door, opening or piece of furniture smaller than a character, a figure in the background bigger than the people in front, a character floating off the ground or beyond a ledge, a giant body part entering the frame — major;
- **geography**: the background behind a character is not the one their side of the axis shows on the plates (shot on Mara must show the reverse plate, not the main view) — major;
- **set and position jumps between takes**: compare the previous take's last frame with this take's first frame. A character who is already somewhere at the end of take N but back at the start position in take N+1, or who is lying down when they should be seated, is a major staging break.

**Audit sheet (mandatory before you report the takes).** Run `scripts/raccord_sheet.py` on all retained takes in story order. Each sheet shows the previous take's last frame, then the first and last frame of each detected shot, and the script writes a `raccord_checklist.md` with one row per cut. Read each sheet on its own with vision; do not use a whole-episode contact sheet, it is too downscaled to show positions or proportions. Fill in every row for every check (positions and sides, background against the plate, proportions, states, traits, eyelines). The review is finished only when no cell is empty. A per-take 1 fps strip is not enough, because it never shows the cut between two blocks. List every major defect in the report before asking for retakes, plus the plate that caused it when one did.

Fix one thing per retake. A tight framing refused twice for camera reasons is not fixed by one more instruction: recrop in the edit or change the staging. A shot shorter than 2.5 s is generated longer and cut to its start: judge the action in the kept part; the fix is "the key gesture in the first half-second", not an acting note.

### 9. Size Multi-shot Blocks, Then Retake Shot by Shot

One generation can carry several consecutive shots (a "block"). Rules ported from the operator's motion-tools-agent multishot planner, measured on Seedance 2.0/2.5 takes:

- **Block size.** Mixed cast (different characters across the shots): at most 3 shots, 12 s and 3 characters — 3 shots in 12 s is the largest size where every cut came out. Same cast in every shot: up to 4 shots and 15 s. Prefer 2 shots per block; on 3-shot blocks one cut in two went missing in one series. Longer blocks (5 shots in 16–18 s) merge or drop cuts.
- **Speakers.** At most 2 speaking characters per block (lip-sync holds for 2 voices, not more); a shot with more speakers is shot alone.
- **Shot alone (single-shot take):** a shot chained to the previous take's last frame (real start image), a shot with more speakers than the block allows, a short insert when the cast differs, and any shot that would put a character and one of their state variants (base sheet + `<name>-soaked`) in the same block.
- **Same or neighbouring location** inside a block; a hard location change splits the block unless the two places are declared neighbours (alley → workshop door).
- **Prompt.** One shared header (style, references, characters, location maps, locks), then one paragraph per shot with its time range — `Shot N (a-b s): …` ending in `Hard cut.` (`.agents/skills/seedance-2-5/SKILL.md`). If a provider filter rejects the word "shot", write `CUT N (a-b s): … HARD CUT to CUT N+1`.
- **Split at the real cuts.** The prompt's time ranges are not exact: detect the cuts in the delivered take (`ffmpeg … -vf "scdet=threshold=10,metadata=print:key=lavfi.scd.time"`), assign them to the planned shots, and treat a missing cut as a failed shot.
- **Judge every shot separately**, with the take review of step 8.
- **Retake only the refused shot**, as a single-shot take with the block's references (plus the last frame of the kept shot before it, step 6), then splice it back in place (`ffmpeg` trim + concat, audio included). Accepted shots are frozen: never regenerated because a neighbour changed.

## Good / Bad Examples

| Situation | Bad | Good |
|---|---|---|
| Arrives soaked indoors | C7 (indoor chase) says nothing about water, C8 says "soaked and dirty" | Ledger: soaked since the rain in C5; C6, C7, C8 all carry "soaked through, hair plastered"; soaked-jacket sheet attached |
| Entrance | "Mara enters from the left" | "Mara already stands just inside the door, frame left, water dripping onto the marble" |
| Dialogue | Both characters "face each other", sides unstated | "Reyes frame right, eyes toward frame left; Mara frame left over-shoulder foreground, eyes toward frame right" |
| Broken prop | Later shot cites the intact prop sheet | Later shot cites `mirror-facedown` from the moment it fell |
| Missing reverse view | Prompt rewritten so the camera "doesn't really see" the other wall | Generate `salon-reverse` plate, then shoot |

## Common Pitfalls

- Treating the kit's per-clip prompts as independent: each was written alone, so states mentioned in one ("soaked") are missing from its neighbours. Build the ledger across all of them first.
- Letting a state word appear only in the shot where it matters.
- Writing positions relative to a character ("to her left") instead of from the camera.
- Asking for stillness ("stands still", "freezes"): the model freezes the whole room; give a small continuous activity instead.
- Compensating for a missing asset with more text.
- Judging a take from one frame: check the shot boundaries and the last frame.
- Expecting a state sheet alone to hold a garment state: with a hood-down/soaked sheet and "hood down" in every shot, Seedance 2.5 still pulled the hood back up in wide and fast-action shots (run, cable slide). A `HOOD LOCK` line at the top of the prompt fixed the close and medium shots only. Plan the garment state so wide action shots tolerate it, or splice the good shots of two takes (`ffmpeg` trim + concat, audio included) instead of a third retake.
- Writing "a handwritten signature" in condensation or on paper: the model may write a readable wrong word. Keep the name off-screen or keep the illegible take.
