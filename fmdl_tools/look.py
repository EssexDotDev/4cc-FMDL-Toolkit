"""Outline shell and the unlit anime shader.

The outline is the inverse shell from the Blender tutorials page: duplicate
the mesh, merge the split edges, flip the normals, and solidify with a
negative thickness. PESfmdl export reads the mesh, not the modifier, so the
solidify is applied.

The anime shader is the shadeless head template:
fox3dfw_constant_srgb_ndr_solid. It draws the texture as painted. The lit
blin shader is what washes an anime paint out.
"""

import bmesh
import bpy


_SHADER = "fox3dfw_constant_srgb_ndr_solid"
_TECHNIQUE = "fox3DFW_ConstantSRGB_NDR_Solid"
_ALPHA = 16
_SHADOW = 5
_SKIP_GROUP = "outline_skip"


def _selected_meshes(context):
	found = []
	seen = set()
	for obj in context.selected_objects:
		if obj.type != "MESH" or obj.name in seen:
			continue
		if obj.get("fmdl_part") == "outline":
			continue
		seen.add(obj.name)
		found.append(obj)
	if not found:
		raise ValueError("Select the mesh to outline.")
	return found


def _set_prop(obj, name, value):
	try:
		setattr(obj, name, value)
	except (AttributeError, TypeError):
		pass
	obj[name] = value


def _drop_extra_maps(material):
	"""The constant shader only reads the color texture."""
	if material.node_tree is None:
		return
	for node in list(material.node_tree.nodes):
		if node.type != "TEX_IMAGE":
			continue
		label = (node.label or "").lower()
		image = getattr(node, "image", None)
		role = ""
		if image is not None:
			role = str(image.get("fmdl_texture_role", "")).lower()
			role += " " + str(getattr(image, "fmdl_texture_role", "")).lower()
		text = label + " " + role
		if "normal" in text or "specular" in text:
			material.node_tree.nodes.remove(node)


def _constant_shader(material, one_sided):
	"""Unlit solid shader from the shadeless PES head."""
	_set_prop(material, "fmdl_material_shader", _SHADER)
	_set_prop(material, "fmdl_material_technique", _TECHNIQUE)
	_set_prop(material, "fmdl_alpha_flags", _ALPHA)
	_set_prop(material, "fmdl_shadow_flags", _SHADOW)
	params = getattr(material, "fmdl_material_parameters", None)
	if params is not None:
		params.clear()
	_drop_extra_maps(material)
	if one_sided:
		try:
			material.use_backface_culling = True
		except (AttributeError, TypeError):
			pass


def use_anime_shader(context):
	"""Switch the selected meshes to the unlit constant shader."""
	meshes = _selected_meshes(context)
	changed = []
	seen = set()
	for mesh_obj in meshes:
		for material in mesh_obj.data.materials:
			if material is None or material.name in seen:
				continue
			seen.add(material.name)
			_constant_shader(material, one_sided=False)
			changed.append(material.name)
	if not changed:
		raise ValueError("The selected mesh has no material.")
	return {"materials": changed}


def _duplicate(context, source):
	data = source.data.copy()
	obj = bpy.data.objects.new(source.name + "_outline", data)
	obj.matrix_world = source.matrix_world.copy()
	for collection in source.users_collection:
		collection.objects.link(obj)
	if not obj.users_collection:
		context.scene.collection.objects.link(obj)
	world = obj.matrix_world.copy()
	obj.parent = source.parent
	obj.matrix_world = world
	for group in source.vertex_groups:
		obj.vertex_groups.new(name=group.name)
	for modifier in source.modifiers:
		if modifier.type != "ARMATURE" or modifier.object is None:
			continue
		copied = obj.modifiers.new(modifier.name or "Armature", "ARMATURE")
		copied.object = modifier.object
	for index, material in enumerate(list(data.materials)):
		if material is not None:
			data.materials[index] = material.copy()
	obj["fmdl_part"] = "outline"
	return obj


def _merge_and_flip(mesh):
	block = bmesh.new()
	block.from_mesh(mesh)
	bmesh.ops.remove_doubles(block, verts=block.verts, dist=0.0001)
	bmesh.ops.reverse_faces(block, faces=block.faces)
	block.to_mesh(mesh)
	mesh.update()
	block.free()


def _solidify(context, obj, thickness):
	# The armature has to come off first. Applying Solidify under it
	# does not build the outer shell.
	armatures = []
	for modifier in list(obj.modifiers):
		if modifier.type == "ARMATURE" and modifier.object is not None:
			armatures.append(modifier.object)
			obj.modifiers.remove(modifier)
	modifier = obj.modifiers.new("Outline", "SOLIDIFY")
	modifier.thickness = thickness
	modifier.offset = 1.0
	modifier.use_quality_normals = True
	if obj.vertex_groups.get(_SKIP_GROUP) is not None:
		modifier.vertex_group = _SKIP_GROUP
		modifier.invert_vertex_group = True
	for other in context.view_layer.objects:
		if other is not None:
			other.select_set(False)
	obj.select_set(True)
	context.view_layer.objects.active = obj
	if abs(obj.scale.x - 1.0) > 1e-4 or abs(obj.scale.y - 1.0) > 1e-4 or abs(obj.scale.z - 1.0) > 1e-4:
		bpy.ops.object.transform_apply(scale=True)
	bpy.ops.object.modifier_apply(modifier=modifier.name)
	for arm in armatures:
		copied = obj.modifiers.new("Armature", "ARMATURE")
		copied.object = arm


def add_outline(context, thickness=-0.002):
	"""Build the inverse-shell outline and give it the unlit shader."""
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	if thickness >= 0:
		raise ValueError("Outline thickness has to be negative.")
	sources = _selected_meshes(context)
	made = []
	for source in sources:
		if source.data.shape_keys is not None:
			raise ValueError("%s has shape keys. The outline shell cannot be applied onto them." % source.name)
		shell = _duplicate(context, source)
		_merge_and_flip(shell.data)
		_solidify(context, shell, thickness)
		for material in shell.data.materials:
			if material is not None:
				_constant_shader(material, one_sided=True)
		made.append(shell.name)
	for obj in context.view_layer.objects:
		if obj is not None:
			obj.select_set(False)
	for name in made:
		bpy.data.objects[name].select_set(True)
	context.view_layer.objects.active = bpy.data.objects[made[-1]]
	return {"shells": made, "thickness": thickness}
