# Fiction Shot Direction — Camera, Acting, Dialogue and Sound for Generated Takes

## When to Use

Read this with `skills/creative/shot-continuity.md` when planning and prompting a narrative piece made of generated takes (cinematic fiction, micro-drama episodes, series). Continuity keeps the world stable; this skill makes each shot readable and motivated: camera chosen from intent, acting written so a video model can perform it, dialogue that lip-syncs, sound and music that carry the cut, and episode dramaturgy that holds a vertical-feed viewer.

Distilled from the operator's motion-tools-agent project (its storyboarder, screenwriter, showrunner, sound-designer, composer and art-director agent instructions and its Seedance prompting notes), validated on real Seedance 2.5 takes. Sources they condense (MIT): jtydhr88/screenwriting-skills, zenstory-ai/drama-skills, nolanx-ai/nolanx.ai, Emily2040/seedance-2.0, Nagacash/narrative-film-direction, Fred-In-tech/story-first-video-editing, fal-ai-community/skills, OSideMedia/higgsfield-ai-prompt-skill; Walter Murch's editing rules. General camera vocabulary lives in `skills/creative/video-gen-prompting.md`; provider syntax in the Layer 3 skill (`.agents/skills/seedance-2-5/SKILL.md`).

## 1. Camera Motivated by Intent

Write the intent first (what the viewer must feel or learn), then pick framing, angle, lens and move that serve it together. Change any choice you cannot justify with "because the intent is…". Every shot adds new information, a stronger emotion, or a clearer action than the previous one.

| Intent | Framing | Angle | Move (speed) | Lens | Length |
|---|---|---|---|---|---|
| Establish who is where | wide | high | crane up (slow) | wide | long |
| Isolation | extreme wide | overhead | pull back (slow) | wide | long |
| Realisation | close | eye level | slow push-in | normal | long |
| Reveal that flips everything | medium | eye level | dolly zoom (medium) | normal | long |
| Shock | close | eye level | crash zoom (fast) | normal | short |
| Sudden turn to the cause | medium | eye level | whip pan (fast) | wide | short |
| Confrontation | medium | over shoulder | slow orbit | telephoto | medium |
| Domination, threat | medium | low | tilt up (slow) | wide | medium |
| Vulnerability, humiliation | medium | high | tilt down (slow) | normal | medium |
| Confidence, intimate listening | close | over shoulder | static | telephoto | long |
| Heavy silence | medium | eye level | static | normal | long |
| Fear, panic | close | dutch | handheld shake | wide | short |
| Run, escape | wide | worm's eye | low tracking (fast) | wide | medium |
| Determined walk | medium | eye level | lateral tracking | normal | medium |
| Clue that tells the story | insert | overhead | slow push-in | macro | short |
| Decisive hand gesture | insert | eye level | static | macro | short |
| From detail to face | close | eye level | rack focus | telephoto | medium |
| Suspicion, watching | medium | POV | slow pan | telephoto | medium |
| Triumph | wide | low | slow orbit | wide | medium |
| Departure, loss | wide | eye level | pull back (slow) | normal | long |
| Comic beat | medium | eye level | static | wide | medium |
| Episode hook | medium | low | push-in (fast) | wide | short |
| Reaction to a line | close | eye level | slow pan | telephoto | short |

- **Power through camera height**: low angle for whoever takes the upper hand, high angle for whoever loses it, eye level for equals. When the balance flips, flip the angles.
- **Lens for psychological space**: telephoto isolates or compresses a duel, wide opens the set or pressures a close subject, macro for the texture of an insert.
- **Grammar evolves**: tighter, more contrast, more active camera as tension rises. Break the pattern once, at the turn (the only static shot, or the only very wide one). Never ask for several static shots "for restraint".
- A move must not contradict the beat (whip pan on a silence, static on a shock). One camera move per shot, with its end point named ("slow push-in, ends on her eyes"). "Fast" is the word that degrades output most; a whip pan needs at least 0.8 s of blur.
- **Vertical 9:16 composition**: eyes in the upper third, floor and foreground in the lower third; stack two characters in depth rather than side by side and small. Build three depth layers: blurred foreground (shoulder, door frame), sharp subject, background landmark.
- Light carries mood through its source: hard contrast and dense shadows for threat, soft open light for trust. Never add light without a source.

## 2. Dialogue Coverage

- Cover a dialogue as a funnel: an opening wide that shows who is where, then shot/reverse shot over the shoulder, with the listener's shoulder in the foreground. Keep the close-up for the line that tips the scene.
- Give a reaction shot to lines that hit. The camera stands on the side of what provokes the reaction; the gaze passes near the lens without looking into it.
- Move a character only when something changes for them (power, fear, discovery). Distance says intimacy or threat: bring them closer as the scene tightens, apart as it breaks.
- **One speaking face per take.** Reliable lip-sync: 3–8 s, one face talking, medium close-up or tighter, locked or slow push-in camera. The other line goes off-screen (speaker out of the cast) or into its own reverse take. A speaker seen from behind in the foreground does not count as a talking face. Two lines in one take are said one after the other, never overlapping, ending at least half a second before the cut. A line is never split across two shots.
- Bind each voice reference to its character with a **conditioning sentence written once and pasted verbatim** every time they speak ("Mara speaks with the voice timbre of @Audio1: low, slightly hoarse adult female voice, tired"). Rewording destabilises the voice.
- Lines go in the audio block, language marked, in quotes: `Mara (wary) says in English: "…"`. Add "each speaks only the line in quotes; nobody else speaks" and "lips at rest" for silent faces. Do not time lines in milliseconds (not respected). Repeating words of a line invites burnt-in subtitles.
- Delivery is given as acting notes (intensity, pace, breath, the stressed word, where the voice cracks), never as a clause that reads like a sentence ("as if it hurt"): the model has spoken such clauses aloud.

## 3. Writing Motion for a Video Model

Write what a camera records, in English:

- No idioms or comparisons ("his face fell" renders a face changing color). Color comes from a source ("cold blue light from the phone"), never from a mood.
- **One main action per shot**, carried by one character (concrete verb plus its follow-through), at most one secondary action. Every other character gets a continuous occupation ("Mila keeps stirring her tea, eyes on Tom").
- Never ask a person to "stand still", "freeze", "not move": the model freezes the whole room. A gesture that ends in a hold ("raises the glass and holds it") is correct; without a follow-through the model plays it backward.
- Emotion becomes a visible gesture, never a state word: not "she is sad" but "folds the letter and does not look up". A reaction = one simple feeling + one physical anchor ("stunned, Tom lowers the cup halfway and swallows"). Play subtext by contradiction (she smiles while gripping the cup).
- Props: write the hand and its side ("right hand grips the mug by the handle, tilts it, sets it down on the cloth") and the prop's final state. A change of grip or an object flipped in the air goes in its own shot, often an insert. Inserts are causally motivated and name whose detail ("his hand gripping the cable").
- Write the voluntary action, never the accident as a goal (coat caught, slip, spilled glass): the model plays it on purpose. Leave the consequence for the next shot.
- No prestige adjectives (cinematic, epic, stunning, 8K, masterpiece) and no numeric durations in action text: write the concrete state wanted.
- Violence: gesture then fall or recoil, never contact on the body ("swings her fist upward, the guard is thrown backward off his feet"; "handgun raised, arm extended toward frame left", never "aims at X"). No visible blood or wound beyond a torn sleeve or a hand pressed to the ribs. Threat reads better through stillness, sound and light (a key scraping the lock, held breath) than a named weapon.
- Text-filter words to avoid: shot (write "view"), child (write "little boy"), skin (write "surface"), bare, whip.
- Gestures the model fails every time: arm plunged into water to the elbow, an apparition forming in a mirror, a character spinning in place in 1.5 s, reflections in general. Replace with a simple readable gesture or split into two shots.
- Shot under 2.5 s: the model generates at least 4 s and the edit keeps the start; write the key gesture "in the first half-second". A two-beat action needs at least 2.5 s.
- A memory tint (sepia, monochrome) is a grade applied in the edit; do not write it in the shot text.

## 4. References and Character Sheets

- Freeze an English identity anchor per character (face shape, eyes, hair, build, silhouette, the side of every asymmetric trait from the character's point of view) and paste it verbatim; the variable part (expression, pose, framing, light) goes in a separate block.
- Give each attached image one role and say what must not pass ("take the outfit only, not the pose or the background"). Never cite a reference handle that is absent from the shot: the model forces it into the frame. 4–5 references per take is the sweet spot.
- Location plates are empty of people; write the wanted state ("blank plaster wall") rather than a list of negations.
- If identity drifts, strengthen the anchor or restart from the approved sheet in edit mode; never add style words to fix identity.

## 5. Sound and Music

- Sync an impact sound to the frame of contact seen in the take, not to the start of the shot or of the gesture.
- Sound leads picture: in the last 300 ms of a shot, place the sound of what comes next (footsteps, door, engine).
- Ambience is a stable bed with no isolated event (no single horn, thunderclap or bark): it runs under every shot of the location, and a one-off event would repeat or jump at the cut. Keep the same sonic grain for a location from shot to shot; change it only at a real change of place or time. One continuous room-tone bed per location, laid in the edit.
- After a strong line or reveal, leave 0.5–1 s with only the ambience: silence lands the emotion.
- Phone speakers: every impact and accent needs a crisp attack in the mids and highs (crack, clap, glass, breath), not just low end.
- Music: a stable tempo in BPM so silent cuts can land on beats; a recognisable mid-range motif from 0 s (no slow intro over 1 s — viewers decide to stay in 3 s); the tension peak on the cliffhanger shot, not before; after the emotional peak, thin out for 1–2 s to one held element instead of cutting to silence. Music never contradicts the scene.

## 6. Episode Dramaturgy (Micro-Drama, Series)

- Sum up the episode as one dramatic question that the ending leaves open. Logline: who wants what, who blocks them with what leverage, what it costs.
- Open on an unstable situation readable without knowing the characters: a risky gesture already under way, proof contradicting a word, a consequence landing. Never a mood or a landscape (unless a title opening is planned). Enter as late as possible; what came before is guessed from an object, a gesture, a power balance — never a recap.
- Each beat is an action or reaction that changes something (who dominates, what is known, what is lost). Chain beats with "therefore" and "but", never "and then". Put a power reversal around the first third and another around two thirds, each coming from the opponent. Escalate by changing method, not volume.
- Plant early, with an innocent reason, the object or line that will pay off; bring it back changed by another character's gesture, underlined by an insert. Every twist comes from something already shown.
- The hero wins or loses by their own act — no rescue or chance. Give the episode a net result (won, lost, refused, revealed) before the ending, then cut on the peak: a threat already launched or a decision already made, with no cool-down.
- Dialogue: ask first whether the beat works without words. Each line is an attack, a parry, a threat or a retreat; nobody says what both already know; never name the feeling; end the line on the word that hits. Give each character a one-word trait their first line shows; pass a face-to-face through a third object they fight over, hand over or break.
- Play melodrama and comedy straight: characters react very seriously to the absurd.
- From one episode to the next, change at least one state (who knows what, who dominates, what is lost, what becomes irreversible); what the previous episode left comes back in the hook through a visible object or situation.

## Common Pitfalls

- Camera chosen for variety alone, not for intent.
- Two talking faces in one take, then blaming the voice model for bad lip-sync.
- Emotion written as adjectives; the model renders nothing or the wrong thing.
- An ambience with one-off events that jump at every cut.
- An episode that ends on "we'll know soon" instead of a result and a launched threat.
