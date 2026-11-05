# Launcher character selection and identity specification

Status: design specification only. No launcher UI, physics patch, gameplay hook, rendering change, protocol migration, or asset modification is implemented by this specification.

## 1. Objective

Give each local participant a persistent choice of Mario, Luigi, Wario, or Waluigi before joining/hosting. Carry the confirmed choice safely as character identity through launcher configuration, game-side bridge state, and online messages. Keep the player/session identity, physics profile, presentation model, and locale independent.

The launcher interaction takes inspiration from New Super Mario Bros. Wii's multiplayer character-select flow: choose from a character roster, preview, then confirm. It does not reproduce Nintendo UI assets or imply a relationship with that game's implementation.

## 2. User flow and preview

The character page appears before lobby readiness. A four-item carousel supports keyboard and conventional gamepad navigation. The highlighted item displays name and concise movement traits, with a slowly rotating 3D preview. Confirm saves the selected default and marks the participant ready; Back returns to the lobby without changing the saved choice. A missing preview asset is shown as unavailable and cannot be confirmed. Mario is the first-run default.

Preview rendering is launcher-only and must not load or mutate the Dolphin process. Mario and Luigi preview resources may be read from the user's PAL game installation under existing asset-use constraints; they are never extracted into or redistributed by MultiGalaxy1. Wario/Waluigi models and retargeted animation are supplied separately by MultiGalaxy1-Art. This repository must not edit, copy, package, or assume those files are present. Until an approved asset manifest is installed, those choices may be shown as unavailable rather than substituting a misleading model.

The UI renderer and asset-loading crate remain an implementation decision. The choice screen must not delay lobby networking, require a specific graphics backend, or expose technical implementation details to users.

## 3. Stable identifiers and data boundaries

Use a closed `CharacterId` enum with stable wire/config values: `1=Mario`, `2=Luigi`, `3=Wario`, `4=Waluigi`. Parsing rejects zero, unknown values, truncation, and future values unless an explicit negotiated protocol version supports them. Never silently map an invalid value to Mario.

Keep distinct values:

| Value | Meaning | Authority |
|---|---|---|
| `SessionPlayerId` | Participant identity assigned for one session | Server |
| `CharacterId` | Participant's chosen character | Confirmed client choice, validated by server |
| `PhysicsProfileId` | Movement-rule selection for the character | Deterministic game rules keyed by character and ruleset version |
| `PresentationAssetId` | Local model/animation family | Client-local asset registry keyed by character |
| locale | Text/voice presentation preference | Client-local installation |

No physics values are inferred from the model or network identity. Rendering consumes `CharacterId`/presentation asset and pose. The physics system consumes the versioned ruleset profile. Locale and local asset paths never enter deterministic gameplay state or network authority.

## 4. Persistence

Persist the confirmed default in the launcher's versioned per-user settings file using a platform-appropriate user configuration directory (or a documented portable config location selected by the user). Schema includes a format version and `default_character_id`; it excludes game paths, save contents, locale files, and network secrets. On read, validate the enum and schema; unknown/corrupt files leave the user at Mario with a clear recoverable settings message. On write, write a sibling temporary file, flush/close, then atomically replace the old settings where supported. Do not persist an unconfirmed highlight.

If persistence fails, allow the user to continue with a session-only confirmed selection and show that it will not be saved. Do not block offline launcher use on a settings write failure.

## 5. Multiplayer and lifecycle semantics

- Duplicate character selections are allowed. Session identity is unique; character choice is not. Do not auto-substitute a duplicate character.
- Lobby join sends the validated `CharacterId`; the server binds it to the assigned `SessionPlayerId`. A peer's display model is selected from this mapping. Position packets do not redefine identity.
- A reconnect resumes the same identity only through the server's existing authenticated/reconnect policy; otherwise it receives a new session identity and explicitly resends character selection. No stale peer model survives timeout/disconnect.
- During scene transition, retain session identity and character choice but invalidate scene-local render handles and pose generations. A renderer may display a character only after a fresh scene/model binding succeeds.
- Changing character during a session is a reliable, explicit lobby/session event with a monotonically increasing selection revision. Initial implementation should permit it only while all players are in lobby or at a server-approved safe transition. Server rejects mid-scene changes until replacement physics/presentation lifecycle is proven. Position state is cleared/reinitialized for the affected generation; other clients update presentation atomically.
- Session player IDs remain stable across character changes. Physics-profile changes are versioned session state and must be accepted by the server before activation.
- Player departure hides their remote presentation after the existing bounded stale timeout; rejoining does not resurrect cached transforms or old scene generations.

## 6. Protocol evolution and bridge boundary

Do not reinterpret M4 protocol v1's current player field. It currently conflates the two supported character/player labels and is limited to Mario/Luigi; keep it byte-for-byte unchanged for current clients. Introduce a separately negotiated protocol version with explicit `SessionPlayerId`, `CharacterId`, character revision, physics-ruleset ID, and scene generation. The handshake and reliable character-change event carry character metadata; high-rate pose messages reference the session ID and do not repeat character selection.

The launcher validates the closed enum before writing the bridge mailbox. The bridge format adds a schema/version field and validates every ID before publishing. Rust networking validates it again at ingress and egress. Unknown versions/IDs fail closed: refuse that peer or hide the remote, never render a default character. Until the new bridge and protocol are implemented and negotiated, only the existing M4 semantics apply; this spec does not claim support for four characters in live play.

## 7. PAL movement evidence and profile composition

The verified target is PAL RMGP01 Rev 0. `validate-mario-map` binds the DOL and Petari symbol file before use. Petari's `MarioActor::init2` source selects `MarioConstTable` index 0 for Mario and index 1 for Luigi after `MR::isPlayerLuigi`; the PAL call at `0x802AEDBC` branches to `isPlayerLuigi` at `0x803CC5A4`. This is static source/DOL evidence, not proof that changing a runtime selector alone safely changes an existing actor.

Petari's `MarioConstTable` names the relevant fields: jump-height array at `+0x00`, jumping gravity at `+0x24`, walk speed at `+0x224`, standard stop inertia at `+0x228`, and stop inertia at `+0x238`. These are source-defined structure offsets, not new patch addresses. In the validated PAL DOL's `gMarioConstDefault` (`0x805B0670`) and `gMarioConstDefault2` (`0x805B0E14`), observed normal-table values are:

| Parameter | Mario | Luigi | Evidence/interpretation |
|---|---:|---:|---|
| Walk speed | 13.0 (`0x41500000`) | 15.0 (`0x41700000`) | Raw PAL DOL constants; consistent with faster Luigi |
| Standard stop inertia | ~0.94 (`0x3F70A3D7`) | ~0.99 (`0x3F7D70A4`) | Raw constants; higher retention supports a slipperier Luigi target |
| Stop inertia | ~0.85 (`0x3F59999A`) | ~0.97 (`0x3F7851EC`) | Raw constants; supports same handling distinction |
| Jump-height entry 0 | 22.0 for both (`0x41B00000`) | 22.0 | Raw constants; does not by itself imply a higher Luigi jump |
| Jumping-gravity entry 0 | ~1.8 (`0x3FE66666`) | ~1.6 (`0x3FCCCCCD`) | Raw constants plus Petari `MarioJump` use of indexed value; lower Luigi gravity is consistent with longer airtime, but in-game jump height is an inference until measured |

Wario/Waluigi targets are compositional design proposals, not existing PAL profiles or runtime patches:

| Character | Handling/traction source | Speed/jump source | Intended result |
|---|---|---|---|
| Wario | Luigi-style higher inertia/slippery handling | Mario-style slower speed and low jump target | Slippery, slow, low jump |
| Waluigi | Mario-style easier handling | Luigi-style fast speed/high-jump target | Easy handling, fast, high jump |

The final ruleset must use verified semantic parameter groups and gameplay tests, not blindly splice memory ranges or assume table layout equivalence. Character mechanics remain independent of model swap. A presentation-only character change cannot claim to change physics.

## 8. Risks and constraints

- Current M4 v1 is not sufficient for four distinct character IDs; a negotiated protocol revision and bridge schema migration are required.
- Preview assets may be unavailable, legally restricted, or incompatible with the selected renderer. Do not bundle game assets or MultiGalaxy1-Art content.
- PAL constants are static defaults. Runtime transformations, states, gravity-relative jumps, and control tables can select other values; displayed trait copy is descriptive until controlled gameplay validation.
- A server must ensure all peers use the same physics-ruleset version. Localized resources remain outside synchronized checksums.
- This feature must not depend on Dolphin NetPlay, local split-screen, or changes to AllStars.

## 9. Acceptance and testing plan

1. Unit-test enum serialization/deserialization, unknown IDs, schema migration, atomic settings writes, corrupt settings fallback, and unconfirmed-selection behavior.
2. UI test all four carousel options, preview spin, unavailable assets, confirmation/cancel, and keyboard/gamepad navigation.
3. Verify duplicate character choices create distinct session IDs and both render the selected model.
4. Integration-test join, reconnect, disconnect timeout, scene generation reset, lobby-only character changes, stale and unknown protocol versions, and mismatched physics-ruleset rejection.
5. Test localization separately: Russian/French/English PAL clients keep local text/voice while the same stable character IDs and physics-ruleset version are synchronized. This is an acceptance target, not a verified compatibility claim.
6. Measure Mario/Luigi movement in a controlled PAL scene before describing gameplay parity; test composed Wario/Waluigi only after an authorized runtime physics milestone.
7. No UI preview result, static constant, synthetic protocol test, or successful build is evidence of Dolphin rendering or online multiplayer.

## 10. Implementation gates

First implement Rust enum/config/persistence and synthetic protocol tests without changing M4 v1. Then select and document a launcher UI/preview backend. Only after negotiated protocol/bridge migration is specified may character metadata be sent live. Physics profiles and rendering remain separate later milestones. This spec authorizes no runtime physics patch, model rendering change, Dolphin modification, or asset change.
