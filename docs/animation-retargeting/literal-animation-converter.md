# Literal Galaxy animation converter

## Scope

The first reusable adapter is Luigi Galaxy to the Sluggers Waluigi rig. Its
approved V2 walk is the golden fixture. The converter keeps the V2 rest-frame
math and multi-output Waluigi arm-branch mapping, samples the original action
curves at their original key times plus quarter-frame intervals, and bakes a
constraint-free action on a duplicate rig and duplicate preview meshes.

Generic evaluation, rest-frame conversion, keyed-bone coverage checks, baking,
replay validation, and preview rendering live in
`tools/blender/convert_galaxy_action.py` (implementation:
`tools/blender/prototype_direct_luigi_walk_literal.py`). Character differences
live in `tools/blender/profiles/luigi_to_waluigi.json`: rig and action owners,
bone map, branch outputs, object scale, root-motion bone, and fixed-fist profile.
The fist profile also supplies the left/right bone correspondence and the local
reflection basis used to preserve a symmetric closed fist.
The V2 object-root relocation remains part of the profile's converter contract:
Galaxy root displacement becomes armature-object translation, and Waluigi
`bone_6` is held at its initial local translation. Do not apply object scale.

The profile maps `Center` to Waluigi `bone_5` for clips where Galaxy animates
that center joint. `JointRoot` maps to Waluigi `bone_6`, whose translation is
rebased to object space by the V2 rule. `HandL0` and `HandR0` are explicitly
ignored because the profile fixes both Waluigi hands in their validated closed-
fist pose. `PartsControl` is ignored because this source model-specific control
has no Waluigi equivalent. Each is reported whenever a clip keys it. Any other
animated source bone without a profile mapping fails closed. Bank inventory
identifies `All_Root` as the remaining unmapped control, used by some cinematic
and long-form clips; those clips remain unsupported until the profile has a
validated object-root policy for it.

## Animation bank inventory

`inventory_luigi_bck_bank.py` reads the raw BCK files with the installed
J3DBlend parser, against the 30-joint Galaxy source armature order. Categories
are filename heuristics; loop type, frame length, joint count, and animated
bone/channel lists come from each BCK. The report is generated locally and
ignored by Git because it reflects external proprietary source assets.

The current inventory contains 431 clips, all parse with 30 joints. Filename
families: 15 walk, 22 run, 49 jump, 34 swim, 39 fall/land, 52 wait/idle, 20
hurt/reaction, 12 action/attack, and 188 other. Six source controls are never
animated in the bank (`CapPosition`, `Cap0`, `EyePosition`, `Eye0`,
`FacePosition`, `Face0`). The profile maps 20 animated Galaxy bones, and
explicitly ignores `HandL0`, `HandR0`, and `PartsControl`. `All_Root` is keyed
in 38 clips and is the sole remaining unmapped source control; the converter
rejects those clips. The three tested clips do not key it.

| Test clip | Source range | BCK loop | Keyed source bones | ignored source channels |
|---|---:|---|---:|---|
| `luigirun.bck` | 0–61 | LOOP | 17 | none |
| `luigijump.bck` | 0–24 | ONESHOT | 18 | none |
| `swimbreast.bck` | 0–59 | ONESHOT | 18 | `PartsControl` |

## Raw BDL/BCK import

`import_luigi_bck_clips.py` stages copies of only selected source files in a
temporary directory because J3DBlend searches sibling BCK files. It imports
the original BDL and selected BCKs through the installed add-on, preserving
their action timing and interpolation semantics. It does not alter the source
assets. This relies on J3DBlend being installed for Blender 5.2. Its current
build needs a small runtime workaround for the `ic_sc` import property and
prints a harmless cleanup traceback at process exit; neither workaround code
nor the third-party add-on is copied into this repository.

## Run a conversion

From PowerShell at the repository root, first import only the required raw
clips into a disposable copy of the asset workfile:

```powershell
$assetRoot = 'C:\MultiGalaxy1-Assets\Working\Galaxy\LuigiFullAnimationTest'
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
& $blender --background 'tools\blender\reports\luigi_walk_semantic_waluigi_both_arms_v7_texture_repaired.blend' `
  --python 'tools\blender\import_luigi_bck_clips.py' -- `
  --bdl "$assetRoot\luigi.bdl" --bck-dir $assetRoot `
  --clips luigirun luigijump swimbreast `
  --output 'tools\blender\reports\luigi_bck_run_jump_swim_imported.blend' `
  --manifest 'tools\blender\reports\luigi_bck_run_jump_swim_imported.json'
```

Then call the converter once per action, selecting the imported raw BDL rig and
action. Set a new preview/report/render directory for each run. For example:

```powershell
& $blender --background 'tools\blender\reports\luigi_bck_run_jump_swim_imported.blend' `
  --python 'tools\blender\convert_galaxy_action.py' -- `
  --profile 'tools\blender\profiles\luigi_to_waluigi.json' `
  --source-rig 'luigi_armature' --source-action 'luigi_armature_luigirun' `
  --preview 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v1.blend' `
  --report 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v1.json' `
  --renders 'tools\blender\reports\direct_galaxy_luigirun_waluigi_v1_renders'
```

`--start`, `--end`, and `--step` optionally override the source action range
and 0.25 default sample interval. All original key times in the chosen range
are added even when they are off the regular sample grid. The preview creates
front and side renders at five evenly spaced integer frames. That sampling
approximates nonlinear quaternion basis conversion between keys; it does not
change source timing or claim visual approval.

## Regression gate

`test_literal_converter_golden.py` compares the generated walk report with the
approved V2 report across every frame/bone basis matrix and armature-object
location. The profile-driven walk must match all 241 quarter-frame samples to
near numerical precision. Preview files and generated reports remain local;
only the converter code, profile, tests, and this specification belong in Git.

## Current coverage limits

The 30-joint source has extra non-deforming/model controls such as `PartsControl`
and helper joints. The chosen walk/run/jump/swim actions exercise different
subsets. A clip is not considered supported solely because it imports: every
animated joint must be mapped or listed explicitly as an ignored source
control, and each disposable preview still requires front/side visual review.
This first milestone does not batch-convert the bank and does not claim
Mario-to-Wario compatibility. That pair needs its own character profile and
golden regression fixture.
