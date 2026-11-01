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

