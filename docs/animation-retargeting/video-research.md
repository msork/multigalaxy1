# Video research for clip-specific retarget diagnostics

Research date: 2026-11-04

## Retrieval status

The public YouTube player metadata was reachable for both links, so titles and descriptions could be checked. The watch pages could not be opened through the available browser fetcher. Video 1 advertises no caption tracks. Video 2 advertises English auto-generated captions, but its timed-text endpoint returned an empty body. No transcript or playable video content was available to verify the techniques shown. This note deliberately does not infer tutorial steps from titles or descriptions.

## Video 1

[Super Mario Galaxy - How to Create a Custom Character (Very Difficult!)](https://www.youtube.com/watch?v=GKDDP4vZfoc), by GamerLillie.

The description says the creator demonstrates making a custom character for Super Mario Galaxy, replacing Mario with Liko, and lists SuperBMD, Dolphin, WiiExplorer, Blender 3.6, and Blenxy among the tools/resources. It does not, by itself, establish that the video demonstrates animation retargeting, rest-basis conversion, or multi-branch deformation handling. No captions were offered, and the video could not be reviewed directly.

## Video 2

[Model importing with Materials in Mario Galaxy - Tutorial](https://www.youtube.com/watch?v=1NGAglw6Zqo), by Alex SMG.

The description calls this an update to earlier tutorials and says it covers the creator's model/material workflow, with links to tools and Blender. The title and description establish model importing/materials as the subject, but do not verify a skeleton-retarget method. English auto-generated captions are listed by YouTube, but the caption content endpoint returned no text, and the video could not be reviewed directly.

## Relevance to the current defects

Neither video provides verifiable evidence for a fix to the RUN left-arm backward reach, JUMP left-wrist twist, or SWIM elbow/forearm deformation. The descriptions suggest Galaxy character/model asset workflows, which may be useful for understanding import/export context, but they do not establish how either tutorial handles our specific rig problem.

Our converter transfers Luigi's local animation through rest-basis conversion. That successfully preserves the approved WALK V2, but Waluigi's visible arm deformation is driven by overlapping deform branches and mesh weights. A bone-transform match alone can therefore miss the actual surface defect. The tutorials' retrievable metadata does not show whether they address such branches or validate evaluated skinned vertices.

## V5 diagnostic plan

Do not change the plan based on these videos. For each reported clip/frame, continue with disposable controlled perturbations and evaluated-mesh measurements: identify the weighted vertices/groups in the defective region, perturb candidate deform controls one at a time and as branch groups, then compare the resulting vertex displacement and silhouette direction. This determines whether a correction is reaching the visible geometry or is diluted/canceled by another branch. Keep the converter and approved WALK V2 unchanged unless those measurements demonstrate a specific converter defect.

## Limits

This is a metadata-level assessment, not a review of demonstrated video content. If the videos become accessible or a transcript is supplied, the technique comparison should be revisited before treating either as evidence for a retarget method.
