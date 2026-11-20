"""Cut the head and the hair off a converted body so they can sit on a kit.

A PES face folder is hair_high.fmdl plus the template face_high.fmdl.
hair_high is the custom head and hair, all weighted to sk_head. The body
mesh and every other bone are removed. face_high from the PES19 head
template is the collar on the kit, and it is not part of this chop.
"""

import bmesh
import bpy


_LIMB_TOKENS = ("arm", "forearm", "hand", "thigh", "leg", "foot", "toe", "wrist", "clavicle")


def _bone_world(arm, name):
	bone = arm.data.bones.get(name)
	if bone is None:
		return None
	return arm.matrix_world @ bone.head_local


def _pes_armature(mesh):
	for modifier in mesh.modifiers:
		if modifier.type != "ARMATURE" or modifier.object is None:
			continue
		bones = modifier.object.data.bones
		if bones.get("sk_head") is not None and bones.get("dsk_hip") is not None:
			return modifier.object
	return None


def _body_mesh(context):
	candidates = []
	seen = set()

	def add(obj):
		if obj is None or obj.type != "MESH" or obj.name in seen:
			return
		if obj.get("fmdl_part") == "hair_high":
			return
		seen.add(obj.name)
		if _pes_armature(obj) is None:
			return
		# The imported medic body is the reference. The converted kit is not.
		lower = obj.name.lower()
		if "medic" in lower or "medical" in lower:
			return
		candidates.append(obj)

	for obj in context.selected_objects:
		add(obj)
		if obj.type == "ARMATURE":
			for child in obj.children:
				add(child)
	if not candidates:
		for obj in context.scene.objects:
			add(obj)
	if not candidates:
		raise ValueError("Select the converted body. It needs the PES skeleton.")
	return max(candidates, key=lambda obj: len(obj.data.vertices))


def _weights(mesh, index):
	groups = {group.index: group.name for group in mesh.vertex_groups}
	bucket = {}
	for item in mesh.data.vertices[index].groups:
		name = groups.get(item.group)
		if name and item.weight > 0.001:
			bucket[name] = item.weight
	return bucket


def _limb_owned(bucket):
	for name, weight in bucket.items():
		if weight < 0.5:
			continue
		lower = name.lower()
		if any(token in lower for token in _LIMB_TOKENS):
			return True
	return False


def _head_hair_verts(mesh, arm):
	"""Head, ears, hat, and the hair sheet. The neck column stays on the body.

	Long hair hangs behind the torso, so a flat cut at the jaw would leave
	it on the kit. The walk starts at sk_head and stops at the neck, which
	is the same place the kit's face_high collar meets the head.
	"""
	neck = _bone_world(arm, "sk_neck")
	head = _bone_world(arm, "sk_head")
	if neck is None or head is None:
		raise ValueError("This skeleton has no sk_head. Convert the body onto the PES skeleton first.")
	matrix = mesh.matrix_world
	points = [matrix @ vertex.co for vertex in mesh.data.vertices]
	weld = set()
	seeds = set()
	for index, point in enumerate(points):
		radius = ((point.x - neck.x) ** 2 + (point.y - neck.y) ** 2) ** 0.5
		if neck.z - 0.12 <= point.z <= neck.z + 0.03 and radius < 0.06:
			weld.add(index)
			continue
		bucket = _weights(mesh, index)
		if _limb_owned(bucket):
			weld.add(index)
			continue
		if bucket.get("sk_head", 0.0) >= 0.5:
			seeds.add(index)
			continue
		above = point.z > head.z - 0.02 and (point - head).length < 0.22
		if above and bucket.get("sk_head", 0.0) >= 0.2:
			seeds.add(index)
	neighbors = {}
	for poly in mesh.data.polygons:
		verts = poly.vertices
		count = len(verts)
		for cursor in range(count):
			a = verts[cursor]
			b = verts[(cursor + 1) % count]
			neighbors.setdefault(a, set()).add(b)
			neighbors.setdefault(b, set()).add(a)
	chosen = set()
	stack = list(seeds)
	while stack:
		index = stack.pop()
		if index in chosen or index in weld:
			continue
		point = points[index]
		# Below the jaw, only verts already on sk_head. That keeps a long
		# ponytail and leaves the dress, which hangs in the same place.
		if point.z < neck.z - 0.02 and _weights(mesh, index).get("sk_head", 0.0) < 0.35:
			continue
		chosen.add(index)
		stack.extend(neighbors.get(index, ()))
	return chosen


def _faces_of(mesh, verts):
	faces = []
	for poly in mesh.data.polygons:
		if all(index in verts for index in poly.vertices):
			faces.append(poly.index)
	return faces


def _split(mesh_obj, face_indices):
	source = bmesh.new()
	source.from_mesh(mesh_obj.data)
	source.faces.ensure_lookup_table()
	head = source.copy()
	head.faces.ensure_lookup_table()
	wanted = set(face_indices)
	bmesh.ops.delete(
		head,
		geom=[face for face in head.faces if face.index not in wanted],
		context="FACES",
	)
	bmesh.ops.delete(
		source,
		geom=[face for face in source.faces if face.index in wanted],
		context="FACES",
	)
	for block in (source, head):
		block.verts.ensure_lookup_table()
		loose = [vert for vert in block.verts if not vert.link_faces]
		if loose:
			bmesh.ops.delete(block, geom=loose, context="VERTS")
		block.faces.ensure_lookup_table()
	# Read these before to_mesh. Writing the body mesh back can drop slots.
	materials = list(mesh_obj.data.materials)
	indices = [face.material_index for face in head.faces]
	source.to_mesh(mesh_obj.data)
	mesh_obj.data.update()
	head_data = bpy.data.meshes.new("hair_high")
	for material in materials:
		head_data.materials.append(material)
	head.to_mesh(head_data)
	head_data.update()
	source.free()
	head.free()
	if len(head_data.polygons) == len(indices):
		for poly, index in zip(head_data.polygons, indices):
			poly.material_index = index
	return head_data


def _link(context, source, obj):
	for collection in source.users_collection:
		collection.objects.link(obj)
	if not obj.users_collection:
		context.scene.collection.objects.link(obj)


def _keep_only_head_bone(context, source_arm):
	"""One bone, sk_head, in the same place as the kit's head.

	The head keeps that same bone. Building a second armature and
	rebinding the mesh is what throws the head off the joint.
	"""
	context.view_layer.update()
	for obj in list(context.view_layer.objects):
		if obj is None:
			continue
		obj.select_set(False)
	source_arm.select_set(True)
	context.view_layer.objects.active = source_arm
	bpy.ops.object.mode_set(mode="EDIT")
	head = source_arm.data.edit_bones.get("sk_head")
	if head is None:
		bpy.ops.object.mode_set(mode="OBJECT")
		raise ValueError("The body skeleton has no sk_head.")
	# Unparent first. Deleting sk_neck would take the head bone with it.
	head.parent = None
	for name in [bone.name for bone in source_arm.data.edit_bones if bone.name != "sk_head"]:
		bone = source_arm.data.edit_bones.get(name)
		if bone is not None:
			source_arm.data.edit_bones.remove(bone)
	bpy.ops.object.mode_set(mode="OBJECT")
	return source_arm


def _bind_existing(mesh_obj, arm):
	modifier = mesh_obj.modifiers.new("Armature", "ARMATURE")
	modifier.object = arm
	return modifier


def _only_head_weight(obj):
	obj.vertex_groups.clear()
	group = obj.vertex_groups.new(name="sk_head")
	group.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")


def _compact_materials(mesh_data):
	"""Drop unused slots, then copy materials so the body keeps its own."""
	used = sorted({poly.material_index for poly in mesh_data.polygons})
	if used and used != list(range(len(mesh_data.materials))):
		remap = {old: new for new, old in enumerate(used)}
		kept = [mesh_data.materials[index] for index in used]
		for poly in mesh_data.polygons:
			poly.material_index = remap[poly.material_index]
	else:
		kept = list(mesh_data.materials)
	# Clearing the slots clamps every face back to slot 0.
	indices = [poly.material_index for poly in mesh_data.polygons]
	mesh_data.materials.clear()
	for material in kept:
		mesh_data.materials.append(material.copy() if material is not None else None)
	for poly, index in zip(mesh_data.polygons, indices):
		if index < len(mesh_data.materials):
			poly.material_index = index


def _export_root(context, mesh_obj):
	"""A scene root PESfmdl can export without the rest of the body."""
	empty = bpy.data.objects.new("hair_high", None)
	empty.empty_display_size = 0.1
	empty["fmdl_file"] = True
	empty["fmdl_filename"] = "hair_high.fmdl"
	empty["fmdl_part"] = "hair_high"
	mesh_obj["fmdl_part"] = "hair_high"
	for collection in mesh_obj.users_collection:
		collection.objects.link(empty)
	if not empty.users_collection:
		context.scene.collection.objects.link(empty)
	world = mesh_obj.matrix_world.copy()
	mesh_obj.parent = empty
	mesh_obj.matrix_world = world
	return empty


def _top(obj):
	while obj.parent is not None:
		obj = obj.parent
	return obj


def _drop_body(arm, head_obj, root):
	"""Remove the body mesh and the old file root. The head bone stays."""
	top = _top(arm)
	keep = {arm, head_obj, root}
	world_arm = arm.matrix_world.copy()
	world_head = head_obj.matrix_world.copy()
	arm.parent = root
	arm.matrix_world = world_arm
	head_obj.parent = root
	head_obj.matrix_world = world_head
	if top in keep:
		for obj in list(bpy.data.objects):
			if obj in keep or obj.type != "MESH":
				continue
			for modifier in obj.modifiers:
				if modifier.type == "ARMATURE" and modifier.object == arm:
					bpy.data.objects.remove(obj, do_unlink=True)
					break
		return
	def walk(obj):
		for child in list(obj.children):
			walk(child)
		if obj not in keep:
			bpy.data.objects.remove(obj, do_unlink=True)
	walk(top)


def chop_head(context):
	"""Separate the head and hair onto the existing sk_head bone.

	The body mesh and the rest of the skeleton are removed. A second
	armature would leave the full skeleton in the file and pull the
	head off its joint.
	"""
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	mesh = _body_mesh(context)
	mesh_name = mesh.name
	arm = _pes_armature(mesh)
	before = arm.matrix_world @ arm.data.bones["sk_head"].head_local
	verts = _head_hair_verts(mesh, arm)
	faces = _faces_of(mesh, verts)
	if len(faces) < 20:
		raise ValueError("No head or hair to chop. Convert the body onto the PES skeleton first.")
	if len(faces) > len(mesh.data.polygons) * 0.85:
		raise ValueError("The chop would take almost the whole body. The head weights do not separate from the kit.")
	head_data = _split(mesh, faces)
	_compact_materials(head_data)
	head_obj = bpy.data.objects.new("hair_high_mesh", head_data)
	head_obj.matrix_world = mesh.matrix_world.copy()
	for collection in mesh.users_collection:
		collection.objects.link(head_obj)
	if not head_obj.users_collection:
		context.scene.collection.objects.link(head_obj)
	_bind_existing(head_obj, arm)
	_only_head_weight(head_obj)
	root = _export_root(context, head_obj)
	# Parent away from the body before the body tree is deleted.
	_drop_body(arm, head_obj, root)
	skeleton = _keep_only_head_bone(context, arm)
	after = skeleton.matrix_world @ skeleton.data.bones["sk_head"].head_local
	if (after - before).length > 0.001:
		raise ValueError("sk_head moved while the other bones were removed.")
	for obj in list(context.view_layer.objects):
		if obj is None:
			continue
		obj.select_set(False)
	head_obj.select_set(True)
	root.select_set(True)
	context.view_layer.objects.active = head_obj
	return {
		"mesh": mesh_name,
		"head": head_obj.name,
		"root": root.name,
		"faces": len(faces),
		"verts": len(head_data.vertices),
		"body_verts": 0,
		"skeleton": skeleton.name,
		"bones": [bone.name for bone in skeleton.data.bones],
	}
