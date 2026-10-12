"""Pack a model's color textures into one 2048 body.dds and move the UVs with them.

Each material keeps its own texture in normal 0-1 UV space. The manual
Kearsarge atlas did the same thing in Photoshop: scale a whole texture into
a rectangle, then scale that material's UVs into the same rectangle. This
does that for every color texture on the selected meshes. Toon and sphere
maps are not color, so they stay out of the sheet.

It does not reproduce a hand-arranged sheet. Five full 2048 textures cannot
all keep the 1024-pixel slots the Kearsarge body and cloth were given.
"""

import array
import math
import os
import subprocess

import bpy

ATLAS = 2048
GAP = 8
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


def _selected_meshes(context):
	meshes = []
	seen = set()
	for obj in context.selected_objects:
		if obj.type != "MESH" or obj.name in seen:
			continue
		if obj.data.uv_layers.active is None:
			continue
		seen.add(obj.name)
		meshes.append(obj)
	return meshes


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


def _shelf(items, scale):
	"""Place (name, width, height) at this scale. None when a row falls off."""
	x = 0
	y = 0
	row_h = 0
	placed = []
	ordered = sorted(items, key=lambda item: max(item[1], item[2]), reverse=True)
	for name, width, height in ordered:
		pw = max(1, int(round(width * scale)))
		ph = max(1, int(round(height * scale)))
		if pw > ATLAS or ph > ATLAS:
			return None
		if x > 0 and x + pw > ATLAS:
			x = 0
			y += row_h + GAP
			row_h = 0
		if y + ph > ATLAS:
			return None
		placed.append((name, x, y, pw, ph))
		x += pw + GAP
		row_h = max(row_h, ph)
	return placed


def pack_slots(items):
	"""Uniform scale, then a shelf pack. items are (name, pixel width, pixel height)."""
	if not items:
		return None, 1.0
	best = None
	best_scale = 0.0
	low = 0.02
	high = 1.0
	for _ in range(20):
		scale = (low + high) * 0.5
		placed = _shelf(items, scale)
		if placed is None:
			high = scale
		else:
			best = placed
			best_scale = scale
			low = scale
	return best, best_scale


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


def _blit(dest, src, px, py, pw, ph):
	for row in range(ph):
		if py + row < 0 or py + row >= ATLAS:
			continue
		width = min(pw, ATLAS - px)
		if width <= 0:
			continue
		src_i = row * pw * 4
		dest_i = ((py + row) * ATLAS + px) * 4
		dest[dest_i:dest_i + width * 4] = src[src_i:src_i + width * 4]


def _bleed(dest, px, py, pw, ph, pad):
	"""Repeat the slot edge into the gap so the mip chain does not pick up the grey."""
	for n in range(1, pad + 1):
		for row in range(ph):
			y = py + row
			if y < 0 or y >= ATLAS:
				continue
			left = px - n
			right = px + pw - 1 + n
			src_left = (y * ATLAS + px) * 4
			src_right = (y * ATLAS + px + pw - 1) * 4
			if left >= 0:
				dest_i = (y * ATLAS + left) * 4
				dest[dest_i:dest_i + 4] = dest[src_left:src_left + 4]
			if right < ATLAS:
				dest_i = (y * ATLAS + right) * 4
				dest[dest_i:dest_i + 4] = dest[src_right:src_right + 4]
		for col in range(px - n, px + pw + n):
			if col < 0 or col >= ATLAS:
				continue
			below = py - n
			above = py + ph - 1 + n
			sample_y = py if below == py - n else py + ph - 1
			if below >= 0:
				src_i = (sample_y * ATLAS + col) * 4
				dest_i = (below * ATLAS + col) * 4
				dest[dest_i:dest_i + 4] = dest[src_i:src_i + 4]
			if above < ATLAS and n:
				src_i = ((py + ph - 1) * ATLAS + col) * 4
				dest_i = (above * ATLAS + col) * 4
				dest[dest_i:dest_i + 4] = dest[src_i:src_i + 4]


def _write_image(buf):
	image = bpy.data.images.new("body_atlas", ATLAS, ATLAS, alpha=True, float_buffer=False)
	image.colorspace_settings.name = "sRGB"
	image.pixels.foreach_set(buf)
	image.update()
	image.pack()
	return image


def _move_uvs(mesh_obj, material_index, bounds, rect):
	umin, vmin, umax, vmax = bounds
	px, py, pw, ph = rect
	sx = (pw / float(ATLAS)) / (umax - umin)
	sy = (ph / float(ATLAS)) / (vmax - vmin)
	ox = px / float(ATLAS) - umin * sx
	oy = py / float(ATLAS) - vmin * sy
	layer = mesh_obj.data.uv_layers.active
	for poly in mesh_obj.data.polygons:
		if poly.material_index != material_index:
			continue
		for loop in range(poly.loop_start, poly.loop_start + poly.loop_total):
			uv = layer.data[loop].uv
			uv.x = ox + uv.x * sx
			uv.y = oy + uv.y * sy


def _point_materials(meshes, atlas_image, packed_names):
	seen = set()
	for mesh_obj in meshes:
		for material in mesh_obj.data.materials:
			if material is None or material.name in seen or material.node_tree is None:
				continue
			seen.add(material.name)
			for node in material.node_tree.nodes:
				image = getattr(node, "image", None)
				if image is not None and image.name in packed_names:
					node.image = atlas_image


def _save_dds(image, directory):
	png_path = os.path.join(directory, "body.png")
	dds_path = os.path.join(directory, "body.dds")
	image.filepath_raw = png_path
	image.file_format = "PNG"
	image.save()
	if not os.path.isfile(_NVTT):
		return dds_path, "NVIDIA Texture Tools is not installed, so only body.png was written."
	command = [
		_NVTT,
		png_path,
		"-o", dds_path,
		"-f", "bc3n-rxgb",
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


def build_body_atlas(context):
	"""Pack color textures into body.dds and move the active UV map to match."""
	if context.object is not None and context.object.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	meshes = _selected_meshes(context)
	if not meshes:
		raise ValueError("Select the mesh. It needs a UV map and a color texture on each material.")
	users = {}
	missing = []
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
			users.setdefault(image.name, {"image": image, "bounds": [], "slots": []})
			users[image.name]["bounds"].append(bounds)
			users[image.name]["slots"].append((mesh_obj, index))
	if not users:
		raise ValueError("None of the selected materials have a color texture.")
	items = []
	merged = {}
	for name, entry in users.items():
		image = entry["image"]
		bounds = _union_bounds(entry["bounds"])
		src_w = max(1, int(image.size[0]))
		src_h = max(1, int(image.size[1]))
		crop_w = max(1, int(math.ceil((bounds[2] - bounds[0]) * src_w)))
		crop_h = max(1, int(math.ceil((bounds[3] - bounds[1]) * src_h)))
		items.append((name, crop_w, crop_h))
		merged[name] = bounds
	placed, scale = pack_slots(items)
	if not placed:
		raise ValueError("The textures do not fit in a 2048 atlas.")
	rects = {name: (px, py, pw, ph) for name, px, py, pw, ph in placed}
	dest = array.array("f", [0.8, 0.8, 0.8, 1.0]) * (ATLAS * ATLAS)
	report = []
	for name, entry in users.items():
		px, py, pw, ph = rects[name]
		chunk = _crop_scale(entry["image"], merged[name], pw, ph)
		_blit(dest, chunk, px, py, pw, ph)
		_bleed(dest, px, py, pw, ph, GAP // 2)
		report.append((entry["image"].name, int(entry["image"].size[0]), pw))
	atlas = _write_image(dest)
	for entry in users.values():
		bounds = merged[entry["image"].name]
		rect = rects[entry["image"].name]
		for mesh_obj, index in entry["slots"]:
			_move_uvs(mesh_obj, index, bounds, rect)
	_point_materials(meshes, atlas, set(users))
	directory = bpy.path.abspath("//")
	if not bpy.data.filepath or not os.path.isdir(directory):
		directory = os.path.dirname(bpy.path.abspath(users[next(iter(users))]["image"].filepath))
	if not directory or not os.path.isdir(directory):
		directory = bpy.app.tempdir
	dds_path, note = _save_dds(atlas, directory)
	return {
		"textures": len(users),
		"missing": missing,
		"scale": scale,
		"slots": report,
		"dds": dds_path,
		"note": note,
		"image": atlas.name,
	}
