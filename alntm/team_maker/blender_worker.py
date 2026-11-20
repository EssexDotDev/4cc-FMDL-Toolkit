"""Run inside Blender. Press the FMDL Tools and PES FMDL operators, then stop.

This file does not import the converter. A newer FMDL Tools add-on is picked
up on the next run as long as these operator ids still exist:

  fmdltools.retarget_pes
  fmdltools.boneless_pes
  fmdltools.build_atlas
  fmdltools.chop_head
  fmdltools.build_head_atlas
  fmdltools.anime_shader
  fmdl.export_fmdl
"""

import json
import os
import shutil
import sys
import traceback

CONVERT_EXISTING = "fmdltools.retarget_pes"
CONVERT_BONELESS = "fmdltools.boneless_pes"
ATLAS = "fmdltools.build_atlas"
CHOP = "fmdltools.chop_head"
HEAD_ATLAS = "fmdltools.build_head_atlas"
ANIME = "fmdltools.anime_shader"
EXPORT = "fmdl.export_fmdl"

# The compiler rewrites the k#### segment of this asset path from the folder name.
BOOTS_ASSET = "/Assets/pes16/model/character/boots/%s/"
# Section 7 of this path is five digits. The compiler replaces those digits
# with the team id plus the player number from the Faces folder name.
FACE_ASSET = "/Assets/pes16/model/character/face/real/%s/sourceimages/"

FPK_XML = """<?xml version="1.0"?>
<ArchiveFile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xsi:type="FpkFile" Name="boots.fpk" FpkType="Fpk">
  <Entries>
    <Entry FilePath="boots.skl" />
    <Entry FilePath="boots.fmdl" />
  </Entries>
  <References />
</ArchiveFile>
"""

FACE_FPK = """<?xml version="1.0"?>
<ArchiveFile xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xsi:type="FpkFile" Name="face.fpk" FpkType="Fpk">
  <Entries>
    <Entry FilePath="face_diff.bin" />
    <Entry FilePath="face_high.fmdl" />
    <Entry FilePath="hair_high.fmdl" />
  </Entries>
  <References />
</ArchiveFile>
"""

# The collar stays the PES19 head template. This run only writes the head.
_FACE_TEMPLATE_FILES = ("face_high.fmdl", "face.dds", "face_diff.bin")


def say(kind, message):
	print("ALNTM\t%s\t%s" % (kind, message), flush=True)


def job_path():
	if "--" not in sys.argv:
		raise RuntimeError("Blender was started without a job file.")
	args = sys.argv[sys.argv.index("--") + 1:]
	if not args:
		raise RuntimeError("Blender was started without a job file.")
	return args[0]


def enable_addons():
	import addon_utils
	import bpy

	errors = []
	for name in ("fmdl_tools", "PESfmdl", "io_scene_fbx", "io_scene_gltf2"):
		try:
			addon_utils.enable(name, default_set=False, persistent=False)
		except Exception as error:
			errors.append("%s: %s" % (name, error))
	missing = []
	for operator in (CONVERT_EXISTING, CONVERT_BONELESS, ATLAS, CHOP, HEAD_ATLAS, EXPORT):
		owner, dot, method = operator.partition(".")
		namespace = getattr(bpy.ops, owner, None)
		if namespace is None or dot == "" or not hasattr(namespace, method):
			missing.append(operator)
	if missing:
		detail = " ".join(errors)
		raise RuntimeError(
			"Blender is missing %s. Install FMDL Tools and PES FMDL for this Blender, then enable both add-ons. %s"
			% (", ".join(missing), detail)
		)


def medic_file():
	import bpy

	try:
		prefs = bpy.context.preferences.addons["fmdl_tools"].preferences
	except KeyError as error:
		raise RuntimeError("Enable the FMDL Tools add-on in this Blender.") from error
	path = bpy.path.abspath(getattr(prefs, "medic_fmdl", "") or "")
	if not path or not os.path.isfile(path):
		raise RuntimeError(
			"Set the Medic FMDL path once in Blender's FMDL tools panel. This program uses that saved path."
		)
	return path


def _view3d():
	import bpy

	manager = getattr(bpy.context, "window_manager", None)
	if manager is None:
		return None
	for window in manager.windows:
		screen = window.screen
		if screen is None:
			continue
		for area in screen.areas:
			if area.type != "VIEW_3D":
				continue
			for region in area.regions:
				if region.type == "WINDOW":
					return {"window": window, "area": area, "region": region}
	return None


def reselect(names):
	"""Select the model again. Importing the medic FMDL clears the selection."""
	import bpy

	view = bpy.context.view_layer
	wanted = set(names)
	chosen = []
	for obj in view.objects:
		keep = obj.name in wanted
		try:
			obj.select_set(keep)
		except RuntimeError:
			continue
		if keep:
			chosen.append(obj)
	meshes = [obj for obj in chosen if obj.type == "MESH"]
	if meshes:
		view.objects.active = max(meshes, key=lambda obj: len(obj.data.vertices))
	elif chosen:
		view.objects.active = chosen[0]
	return chosen


def call_operator(name, prep=None, **kwargs):
	import bpy

	owner, method = name.split(".", 1)
	operator = getattr(getattr(bpy.ops, owner), method)

	def invoke():
		if prep is not None:
			prep()
		try:
			return operator(**kwargs)
		except RuntimeError as error:
			text = str(error)
			if text.startswith("Error: "):
				text = text[len("Error: "):]
			raise RuntimeError(text) from error

	override = _view3d()
	if override:
		with bpy.context.temp_override(**override):
			result = invoke()
	else:
		result = invoke()
	if "FINISHED" not in set(result):
		raise RuntimeError(
			"%s did not finish. The Blender log above has the add-on's reason." % name
		)


def import_model(path):
	import bpy

	ext = os.path.splitext(path)[1].lower()
	if ext == ".blend":
		bpy.ops.wm.open_mainfile(filepath=path)
		return
	for obj in list(bpy.data.objects):
		bpy.data.objects.remove(obj, do_unlink=True)
	if ext == ".fbx":
		result = bpy.ops.import_scene.fbx(filepath=path)
	elif ext in (".glb", ".gltf"):
		result = bpy.ops.import_scene.gltf(filepath=path)
	elif ext == ".obj":
		result = bpy.ops.wm.obj_import(filepath=path)
	else:
		raise RuntimeError("This program cannot import %s. Use a blend, fbx, glb, gltf, or obj file." % ext)
	if "FINISHED" not in set(result):
		raise RuntimeError("Blender could not import %s." % os.path.basename(path))


def selected_meshes():
	import bpy

	view = bpy.context.view_layer
	active = bpy.context.object
	if active is not None and active.mode != "OBJECT":
		try:
			bpy.ops.object.mode_set(mode="OBJECT")
		except RuntimeError:
			pass
	meshes = [obj for obj in view.objects if obj.type == "MESH"]
	for obj in view.objects:
		obj.select_set(obj.type == "MESH")
	if meshes:
		view.objects.active = max(meshes, key=lambda obj: len(obj.data.vertices))
	return meshes


def has_weights(meshes):
	return any(len(obj.vertex_groups) > 0 for obj in meshes)


def scene_has_pes_skeleton():
	"""The three bones FMDL Tools uses to recognize the medic skeleton."""
	import bpy

	needed = {"dsk_hip", "sk_chest", "sk_head"}
	for obj in bpy.data.objects:
		if obj.type != "ARMATURE" or obj.data is None:
			continue
		names = {bone.name for bone in obj.data.bones}
		if needed <= names:
			return True
	return False


def _tag_texture(image, filename, directory):
	if hasattr(image, "fmdl_texture_directory"):
		image.fmdl_texture_directory = directory
	else:
		image["fmdl_texture_directory"] = directory
	if hasattr(image, "fmdl_texture_filename"):
		image.fmdl_texture_filename = filename
	else:
		image["fmdl_texture_filename"] = filename


def mark_body_texture(model_id):
	"""Point the atlas image at the boots asset path PES FMDL writes into the file."""
	import bpy

	image = bpy.data.images.get("body.dds")
	if image is None:
		for candidate in bpy.data.images:
			if candidate.name.lower().startswith("body"):
				image = candidate
				break
	if image is None:
		return
	_tag_texture(image, "body.dds", BOOTS_ASSET % model_id)


def mark_head_texture(model_id):
	"""Point head.dds at the face path. The compiler rewrites the five-digit id."""
	import bpy

	image = bpy.data.images.get("head.dds")
	if image is None:
		for candidate in bpy.data.images:
			if candidate.name.lower().startswith("head"):
				image = candidate
				break
	if image is None:
		return
	player = model_id[-2:].zfill(2)
	_tag_texture(image, "head.dds", FACE_ASSET % ("000" + player))


def take_dds(work_dir, output_dir, stem):
	destination = os.path.join(output_dir, stem + ".dds")
	source = os.path.join(work_dir, stem + ".dds")
	if os.path.isfile(source):
		shutil.copy2(source, destination)
	if not os.path.isfile(destination):
		raise RuntimeError(
			"FMDL Tools did not write %s.dds. The atlas step uses NVIDIA Texture Tools "
			"(nvtt_export.exe) when that file is missing. Install it, then run this row again."
			% stem
		)
	for folder in (work_dir, output_dir):
		png = os.path.join(folder, stem + ".png")
		if os.path.isfile(png):
			os.remove(png)
	return destination


def find_head_template(medic):
	"""The PES19 head template holds the collar. The chopped head does not."""
	medic_dir = os.path.dirname(medic)
	bases = [medic_dir, os.path.dirname(medic_dir), os.path.dirname(os.path.dirname(medic_dir))]
	seen = set()
	for base in bases:
		if not base or base in seen or not os.path.isdir(base):
			continue
		seen.add(base)
		if os.path.isfile(os.path.join(base, "face_high.fmdl")):
			return base
		try:
			names = os.listdir(base)
		except OSError:
			continue
		for name in names:
			candidate = os.path.join(base, name)
			if not os.path.isdir(candidate):
				continue
			if os.path.isfile(os.path.join(candidate, "face_high.fmdl")):
				return candidate
			shaded = os.path.join(candidate, "Shaded")
			if os.path.isfile(os.path.join(shaded, "face_high.fmdl")):
				return shaded
	return ""


def face_sidecars(output_dir, medic):
	template = find_head_template(medic)
	if not template:
		say("INFO", "No head template was found beside the medic, so face_high.fmdl was not copied.")
	else:
		for name in _FACE_TEMPLATE_FILES:
			source = os.path.join(template, name)
			if os.path.isfile(source):
				shutil.copy2(source, os.path.join(output_dir, name))
			else:
				say("INFO", "The head template has no %s." % name)
		xml_source = os.path.join(template, "face.fpk.xml")
		if os.path.isfile(xml_source):
			shutil.copy2(xml_source, os.path.join(output_dir, "face.fpk.xml"))
			return
	with open(os.path.join(output_dir, "face.fpk.xml"), "w", encoding="utf-8", newline="\n") as handle:
		handle.write(FACE_FPK)


def sidecars(output_dir, medic):
	folder = os.path.dirname(medic)
	skeleton = os.path.join(folder, "boots.skl")
	destination = os.path.join(output_dir, "boots.skl")
	if os.path.isfile(skeleton):
		shutil.copy2(skeleton, destination)
	else:
		say("INFO", "No boots.skl next to the Medic FMDL, so the skeleton file was not copied.")
	xml_source = os.path.join(folder, "boots.fpk.xml")
	xml_destination = os.path.join(output_dir, "boots.fpk.xml")
	if os.path.isfile(xml_source):
		shutil.copy2(xml_source, xml_destination)
	else:
		with open(xml_destination, "w", encoding="utf-8", newline="\n") as handle:
			handle.write(FPK_XML)


def check_install():
	import bpy

	enable_addons()
	import fmdl_tools

	version = ".".join(str(part) for part in fmdl_tools.bl_info.get("version", ()))
	say("INFO", "Blender %s" % bpy.app.version_string)
	say("INFO", "FMDL Tools %s" % (version or "installed"))
	medic_file()
	say("INFO", "Medic FMDL is set")
	say("DONE", "ready")


def apply_anime_shader():
	"""Unlit constant shader. Run it after the atlas, which stamps the lit blin material."""
	import bpy

	if not hasattr(getattr(bpy.ops, "fmdltools", None), "anime_shader"):
		raise RuntimeError("Reload FMDL Tools in Blender. Use anime shader is not installed yet.")
	say("STEP", "0.75 Anime shader")
	call_operator(ANIME, prep=selected_meshes)


def convert_one(job):
	import bpy

	model = job["model"]
	output_dir = job["output_dir"]
	work_dir = job["work_dir"]
	model_id = job["model_id"]
	os.makedirs(output_dir, exist_ok=True)
	os.makedirs(work_dir, exist_ok=True)

	say("STEP", "0.10 Importing %s" % os.path.basename(model))
	enable_addons()
	medic = medic_file()
	import_model(model)

	say("STEP", "0.30 Converting skeleton")
	meshes = selected_meshes()
	if not meshes:
		raise RuntimeError("The file has no mesh to convert.")
	names = [obj.name for obj in meshes]
	# Import the medic before Convert. The operator also imports it, but that
	# import drops the selection when it happens inside the button, and a
	# cancelled button undoes the import. Loading it first matches a scene
	# that already has the PES skeleton.
	if not scene_has_pes_skeleton():
		call_operator("fmdl.import_fmdl", filepath=medic)
	chosen = reselect(names)
	if has_weights(chosen or meshes):
		operator = CONVERT_EXISTING
	else:
		say("INFO", "No vertex groups, using the boneless convert.")
		operator = CONVERT_BONELESS
	call_operator(operator, prep=lambda: reselect(names))

	blend_path = os.path.join(work_dir, "work.blend")
	if job.get("head_only"):
		say("STEP", "0.50 Chopping the head")

		def select_body():
			if not reselect(names):
				selected_meshes()

		call_operator(CHOP, prep=select_body)
		say("STEP", "0.70 Building the head texture")
		bpy.ops.wm.save_as_mainfile(filepath=blend_path)
		call_operator(HEAD_ATLAS, prep=selected_meshes)
		if job.get("anime_shader"):
			apply_anime_shader()
		mark_head_texture(model_id)
		say("STEP", "0.85 Exporting hair_high.fmdl")
		fmdl_path = os.path.join(output_dir, "hair_high.fmdl")
		call_operator(EXPORT, filepath=fmdl_path, prep=selected_meshes)
		if not os.path.isfile(fmdl_path):
			raise RuntimeError("PES FMDL did not write hair_high.fmdl.")
		take_dds(work_dir, output_dir, "head")
		face_sidecars(output_dir, medic)
		say("DONE", output_dir)
		return

	say("STEP", "0.60 Building texture atlas")
	bpy.ops.wm.save_as_mainfile(filepath=blend_path)
	call_operator(ATLAS, prep=selected_meshes)
	if job.get("anime_shader"):
		apply_anime_shader()
	mark_body_texture(model_id)

	say("STEP", "0.85 Exporting FMDL")
	fmdl_path = os.path.join(output_dir, "boots.fmdl")
	call_operator(EXPORT, filepath=fmdl_path, prep=selected_meshes)
	if not os.path.isfile(fmdl_path):
		raise RuntimeError("PES FMDL did not write boots.fmdl.")
	take_dds(work_dir, output_dir, "body")
	sidecars(output_dir, medic)
	say("DONE", output_dir)


def main():
	with open(job_path(), "r", encoding="utf-8") as handle:
		job = json.load(handle)
	if job.get("check"):
		check_install()
		return
	convert_one(job)


if __name__ == "__main__":
	try:
		main()
	except Exception as error:
		say("ERROR", str(error))
		traceback.print_exc()
		sys.exit(1)
