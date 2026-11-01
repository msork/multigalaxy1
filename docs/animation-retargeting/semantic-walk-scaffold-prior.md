# Semantic walk fit with native temporal scaffold prior

## Scope and safety

This prototype refits Luigi's luigi.001_armature_walk, frames 0–60, on a
disposable Waluigi duplicate. It does not write production rigs/actions. The
raw-ANM 38-bone dataset remains authoritative for target deformation structure.
The Running clip supplies only a low-dimensional temporal prior; its arm pose
is never copied to the output.

## Solver

Fit a compact training-only PCA pose manifold from the raw-ANM 38-bone context
samples. Decode a sequence of latent states jointly. The objective follows
rest-calibrated Luigi shoulder, absolute elbow, forearm-roll, wrist, and signed
hand-position semantics. Shoulder and elbow residuals carry strong weights;
shoulder component limits and the Luigi elbow range are explicit constraints.
The scaffold contributes a soft latent prior and temporal velocity/acceleration
terms. It is not a target semantic curve or a per-frame output pose.

Every decoded pose retains both overlapping native arm branches and their
context bones together. Left and right source semantics are evaluated
independently in character-neutral anatomical frames. The final target pose is
composed over the validated leg/torso walk baseline before continuity and
attachment checks.

Both hands use fixed local finger transforms sampled from the read-only
Waluigi reference pose. These transforms are copied unchanged into each frame;
finger state is never read from the Running scaffold or the fitted PCA output.
The semantic arms still control each palm/wrist, so the glove and closed fist
follow the solved arm.

## Gates

Generate a preview only if:

- shoulder, elbow, and wrist semantic excursion ratios remain within 80–120%
  of Luigi's measured walk excursion;
- every target elbow angle stays within Luigi's rest-calibrated range plus a
  small fixed numerical margin, and shoulder semantics stay within Luigi's
  component range plus a small margin;
- both finger hierarchies reproduce the same reference local transforms on
  every frame;
- maximum final modeled-context normalized SE(3) step is below 0.5;
- maximum left/right wrist-to-glove gap is below 0.02 armature units; and
- no transform, scale, or evaluated mesh position is non-finite.

No post-fit or post-bake smoothing is allowed. Temporal regularization is part
of the joint solve. A passing preview remains subject to viewport review.

## First constrained-fit result

The initial 61-frame run did not pass the gates and therefore emitted no
preview. Both finger hierarchies reproduced their sampled local transforms to
1.4e-15 matrix error, and the maximum normalized pose step was 0.371. However,
shoulder and elbow semantics remained outside the source envelope, elbow
excursion retention was only 19.1% left / 20.5% right, and maximum
wrist-to-glove endpoint gap was 0.296 armature units. Wrist excursion retention
was 2.8%. Semantic RMSE was 0.44 rad for shoulder orientation and 0.36 / 0.34
rad for elbow flexion.

This run is not suitable for visual sign-off. Fixing the finger locals alone
does not ensure wrist/glove attachment, and the current latent fit does not
provide enough semantic leverage. Do not relax the gates or use this report as
evidence of an acceptable walk. The next iteration must include wrist/glove
gap in the solved objective and resolve shoulder, elbow, and wrist semantic
residuals before creating a preview.
