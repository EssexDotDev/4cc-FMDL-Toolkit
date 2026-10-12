"""Merge PES glove meshes and hand bones onto the body."""

import bpy
from mathutils import Vector


def _root(obj):
	while obj.parent is not None:
		obj = obj.parent
	return obj


def _walk(obj):
	yield obj
	for child in obj.children:
		yield from _walk(child)


def _bone_names(arm_obj):
	return {bone.name for bone in arm_obj.data.bones}


def _side_from_bones(arm_obj):
	names = _bone_names(arm_obj)
	left = any(name.endswith("_l") for name in names)
	right = any(name.endswith("_r") for name in names)
	if left and not right:
		return "l"
	if right and not left:
		return "r"
	return None


def _is_body_armature(arm_obj):
	names = _bone_names(arm_obj)
	return "dsk_hip" in names or "sk_chest" in names or "sk_thigh_l" in names


def _is_glove_armature(arm_obj):
	if _is_body_armature(arm_obj):
		return False
	names = _bone_names(arm_obj)
	return any(name.startswith("skh_") or name.startswith("dsk_wrist_") for name in names)


def _selected_trees(context):
	roots = []
	seen = set()
	for obj in context.selected_objects:
		root = _root(obj)
		if root.name in seen:
			continue
		seen.add(root.name)
		roots.append(root)
	objects = []
	for root in roots:
		objects.extend(_walk(root))
	return objects


def _armatures_in(objects):
	return [obj for obj in objects if obj.type == "ARMATURE"]


def _meshes_for_armature(arm_obj, pool):
	found = []
	for obj in pool:
		if obj.type != "MESH":
			continue
		for modifier in obj.modifiers:
			if modifier.type == "ARMATURE" and modifier.object == arm_obj:
				found.append(obj)
				break
	if found:
		return found
	root = _root(arm_obj)
	return [obj for obj in _walk(root) if obj.type == "MESH"]


def _body_material(mesh_obj):
	for material in mesh_obj.data.materials:
		if material is None or material.node_tree is None:
			continue
		for node in material.node_tree.nodes:
			image = getattr(node, "image", None)
			if image is not None and "body" in image.name.lower():
				return material
	for material in mesh_obj.data.materials:
		if material is not None:
			return material
	return None


def _world_from_arm(arm_obj, local):
	return arm_obj.matrix_world @ Vector(local)


def _local_on_arm(arm_obj, world):
	return arm_obj.matrix_world.inverted() @ Vector(world)


def _glove_top(obj, body_root):
	top = obj
	while top.parent is not None and top.parent != body_root:
		top = top.parent
	if top == body_root:
		return obj
	return top


def _align_glove(body_arm, glove_arm, glove_objects, side):
	"""Move the glove so dsk_wrist sits on the tail of the body's dsk_forearm."""
	forearm_name = "dsk_forearm_%s" % side
	wrist_name = "dsk_wrist_%s" % side
	if forearm_name not in body_arm.data.bones:
		raise ValueError("Body is missing %s" % forearm_name)
	if wrist_name not in glove_arm.data.bones:
		raise ValueError("%s is missing %s" % (glove_arm.name, wrist_name))
	target = _world_from_arm(body_arm, body_arm.data.bones[forearm_name].tail_local)
	source = _world_from_arm(glove_arm, glove_arm.data.bones[wrist_name].head_local)
	delta = target - source
	if delta.length < 1e-8:
		return delta
	body_root = _root(body_arm)
	seen = set()
	for obj in glove_objects:
		top = _glove_top(obj, body_root)
		if top.name in seen:
			continue
		seen.add(top.name)
		matrix = top.matrix_world.copy()
		matrix.translation += delta
		top.matrix_world = matrix
	return delta


def _copy_hand_bones(body_arm, glove_arm, side):
	"""Body dsk_forearm stays. Body sk_hand is replaced by the glove hand."""
	keep_body = {"sk_forearm_%s" % side, "dsk_forearm_%s" % side}
	replace = "sk_hand_%s" % side
	bpy.context.view_layer.objects.active = body_arm
	bpy.ops.object.mode_set(mode="EDIT")
	edit = body_arm.data.edit_bones
	if replace in edit:
		edit.remove(edit[replace])

	sources = [bone for bone in glove_arm.data.bones if bone.name not in keep_body]
	created = {}
	for bone in sources:
		if bone.name in edit:
			continue
		new = edit.new(bone.name)
		new.head = _local_on_arm(body_arm, _world_from_arm(glove_arm, bone.head_local))
		new.tail = _local_on_arm(body_arm, _world_from_arm(glove_arm, bone.tail_local))
		# Bone.z_axis is not the armature-space Z column. Roll comes from matrix_local.
		world_matrix = glove_arm.matrix_world @ bone.matrix_local
		local_matrix = body_arm.matrix_world.inverted() @ world_matrix
		z_local = local_matrix.col[2].xyz
		if z_local.length > 1e-8:
			new.align_roll(z_local)
		new.use_deform = bone.use_deform
		new.use_connect = False
		created[bone.name] = new

	for bone in sources:
		new = created.get(bone.name)
		if new is None or bone.parent is None:
			continue
		parent = edit.get(bone.parent.name)
		if parent is not None:
			new.parent = parent
	bpy.ops.object.mode_set(mode="OBJECT")
	return len(created)


def _match_uv_names(body_mesh, glove_mesh):
	body_layers = body_mesh.data.uv_layers
	glove_layers = glove_mesh.data.uv_layers
	for index, layer in enumerate(glove_layers):
		if index >= len(body_layers):
			break
		target = body_layers[index].name
		if layer.name != target and target not in glove_layers:
			layer.name = target


def _use_body_material(glove_mesh, material):
	if material is None:
		return
	mesh = glove_mesh.data
	if mesh.users > 1:
		glove_mesh.data = mesh.copy()
		mesh = glove_mesh.data
	mesh.materials.clear()
	mesh.materials.append(material)
	for polygon in mesh.polygons:
		polygon.material_index = 0


def _prune_empty(obj):
	if obj is None or obj.type != "EMPTY":
		return
	for child in list(obj.children):
		_prune_empty(child)
	if len(obj.children) == 0:
		bpy.data.objects.remove(obj, do_unlink=True)


def selection_summary(context):
	if not context.selected_objects:
		return ["Nothing selected"]
	pool = _selected_trees(context)
	body = [obj.name for obj in _armatures_in(pool) if _is_body_armature(obj)]
	gloves = []
	for obj in _armatures_in(pool):
		if _is_glove_armature(obj):
			side = _side_from_bones(obj) or "?"
			gloves.append("%s (%s)" % (obj.name, side))
	lines = []
	lines.append("Body: %s" % (", ".join(body) if body else "not in selection"))
	lines.append("Gloves: %s" % (", ".join(gloves) if gloves else "not in selection"))
	return lines


def merge_gloves(context):
	pool = _selected_trees(context)
	if not pool:
		raise ValueError("Select the body and the gloves")

	body_arms = [obj for obj in _armatures_in(pool) if _is_body_armature(obj)]
	glove_arms = [obj for obj in _armatures_in(pool) if _is_glove_armature(obj)]
	if len(body_arms) != 1:
		raise ValueError("Select one body (boots) and the glove objects")
	if not glove_arms:
		raise ValueError("Select glove_l and glove_r as well as the body")

	body_arm = body_arms[0]
	body_meshes = _meshes_for_armature(body_arm, pool)
	if len(body_meshes) != 1:
		raise ValueError("The body needs one mesh. Found %d" % len(body_meshes))
	body_mesh = body_meshes[0]
	material = _body_material(body_mesh)

	context.view_layer.objects.active = body_mesh
	if context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	context.view_layer.update()

	glove_meshes = []
	added = 0
	for glove_arm in glove_arms:
		side = _side_from_bones(glove_arm)
		if side is None:
			raise ValueError("%s has both left and right bones" % glove_arm.name)
		meshes = _meshes_for_armature(glove_arm, pool)
		if not meshes:
			raise ValueError("%s has no mesh" % glove_arm.name)
		_align_glove(body_arm, glove_arm, [glove_arm, *meshes], side)
		context.view_layer.update()
		added += _copy_hand_bones(body_arm, glove_arm, side)
		for mesh_obj in meshes:
			_match_uv_names(body_mesh, mesh_obj)
			_use_body_material(mesh_obj, material)
			glove_meshes.append(mesh_obj)

	context.view_layer.update()
	bpy.ops.object.select_all(action="DESELECT")
	body_mesh.select_set(True)
	for mesh_obj in glove_meshes:
		mesh_obj.select_set(True)
	context.view_layer.objects.active = body_mesh
	bpy.ops.object.join()

	roots = []
	for glove_arm in glove_arms:
		top = glove_arm
		body_root = _root(body_arm)
		while top.parent is not None and top.parent != body_root:
			top = top.parent
		roots.append(top if top != body_arm else glove_arm)
		bpy.data.objects.remove(glove_arm, do_unlink=True)
	for top in roots:
		if top.name in bpy.data.objects and top != body_arm and top != body_mesh:
			_prune_empty(bpy.data.objects.get(top.name))

	return {
		"mesh": body_mesh.name,
		"bones": added,
		"gloves": len(glove_arms),
		"material": material.name if material is not None else "",
	}
