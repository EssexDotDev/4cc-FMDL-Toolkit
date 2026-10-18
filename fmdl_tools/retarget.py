"""Retarget a Blender mesh onto the PES medic skeleton.

A mesh that already has vertex groups is merged by bone name. A mesh
with none gets the medic body's weights by surface proximity. The medic
FMDL is read with the PESfmdl importer when that skeleton is not already
in the scene.
"""

import importlib.util
import os
import sys
from collections import defaultdict

import bpy
from mathutils import Vector

_PESFMDL_DIR = os.path.join(
	os.environ.get("APPDATA", ""),
	"Blender Foundation",
	"Blender",
	"5.2",
	"scripts",
	"addons",
	"PESfmdl",
)
_skeleton_data = None


# Core name, without a side suffix, to a PES bone role.
# MMD names are the merges used on the Kearsarge body.
_JP = {
	"下半身": "dsk_hip",
	"上半身": "sk_belly",
	"上半身2": "sk_chest",
	"上半身3": "sk_chest",
	"首": "sk_neck",
	"頭": "sk_head",
	"頭先": "sk_head",
	"肩": "dsk_clavicle",
	"肩P": "dsk_clavicle",
	"肩C": "dsk_clavicle",
	"腕": "dsk_upperarm",
	"腕捩": "dsk_upperarm",
	"腕捩1": "dsk_upperarm",
	"腕捩2": "dsk_upperarm",
	"腕捩3": "dsk_upperarm",
	"ひじ": "sk_forearm",
	"ひじ補助": "sk_forearm",
	"手捩": "dsk_wrist",
	"手捩1": "sk_forearm",
	"手捩2": "sk_forearm",
	"手捩3": "dsk_wrist",
	"手首": "sk_hand",
	"手先": "sk_hand",
	"ダミー": "sk_hand",
	"足": "sk_thigh",
	"足D": "sk_thigh",
	"ひざ": "sk_leg",
	"ひざD": "sk_leg",
	"足首": "sk_foot",
	"足首D": "sk_foot",
	"つま先": "dsk_toe",
	"足先EX": "dsk_toe",
	"臀修复": "dsk_belly_ba",
	"臀物理": "dsk_belly_ba",
	"腿物理": "sk_thigh",
	"胯修复": "sk_thigh",
	"胸上": "sk_chest",
	"胸上2": "sk_chest",
	"胸上2-": "sk_chest",
	"胸下": "sk_chest",
	"胸変形": "sk_chest",
	"胸亲": "sk_chest",
	"両目": "sk_head",
	"目": "sk_head",
	"目戻": "sk_head",
	# Groove sits on the pelvis and carries the butt. It is not a handle.
	"グルーブ": "dsk_hip",
}

_EN = {
	"hips": "dsk_hip",
	"pelvis": "dsk_hip",
	"hip": "dsk_hip",
	"waist": "dsk_hip",
	"lowerbody": "dsk_hip",
	"spine": "sk_belly",
	"spine1": "sk_belly",
	"spine01": "sk_belly",
	"upperbody": "sk_belly",
	"abdomen": "sk_belly",
	"spine2": "sk_chest",
	"spine02": "sk_chest",
	"spine3": "sk_chest",
	"spine03": "sk_chest",
	"spine4": "sk_chest",
	"spine04": "sk_chest",
	"chest": "sk_chest",
	"upperchest": "sk_chest",
	"upperbody2": "sk_chest",
	"neck": "sk_neck",
	"neck1": "sk_neck",
	"neck01": "sk_neck",
	"head": "sk_head",
	"clavicle": "dsk_clavicle",
	"shoulder": "dsk_clavicle",
	"upperarm": "dsk_upperarm",
	"arm": "dsk_upperarm",
	"lowerarm": "sk_forearm",
	"forearm": "sk_forearm",
	"elbow": "sk_forearm",
	"hand": "sk_hand",
	"wrist": "sk_hand",
	"thigh": "sk_thigh",
	"upperleg": "sk_thigh",
	"upleg": "sk_thigh",
	"calf": "sk_leg",
	"lowerleg": "sk_leg",
	"shin": "sk_leg",
	"knee": "sk_leg",
	"leg": "sk_leg",
	"foot": "sk_foot",
	"ankle": "sk_foot",
	"toe": "dsk_toe",
	"toebase": "dsk_toe",
	"ball": "dsk_toe",
	"groove": "dsk_hip",
}

_CENTER = {"dsk_hip", "sk_belly", "sk_chest", "sk_neck", "sk_head"}
_FINGERS = ("親指", "人指", "中指", "薬指", "小指", "thumb", "index", "middle", "ring", "pinky", "little", "finger")
_FALLBACK = {
	"dsk_belly_ba": ("sk_belly", "dsk_hip"),
	"dsk_upperarm": ("sk_upperarm",),
	"dsk_forearm": ("sk_forearm",),
	"dsk_wrist": ("sk_hand",),
	"dsk_clavicle": ("sk_shoulder",),
	"dsk_toe": ("sk_foot",),
	"sk_hand": ("dsk_wrist",),
	"sk_upperarm": ("dsk_upperarm",),
	"sk_forearm": ("dsk_forearm",),
	"sk_shoulder": ("dsk_clavicle",),
	"sk_foot": ("dsk_toe",),
}


def _split_side(name):
	for suffix, side in ((".", "dot"), ("_", "bar")):
		if len(name) > 2 and name[-2] == suffix and name[-1] in "LlRr":
			return name[:-2], "l" if name[-1] in "Ll" else "r", side
	lower = name.lower()
	if lower.startswith("left"):
		return name[4:].lstrip("_ "), "l", "word"
	if lower.startswith("right"):
		return name[5:].lstrip("_ "), "r", "word"
	return name, "", ""


def _token_prefix(text, word):
	"""True when word begins a letter-run, so 'ear' does not match 'forearm'."""
	start = 0
	while True:
		found = text.find(word, start)
		if found < 0:
			return False
		if found == 0 or not text[found - 1].isalpha():
			return True
		start = found + 1


def _compact(core):
	text = core.lower().replace("mixamorig:", "").replace(" ", "").replace("_", "").replace("-", "")
	if text.startswith("bip001") or text.startswith("bip01"):
		text = text.split("bip01", 1)[-1].lstrip("01")
	return text


def _is_skip(name):
	lower = name.lower()
	if lower in ("mmd_edge_scale", "mmd_vertex_order"):
		return True
	if any(token in name for token in ("全ての親", "操作中心", "センター", "ＩＫ")):
		return True
	if "dildo" in lower:
		return True
	if lower.endswith("ik") or ".ik" in lower or "_ik" in lower or " ik" in lower:
		return True
	return False


def _place(role, side, available):
	use_side = "" if role in _CENTER else side
	candidates = []
	primary = role if not use_side else "%s_%s" % (role, use_side)
	candidates.append(primary)
	for alt in _FALLBACK.get(role, ()):
		if alt in _CENTER or not use_side:
			candidates.append(alt)
		else:
			candidates.append("%s_%s" % (alt, use_side))
	for name in candidates:
		if name in available:
			return name
	return None


def map_group_name(name, available):
	"""Return the PES bone for a source vertex group, or None to drop it."""
	if name in available:
		return name
	if _is_skip(name):
		return None
	core, side, style = _split_side(name)
	for prefix in ("_dummy_", "_shadow_", "+"):
		if core.startswith(prefix):
			core = core[len(prefix):]
			break
	if core in _JP:
		role = _JP[core]
		# MMD ".L" leg/arm words are handled by the Japanese table.
		return _place(role, side, available)
	lower_core = core.lower()
	if _has_finger(core, lower_core):
		return _place("sk_hand", side, available)
	if (
		lower_core.startswith("hf_")
		or lower_core.startswith("qf")
		or "hair" in lower_core
		or "髪" in core
		or "耳" in core
		or _token_prefix(lower_core, "ear")
		or "舌" in core
		or "tongue" in lower_core
		or _token_prefix(lower_core, "eye")
		or _token_prefix(lower_core, "lid")
		or _token_prefix(lower_core, "brow")
		or lower_core.startswith("brw")
		or _token_prefix(lower_core, "mouth")
		or _token_prefix(lower_core, "lip")
		or _token_prefix(lower_core, "chin")
		or "hat" in lower_core
		or ("ribbon" in lower_core and "leg" not in lower_core)
		or lower_core.startswith("m_line")
		or core.startswith("目")
	):
		return _place("sk_head", "", available)
	compact = _compact(core)
	if compact.startswith("toe"):
		return _place("dsk_toe", side, available)
	if compact.startswith("ankle") or compact.startswith("foot"):
		return _place("sk_foot", side, available)
	if "tail" in compact or "尾" in core:
		return _place("dsk_hip", "", available)
	if "bust" in compact or "breast" in compact:
		return _place("sk_chest", "", available)
	if compact.startswith("chest"):
		return _place("sk_chest", side, available)
	if compact.startswith("thigh"):
		return _place("sk_thigh", side, available)
	if compact in ("root", "cog"):
		return _place("dsk_hip", side, available)
	if _cloth_kind(name):
		# Skirts, cloaks, and sleeves are placed from where the verts hang.
		return None
	# Mixamo "LeftLeg" is the calf. MMD "leg.L" is the thigh.
	if compact == "leg" and style == "dot":
		return _place("sk_thigh", side, available)
	if compact == "arm" and style == "dot":
		return _place("dsk_upperarm", side, available)
	if compact == "wrist" and style == "dot":
		return _place("sk_hand", side, available)
	if "twist" in compact or "roll" in compact:
		if "shoulder" in compact or "upper" in compact or compact.startswith("arm"):
			return _place("dsk_upperarm", side, available)
		if "fore" in compact or "lower" in compact or "elbow" in compact:
			return _place("sk_forearm", side, available)
		if "wrist" in compact or "hand" in compact:
			return _place("dsk_wrist", side, available)
	role = _EN.get(compact)
	if role:
		return _place(role, side, available)
	return None


def _has_finger(core, lower_core):
	"""Finger words inside the group name. 'ring' in 'earring' is not a finger."""
	text = lower_core.replace("earring", " ")
	for word in _FINGERS:
		if word.isascii():
			if word in text:
				return True
		elif word in core or word in lower_core:
			return True
	return False


def _embedded_side(name):
	"""Side marker anywhere in the name. Cloth chains hide it in the middle."""
	_core, side, _style = _split_side(name)
	if side:
		return side
	lower = name.lower().replace(".", "_")
	if "_l_" in lower or "_l1" in lower or "_l2" in lower:
		return "l"
	if "_r_" in lower or "_r1" in lower or "_r2" in lower:
		return "r"
	return ""


def _cloth_kind(name):
	"""A garment chain, or None when the group is ordinary skin.

	'sleeve' follows the arm. 'hang' is a skirt, cloak, or coat.
	'upper' is a frill or a strap on the torso. 'leg' is a boot ribbon.
	"""
	compact = _compact(_split_side(name)[0])
	if "sleeve" in compact or "sode" in compact:
		return "sleeve"
	if "ribbon" in compact and "leg" in compact:
		return "leg"
	if any(token in compact for token in ("skirt", "cloak", "cape", "mantle", "coat", "dress", "hem")):
		return "hang"
	if any(token in compact for token in ("frill", "strap", "sash", "epaulette")):
		return "upper"
	return None


def _is_pes_armature(obj):
	if obj is None or obj.type != "ARMATURE":
		return False
	names = {bone.name for bone in obj.data.bones}
	return "dsk_hip" in names and "sk_chest" in names and "sk_head" in names


def _driven_by(obj, arm):
	for modifier in obj.modifiers:
		if modifier.type == "ARMATURE" and modifier.object == arm:
			return True
	return False


def _walk(obj):
	yield obj
	for child in obj.children:
		yield from _walk(child)


def find_pes_armature(context):
	found = [obj for obj in context.scene.objects if _is_pes_armature(obj)]
	if not found:
		return None
	selected = []
	for obj in context.selected_objects:
		for node in _walk(obj):
			if node in found and node not in selected:
				selected.append(node)
	if len(selected) == 1:
		return selected[0]
	for obj in found:
		parent = obj.parent.name.lower() if obj.parent else ""
		if "medic" in parent or "medical" in parent:
			return obj
	return found[0]


def _pes_skeleton_data():
	"""Bone positions from PESfmdl, the same table its importer uses."""
	global _skeleton_data
	if _skeleton_data is not None:
		return _skeleton_data
	path = os.path.join(_PESFMDL_DIR, "PesSkeletonData.py")
	if not os.path.isfile(path):
		return None
	spec = importlib.util.spec_from_file_location("fmdltools_pes_skeleton", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	_skeleton_data = module
	return module


def _enable_pesfmdl():
	"""Turn on PESfmdl so bpy.ops.fmdl.import_fmdl can read the medic file."""
	addons = os.path.dirname(_PESFMDL_DIR)
	if addons and addons not in sys.path:
		sys.path.append(addons)
	try:
		import addon_utils
		addon_utils.enable("PESfmdl", default_set=False, persistent=False)
	except Exception:
		pass
	try:
		import PESfmdl
		PESfmdl.register()
	except ValueError as error:
		if "already registered" not in str(error):
			raise
	except ModuleNotFoundError as error:
		raise ValueError("Enable the PESfmdl add-on. It reads the medic FMDL.") from error


def import_medic(context, filepath):
	"""Read a medic FMDL with the PESfmdl importer and return its armature."""
	if not filepath or not os.path.isfile(bpy.path.abspath(filepath)):
		raise ValueError("Set the medic FMDL path. The PES skeleton is not in this scene.")
	path = bpy.path.abspath(filepath)
	_enable_pesfmdl()
	operator = bpy.ops.fmdl.import_fmdl
	before = set(bpy.data.objects)
	result = operator(filepath=path)
	if "FINISHED" not in set(result):
		raise ValueError("PESfmdl could not import %s" % os.path.basename(path))
	created = [
		obj for obj in bpy.data.objects
		if obj not in before and _is_pes_armature(obj)
	]
	if created:
		return created[0]
	found = find_pes_armature(context)
	if found is None:
		raise ValueError("The medic FMDL imported without a body skeleton.")
	return found


def _source_meshes(context, pes_arm):
	chosen = []
	seen = set()

	def add(obj):
		if obj.type != "MESH" or obj.name in seen:
			return
		if pes_arm is not None and _driven_by(obj, pes_arm):
			return
		if len(obj.vertex_groups) == 0:
			return
		parent = obj.parent.name.lower() if obj.parent else ""
		if parent in ("rigidbodies", "rigidbody", "joints"):
			return
		seen.add(obj.name)
		chosen.append(obj)

	for obj in context.selected_objects:
		if obj.type == "MESH":
			add(obj)
		elif obj.type in ("EMPTY", "ARMATURE") and not _is_pes_armature(obj):
			for child in _walk(obj):
				add(child)
	return chosen


def _join(context, meshes):
	if len(meshes) == 1:
		return meshes[0]
	for obj in context.view_layer.objects:
		obj.select_set(False)
	for obj in meshes:
		obj.select_set(True)
	context.view_layer.objects.active = meshes[0]
	bpy.ops.object.join()
	return context.view_layer.objects.active


def _apply_source_armatures(context, mesh, pes_arm):
	context.view_layer.objects.active = mesh
	if context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	pending = [
		modifier for modifier in mesh.modifiers
		if modifier.type == "ARMATURE" and modifier.object is not None and modifier.object != pes_arm
	]
	if not pending:
		return
	if mesh.data.users > 1:
		mesh.data = mesh.data.copy()
	if mesh.data.shape_keys is not None:
		# The operator needs a window. shape_key_clear does not.
		mesh.shape_key_clear()
	for modifier in pending:
		try:
			bpy.ops.object.modifier_apply(modifier=modifier.name)
		except RuntimeError:
			mesh.modifiers.remove(modifier)


def _nearest_bone(point, heads):
	best_name = None
	best_distance = None
	height = 1.0
	if heads:
		zs = [head.z for head in heads.values()]
		height = max(zs) - min(zs) or 1.0
	limit = max(0.25, height * 0.22)
	for name, head in heads.items():
		if name.endswith("_l") and point.x < -0.03:
			continue
		if name.endswith("_r") and point.x > 0.03:
			continue
		distance = (head - point).length
		if best_distance is None or distance < best_distance:
			best_distance = distance
			best_name = name
	if best_distance is None or best_distance > limit:
		return None
	return best_name


def _add_missing_bones(context, arm_obj, needed):
	"""Add PES bones the medic FMDL left out, placed the way PESfmdl places them."""
	data = _pes_skeleton_data()
	if data is None:
		return []
	present = {bone.name for bone in arm_obj.data.bones}
	order = []

	def walk(name):
		bone = data.bones.get(name)
		if bone is None or name in present or name in order:
			return
		parent = bone.renderParent
		if parent:
			walk(parent)
		order.append(name)

	for name in needed:
		walk(name)
	if not order:
		return []
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	context.view_layer.objects.active = arm_obj
	bpy.ops.object.mode_set(mode="EDIT")
	edit = arm_obj.data.edit_bones
	added = []
	for name in order:
		pes = data.bones[name]
		head = (pes.startPosition[0], -pes.startPosition[2], pes.startPosition[1])
		tail = (pes.endPosition[0], -pes.endPosition[2], pes.endPosition[1])
		if sum((tail[i] - head[i]) ** 2 for i in range(3)) < 1e-12:
			tail = (head[0], head[1], head[2] - 0.00001)
		bone = edit.new(name)
		bone.head = head
		bone.tail = tail
		parent_name = pes.renderParent
		if parent_name and parent_name in edit:
			bone.parent = edit[parent_name]
			parent = data.bones.get(parent_name)
			if parent is not None:
				distance = sum((parent.endPosition[i] - pes.startPosition[i]) ** 2 for i in range(3))
				bone.use_connect = distance < 1e-10
		added.append(name)
	bpy.ops.object.mode_set(mode="OBJECT")
	return added


def _source_armature(mesh):
	for modifier in mesh.modifiers:
		if modifier.type == "ARMATURE" and modifier.object and not _is_pes_armature(modifier.object):
			return modifier.object
	# The modifier is applied before weights are read. The parent is still there.
	parent = mesh.parent
	if parent is not None and parent.type == "ARMATURE" and not _is_pes_armature(parent):
		return parent
	return None


def _torso_levels(mesh):
	"""Hip, chest, and neck height on the source skeleton, in world Z."""
	arm = _source_armature(mesh)
	levels = {}
	exact = {
		"hip": "dsk_hip",
		"pelvis": "dsk_hip",
		"chest": "sk_chest",
		"neck": "sk_neck",
	}
	if arm is not None:
		for bone in arm.data.bones:
			if _cloth_kind(bone.name):
				continue
			core, _side, _style = _split_side(bone.name)
			role = exact.get(_compact(core))
			if role is None or role in levels:
				continue
			levels[role] = (arm.matrix_world @ bone.head_local).z
	if "dsk_hip" not in levels or "sk_chest" not in levels:
		zs = [(mesh.matrix_world @ vertex.co).z for vertex in mesh.data.vertices]
		if zs:
			low, high = min(zs), max(zs)
			span = high - low
			levels.setdefault("dsk_hip", low + span * 0.48)
			levels.setdefault("sk_chest", low + span * 0.72)
			levels.setdefault("sk_neck", low + span * 0.86)
	return levels


def _source_bone(mesh, names, side):
	arm = _source_armature(mesh)
	if arm is None:
		return None
	wanted = set(names)
	for bone in arm.data.bones:
		core, bone_side, _style = _split_side(bone.name)
		if bone_side != side and _embedded_side(bone.name) != side:
			continue
		if _compact(core) not in wanted and _compact(bone.name) not in wanted:
			continue
		return arm.matrix_world @ bone.head_local
	return None


def _assign_cloth(mesh, weights, unknown, named, spatial):
	"""Hang coats and skirts on the torso. Split sleeves at the elbow.

	Returns how many verts belong to a skirt, cloak, or coat. A real garment
	is left in its built pose instead of being swung onto the medic arms.
	"""
	kinds = {}
	for group in unknown:
		kind = _cloth_kind(group.name)
		if kind:
			kinds[group.name] = kind
	if not kinds:
		return 0
	world = mesh.matrix_world
	levels = _torso_levels(mesh)
	hip_z = levels.get("dsk_hip", 0.9)
	chest_z = levels.get("sk_chest", hip_z + 0.25)
	neck_z = levels.get("sk_neck", chest_z + 0.16)
	elbow = {
		"l": _source_bone(mesh, ("forearm", "elbow"), "l"),
		"r": _source_bone(mesh, ("forearm", "elbow"), "r"),
	}
	shoulder = {
		"l": _source_bone(mesh, ("arm", "upperarm"), "l"),
		"r": _source_bone(mesh, ("arm", "upperarm"), "r"),
	}
	index_of = {group.index: group.name for group in mesh.vertex_groups}
	hanging = set()
	for vertex in mesh.data.vertices:
		point = None
		for item in vertex.groups:
			if item.weight <= 0.0001:
				continue
			source = index_of.get(item.group)
			kind = kinds.get(source)
			if not kind:
				continue
			if point is None:
				point = world @ vertex.co
			if kind == "leg":
				side = _embedded_side(source) or ("l" if point.x >= 0.0 else "r")
				target = "sk_leg_%s" % side
			elif kind == "sleeve":
				side = _embedded_side(source) or ("l" if point.x >= 0.0 else "r")
				joint = elbow.get(side)
				root = shoulder.get(side)
				past = False
				if joint is not None and root is not None and (joint - root).length > 1e-4:
					direction = (joint - root).normalized()
					past = (point - joint).dot(direction) > -0.02
				target = "sk_forearm_%s" % side if past else "dsk_upperarm_%s" % side
			elif kind == "upper":
				target = "sk_neck" if point.z >= neck_z - 0.03 else "sk_chest"
			else:
				hanging.add(vertex.index)
				if point.z >= chest_z:
					target = "sk_chest"
				elif point.z >= (hip_z + chest_z) * 0.5:
					target = "sk_chest"
				else:
					target = "dsk_hip"
			bucket = weights.setdefault(vertex.index, {})
			bucket[target] = bucket.get(target, 0.0) + item.weight
			named[source] = target
	for source in kinds:
		spatial[source] = named.get(source) or "cloth"
	return len(hanging)


def _with_attached_parts(context, meshes, pes_arm):
	"""Face, hair, and a hat on the same armature scale with the body.

	Leaving them behind puts the scaled neck up through the unscaled head.
	"""
	if not meshes:
		return meshes
	arms = []
	for mesh in meshes:
		arm = _source_armature(mesh)
		if arm is not None and arm not in arms:
			arms.append(arm)
	seen = {mesh.name for mesh in meshes}
	extra = []
	for arm in arms:
		for obj in context.scene.objects:
			if obj.type != "MESH" or obj.name in seen:
				continue
			if not _driven_by(obj, arm):
				continue
			if pes_arm is not None and _driven_by(obj, pes_arm):
				continue
			parent = obj.parent.name.lower() if obj.parent else ""
			if parent in ("rigidbodies", "rigidbody", "joints"):
				continue
			if len(obj.vertex_groups) == 0:
				continue
			seen.add(obj.name)
			extra.append(obj)
	return list(meshes) + extra


def _accumulate(mesh_obj, pes_arm):
	catalog = _pes_skeleton_data()
	available = {bone.name for bone in pes_arm.data.bones}
	if catalog is not None:
		available.update(catalog.bones)
	heads = {
		bone.name: pes_arm.matrix_world @ Vector(bone.head_local)
		for bone in pes_arm.data.bones
	}
	groups = list(mesh_obj.vertex_groups)
	index_of = {group.index: group.name for group in groups}
	named = {}
	unknown = []
	skipped = []
	for group in groups:
		if _is_skip(group.name):
			skipped.append(group.name)
			named[group.name] = None
			continue
		target = map_group_name(group.name, available)
		named[group.name] = target
		if target is None:
			unknown.append(group)

	mesh = mesh_obj.data
	world = mesh_obj.matrix_world
	weights = {}
	centroids = {group.name: [Vector((0.0, 0.0, 0.0)), 0.0] for group in unknown}
	for vertex in mesh.vertices:
		bucket = None
		co = None
		for item in vertex.groups:
			if item.weight <= 0.0001:
				continue
			source = index_of.get(item.group)
			if source is None:
				continue
			target = named.get(source)
			if target:
				if bucket is None:
					bucket = weights.setdefault(vertex.index, {})
				bucket[target] = bucket.get(target, 0.0) + item.weight
			elif source in centroids:
				if co is None:
					co = world @ vertex.co
				slot = centroids[source]
				slot[0] += co * item.weight
				slot[1] += item.weight
	unmapped = []
	spatial = {}
	garment = _assign_cloth(mesh_obj, weights, unknown, named, spatial)
	# A named humanoid already tells us where the arms are. Guessing by
	# proximity sticks those verts to the head when long hair hangs beside them.
	named_hits = len([target for target in named.values() if target])
	use_spatial = named_hits < 8
	for group in unknown:
		if _cloth_kind(group.name):
			continue
		total, mass = centroids[group.name]
		if mass <= 0.0:
			skipped.append(group.name)
			continue
		if not use_spatial:
			unmapped.append(group.name)
			continue
		target = _nearest_bone(total / mass, heads)
		if target is None or target in ("sk_head", "sk_neck"):
			unmapped.append(group.name)
			continue
		spatial[group.name] = target
	if spatial:
		for vertex in mesh.vertices:
			for item in vertex.groups:
				if item.weight <= 0.0001:
					continue
				source = index_of.get(item.group)
				target = spatial.get(source)
				if not target:
					continue
				bucket = weights.setdefault(vertex.index, {})
				bucket[target] = bucket.get(target, 0.0) + item.weight
	return weights, named, spatial, skipped, unmapped, garment


def _write_groups(mesh_obj, weights):
	if mesh_obj.data.users > 1:
		mesh_obj.data = mesh_obj.data.copy()
	mesh_obj.vertex_groups.clear()
	batches = defaultdict(list)
	for index, bucket in weights.items():
		ordered = sorted(bucket.items(), key=lambda item: item[1], reverse=True)[:4]
		total = sum(weight for _, weight in ordered)
		if total > 1.0:
			ordered = [(name, weight / total) for name, weight in ordered]
		for name, weight in ordered:
			if weight < 0.001:
				continue
			batches[(name, round(weight, 4))].append(index)
	groups = {}
	for (name, weight), indices in batches.items():
		group = groups.get(name)
		if group is None:
			group = mesh_obj.vertex_groups.new(name=name)
			groups[name] = group
		group.add(indices, weight, "REPLACE")
	return len(groups)


_FOOT_GROUPS = ("sk_foot_l", "sk_foot_r", "dsk_toe_l", "dsk_toe_r")


def _medic_mesh(context, pes_arm, source):
	for obj in context.scene.objects:
		if obj.type == "MESH" and obj != source and _driven_by(obj, pes_arm) and len(obj.data.vertices) > 100:
			return obj
	return None


def _bone_world(arm, name):
	bone = arm.data.bones.get(name)
	if bone is None:
		return None
	return arm.matrix_world @ bone.head_local


def _bbox_z(obj):
	points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
	zs = [point.z for point in points]
	return min(zs), max(zs)


def _zs_from_groups(obj, names, threshold):
	wanted = set(names)
	index = {group.index: group.name for group in obj.vertex_groups}
	found = []
	for vertex in obj.data.vertices:
		for item in vertex.groups:
			if item.weight < threshold:
				continue
			if index.get(item.group) in wanted:
				found.append((obj.matrix_world @ vertex.co).z)
				break
	return found


def _zs_from_weights(mesh, weights, names, threshold):
	wanted = set(names)
	found = []
	world = mesh.matrix_world
	vertices = mesh.data.vertices
	for index, bucket in weights.items():
		if any(bucket.get(name, 0.0) >= threshold for name in wanted):
			found.append((world @ vertices[index].co).z)
	return found


def _hip_xy(mesh, weights):
	world = mesh.matrix_world
	total = Vector((0.0, 0.0, 0.0))
	count = 0.0
	for index, bucket in weights.items():
		weight = bucket.get("dsk_hip", 0.0)
		if weight < 0.5:
			weight = bucket.get("sk_belly", 0.0)
		if weight < 0.5:
			continue
		total += world @ mesh.data.vertices[index].co
		count += 1.0
	if count == 0.0:
		low, high = _bbox_z(mesh)
		center = sum((mesh.matrix_world @ Vector(corner) for corner in mesh.bound_box), Vector()) / 8.0
		return center.x, center.y, low
	center = total / count
	return center.x, center.y, None


def _target_span(medic, pes_arm):
	foot = None
	top = None
	if medic is not None:
		feet = _zs_from_groups(medic, _FOOT_GROUPS, 0.5)
		necks = _zs_from_groups(medic, ("sk_neck",), 0.5)
		if feet:
			foot = min(feet)
		if necks:
			top = sum(necks) / len(necks)
	if foot is None:
		zs = []
		for name in _FOOT_GROUPS:
			point = _bone_world(pes_arm, name)
			if point is not None:
				zs.append(point.z)
		if not zs:
			low, _high = _bbox_z(pes_arm)
			zs = [low]
		foot = min(zs)
	if top is None:
		point = _bone_world(pes_arm, "sk_neck") or _bone_world(pes_arm, "sk_head")
		top = point.z if point is not None else foot + 1.5
	return foot, top


def _source_span(mesh, weights):
	feet = _zs_from_weights(mesh, weights, _FOOT_GROUPS, 0.5)
	necks = _zs_from_weights(mesh, weights, ("sk_neck",), 0.5)
	foot = min(feet) if feet else _bbox_z(mesh)[0]
	if necks:
		top = sum(necks) / len(necks)
	else:
		heads = _zs_from_weights(mesh, weights, ("sk_head",), 0.5)
		top = (sum(heads) / len(heads)) if heads else _bbox_z(mesh)[1]
	return foot, top


def _apply_uniform(mesh, origin, target, scale):
	matrix = mesh.matrix_world.copy()
	inverse = matrix.inverted()
	for vertex in mesh.data.vertices:
		world = matrix @ vertex.co
		vertex.co = inverse @ (target + (world - origin) * scale)
	mesh.data.update()


def _fit_identity():
	zero = Vector((0.0, 0.0, 0.0))
	return {"scale": 1.0, "origin": zero, "target": zero.copy()}


def _world_height(obj):
	points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
	zs = [point.z for point in points]
	return max(zs) - min(zs)


def _fit_to_medic(context, mesh, pes_arm, weights):
	"""Uniform-scale the mesh so its neck and feet match the medic body."""
	medic = _medic_mesh(context, pes_arm, mesh)
	# Ears and a hair tuft make the bounding box taller than the skull.
	# The medic mesh has no head, so its box stops at the neck.
	points = [mesh.matrix_world @ vertex.co for vertex in mesh.data.vertices]
	# The medic mesh ends at the neck. Line the necks up. Ears and hair sit above that.
	# The collar verts rise past the neck joint, so the joint is the match, not the mesh tip.
	neck_points = _weight_points(mesh, weights, ("sk_neck",), 0.35)
	dst_neck = _medic_neck(pes_arm)
	if points and neck_points and dst_neck is not None and medic is not None and len(medic.data.vertices) > 0:
		src_foot = min(point.z for point in points)
		src_top = sum(point.z for point in neck_points) / len(neck_points)
		dst_foot = min((medic.matrix_world @ vertex.co).z for vertex in medic.data.vertices)
		dst_top = dst_neck
	elif points:
		src_foot = min(point.z for point in points)
		src_top = _crown_z(points)
		if medic is not None and len(medic.data.vertices) > 0:
			dst_foot = min((medic.matrix_world @ vertex.co).z for vertex in medic.data.vertices)
			dst_top = max((medic.matrix_world @ vertex.co).z for vertex in medic.data.vertices)
		else:
			dst_foot, dst_top = _target_span(None, pes_arm)
	else:
		src_foot, src_top = _source_span(mesh, weights)
		dst_foot, dst_top = _target_span(medic, pes_arm)
	src_height = src_top - src_foot
	dst_height = dst_top - dst_foot
	if src_height < 1e-4 or dst_height < 1e-4:
		return _fit_identity()
	scale = dst_height / src_height
	if scale < 0.02 or scale > 50.0:
		return _fit_identity()
	src_x, src_y, _low = _hip_xy(mesh, weights)
	origin = Vector((src_x, src_y, src_foot))
	target = Vector((src_x, src_y, dst_foot))
	# A neck already on the medic neck stays put. Ears above it are not a reason to scale.
	if abs(scale - 1.0) < 0.03 and abs(dst_foot - src_foot) < 0.02:
		return _fit_identity()
	# Grow around the body's own hips. Only the sole height is lined up with the medic.
	_apply_uniform(mesh, origin, target, scale)
	return {"scale": scale, "origin": origin, "target": target}


def _mapped_point(point, fit):
	return fit["target"] + (point - fit["origin"]) * fit["scale"]


def _source_armatures(context, pes_arm):
	found = []
	for obj in context.scene.objects:
		if obj.type == "ARMATURE" and obj != pes_arm and not _is_pes_armature(obj):
			found.append(obj)
	return found


def _joint_from_sources(armatures, named, role, prefer):
	candidates = [name for name, target in named.items() if target == role]
	found = []
	for name in candidates:
		for arm in armatures:
			if arm.data.bones.get(name) is not None:
				found.append((arm, name))
				break
	if not found:
		return None

	def rank(item):
		lower = item[1].lower()
		penalty = 0
		if any(bad in lower for bad in ("roll", "twist", "nub", "finger", "指", "捩", "dummy", "shadow")):
			penalty = 20
		compact = _compact(_split_side(item[1])[0])
		for index, key in enumerate(prefer):
			if compact == key:
				return penalty + index
		return penalty + 40 + len(item[1])

	arm, name = min(found, key=rank)
	return arm.matrix_world @ arm.data.bones[name].head_local


def _weight_points(mesh, weights, names, threshold):
	world = mesh.matrix_world
	points = []
	for index, bucket in weights.items():
		if any(bucket.get(name, 0.0) >= threshold for name in names):
			points.append(world @ mesh.data.vertices[index].co)
	return points


def _mean_point(points):
	if not points:
		return None
	return sum(points, Vector()) / len(points)


_ARM_GROUPS = {
	"l": ("dsk_upperarm_l", "sk_upperarm_l", "dsk_forearm_l", "sk_forearm_l", "dsk_wrist_l", "sk_hand_l"),
	"r": ("dsk_upperarm_r", "sk_upperarm_r", "dsk_forearm_r", "sk_forearm_r", "dsk_wrist_r", "sk_hand_r"),
}
_LEG_GROUPS = {
	"l": ("sk_thigh_l", "dsk_thigh_l", "sk_leg_l", "dsk_knee_l", "sk_foot_l", "dsk_toe_l"),
	"r": ("sk_thigh_r", "dsk_thigh_r", "sk_leg_r", "dsk_knee_r", "sk_foot_r", "dsk_toe_r"),
}


def _end_point(mesh, weights, roles, anchor, which):
	"""The mesh point that should sit on a joint.

	'near' is the end closest to the body. 'far' is the hand or the foot,
	not the bone that stops at the wrist while the fingers carry on.
	"""
	points = _weight_points(mesh, weights, roles, 0.35)
	if not points:
		return None
	if anchor is None or len(points) < 8:
		return _mean_point(points)
	points.sort(key=lambda point: (point - anchor).length)
	count = max(1, len(points) // 5)
	sample = points[:count] if which == "near" else points[-count:]
	return _mean_point(sample)


def _scale_arms(context, mesh, pes_arm, weights, named, fit, warp=True):
	"""Lengthen each limb from the joint it already has.

	Moving the shoulder or the hip onto the medic bone scrunches a narrow
	shoulder and drops the pelvis, which bends the spine. The root stays.
	The hand and the foot scale toward the medic joint.
	"""
	_keep_pelvis(mesh, weights, pes_arm)
	_keep_shoulders(mesh, weights, pes_arm)
	# A coat or a dress stays in the pose it was built in. Swinging the
	# sleeves down onto the medic arm pulls them out of the garment.
	if not warp:
		return {}
	armatures = _source_armatures(context, pes_arm)
	body = _mean_point(_weight_points(
		mesh, weights, ("dsk_hip", "sk_belly", "sk_chest", "sk_neck"), 0.5,
	))
	matrix = mesh.matrix_world.copy()
	inverse = matrix.inverted()
	moves = []
	chains = []
	for side in ("l", "r"):
		chains.append((
			"arm_%s" % side,
			("dsk_upperarm_%s" % side, "sk_upperarm_%s" % side),
			("sk_hand_%s" % side, "dsk_wrist_%s" % side),
			("dsk_upperarm_%s" % side, "sk_upperarm_%s" % side),
			("sk_hand_%s" % side, "dsk_wrist_%s" % side),
			_ARM_GROUPS[side],
		))
		chains.append((
			"leg_%s" % side,
			("sk_thigh_%s" % side, "dsk_thigh_%s" % side),
			("dsk_toe_%s" % side, "sk_foot_%s" % side),
			("sk_thigh_%s" % side,),
			("dsk_toe_%s" % side, "sk_foot_%s" % side),
			_LEG_GROUPS[side],
		))
	for key, prox_roles, dist_roles, prox_bones, dist_bones, _groups in chains:
		# The arm pivots on the shoulder the mesh already has. The medic
		# shoulder is wider, and moving onto it scrunches the deltoid.
		# A pivot out on the sleeve rotates the cap up and bends the humerus.
		proximal = None
		if key.startswith("arm"):
			for role in prox_roles:
				proximal = _joint_from_sources(armatures, named, role, ("upperarm", "arm"))
				if proximal is not None:
					proximal = _mapped_point(proximal, fit)
					break
		if proximal is None:
			proximal = _end_point(mesh, weights, prox_roles, body, "near")
		if proximal is None:
			for role in prox_roles:
				proximal = _joint_from_sources(armatures, named, role, ("upperarm", "arm", "thigh"))
				if proximal is not None:
					proximal = _mapped_point(proximal, fit)
					break
		# The mean of the hand or foot. The farthest fingertip is off to the side
		# and stretching toward it flattens the arm.
		distal = _end_point(mesh, weights, dist_roles, None, "near")
		if distal is None:
			for role in dist_roles:
				distal = _joint_from_sources(armatures, named, role, ("hand", "wrist", "foot", "toe"))
				if distal is not None:
					distal = _mapped_point(distal, fit)
					break
		target_prox = None
		for bone_name in prox_bones:
			target_prox = _bone_world(pes_arm, bone_name)
			if target_prox is not None:
				break
		target_dist = None
		for bone_name in dist_bones:
			target_dist = _bone_world(pes_arm, bone_name)
			if target_dist is not None:
				break
		if proximal is None or distal is None or target_prox is None or target_dist is None:
			continue
		src = distal - proximal
		dst = target_dist - proximal
		if src.length < 1e-4 or dst.length < 1e-4:
			continue
		# The body scale already set the height. A limb may aim at the pose,
		# but it must not grow into a second, longer body.
		length_scale = dst.length / src.length
		if length_scale < 0.82 or length_scale > 1.18:
			length_scale = max(0.82, min(1.18, length_scale))
		rotation = src.rotation_difference(dst)
		moves.append((key, proximal, src.normalized(), src.length, length_scale, rotation, _groups))
	if not moves:
		return {}
	for index, bucket in weights.items():
		world = matrix @ mesh.data.vertices[index].co
		best = None
		best_weight = 0.0
		best_key = ""
		for key, proximal, axis, reach, length_scale, rotation, groups in moves:
			weight = sum(bucket.get(name, 0.0) for name in groups)
			# The deltoid was given to the clavicle. It still has to ride
			# with the humerus, or the shoulder stays shrugged and the arm kinks.
			if key.startswith("arm") and weight < 0.25:
				side = key[-1]
				clav = bucket.get("dsk_clavicle_%s" % side, 0.0)
				if clav >= 0.35 and (world - proximal).length <= 0.12:
					weight = clav
			if weight > best_weight:
				best_weight = weight
				best = (proximal, axis, reach, length_scale, rotation)
				best_key = key
		if best is None or best_weight < 0.25:
			continue
		proximal, axis, reach, length_scale, rotation = best
		delta = world - proximal
		along_amt = axis.dot(delta)
		if best_key.startswith("arm"):
			# Chest side of the shoulder stays. The cap (along ~= 0) rotates
			# with the shaft, so the upper arm stays one straight bone.
			if along_amt < -0.05:
				continue
		elif along_amt < reach * 0.12:
			continue
		# Length only. The part across the limb stays, so the shoulder does not get wider.
		along = axis * along_amt
		perp = delta - along
		stretched = along * length_scale + perp
		scaled = proximal + (rotation @ stretched)
		blend = min(best_weight, 1.0)
		if best_key.startswith("arm") and along_amt < 0.0:
			blend *= (along_amt + 0.05) / 0.05
		mesh.data.vertices[index].co = inverse @ world.lerp(scaled, blend)
	mesh.data.update()
	# After the arm is on the bone. Before that, the T-pose sleeve is not
	# along sk_forearm, so the elbow test misses it and the upper arm keeps it.
	_seat_forearm(mesh, weights, pes_arm)
	return {key: length_scale for key, _prox, _axis, _reach, length_scale, _rot, _groups in moves}


def _seat_forearm(mesh, weights, pes_arm):
	"""The forearm bends with sk_forearm. dsk_forearm is a twist at the wrist.

	Upper-arm weights that run past the elbow keep the whole arm on the
	shoulder, so sk_forearm never picks the sleeve up.
	"""
	matrix = mesh.matrix_world
	for side in ("l", "r"):
		elbow = _bone_world(pes_arm, "sk_forearm_%s" % side)
		wrist = _bone_world(pes_arm, "sk_hand_%s" % side)
		if elbow is None or wrist is None:
			continue
		axis = wrist - elbow
		if axis.length < 1e-4:
			continue
		length = axis.length
		direction = axis / length
		upper_keys = ("dsk_upperarm_%s" % side, "sk_upperarm_%s" % side)
		# Twist helpers. They are not the bone you rotate.
		helper_keys = (
			"dsk_forearm_%s" % side,
			"dsk_forearm_t_%s" % side,
			"dsk_elbow_%s" % side,
		)
		dest = "sk_forearm_%s" % side
		for index, bucket in list(weights.items()):
			upper = sum(bucket.get(key, 0.0) for key in upper_keys)
			helper = sum(bucket.get(key, 0.0) for key in helper_keys)
			if upper < 0.05 and helper < 0.05:
				continue
			point = matrix @ mesh.data.vertices[index].co
			along = (point - elbow).dot(direction)
			# Shoulder side of the elbow stays on the upper arm.
			# Elbow-helper weight just behind the joint still bends with it.
			if along < -0.08:
				continue
			if along < -0.04 and helper < 0.05:
				continue
			# The hand stays on the hand. This pass is the sleeve only.
			if along > length * 0.82:
				continue
			share = (along + 0.04) / 0.10
			share = max(0.0, min(1.0, share))
			moved = 0.0
			for key in upper_keys:
				amount = bucket.get(key, 0.0)
				take = amount * share
				if take <= 0.0:
					continue
				bucket[key] = amount - take
				moved += take
			for key in helper_keys:
				moved += bucket.pop(key, 0.0)
			if moved > 0.0:
				bucket[dest] = bucket.get(dest, 0.0) + moved


def _keep_shoulders(mesh, weights, pes_arm):
	"""The shoulder cap stays on the clavicle. Urara's shoulder is almost all chest.

	Upper-arm weights on the body side of the shoulder joint are the sleeve.
	Leaving them on the arm pulls the cap off the chest when the arm lifts.
	"""
	chest = _bone_world(pes_arm, "sk_chest") or _bone_world(pes_arm, "sk_neck")
	if chest is None:
		return
	matrix = mesh.matrix_world
	for side in ("l", "r"):
		joint = _bone_world(pes_arm, "dsk_upperarm_%s" % side)
		if joint is None:
			joint = _bone_world(pes_arm, "sk_upperarm_%s" % side)
		if joint is None:
			continue
		direction = joint - chest
		if direction.length < 1e-4:
			continue
		direction.normalize()
		joint_along = (joint - chest).dot(direction)
		keys = ("dsk_upperarm_%s" % side, "sk_upperarm_%s" % side)
		dest = "dsk_clavicle_%s" % side
		for index, bucket in weights.items():
			if not any(bucket.get(key, 0.0) >= 0.05 for key in keys):
				continue
			point = matrix @ mesh.data.vertices[index].co
			along = (point - chest).dot(direction)
			# Full clavicle on the chest side of the joint, full arm 6cm past it.
			past = along - joint_along
			if past >= 0.06:
				continue
			share = 1.0 - (past + 0.06) / 0.12
			share = max(0.0, min(1.0, share))
			for key in keys:
				amount = bucket.get(key, 0.0)
				move = amount * share
				if move <= 0.0:
					continue
				bucket[key] = amount - move
				bucket[dest] = bucket.get(dest, 0.0) + move


def _gap_clusters(items, gap):
	"""Group (index, depth, lateral, z) points that sit within `gap` of each other."""
	count = len(items)
	parent = list(range(count))

	def find(index):
		while parent[index] != index:
			parent[index] = parent[parent[index]]
			index = parent[index]
		return index

	limit = gap * gap
	for index in range(count):
		depth, lateral = items[index][1], items[index][2]
		for other in range(index):
			dd = depth - items[other][1]
			ds = lateral - items[other][2]
			if dd * dd + ds * ds > limit:
				continue
			left, right = find(index), find(other)
			if left != right:
				parent[left] = right
	groups = {}
	for index in range(count):
		groups.setdefault(find(index), []).append(items[index])
	return list(groups.values())


def _box_gap(depth, lateral, box):
	"""Distance from a point to a depth/side box. Zero when the point is inside."""
	d0, d1, s0, s1 = box
	if depth < d0:
		dd = d0 - depth
	elif depth > d1:
		dd = depth - d1
	else:
		dd = 0.0
	if lateral < s0:
		ds = s0 - lateral
	elif lateral > s1:
		ds = lateral - s1
	else:
		ds = 0.0
	return (dd * dd + ds * ds) ** 0.5


def _hanging_appendage(mesh, pes_arm):
	"""A tail or a coat leaves the body and hangs down beside it.

	Slices run from the head to the feet. The torso column is the body.
	A cluster that opens a gap, then keeps going parallel to that column,
	is an appendage. Hair does the same thing but it is already there above
	the neck, so a column that reaches the head is left alone.
	"""
	forward = _facing_vector(pes_arm)
	side = Vector((-forward.y, forward.x, 0.0))
	hip = _bone_world(pes_arm, "dsk_hip")
	chest = _bone_world(pes_arm, "sk_chest") or _bone_world(pes_arm, "sk_neck")
	if hip is None:
		return set()
	matrix = mesh.matrix_world
	points = [(vertex.index, matrix @ vertex.co) for vertex in mesh.data.vertices]
	if len(points) < 30:
		return set()
	chest_z = chest.z if chest is not None else hip.z + 0.25

	def project(point):
		return _depth(point, forward), point.x * side.x + point.y * side.y

	body = None
	columns = []
	z = max(point.z for _, point in points) + 0.04
	z_min = min(point.z for _, point in points)
	while z > z_min:
		slab = [(index, point) for index, point in points if z - 0.04 <= point.z < z]
		z -= 0.04
		fresh = []
		for index, point in slab:
			depth, lateral = project(point)
			claimed = False
			for column in columns:
				if (
					column["d0"] - 0.03 <= depth <= column["d1"] + 0.03
					and column["s0"] - 0.03 <= lateral <= column["s1"] + 0.03
				):
					column["ids"].add(index)
					column["d0"] = min(column["d0"], depth)
					column["d1"] = max(column["d1"], depth)
					column["s0"] = min(column["s0"], lateral)
					column["s1"] = max(column["s1"], lateral)
					column["z1"] = min(column["z1"], point.z)
					if point.z > chest_z:
						column["hair"] = True
					column["seen"] = True
					claimed = True
					break
			if not claimed:
				fresh.append((index, depth, lateral, point.z))
		for column in columns:
			if column.pop("seen", False):
				column["miss"] = 0
			else:
				column["miss"] = column.get("miss", 0) + 1
		columns = [column for column in columns if column.get("miss", 0) <= 3]
		if len(fresh) < 8:
			continue
		clusters = _gap_clusters(fresh, 0.045)
		if body is None:
			body_cluster = max(clusters, key=len)
			depths = [item[1] for item in body_cluster]
			sides = [item[2] for item in body_cluster]
			body = (min(depths), max(depths), min(sides), max(sides))
			continue
		body_parts = []
		for cluster in clusters:
			nearest = min(_box_gap(item[1], item[2], body) for item in cluster)
			# Rear is the low end of depth. A tail sits wholly behind the body.
			# A leg sits beside it at the same depth and must stay on the body.
			rear = max(item[1] for item in cluster) < body[0] - 0.02
			if nearest <= 0.02 or not rear or len(cluster) > len(fresh) * 0.45:
				body_parts.extend(cluster)
				continue
			if len(cluster) < 3 or nearest < 0.025:
				# Still touching the butt. Do not grow the body box out to it,
				# or the rest of the tail gets swallowed on the next slice.
				continue
			depths = [item[1] for item in cluster]
			sides = [item[2] for item in cluster]
			zs = [item[3] for item in cluster]
			columns.append({
				"d0": min(depths),
				"d1": max(depths),
				"s0": min(sides),
				"s1": max(sides),
				"z0": max(zs),
				"z1": min(zs),
				"ids": {item[0] for item in cluster},
				"hair": max(zs) > chest_z,
				"miss": 0,
			})
		if body_parts:
			depths = [item[1] for item in body_parts]
			sides = [item[2] for item in body_parts]
			body = (min(depths), max(depths), min(sides), max(sides))
	marked = set()
	for column in columns:
		if column["hair"]:
			continue
		if column["z1"] > hip.z + 0.02:
			continue
		if column["z0"] - column["z1"] < 0.12:
			continue
		marked.update(column["ids"])
	return marked


def _keep_pelvis(mesh, weights, pes_arm):
	"""Thigh weights on the butt belong to the hip, or the leg takes the ass."""
	forward = _facing_vector(pes_arm)
	matrix = mesh.matrix_world
	thigh_keys = ("sk_thigh_l", "sk_thigh_r", "dsk_thigh_l", "dsk_thigh_r")
	leg_keys = thigh_keys + ("sk_leg_l", "sk_leg_r", "sk_foot_l", "sk_foot_r", "dsk_toe_l", "dsk_toe_r")
	# Arms, the head, and the chest stay put when a sleeve or a hair strand
	# hangs in the same slice as a tail.
	protected = (
		"sk_head", "sk_neck", "sk_chest", "sk_belly",
		"dsk_clavicle_l", "dsk_clavicle_r",
		"dsk_upperarm_l", "dsk_upperarm_r", "sk_upperarm_l", "sk_upperarm_r",
		"dsk_forearm_l", "dsk_forearm_r", "sk_forearm_l", "sk_forearm_r",
		"sk_hand_l", "sk_hand_r", "dsk_wrist_l", "dsk_wrist_r",
	)
	hanging = _hanging_appendage(mesh, pes_arm)
	for index in hanging:
		bucket = weights.get(index)
		if bucket and any(bucket.get(name, 0.0) > 0.25 for name in protected):
			continue
		# The boot is the leg. A coat hangs outside that column, so a vert
		# that is already a leg stays a leg.
		if bucket:
			limb = sum(bucket.get(key, 0.0) for key in leg_keys)
			if limb >= 0.5:
				continue
		if not bucket:
			weights[index] = {"dsk_hip": 1.0}
			continue
		moved = 0.0
		for key in leg_keys:
			moved += bucket.pop(key, 0.0)
		if moved or "dsk_hip" not in bucket:
			bucket["dsk_hip"] = bucket.get("dsk_hip", 0.0) + moved
	# The tail is on the hip. Leave it out of the crease, or the centroid
	# drops down the tail and the thigh takes it back.
	hip_points = [
		point for index, point in (
			(index, matrix @ mesh.data.vertices[index].co)
			for index, bucket in weights.items()
			if bucket.get("dsk_hip", 0.0) >= 0.5 and index not in hanging
		)
	]
	if len(hip_points) >= 8:
		hip = sum(hip_points, Vector()) / len(hip_points)
	else:
		hip = _bone_world(pes_arm, "dsk_hip")
		if hip is None:
			return
	hip_depth = _depth(hip, forward)
	for index, bucket in weights.items():
		if index in hanging:
			continue
		point = matrix @ mesh.data.vertices[index].co
		# Full hip just above the joint, full thigh 12cm below. The source
		# groups meet in a hard cut, so both sides of that cut get a share.
		span = (hip.z + 0.04) - point.z
		if span <= 0.0 or span >= 0.22:
			continue
		behind = hip_depth - _depth(point, forward)
		# The rear of the pelvis, not the tail. The tail is already marked.
		if behind > 0.14 or behind < -0.02:
			continue
		if abs(point.x) > 0.22:
			continue
		thigh_here = sum(bucket.get(key, 0.0) for key in thigh_keys)
		if thigh_here < 0.2:
			continue
		# Only give thigh weight back to the hip. A tail root is already on
		# the hip, and this pass must not hand it to the leg.
		share = span / 0.22
		keep = thigh_here * share
		moved = thigh_here - keep
		if moved <= 0.0:
			continue
		for key in thigh_keys:
			amount = bucket.get(key, 0.0)
			if amount <= 0.0:
				continue
			bucket[key] = amount * share
		bucket["dsk_hip"] = bucket.get("dsk_hip", 0.0) + moved


def _attach(context, mesh, pes_arm):
	for modifier in mesh.modifiers:
		if modifier.type == "ARMATURE" and modifier.object == pes_arm:
			break
	else:
		modifier = mesh.modifiers.new(name="Armature", type="ARMATURE")
		modifier.object = pes_arm
	parent = pes_arm.parent
	for obj in context.scene.objects:
		if obj.type == "MESH" and obj != mesh and _driven_by(obj, pes_arm) and obj.parent is not None:
			parent = obj.parent
			break
	if parent is not None and parent != mesh:
		matrix = mesh.matrix_world.copy()
		mesh.parent = parent
		mesh.matrix_world = matrix


def _facing_vector(pes_arm):
	"""Horizontal direction the medic faces. Hair behind the head is the opposite way."""
	head = _bone_world(pes_arm, "sk_head")
	chest = _bone_world(pes_arm, "sk_chest") or _bone_world(pes_arm, "sk_neck")
	if head is not None and chest is not None:
		flat = Vector((head.x - chest.x, head.y - chest.y, 0.0))
		if flat.length >= 0.01:
			flat.normalize()
			return flat
	return Vector((0.0, -1.0, 0.0))


def _depth(point, forward):
	return point.x * forward.x + point.y * forward.y


def _world_points(obj):
	world = obj.matrix_world
	return [world @ vertex.co for vertex in obj.data.vertices]


def _median(values):
	ordered = sorted(values)
	return ordered[len(ordered) // 2]


def _depth_bins(values, step):
	low = min(values)
	high = max(values)
	count = int((high - low) / step) + 1
	bins = [0] * max(count, 1)
	for value in values:
		index = int((value - low) / step)
		if index < 0:
			index = 0
		elif index >= len(bins):
			index = len(bins) - 1
		bins[index] += 1
	return low, bins


def _rear_cutoff(points, forward):
	"""Depth where a separate mass behind the body starts, or None.

	Huge hair behind the head is its own lobe on the back of the torso.
	A normal back falls off and stays empty, so it is not hair.
	"""
	if len(points) < 50:
		return None
	zs = [point.z for point in points]
	low = min(zs)
	high = max(zs)
	span = high - low
	if span < 1e-4:
		return None
	torso = [
		point for point in points
		if low + 0.28 * span <= point.z <= low + 0.78 * span
	]
	if len(torso) < 40:
		torso = points
	origin, bins = _depth_bins([_depth(point, forward) for point in torso], 0.04)
	peak = max(range(len(bins)), key=lambda index: bins[index])
	peak_count = bins[peak]
	if peak_count < 30:
		return None
	valley = None
	for index in range(peak - 1, -1, -1):
		if bins[index] <= peak_count * 0.10:
			valley = index
			break
	if valley is None:
		return None
	lobe = False
	for index in range(valley - 1, -1, -1):
		if bins[index] >= max(80, bins[valley] * 2) and bins[index] >= peak_count * 0.03:
			lobe = True
			break
	if not lobe:
		return None
	return origin + valley * 0.04


def _crown_z(points):
	"""Top of the scalp. Ears, horns, and a hair tuft are sparser than the skull."""
	if not points:
		return 0.0
	zs = [point.z for point in points]
	foot = min(zs)
	top = max(zs)
	span = top - foot
	if span < 1e-4:
		return top
	step = 0.02
	bins = []
	z = foot
	while z < top + 1e-6:
		count = sum(1 for point in points if z <= point.z < z + step)
		bins.append((z, count))
		z += step
	# Walk down from the tip. An accessory sits above the scalp and has far
	# fewer vertices than the head just under it.
	for index in range(len(bins) - 1, -1, -1):
		z, count = bins[index]
		if count < 60:
			continue
		below = [item[1] for item in bins if z - 0.18 <= item[0] < z]
		if not below:
			return z + step
		if count >= max(below) * 0.35:
			return z + step
	return top


def _medic_neck(pes_arm):
	"""Height of the medic neck. The body mesh stops here. The head bone is empty.

	Do not aim the scalp at 1.80. Ears and hair sit above this joint.
	"""
	neck = _bone_world(pes_arm, "sk_neck")
	if neck is None:
		return None
	return neck.z


def _body_frame(points, forward):
	"""Foot, crown, and body center, with rear hair left out of the center."""
	if not points:
		return None
	cutoff = _rear_cutoff(points, forward)
	if cutoff is None:
		body = points
	else:
		body = [point for point in points if _depth(point, forward) >= cutoff]
		if len(body) < len(points) * 0.25:
			body = points
	zs = [point.z for point in body]
	foot = min(zs)
	top = max(zs)
	span = top - foot
	torso = [
		point for point in body
		if span < 1e-4 or foot + 0.30 * span <= point.z <= foot + 0.75 * span
	] or body
	crown = _crown_z(body)
	if crown - foot < span * 0.45:
		crown = top
	return {
		"foot": foot,
		"crown": crown,
		"height": max(crown - foot, 1e-4),
		"center": Vector((_median([point.x for point in torso]), _median([point.y for point in torso]), foot)),
		"hair": cutoff is not None and body is not points,
	}


def _fit_naked_body(mesh, medic, pes_arm):
	"""Line the body up with the medic. Rear hair does not move or scale it.

	The full bounding box treats hair behind the head as part of the body,
	which shoves the mesh forward and shrinks it. A body already close to
	the medic's height stays at that size. The extra is the hair.
	"""
	forward = _facing_vector(pes_arm)
	source = _body_frame(_world_points(mesh), forward)
	if source is None:
		return _fit_identity()
	if medic is not None:
		target = _body_frame(_world_points(medic), forward)
	else:
		target = None
	if target is None:
		dst_foot, dst_top = _target_span(medic, pes_arm)
		hip = _bone_world(pes_arm, "dsk_hip") or Vector((0.0, 0.0, 0.0))
		target = {
			"foot": dst_foot,
			"crown": dst_top,
			"height": max(dst_top - dst_foot, 1e-4),
			"center": Vector((hip.x, hip.y, dst_foot)),
		}
	scale = target["height"] / source["height"]
	if scale < 0.02 or scale > 50.0:
		return _fit_identity()
	# Hair above the skull makes a fitted body look 10-20% tall. Leave it.
	applied = scale
	if abs(scale - 1.0) < 0.03:
		applied = 1.0
	# A tail or a chest moves the depth center by about 10cm. Sliding the body
	# that far puts the feet in front of the legs. Only correct a real offset.
	side = Vector((-forward.y, forward.x, 0.0))
	src_depth = _depth(source["center"], forward)
	dst_depth = _depth(target["center"], forward)
	use_depth = src_depth if abs(dst_depth - src_depth) < 0.25 else dst_depth
	dst_side = target["center"].x * side.x + target["center"].y * side.y
	dest_xy = side * dst_side + Vector((forward.x, forward.y, 0.0)) * use_depth
	origin = Vector((source["center"].x, source["center"].y, source["foot"]))
	dest = Vector((dest_xy.x, dest_xy.y, target["foot"]))
	if abs(applied - 1.0) < 0.01 and (origin - dest).length < 0.01:
		return {"scale": 1.0, "origin": origin, "target": dest, "resized": False, "hair": source["hair"]}
	_apply_uniform(mesh, origin, dest, applied)
	return {"scale": applied, "origin": origin, "target": dest, "resized": applied != 1.0, "hair": source["hair"]}


def _rear_hair_indices(mesh, pes_arm):
	"""Vertex indices in the lobe behind the body. They belong on sk_head."""
	forward = _facing_vector(pes_arm)
	world = mesh.matrix_world
	indexed = [(vertex.index, world @ vertex.co) for vertex in mesh.data.vertices]
	cutoff = _rear_cutoff([point for _, point in indexed], forward)
	if cutoff is None:
		return []
	return [index for index, point in indexed if _depth(point, forward) < cutoff]


def _naked_meshes(context, pes_arm):
	chosen = []
	weighted = []
	seen = set()

	def add(obj):
		if obj.type != "MESH" or obj.name in seen:
			return
		# The medic body is parented to this armature and already weighted.
		# A naked mesh that happens to point at it is still a target.
		if pes_arm is not None and _driven_by(obj, pes_arm) and len(obj.vertex_groups) > 0:
			return
		parent = obj.parent.name.lower() if obj.parent else ""
		if parent in ("rigidbodies", "rigidbody", "joints"):
			return
		seen.add(obj.name)
		if len(obj.vertex_groups) > 0:
			weighted.append(obj.name)
			return
		chosen.append(obj)

	for obj in context.selected_objects:
		if obj.type == "MESH":
			add(obj)
		elif obj.type in ("EMPTY", "ARMATURE") and not _is_pes_armature(obj):
			for child in _walk(obj):
				add(child)
	if weighted and not chosen:
		raise ValueError(
			"This mesh already has vertex groups. Use Convert existing Skeleton to PES Skeleton."
		)
	if weighted and chosen:
		raise ValueError(
			"Some selected meshes already have vertex groups (%s). "
			"Convert those with Convert existing Skeleton to PES Skeleton."
			% ", ".join(weighted[:4])
		)
	return chosen


def _keyless_holder(context, target):
	"""A throwaway copy when shape keys would block applying the transfer.

	Blender refuses to apply a modifier to a mesh that still has shape keys,
	and removing those keys needs a window context this add-on does not have
	while a file is opening in the background. The copy is the basis surface,
	vertex for vertex. The original keys stay put.
	"""
	if target.data.shape_keys is None:
		return target, None
	data = bpy.data.meshes.new("%s transfer" % target.name)
	coords = [vertex.co[:] for vertex in target.data.vertices]
	faces = [tuple(face.vertices) for face in target.data.polygons]
	data.from_pydata(coords, [], faces)
	data.update()
	holder = bpy.data.objects.new("%s transfer" % target.name, data)
	holder.matrix_world = target.matrix_world.copy()
	context.scene.collection.objects.link(holder)
	return holder, holder


def _drop_holder(holder):
	data = holder.data
	bpy.data.objects.remove(holder, do_unlink=True)
	if data.users == 0:
		bpy.data.meshes.remove(data)


def _apply_weight_modifier(context, holder, source):
	context.view_layer.objects.active = holder
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	for obj in context.view_layer.objects:
		obj.select_set(obj == holder)
	holder.select_set(True)
	context.view_layer.update()
	have = {group.name for group in holder.vertex_groups}
	for group in source.vertex_groups:
		if group.name not in have:
			holder.vertex_groups.new(name=group.name)
	modifier = holder.modifiers.new(name="PES Weight Transfer", type="DATA_TRANSFER")
	modifier.object = source
	modifier.use_vert_data = True
	modifier.data_types_verts = {"VGROUP_WEIGHTS"}
	modifier.vert_mapping = "POLYINTERP_NEAREST"
	modifier.layers_vgroup_select_src = "ALL"
	modifier.layers_vgroup_select_dst = "NAME"
	modifier.mix_mode = "REPLACE"
	modifier.mix_factor = 1.0
	try:
		with context.temp_override(
			object=holder,
			active_object=holder,
			selected_objects=[holder],
			selected_editable_objects=[holder],
		):
			result = bpy.ops.object.modifier_apply(modifier=modifier.name)
	except RuntimeError as error:
		if modifier.name in holder.modifiers:
			holder.modifiers.remove(modifier)
		raise ValueError("Weight transfer from %s failed. %s" % (source.name, error)) from error
	if "FINISHED" not in set(result):
		if modifier.name in holder.modifiers:
			holder.modifiers.remove(modifier)
		raise ValueError("Weight transfer from %s did not finish." % source.name)


def _transfer_weights(context, target, source):
	"""Copy the source mesh's vertex groups by nearest-face interpolation."""
	holder, temporary = _keyless_holder(context, target)
	try:
		_apply_weight_modifier(context, holder, source)
		return _read_weights(holder)
	finally:
		if temporary is not None:
			_drop_holder(temporary)


def _read_weights(mesh_obj):
	index_of = {group.index: group.name for group in mesh_obj.vertex_groups}
	weights = {}
	for vertex in mesh_obj.data.vertices:
		bucket = None
		for item in vertex.groups:
			if item.weight <= 0.0001:
				continue
			name = index_of.get(item.group)
			if not name:
				continue
			if bucket is None:
				bucket = weights.setdefault(vertex.index, {})
			bucket[name] = bucket.get(name, 0.0) + item.weight
	return weights


def _span_weights_present(weights):
	feet = False
	top = False
	for bucket in weights.values():
		if not feet and any(bucket.get(name, 0.0) >= 0.5 for name in _FOOT_GROUPS):
			feet = True
		if not top and (bucket.get("sk_neck", 0.0) >= 0.5 or bucket.get("sk_head", 0.0) >= 0.5):
			top = True
		if feet and top:
			return True
	return False


def _arm_side_plausible(mesh, weights, side):
	shoulder = _mean_point(_weight_points(
		mesh, weights, ("dsk_upperarm_%s" % side, "sk_upperarm_%s" % side), 0.5,
	))
	hand = _mean_point(_weight_points(
		mesh, weights, ("sk_hand_%s" % side, "dsk_wrist_%s" % side), 0.5,
	))
	hip = _mean_point(_weight_points(mesh, weights, ("dsk_hip", "sk_belly"), 0.5))
	if shoulder is None or hand is None or hip is None:
		return False
	# An A-pose hand sits inside the shoulder's X. A label stuck on the belly
	# stays near the center and does not clear the hip the way a hand does.
	if abs(hand.x) < abs(shoulder.x) * 0.55:
		return False
	if (hand - hip).length < (shoulder - hip).length * 0.9:
		return False
	return (hand - shoulder).length >= 0.02


def _arm_weights_for_scale(mesh, weights):
	copied = {index: dict(bucket) for index, bucket in weights.items()}
	for side in ("l", "r"):
		if _arm_side_plausible(mesh, weights, side):
			continue
		for bucket in copied.values():
			for name in _ARM_GROUPS[side]:
				bucket.pop(name, None)
	return copied


def _strip_armature_modifiers(mesh):
	for modifier in list(mesh.modifiers):
		if modifier.type == "ARMATURE":
			mesh.modifiers.remove(modifier)


def transfer_boneless_to_pes(context, medic_path):
	"""Rig a mesh that has no vertex groups by copying the medic body's weights.

	Rear hair is kept out of the line-up. A body already near the medic's
	height is not scaled. The copied groups then drive the wrist fit, and
	the hair behind the head is weighted to sk_head.
	"""
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	pes_arm = find_pes_armature(context)
	if pes_arm is None:
		pes_arm = import_medic(context, medic_path)
	meshes = _naked_meshes(context, pes_arm)
	if not meshes:
		raise ValueError(
			"Select the boneless model. The medic body is the weight source and is left unchanged."
		)
	mesh = _join(context, meshes)
	if mesh.data.users > 1:
		mesh.data = mesh.data.copy()
	_strip_armature_modifiers(mesh)
	medic = _medic_mesh(context, pes_arm, mesh)
	if medic is None:
		raise ValueError(
			"The medic body mesh is not in the scene. Set the medic FMDL so its weights can be copied."
		)
	if len(medic.vertex_groups) == 0:
		raise ValueError("The medic body has no vertex groups to copy.")
	bbox_fit = _fit_naked_body(mesh, medic, pes_arm)
	weights = _transfer_weights(context, mesh, medic)
	if not weights:
		raise ValueError(
			"Weight transfer produced no vertex groups. Line the mesh up with the medic and try again."
		)
	weight_fit = _fit_identity()
	# A second height fit would shrink a body whose extra height is hair.
	if bbox_fit.get("resized") and _span_weights_present(weights):
		weight_fit = _fit_to_medic(context, mesh, pes_arm, weights)
	arm_weights = _arm_weights_for_scale(mesh, weights)
	arm_scales = _scale_arms(context, mesh, pes_arm, arm_weights, {}, _fit_identity())
	weights = _transfer_weights(context, mesh, medic)
	if not weights:
		raise ValueError("Weight transfer produced no vertex groups after the mesh was fitted.")
	for index in _rear_hair_indices(mesh, pes_arm):
		weights[index] = {"sk_head": 1.0}
	needed = {name for bucket in weights.values() for name in bucket}
	_add_missing_bones(context, pes_arm, needed)
	count = _write_groups(mesh, weights)
	_attach(context, mesh, pes_arm)
	return {
		"mesh": mesh.name,
		"armature": pes_arm.name,
		"source": medic.name,
		"groups": count,
		"scale": bbox_fit["scale"] * weight_fit["scale"],
		"arms": arm_scales,
	}


def retarget_to_pes(context, medic_path):
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	pes_arm = find_pes_armature(context)
	if pes_arm is None:
		pes_arm = import_medic(context, medic_path)
	meshes = _with_attached_parts(context, _source_meshes(context, pes_arm), pes_arm)
	if not meshes:
		raise ValueError("Select the imported model. The medic mesh is left as the skeleton reference.")
	mesh = _join(context, meshes)
	_apply_source_armatures(context, mesh, pes_arm)
	weights, named, spatial, skipped, unmapped, garment = _accumulate(mesh, pes_arm)
	if not weights:
		raise ValueError("None of the vertex groups matched a PES bone.")
	fit = _fit_to_medic(context, mesh, pes_arm, weights)
	arm_scales = _scale_arms(
		context, mesh, pes_arm, weights, named, fit, warp=garment < 300,
	)
	needed = {name for bucket in weights.values() for name in bucket}
	_add_missing_bones(context, pes_arm, needed)
	count = _write_groups(mesh, weights)
	_attach(context, mesh, pes_arm)
	mapped = len([name for name, target in named.items() if target]) + len(spatial)
	return {
		"mesh": mesh.name,
		"armature": pes_arm.name,
		"groups": count,
		"mapped": mapped,
		"skipped": len(skipped),
		"unmapped": unmapped,
		"scale": fit["scale"],
		"arms": arm_scales,
	}
