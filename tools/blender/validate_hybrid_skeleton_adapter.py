"""Reopen and validate a saved hybrid Galaxy-control/Waluigi-deform preview."""
import argparse
import json
import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))


def cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default=os.path.join(HERE, "profiles", "luigi_hybrid_waluigi.json"))
    p.add_argument("--output", required=True)
    p.add_argument("--step", type=float, default=0.25)
    return p.parse_args(argv)


def curves(action, slot):
    return [fc for layer in action.layers for strip in layer.strips
            for bag in strip.channelbags if bag.slot == slot for fc in bag.fcurves]


def finite(values):
    def flat(x):
        if isinstance(x, (str, bytes)):
            yield x
            return
        try:
            it = iter(x)
        except TypeError:
            yield x
            return
        for item in it:
            yield from flat(item)
    try:
        return all(math.isfinite(float(x)) for x in flat(values))
    except (TypeError, ValueError):
        return False


def curve_copy_error(src, dst, src_slot, dst_slot, lo, hi, step):
    a = {(f.data_path, f.array_index): f for f in curves(src, src_slot)}
    b = {(f.data_path, f.array_index): f for f in curves(dst, dst_slot)}
    if a.keys() != b.keys():
        return float("inf")
    err, x = 0.0, lo
    while x <= hi+1e-6:
        err = max(err, max(abs(float(a[k].evaluate(x))-float(b[k].evaluate(x))) for k in a))
        x += step
    return err


def group_rows(mesh):
    names = {g.index: g.name for g in mesh.vertex_groups}
    return [{names[g.group]: float(g.weight) for g in v.groups if g.group in names}
            for v in mesh.data.vertices]


def main():
    args = cli()
    with open(args.profile, encoding="utf-8") as f:
        profile = json.load(f)
    control = bpy.data.objects.get("HybridGalaxy_Luigi_Control")
    target = bpy.data.objects.get("HybridWaluigi_Deform_Rig")
    if not control or not target:
        raise RuntimeError("hybrid control or deform armature missing")
    if control.type != 'ARMATURE' or target.type != 'ARMATURE':
        raise RuntimeError("hybrid rigs are not armatures")
    control_action = control.animation_data.action if control.animation_data else None
    control_slot = control.animation_data.action_slot if control.animation_data else None
    target_action = target.animation_data.action if target.animation_data else None
    target_slot = target.animation_data.action_slot if target.animation_data else None
    if not control_action or not control_slot or not target_action or not target_slot:
        raise RuntimeError("one or both hybrid actions/slots are missing")
    src_action_name = control_action.name.replace("HybridGalaxy_Original_", "", 1)
    original_action = bpy.data.actions.get(src_action_name)
    if not original_action:
        raise RuntimeError("original source action unavailable for curve audit")
    source_slots = [slot for slot in original_action.slots if curves(original_action, slot)]
    if len(source_slots) != 1:
        raise RuntimeError(f"expected one populated source action slot, found {len(source_slots)}")
    original_slot = source_slots[0]
    first, last = map(float, target_action.frame_range)
    curve_error = curve_copy_error(original_action, control_action, original_slot,
                                   control_slot, first, last, 0.25)
    meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH' and
              o.name.startswith("HybridWaluigi_") and
              any(m.type == 'ARMATURE' and m.object == target for m in o.modifiers)]
    expected_meshes = ["HybridWaluigi_"+n for n in profile["target_meshes"]]
    ref_meshes = [bpy.data.objects.get(n) for n in profile["reference_meshes"]]
    group_equal = []
    for name, ref in zip(expected_meshes, ref_meshes):
        cand = bpy.data.objects.get(name)
        if cand is None or ref is None:
            group_equal.append(False)
            continue
        group_equal.append([g.name for g in cand.vertex_groups] == [g.name for g in ref.vertex_groups]
                           and group_rows(cand) == group_rows(ref))
    visible_meshes = [o.name for o in meshes if not o.hide_viewport and not o.hide_render]
    visible_armatures = [o.name for o in bpy.context.scene.objects if o.type == 'ARMATURE'
                         and not o.hide_viewport and not o.hide_render]
    constraints = sum(len(pb.constraints) for pb in target.pose.bones)
    frame_values, x = [], first
    while x <= last+1e-6:
        frame_values.append(round(x, 6))
        x += args.step
    if not frame_values or frame_values[-1] < last-1e-6:
        frame_values.append(last)
    bad = []
    vertex_count = 0
    for frame in frame_values:
        integer = math.floor(frame)
        bpy.context.scene.frame_set(integer, subframe=frame-integer)
        bpy.context.view_layer.update()
        for pb in target.pose.bones:
            if not finite(pb.matrix) or not finite(pb.matrix_basis):
                bad.append({"frame": frame, "bone": pb.name})
                break
        dg = bpy.context.evaluated_depsgraph_get()
        for obj in meshes:
            ev = obj.evaluated_get(dg)
            me = ev.to_mesh()
            vertex_count += len(me.vertices)
            try:
                for i, v in enumerate(me.vertices):
                    p = ev.matrix_world @ v.co
                    if not finite(p):
                        bad.append({"frame": frame, "mesh": obj.name, "vertex": i})
                        break
            finally:
                ev.to_mesh_clear()
    # Verify digit bone local bases stay fixed after save/reopen.
    fist_errors = []
    fist_bones = [n for n in profile["fist_bones"] if n in target.pose.bones]
    fist_ref = {}
    for frame in (first, last):
        i = math.floor(frame)
        bpy.context.scene.frame_set(i, subframe=frame-i)
        bpy.context.view_layer.update()
        for n in fist_bones:
            basis = target.pose.bones[n].matrix_basis.copy()
            if n not in fist_ref:
                fist_ref[n] = basis
            else:
                e = max(abs(float(basis[r][c])-float(fist_ref[n][r][c]))
                        for r in range(4) for c in range(4))
                if e > 1e-5:
                    fist_errors.append({"bone": n, "error": e})
    report = {
        "schema": "mg1.galaxy-waluigi-hybrid-reopen.v1",
        "blend": bpy.data.filepath,
        "control_bones": len(control.data.bones),
        "deform_bones": len(target.data.bones),
        "control_action": control_action.name,
        "control_action_slot": control_slot.identifier,
        "control_curve_count": len(curves(control_action, control_slot)),
        "curve_copy_error": curve_error,
        "target_action": target_action.name,
        "target_action_slot": target_slot.identifier,
        "target_curve_count": len(curves(target_action, target_slot)),
        "frame_range": [first, last],
        "samples_checked": len(frame_values),
        "evaluated_vertices_checked": vertex_count,
        "candidate_meshes": [m.name for m in meshes],
        "weights_match_reference": group_equal,
        "visible_meshes": visible_meshes,
        "visible_armatures": visible_armatures,
        "target_runtime_constraints": constraints,
        "fixed_fist_bones": len(fist_bones),
        "fist_pose_variations": fist_errors,
        "nonfinite": bad,
        "passed": (len(control.data.bones) == 30 and len(target.data.bones) == 92
                   and curve_error < 1e-6 and len(meshes) == 2 and all(group_equal)
                   and len(visible_meshes) == 2 and visible_armatures == [target.name]
                   and constraints == 0 and not fist_errors and not bad),
    }
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    print("HYBRID_REOPEN_VALIDATION="+json.dumps(report, separators=(',', ':')))


if __name__ == "__main__":
    main()
