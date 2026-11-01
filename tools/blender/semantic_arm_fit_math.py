"""NumPy-only semantic feature and latent-fitting helpers.

The profile mapping is data-driven so the feature math can be reused for a
Wario/Waluigi pair after validating that target's rest profile.
"""
import math

import numpy as np


def _matrix(value):
    return np.asarray(value, dtype=np.float64).reshape(4, 4)


def _rotation(matrix):
    return _matrix(matrix)[:3, :3]


def _normalize(vector, eps=1e-10):
    value = np.asarray(vector, dtype=np.float64)
    length = np.linalg.norm(value)
    if length < eps:
        return np.zeros_like(value)
    return value / length


def _rotvec(rotation):
    u, _s, vt = np.linalg.svd(np.asarray(rotation, dtype=np.float64))
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    cos_angle = float(np.clip((np.trace(r) - 1.0) * 0.5, -1.0, 1.0))
    angle = math.acos(cos_angle)
    skew = np.asarray((r[2, 1] - r[1, 2], r[0, 2] - r[2, 0],
                       r[1, 0] - r[0, 1]), dtype=np.float64)
    if angle < 1e-8:
        return 0.5 * skew
    if math.pi - angle < 1e-5:
        vals, vecs = np.linalg.eigh((r + np.eye(3)) * 0.5)
        axis = vecs[:, int(np.argmax(vals))]
        axis = _normalize(axis)
        if np.dot(axis, skew) < 0:
            axis = -axis
        return axis * angle
    return skew * (angle / (2.0 * math.sin(angle)))


def _matrix_to_quaternion(rotation):
    r = np.asarray(rotation, dtype=np.float64)
    trace = float(np.trace(r))
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        q = np.array([0.25 * s, (r[2, 1] - r[1, 2]) / s,
                      (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s])
    else:
        i = int(np.argmax(np.diag(r)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(max(1e-16, 1.0 + r[i, i] - r[j, j] - r[k, k])) * 2.0
        q = np.zeros(4)
        q[0] = (r[k, j] - r[j, k]) / s
        q[i + 1] = 0.25 * s
        q[j + 1] = (r[j, i] + r[i, j]) / s
        q[k + 1] = (r[k, i] + r[i, k]) / s
    return q / max(np.linalg.norm(q), 1e-12)


def _twist_y(rotation):
    """Signed quaternion twist about the rest bone's local long (+Y) axis."""
    q = _matrix_to_quaternion(rotation)
    twist = np.array([q[0], 0.0, q[2], 0.0], dtype=np.float64)
    norm = np.linalg.norm(twist)
    if norm < 1e-10:
        return 0.0
    twist /= norm
    angle = 2.0 * math.atan2(float(twist[2]), float(twist[0]))
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def _point(rest_bones, pose, bone_name, endpoint):
    rest = _matrix(rest_bones[bone_name]["rest_matrix"])
    current = _matrix(pose[bone_name])
    p = np.ones(4, dtype=np.float64)
    p[:3] = rest_bones[bone_name][endpoint]
    return (current @ np.linalg.inv(rest) @ p)[:3]


def _body_point(rest_bones, pose, torso_name, bone_name, endpoint):
    body = _matrix(pose[torso_name])
    return (np.linalg.inv(body) @ np.r_[_point(rest_bones, pose, bone_name, endpoint), 1.0])[:3]


def _rest_body_point(rest_bones, torso_name, bone_name, endpoint):
    torso = _matrix(rest_bones[torso_name]["rest_matrix"])
    point = np.r_[rest_bones[bone_name][endpoint], 1.0]
    return (np.linalg.inv(torso) @ point)[:3]


def _average_points(points):
    return np.mean(np.asarray(points, dtype=np.float64), axis=0)


def _angle(a, b):
    a, b = _normalize(a), _normalize(b)
    return math.atan2(float(np.linalg.norm(np.cross(a, b))),
                      float(np.clip(np.dot(a, b), -1.0, 1.0)))


def semantic_features(rest_bones, pose, profile, body_frame_map=None,
                      forearm_frame_map=None):
    """Return the shared 11D semantic state defined by a rig profile.

    ``profile`` keys: torso, shoulder, upper, elbow_a/elbow_b, forearm,
    forearm_overlap, wrist, hand, semantic shoulder/elbow/wrist/tip endpoints.
    Matrices and rest endpoints are armature-space. The optional frame maps
    convert source rest torso/forearm coordinates into the target equivalents.
    """
    identity = np.eye(3)
    body_map = identity if body_frame_map is None else np.asarray(body_frame_map, dtype=np.float64)
    forearm_map = identity if forearm_frame_map is None else np.asarray(forearm_frame_map, dtype=np.float64)
    torso = profile["torso"]
    shoulder = profile["shoulder"]
    upper = profile["upper"]
    forearm = profile["forearm"]
    wrist = profile["wrist"]
    hand = profile["hand"]

    rest_body = _matrix(rest_bones[torso]["rest_matrix"])
    rest_shoulder = _matrix(rest_bones[shoulder]["rest_matrix"])
    current_body = _matrix(pose[torso])
    current_shoulder = _matrix(pose[shoulder])
    rest_shoulder_rel = np.linalg.inv(_rotation(rest_body)) @ _rotation(rest_shoulder)
    current_shoulder_rel = np.linalg.inv(_rotation(current_body)) @ _rotation(current_shoulder)
    shoulder_delta = np.linalg.inv(rest_shoulder_rel) @ current_shoulder_rel
    shoulder_rot = _rotvec(body_map @ shoulder_delta @ body_map.T)

    body_names = {"shoulder": profile["shoulder_point_bone"],
                  "upper_head": upper, "elbow_a": profile["elbow_a"],
                  "elbow_b": profile["elbow_b"], "forearm": forearm,
                  "forearm_overlap": profile["forearm_overlap"],
                  "wrist": wrist, "hand": hand}
    cur = {key: _body_point(rest_bones, pose, torso, bone, endpoint)
           for key, bone, endpoint in (
               ("shoulder", body_names["shoulder"], profile["shoulder_endpoint"]),
               ("upper_head", upper, "head"),
               ("elbow_a", body_names["elbow_a"], profile["elbow_endpoint_a"]),
               ("elbow_b", body_names["elbow_b"], profile["elbow_endpoint_b"]),
               ("forearm", forearm, profile["forearm_endpoint"]),
               ("forearm_overlap", body_names["forearm_overlap"], profile["overlap_endpoint"]),
               ("wrist", wrist, profile["wrist_endpoint"]),
               ("hand", hand, profile["hand_endpoint"]))}
    rest = {key: _rest_body_point(rest_bones, torso, bone, endpoint)
            for key, bone, endpoint in (
                ("shoulder", body_names["shoulder"], profile["shoulder_endpoint"]),
                ("upper_head", upper, "head"),
                ("elbow_a", body_names["elbow_a"], profile["elbow_endpoint_a"]),
                ("elbow_b", body_names["elbow_b"], profile["elbow_endpoint_b"]),
                ("forearm", forearm, profile["forearm_endpoint"]),
                ("forearm_overlap", body_names["forearm_overlap"], profile["overlap_endpoint"]),
                ("wrist", wrist, profile["wrist_endpoint"]),
                ("hand", hand, profile["hand_endpoint"]))}

    shoulder_rest = rest["shoulder"]
    elbow_rest = _average_points((rest["elbow_a"], rest["elbow_b"]))
    wrist_rest = rest["wrist"]
    shoulder_cur = cur["shoulder"]
    elbow_cur = _average_points((cur["elbow_a"], cur["elbow_b"]))
    wrist_cur = cur["wrist"]
    upper_rest_vec = elbow_rest - shoulder_rest
    lower_rest_vec = wrist_rest - elbow_rest
    upper_cur_vec = elbow_cur - shoulder_cur
    lower_cur_vec = wrist_cur - elbow_cur
    elbow_delta = _angle(upper_cur_vec, lower_cur_vec) - _angle(upper_rest_vec, lower_rest_vec)

    forearm_rest = _rotation(rest_bones[forearm]["rest_matrix"])
    forearm_pose = _rotation(_matrix(pose[forearm]))
    overlap_rest = _rotation(rest_bones[body_names["forearm_overlap"]]["rest_matrix"])
    overlap_pose = _rotation(_matrix(pose[body_names["forearm_overlap"]]))
    twist_a = _twist_y(np.linalg.inv(forearm_rest) @ forearm_pose)
    twist_b = _twist_y(np.linalg.inv(overlap_rest) @ overlap_pose)
    forearm_roll = math.atan2(math.sin(twist_a) + math.sin(twist_b),
                              math.cos(twist_a) + math.cos(twist_b))

    rest_forearm = _rotation(rest_bones[forearm]["rest_matrix"])
    rest_wrist = _rotation(rest_bones[wrist]["rest_matrix"])
    pose_forearm = _rotation(_matrix(pose[forearm]))
    pose_wrist = _rotation(_matrix(pose[wrist]))
    wrist_rest_rel = np.linalg.inv(rest_forearm) @ rest_wrist
    wrist_pose_rel = np.linalg.inv(pose_forearm) @ pose_wrist
    wrist_delta = np.linalg.inv(wrist_rest_rel) @ wrist_pose_rel
    wrist_rot = _rotvec(forearm_map @ wrist_delta @ forearm_map.T)

    rest_limb_length = max(float(np.linalg.norm(upper_rest_vec) + np.linalg.norm(lower_rest_vec)), 1e-8)
    hand_rest_offset = rest["hand"] - rest["shoulder"]
    hand_cur_offset = cur["hand"] - cur["shoulder"]
    hand_delta = body_map @ ((hand_cur_offset - hand_rest_offset) / rest_limb_length)

    return np.r_[shoulder_rot, elbow_delta, forearm_roll, wrist_rot, hand_delta]


def encode_context_sample(sample, bones):
    """PCA input vector: rest-relative translation plus quaternion log."""
    features = []
    for bone in bones:
        tr = sample["bones"][bone]["rest_relative_se3"]
        q = np.asarray(tr["rotation_quaternion"], dtype=np.float64)
        q /= max(float(np.linalg.norm(q)), 1e-12)
        if q[0] < 0:
            q = -q
        xyz = q[1:]
        sn = float(np.linalg.norm(xyz))
        angle = 2.0 * math.atan2(sn, float(q[0]))
        rotvec = xyz * angle / sn if sn > 1e-10 else 2.0 * xyz
        features.extend(tr["translation"])
        features.extend(rotvec.tolist())
    return np.asarray(features, dtype=np.float64)


def mirror_pose_across_sagittal(left_pose, torso_pose, torso_rest,
                                left_bone_rest, right_bone_rest):
    """Reflect an armature-space bone pose into its homologous right rest basis.

    Reflection is performed in torso-local coordinates. A per-bone local
    correspondence matrix is derived from the two rest transforms, so mirrored
    bones with different local roll still land exactly on the right rest pose.
    """
    left_pose = _matrix(left_pose)
    torso_pose = _matrix(torso_pose)
    torso_rest = _matrix(torso_rest)
    left_bone_rest = _matrix(left_bone_rest)
    right_bone_rest = _matrix(right_bone_rest)
    sagittal = np.eye(4, dtype=np.float64)
    sagittal[0, 0] = -1.0
    left_rest_relative = np.linalg.inv(torso_rest) @ left_bone_rest
    right_rest_relative = np.linalg.inv(torso_rest) @ right_bone_rest
    local_correspondence = (np.linalg.inv(left_rest_relative) @ sagittal @
                            right_rest_relative)
    left_pose_relative = np.linalg.inv(torso_pose) @ left_pose
    return torso_pose @ sagittal @ left_pose_relative @ local_correspondence


def rbf_features(x, centers, gamma):
    x = np.asarray(x, dtype=np.float64)
    centers = np.asarray(centers, dtype=np.float64)
    d2 = np.maximum(0.0, (x * x).sum(axis=1, keepdims=True) +
                    (centers * centers).sum(axis=1)[None, :] - 2.0 * x @ centers.T)
    return np.exp(-gamma * d2)


def farthest_centers(x, count):
    x = np.asarray(x, dtype=np.float64)
    center = x.mean(axis=0)
    first = int(np.argmin(((x - center) ** 2).sum(axis=1)))
    indices = [first]
    min_d2 = ((x - x[first]) ** 2).sum(axis=1)
    for _ in range(1, min(count, len(x))):
        index = int(np.argmax(min_d2))
        indices.append(index)
        min_d2 = np.minimum(min_d2, ((x - x[index]) ** 2).sum(axis=1))
    return x[indices].copy()


def fit_rbf_ridge(x, y, centers, gamma, alpha):
    phi = rbf_features(x, centers, gamma)
    gram = phi.T @ phi + float(alpha) * np.eye(phi.shape[1])
    weights = np.linalg.solve(gram, phi.T @ y)
    return {"centers": centers, "gamma": float(gamma), "alpha": float(alpha),
            "weights": weights}


def predict_rbf(x, model):
    return rbf_features(x, model["centers"], model["gamma"]) @ model["weights"]


def smooth_latent_sequence(proposal, native_prior, latent_scale,
                           lambda_native=0.2, lambda_velocity=0.05,
                           lambda_acceleration=0.5):
    """Solve the joint semantic, native-manifold, velocity and acceleration objective."""
    proposal = np.asarray(proposal, dtype=np.float64)
    native_prior = np.asarray(native_prior, dtype=np.float64)
    # Caller supplies standardized latent coordinates; scale remains an
    # explicit parameter for profiles that choose a different normalization.
    scale = np.maximum(np.asarray(latent_scale, dtype=np.float64), 1e-5)
    n = len(proposal)
    d1 = np.zeros((max(0, n - 1), n), dtype=np.float64)
    for i in range(n - 1):
        d1[i, i], d1[i, i+1] = -1.0, 1.0
    d2 = np.zeros((max(0, n - 2), n), dtype=np.float64)
    for i in range(n - 2):
        d2[i, i:i+3] = (1.0, -2.0, 1.0)
    h = ((1.0 + lambda_native) * np.eye(n) +
         lambda_velocity * (d1.T @ d1) +
         lambda_acceleration * (d2.T @ d2))
    rhs = proposal + lambda_native * native_prior
    return np.linalg.solve(h, rhs)


def refine_latent_sequence_joint(initial, native_prior, evaluate, target,
                                 output_weights, temporal_specs=(),
                                 lambda_native=0.01, lambda_velocity=0.002,
                                 lambda_acceleration=0.01, iterations=8,
                                 cg_iterations=80, damping=1e-4):
    """Jointly optimize every frame's latent pose with feature-time penalties.

    `evaluate(z_t)` returns a generic feature vector. Each temporal spec is a
    mapping with `index`, `velocity_target`, `acceleration_target`, and their
    respective weights. This keeps the solver target-profile agnostic while
    supporting timing-aware semantic channels and branch-continuity features.
    A matrix-free conjugate-gradient Gauss-Newton step avoids a dense
    (frames*latent)^2 solve.
    """
    z = np.asarray(initial, dtype=np.float64).copy()
    prior = np.asarray(native_prior, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    weights = np.asarray(output_weights, dtype=np.float64)
    if z.ndim != 2 or prior.shape != z.shape:
        raise ValueError("initial and native_prior must have matching frame x latent shapes")
    if target.shape[0] != len(z) or weights.shape != (target.shape[1],):
        raise ValueError("target/output_weights dimensions do not match sequence output")
    if np.any(weights < 0) or not np.all(np.isfinite(z)):
        raise ValueError("joint sequence inputs contain invalid values")
    n, dim = z.shape
    feature_count = target.shape[1]
    specs = []
    for spec in temporal_specs:
        index = int(spec["index"])
        if not 0 <= index < feature_count:
            raise ValueError("temporal feature index is out of range")
        vel_target = np.asarray(spec.get("velocity_target", np.zeros(n-1)), dtype=np.float64)
        acc_target = np.asarray(spec.get("acceleration_target", np.zeros(n-2)), dtype=np.float64)
        if vel_target.shape != (n-1,) or acc_target.shape != (n-2,):
            raise ValueError("temporal targets must match first/second difference lengths")
        specs.append((index, vel_target, acc_target,
                      float(spec.get("velocity_weight", 0.0)),
                      float(spec.get("acceleration_weight", 0.0))))

    def outputs(sequence):
        return np.asarray([evaluate(row) for row in sequence], dtype=np.float64)

    def objective(sequence, values=None):
        values = outputs(sequence) if values is None else values
        residual = values - target
        total = 0.5 * float(np.sum(residual * residual * weights[None, :]))
        delta = sequence - prior
        total += 0.5 * lambda_native * float(np.sum(delta * delta))
        d1 = np.diff(sequence, axis=0)
        d2 = np.diff(sequence, n=2, axis=0)
        total += 0.5 * lambda_velocity * float(np.sum(d1 * d1))
        total += 0.5 * lambda_acceleration * float(np.sum(d2 * d2))
        for index, vel_target, acc_target, wv, wa in specs:
            rv = np.diff(values[:, index]) - vel_target
            ra = np.diff(values[:, index], n=2) - acc_target
            total += 0.5 * wv * float(rv @ rv) + 0.5 * wa * float(ra @ ra)
        return total, values

    history = []
    eps = 1e-4
    for _iteration in range(iterations):
        values = outputs(z)
        jac = np.empty((n, feature_count, dim), dtype=np.float64)
        for t in range(n):
            shifted = z[t].copy()
            for k in range(dim):
                shifted[k] += eps
                jac[t, :, k] = (np.asarray(evaluate(shifted)) - values[t]) / eps
                shifted[k] -= eps
        residual = values - target
        gradient = np.einsum("tmd,tm,m->td", jac, residual, weights)
        gradient += lambda_native * (z - prior)
        d1 = np.diff(z, axis=0)
        gradient[:-1] -= lambda_velocity * d1
        gradient[1:] += lambda_velocity * d1
        d2 = np.diff(z, n=2, axis=0)
        gradient[:-2] += lambda_acceleration * d2
        gradient[1:-1] -= 2.0 * lambda_acceleration * d2
        gradient[2:] += lambda_acceleration * d2

        temporal_jac = [(index, jac[:, index, :], vel_target, acc_target, wv, wa)
                        for index, vel_target, acc_target, wv, wa in specs]
        for index, feature_jac, vel_target, acc_target, wv, wa in temporal_jac:
            rv = np.diff(values[:, index]) - vel_target
            ra = np.diff(values[:, index], n=2) - acc_target
            grad_feature = -wv * rv
            grad_feature = np.r_[grad_feature, 0.0] + np.r_[0.0, wv * rv]
            if n > 2:
                grad_feature += np.r_[wa * ra, 0.0, 0.0]
                grad_feature += np.r_[0.0, -2.0 * wa * ra, 0.0]
                grad_feature += np.r_[0.0, 0.0, wa * ra]
            gradient += feature_jac * grad_feature[:, None]

        local_h = np.einsum("tmd,m,tme->tde", jac, weights, jac)
        diagonal = np.diagonal(local_h, axis1=1, axis2=2).copy() + lambda_native + damping
        diagonal[:-1] += lambda_velocity
        diagonal[1:] += lambda_velocity
        diagonal[:-2] += lambda_acceleration
        diagonal[1:-1] += 4.0 * lambda_acceleration
        diagonal[2:] += lambda_acceleration
        for index, feature_jac, _vt, _at, wv, wa in temporal_jac:
            square = feature_jac * feature_jac
            if n > 1 and wv:
                diagonal[:-1] += wv * square[:-1]
                diagonal[1:] += wv * square[1:]
            if n > 2 and wa:
                diagonal[:-2] += wa * square[:-2]
                diagonal[1:-1] += 4.0 * wa * square[1:-1]
                diagonal[2:] += wa * square[2:]
        diagonal = np.maximum(diagonal, 1e-8)

        def hessian_product(value):
            result = np.einsum("tde,te->td", local_h, value)
            result += (lambda_native + damping) * value
            dv = np.diff(value, axis=0)
            result[:-1] -= lambda_velocity * dv
            result[1:] += lambda_velocity * dv
            da = np.diff(value, n=2, axis=0)
            result[:-2] += lambda_acceleration * da
            result[1:-1] -= 2.0 * lambda_acceleration * da
            result[2:] += lambda_acceleration * da
            for index, feature_jac, _vt, _at, wv, wa in temporal_jac:
                u = np.einsum("td,td->t", feature_jac, value)
                if wv:
                    du = np.diff(u)
                    bu = np.r_[-wv * du, 0.0] + np.r_[0.0, wv * du]
                    result += feature_jac * bu[:, None]
                if wa:
                    ddu = np.diff(u, n=2)
                    bu = np.r_[wa * ddu, 0.0, 0.0]
                    bu += np.r_[0.0, -2.0 * wa * ddu, 0.0]
                    bu += np.r_[0.0, 0.0, wa * ddu]
                    result += feature_jac * bu[:, None]
            return result

        rhs = -gradient
        step = np.zeros_like(z)
        residual_cg = rhs.copy()
        preconditioned = residual_cg / diagonal
        direction = preconditioned.copy()
        rz = float(np.sum(residual_cg * preconditioned))
        initial_norm = math.sqrt(max(float(np.sum(rhs * rhs)), 0.0))
        for _ in range(cg_iterations):
            product = hessian_product(direction)
            denom = float(np.sum(direction * product))
            if denom <= 1e-20:
                break
            alpha = rz / denom
            step += alpha * direction
            residual_cg -= alpha * product
            if math.sqrt(float(np.sum(residual_cg * residual_cg))) <= max(1e-8, initial_norm * 1e-5):
                break
            new_preconditioned = residual_cg / diagonal
            new_rz = float(np.sum(residual_cg * new_preconditioned))
            direction = new_preconditioned + (new_rz / max(rz, 1e-30)) * direction
            preconditioned, rz = new_preconditioned, new_rz
        step_norm = np.linalg.norm(step, axis=1)
        over = step_norm > 2.0
        step[over] *= (2.0 / step_norm[over])[:, None]
        old_cost, _ = objective(z, values)
        accepted = False
        for fraction in (1.0, 0.5, 0.25, 0.125, 0.0625):
            candidate = z + fraction * step
            new_cost, new_values = objective(candidate)
            if new_cost < old_cost:
                z = candidate
                history.append({"objective": new_cost, "step_fraction": fraction,
                                "max_latent_step": float(np.max(np.linalg.norm(fraction*step, axis=1)))})
                accepted = True
                break
        if not accepted:
            history.append({"objective": old_cost, "step_fraction": 0.0,
                            "max_latent_step": 0.0})
            break
    final_values = outputs(z)
    return z, final_values, history

