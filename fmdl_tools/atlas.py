"""Pack a model's color textures into one 2048 body.dds and move the UVs with them.

Cell size follows the number of color textures. One texture fills the sheet.
Two to four textures get 1024. More than that keeps 1024 for the body, the
face, and the clothes, and puts hair and the rest in smaller power-of-two cells.
"""

import array
import math
import os
import subprocess

import bpy

ATLAS = 2048
_RANK = {"body": 0, "cloth": 1, "face": 2, "other": 3, "hair": 4, "accessory": 5}
_NVTT = os.path.join(
	os.environ.get("ProgramFiles", r"C:\Program Files"),
	"NVIDIA Corporation",
	"NVIDIA Texture Tools",
	"nvtt_export.exe",
)


def _walk_images(tree):
	for node in tree.nodes:
		image = getattr(node, "image", None)
		if image is not None:
			yield image
		group = getattr(node, "node_tree", None)
		if group is not None:
			yield from _walk_images(group)


def color_image(material):
	"""Largest image on the material. The color map is the big one."""
	if material is None or material.node_tree is None:
		return None
	best = None
	best_area = 0
	for image in _walk_images(material.node_tree):
		area = int(image.size[0]) * int(image.size[1])
		if area > best_area:
			best = image
			best_area = area
	return best


def _is_head_mesh(obj):
	if obj.get("fmdl_part") == "hair_high":
		return True
	parent = obj.parent
	return parent is not None and parent.get("fmdl_part") == "hair_high"


def _selected_meshes(context):
	meshes = []
	seen = set()

	def add(obj):
		if obj.type != "MESH" or obj.name in seen:
			return
		if obj.data.uv_layers.active is None:
			return
		seen.add(obj.name)
		meshes.append(obj)

	for obj in context.selected_objects:
		if obj.type == "EMPTY":
			for child in obj.children:
				add(child)
			continue
		add(obj)
	return meshes


# Words that mark a head or hair texture. The body, the clothes, and the
# tail stay out of a head atlas.
_HEAD_WORDS = (
	"hair", "髪", "face", "顔", "head", "eye", "目", "mayu", "brow",
	"lash", "mouth", "lip", "teeth", "tongue", "瞳",
)
_BODY_WORDS = ("body", "bdy", "skin", "cloth", "dress", "skirt", "tail", "体", "肌", "尾")


def _is_head_texture(material_name, image_name):
	"""True for a face, eye, brow, or hair map. A body or cloth map is not."""
	material = (material_name or "").lower()
	image = (image_name or "").lower()
	text = material + " " + image
	head = any(word in text for word in _HEAD_WORDS)
	if not head:
		return False
	# The material name wins when a face map and a body word share a string.
	if any(word in material for word in _HEAD_WORDS):
		return True
	return not any(word in text for word in _BODY_WORDS)


def _uv_bounds(mesh_obj, material_index):
	layer = mesh_obj.data.uv_layers.active
	minx = miny = 1e9
	maxx = maxy = -1e9
	used = False
	for poly in mesh_obj.data.polygons:
		if poly.material_index != material_index:
			continue
		for loop in range(poly.loop_start, poly.loop_start + poly.loop_total):
			uv = layer.data[loop].uv
			minx = min(minx, uv.x)
			miny = min(miny, uv.y)
			maxx = max(maxx, uv.x)
			maxy = max(maxy, uv.y)
			used = True
	if not used:
		return None
	return (minx, miny, maxx, maxy)


def _union_bounds(boxes):
	minx = min(box[0] for box in boxes)
	miny = min(box[1] for box in boxes)
	maxx = max(box[2] for box in boxes)
	maxy = max(box[3] for box in boxes)
	# One texel of margin so the filter does not read the neighbouring slot.
	minx = max(0.0, minx - 0.002)
	miny = max(0.0, miny - 0.002)
	maxx = min(1.0, maxx + 0.002)
	maxy = min(1.0, maxy + 0.002)
	if maxx - minx < 1e-4 or maxy - miny < 1e-4:
		return (0.0, 0.0, 1.0, 1.0)
	return (minx, miny, maxx, maxy)


def _role(name):
	"""Who gets a 1024 cell. Hair stays small. A bikini loses to the body."""
	text = name.lower()
	if "hair" in text or "髪" in text:
		return "hair"
	if any(word in text for word in ("bra", "bikini", "panty", "panties", "underwear", "thong")):
		return "accessory"
	if "face" in text or "顔" in text:
		return "face"
	if any(word in text for word in ("body", "skin", "体", "肌")):
		return "body"
	if any(word in text for word in ("cloth", "suit", "outfit", "wear", "dress", "uniform", "shirt", "skirt", "sock", "glove", "boot")):
		return "cloth"
	return "other"


def _cell_sizes(entries, sheet):
	"""Power-of-two side length for each entry, already in priority order.

	One texture fills the sheet. Up to four textures take half the sheet.
	Past that, the body, the face, and the clothes are grown first.
	"""
	count = len(entries)
	if count == 1:
		return [sheet]
	half = sheet // 2
	if count <= 4:
		return [half] * count
	if count <= 16:
		base = max(half // 2, 64)
	elif count <= 64:
		base = max(half // 4, 32)
	else:
		base = max(half // 8, 32)
	sizes = [base] * count
	limit = sheet * sheet
	changed = True
	while changed:
		changed = False
		for index, side in enumerate(sizes):
			if side >= half:
				continue
			grown = side * 2
			used = sum(item * item for item in sizes) - side * side + grown * grown
			if used <= limit:
				sizes[index] = grown
				changed = True
				break
	return sizes


def _place(named_sizes, sheet):
	"""Pack power-of-two squares. Returns rectangles and any name that did not fit."""
	if not named_sizes:
		return {}, []
	step = min(size for _name, size in named_sizes)
	cells = sheet // step
	taken = [[False] * cells for _ in range(cells)]
	rects = {}
	dropped = []
	order = sorted(named_sizes, key=lambda item: -item[1])
	for name, size in order:
		span = size // step
		found = None
		for gy in range(cells - span, -1, -1):
			for gx in range(cells - span + 1):
				if any(taken[gy + dy][gx + dx] for dy in range(span) for dx in range(span)):
					continue
				found = (gx, gy)
				break
			if found is not None:
				break
		if found is None:
			dropped.append(name)
			continue
		gx, gy = found
		for dy in range(span):
			for dx in range(span):
				taken[gy + dy][gx + dx] = True
		rects[name] = (gx * step, gy * step, size, size)
	return rects, dropped


def assign_slots(named, sheet=ATLAS):
	"""Place each texture in a power-of-two cell. named is (name, used-uv area).

	Returns the placed rectangles and the names that did not fit. Rectangles
	are (x, y, width, height) with y measured from the bottom.
	"""
	entries = [{"name": name, "role": _role(name), "area": area} for name, area in named]
	entries.sort(key=lambda entry: (_RANK[entry["role"]], -entry["area"]))
	sizes = _cell_sizes(entries, sheet)
	rects, dropped = _place([(entry["name"], size) for entry, size in zip(entries, sizes)], sheet)
	return rects, dropped


def _pixels(image):
	count = int(image.size[0]) * int(image.size[1]) * 4
	buf = array.array("f", [0.0]) * count
	image.pixels.foreach_get(buf)
	return buf


def _crop_scale(image, bounds, pw, ph):
	"""Return pw*ph RGBA floats of the UV rectangle, bottom-left origin."""
	src_w = int(image.size[0])
	src_h = int(image.size[1])
	if src_w < 1 or src_h < 1:
		image.reload()
		src_w = int(image.size[0])
		src_h = int(image.size[1])
	if src_w < 1 or src_h < 1:
		raise ValueError("%s has no pixels." % image.name)
	umin, vmin, umax, vmax = bounds
	src = _pixels(image)
	out = array.array("f", [0.0]) * (pw * ph * 4)
	span_u = umax - umin
	span_v = vmax - vmin
	for y in range(ph):
		v0 = vmin + y / float(ph) * span_v
		v1 = vmin + (y + 1) / float(ph) * span_v
		sy0 = min(src_h - 1, max(0, int(v0 * src_h)))
		sy1 = min(src_h, max(sy0 + 1, int(math.ceil(v1 * src_h))))
		for x in range(pw):
			u0 = umin + x / float(pw) * span_u
			u1 = umin + (x + 1) / float(pw) * span_u
			sx0 = min(src_w - 1, max(0, int(u0 * src_w)))
			sx1 = min(src_w, max(sx0 + 1, int(math.ceil(u1 * src_w))))
			acc_r = acc_g = acc_b = acc_a = 0.0
			count = 0
			for sy in range(sy0, sy1):
				row = sy * src_w * 4
				for sx in range(sx0, sx1):
					src_i = row + sx * 4
					acc_r += src[src_i]
					acc_g += src[src_i + 1]
					acc_b += src[src_i + 2]
					acc_a += src[src_i + 3]
					count += 1
			dest = (y * pw + x) * 4
			out[dest] = acc_r / count
			out[dest + 1] = acc_g / count
			out[dest + 2] = acc_b / count
			out[dest + 3] = acc_a / count
	return out


def _blit(dest, src, px, py, pw, ph, sheet):
	for row in range(ph):
		if py + row < 0 or py + row >= sheet:
			continue
		width = min(pw, sheet - px)
		if width <= 0:
			continue
		src_i = row * pw * 4
		dest_i = ((py + row) * sheet + px) * 4
		dest[dest_i:dest_i + width * 4] = src[src_i:src_i + width * 4]


def _write_image(buf, sheet, name):
	image = bpy.data.images.new(name, sheet, sheet, alpha=True, float_buffer=False)
	image.colorspace_settings.name = "sRGB"
	image.pixels.foreach_set(buf)
	image.update()
	image.pack()
	return image


def _move_uvs(mesh_obj, material_index, bounds, rect, sheet):
	umin, vmin, umax, vmax = bounds
	px, py, pw, ph = rect
	sx = (pw / float(sheet)) / (umax - umin)
	sy = (ph / float(sheet)) / (vmax - vmin)
	ox = px / float(sheet) - umin * sx
	oy = py / float(sheet) - vmin * sy
	# Every UV map, not only the active one. The exporter can read a
	# different layer than the one Blender is showing.
	for layer in mesh_obj.data.uv_layers:
		for poly in mesh_obj.data.polygons:
			if poly.material_index != material_index:
				continue
			for loop in range(poly.loop_start, poly.loop_start + poly.loop_total):
				uv = layer.data[loop].uv
				uv.x = ox + uv.x * sx
				uv.y = oy + uv.y * sy


# The medic body is a blin material. PES paints the mesh red when that shader,
# or the normal and specular it requires, is missing from the FMDL.
_PES_COMMON = "/Assets/pes16/model/character/common/sourceimages/"


def _tag_image(image, filename, directory, role):
	for key, value in (
		("fmdl_texture_filename", filename),
		("fmdl_texture_directory", directory),
		("fmdl_texture_role", role),
	):
		try:
			setattr(image, key, value)
		except (AttributeError, TypeError):
			pass
		image[key] = value


def _pes_image(name, filename, directory, role):
	image = bpy.data.images.get(name)
	if image is None:
		image = bpy.data.images.new(name, 1, 1, alpha=True)
	_tag_image(image, filename, directory, role)
	return image


def _pes_blin(material):
	"""Match the shaded medic: fox3ddf_blin, opaque alpha, a zero mat param."""
	try:
		material.fmdl_material_shader = "fox3ddf_blin"
		material.fmdl_material_technique = "fox3DDF_Blin"
		material.fmdl_alpha_flags = 128
		material.fmdl_shadow_flags = 0
		material.fmdl_material_parameters.clear()
		param = material.fmdl_material_parameters.add()
		param.name = "MatParamIndex_0"
		param.parameters = (0.0, 0.0, 0.0, 0.0)
	except AttributeError:
		pass


def _single_material(mesh_obj, image, material_name, filename):
	"""One blin material. The color map is the atlas the UVs were moved into."""
	material = bpy.data.materials.get(material_name)
	if material is None:
		material = bpy.data.materials.new(material_name)
	material.use_nodes = True
	nodes = material.node_tree.nodes
	links = material.node_tree.links
	nodes.clear()
	output = nodes.new("ShaderNodeOutputMaterial")
	shader = nodes.new("ShaderNodeBsdfPrincipled")
	texture = nodes.new("ShaderNodeTexImage")
	texture.image = image
	texture.label = "Base_Tex_SRGB"
	_tag_image(image, filename, getattr(image, "fmdl_texture_directory", "") or "", "Base_Tex_SRGB")
	shader.inputs["Alpha"].default_value = 1.0
	texture.location = (-400, 300)
	shader.location = (0, 300)
	output.location = (300, 300)
	links.new(texture.outputs["Color"], shader.inputs["Base Color"])
	links.new(shader.outputs["BSDF"], output.inputs["Surface"])
	for name, filename, role, y in (
		("pes_dummy_nrm", "dummy_nrm.dds", "NormalMap_Tex_NRM", 80),
		("pes_dummy_srm", "dummy_srm.dds", "SpecularMap_Tex_LIN", -140),
	):
		slot = nodes.new("ShaderNodeTexImage")
		slot.image = _pes_image(name, filename, _PES_COMMON, role)
		slot.label = role
		slot.location = (-400, y)
	_pes_blin(material)
	mesh_obj.data.materials.clear()
	mesh_obj.data.materials.append(material)


def _body_material(mesh_obj, image):
	"""One material named body: body.dds into Base Color, alpha 1, out to Surface."""
	_single_material(mesh_obj, image, "body", "body.dds")


def _load_body_dds(dds_path, fallback, filename="body.dds"):
	if dds_path and os.path.isfile(dds_path):
		image = bpy.data.images.load(dds_path, check_existing=True)
		image.name = filename
		return image
	fallback.name = filename
	return fallback


def _save_dds(image, directory, stem="body"):
	png_path = os.path.join(directory, stem + ".png")
	dds_path = os.path.join(directory, stem + ".dds")
	image.filepath_raw = png_path
	image.file_format = "PNG"
	image.save()
	if not os.path.isfile(_NVTT):
		return dds_path, "NVIDIA Texture Tools is not installed, so only %s.png was written." % stem
	# PES reads FourCC DXT5. bc3n-rxgb writes RXGB, and the compiler skips that.
	command = [
		_NVTT,
		png_path,
		"-o", dds_path,
		"-f", "bc3",
		"--mips",
		"--mip-filter", "kaiser",
		"--quality", "normal",
	]
	run = subprocess.run(command, capture_output=True, text=True)
	if run.returncode != 0:
		detail = (run.stderr or run.stdout or "").strip().splitlines()
		tail = detail[-1] if detail else "exit %s" % run.returncode
		raise RuntimeError("DDS export failed. %s" % tail)
	return dds_path, ""


def _output_directory(users):
	directory = bpy.path.abspath("//")
	if not bpy.data.filepath or not os.path.isdir(directory):
		directory = os.path.dirname(bpy.path.abspath(users[next(iter(users))]["image"].filepath))
	if not directory or not os.path.isdir(directory):
		directory = bpy.app.tempdir
	return directory


def _build_atlas(context, meshes, sheet, stem, material_name, head_only):
	"""Pack the color maps, move every UV layer into the cells, then assign one material."""
	users = {}
	missing = []
	skipped = []
	for mesh_obj in meshes:
		if mesh_obj.data.users > 1:
			mesh_obj.data = mesh_obj.data.copy()
		for index, material in enumerate(mesh_obj.data.materials):
			bounds = _uv_bounds(mesh_obj, index)
			if bounds is None:
				continue
			image = color_image(material)
			if image is None:
				missing.append(material.name if material else mesh_obj.name)
				continue
			material_name_src = material.name if material else ""
			if head_only and not _is_head_texture(material_name_src, image.name):
				skipped.append(material_name_src or image.name)
				continue
			users.setdefault(image.name, {"image": image, "bounds": [], "slots": []})
			users[image.name]["bounds"].append(bounds)
			users[image.name]["slots"].append((mesh_obj, index))
	if not users:
		if head_only:
			raise ValueError("This head has no face or hair texture to pack.")
		raise ValueError("None of the selected materials have a color texture.")
	named = []
	for name, entry in users.items():
		bounds = _union_bounds(entry["bounds"])
		named.append((name, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])))
	rects, dropped = assign_slots(named, sheet)
	if not rects:
		raise ValueError("The textures do not fit in a %d atlas." % sheet)
	full = (0.0, 0.0, 1.0, 1.0)
	dest = array.array("f", [0.8, 0.8, 0.8, 1.0]) * (sheet * sheet)
	report = []
	for name, entry in users.items():
		if name not in rects:
			continue
		px, py, pw, ph = rects[name]
		chunk = _crop_scale(entry["image"], full, pw, ph)
		_blit(dest, chunk, px, py, pw, ph, sheet)
		report.append((entry["image"].name, int(entry["image"].size[0]), pw))
	atlas = _write_image(dest, sheet, stem + "_atlas")
	for name, entry in users.items():
		if name not in rects:
			continue
		for mesh_obj, index in entry["slots"]:
			_move_uvs(mesh_obj, index, full, rects[name], sheet)
	directory = _output_directory(users)
	dds_path, note = _save_dds(atlas, directory, stem)
	packed_image = _load_body_dds(dds_path, atlas, stem + ".dds")
	if dds_path and os.path.isfile(dds_path):
		packed_image.reload()
	_tag_image(packed_image, stem + ".dds", "", "Base_Tex_SRGB")
	for mesh_obj in meshes:
		_single_material(mesh_obj, packed_image, material_name, stem + ".dds")
	return {
		"textures": len(rects),
		"missing": missing,
		"dropped": dropped,
		"skipped": skipped,
		"slots": report,
		"dds": dds_path,
		"note": note,
		"image": packed_image.name,
		"size": sheet,
	}


def build_body_atlas(context):
	"""Pack color textures into one 2048 body.dds and assign that material."""
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	meshes = [mesh for mesh in _selected_meshes(context) if not _is_head_mesh(mesh)]
	if not meshes:
		raise ValueError("Select the body. The chopped head uses Build head texture.")
	return _build_atlas(context, meshes, ATLAS, "body", "body", False)


def build_head_atlas(context):
	"""Pack the chopped head's face and hair into one 1024 head.dds."""
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	meshes = [mesh for mesh in _selected_meshes(context) if _is_head_mesh(mesh)]
	if not meshes:
		raise ValueError("Select the chopped head.")
	return _build_atlas(context, meshes, 1024, "head", "Head", True)
