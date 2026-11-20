"""N-panel tools for PES FMDL scenes."""

bl_info = {
	"name": "FMDL tools",
	"author": "EssexDotDev",
	"version": (1, 6, 0),
	"blender": (5, 2, 0),
	"location": "View3D > Sidebar > FMDL tools",
	"description": "Merge gloves onto a body, put a mesh on the PES medic skeleton, and pack a body.dds atlas",
	"category": "Object",
}

import bpy

from .atlas import build_body_atlas, build_head_atlas
from .head import chop_head
from .look import add_outline, use_anime_shader
from .merge import merge_gloves, selection_summary
from .retarget import drape_cloth, retarget_to_pes, transfer_boneless_to_pes


class FMDLTOOLS_AddonPreferences(bpy.types.AddonPreferences):
	bl_idname = __package__

	medic_fmdl: bpy.props.StringProperty(
		name="Medic FMDL",
		description="PES medic body. Imported with PESfmdl when the skeleton is not already in the scene",
		subtype="FILE_PATH",
		default="",
	)
	clean_scene: bpy.props.BoolProperty(
		name="Clean the scene",
		description="Delete everything outside the medic FMDL. Off leaves other objects in the file",
		default=True,
	)


class FMDLTOOLS_OT_merge_gloves(bpy.types.Operator):
	bl_idname = "fmdltools.merge_gloves"
	bl_label = "Merge gloves into body"
	bl_description = (
		"Join glove_l and glove_r onto the body mesh and skeleton. "
		"The glove's dsk_wrist meets the end of the body's dsk_forearm. "
		"The body's sk_hand is replaced by the glove hand, and the body's dsk_forearm is kept. "
		"Hand UVs stay in place on the body texture"
	)
	bl_options = {"REGISTER", "UNDO"}

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		try:
			result = merge_gloves(context)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		self.report(
			{"INFO"},
			"Merged %d glove(s) into %s and added %d bones" % (
				result["gloves"], result["mesh"], result["bones"],
			),
		)
		return {"FINISHED"}


class FMDLTOOLS_OT_retarget_pes(bpy.types.Operator):
	bl_idname = "fmdltools.retarget_pes"
	bl_label = "Convert existing Skeleton to PES Skeleton"
	bl_description = (
		"Merge this model's vertex groups onto the PES medic skeleton. "
		"Use this when the mesh already has a skeleton. "
		"Works on a mesh already in Blender, whether it came from PMX, FBX, Unity, or Unreal. "
		"If the medic skeleton is not in the scene, PESfmdl imports the medic FMDL"
	)
	bl_options = {"REGISTER", "UNDO"}

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		prefs = context.preferences.addons[__package__].preferences
		try:
			result = retarget_to_pes(context, prefs.medic_fmdl, prefs.clean_scene)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		extra = ""
		if result["unmapped"]:
			extra = " Unmapped: %s" % ", ".join(result["unmapped"][:6])
		scale = result.get("scale", 1.0)
		if abs(scale - 1.0) >= 0.01:
			extra = " Height scale %.3f.%s" % (scale, extra)
		if result.get("arms"):
			extra = " Wrists moved onto the medic hands.%s" % extra
		if result.get("rig"):
			extra = " Rig: %s.%s" % ("; ".join(result["rig"]), extra)
		self.report(
			{"INFO"},
			"Converted %s onto %s (%d PES groups, %d source groups).%s" % (
				result["mesh"], result["armature"], result["groups"], result["mapped"], extra,
			),
		)
		if prefs.medic_fmdl:
			try:
				bpy.ops.wm.save_userpref()
			except RuntimeError:
				pass
		return {"FINISHED"}


class FMDLTOOLS_OT_boneless_pes(bpy.types.Operator):
	bl_idname = "fmdltools.boneless_pes"
	bl_label = "Convert \U0001F171oneless Model to PES Skeleton"
	bl_description = (
		"Copy vertex weights from the PES medic body onto a mesh with no vertex groups. "
		"The mesh is lined up to the medic by its height, then weights are transferred "
		"from the nearest faces. Use this when the model has no skeleton at all. "
		"It works best on an upright humanoid facing the same way as the medic, in a similar pose. "
		"Hair behind the head is left out of the line-up and weighted to the head. "
		"A mesh already about the medic's height is not scaled. "
		"If the medic skeleton is not in the scene, PESfmdl imports the medic FMDL"
	)
	bl_options = {"REGISTER", "UNDO"}

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		prefs = context.preferences.addons[__package__].preferences
		try:
			result = transfer_boneless_to_pes(context, prefs.medic_fmdl, prefs.clean_scene)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		extra = ""
		scale = result.get("scale", 1.0)
		if abs(scale - 1.0) >= 0.01:
			extra = " Height scale %.3f." % scale
		if result.get("arms"):
			extra = " Wrists moved onto the medic hands.%s" % extra
		self.report(
			{"INFO"},
			"Rigged %s from %s onto %s (%d PES groups).%s" % (
				result["mesh"], result["source"], result["armature"], result["groups"], extra,
			),
		)
		if prefs.medic_fmdl:
			try:
				bpy.ops.wm.save_userpref()
			except RuntimeError:
				pass
		return {"FINISHED"}


class FMDLTOOLS_OT_drape_cloth(bpy.types.Operator):
	bl_idname = "fmdltools.drape_cloth"
	bl_label = "Drape cloth onto limbs"
	bl_description = (
		"Share a coat or skirt between the torso and the arm or leg it lies on. "
		"Run this after Convert, on the converted body. "
		"The chest or the hip keeps a share so the cloth does not move as one rigid limb"
	)
	bl_options = {"REGISTER", "UNDO"}

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		try:
			result = drape_cloth(context)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		self.report(
			{"INFO"},
			"Draped %d cloth verts on %s (%d seated on an arm)." % (
				result["painted"], result["mesh"], result["seated"],
			),
		)
		return {"FINISHED"}


class FMDLTOOLS_OT_chop_head(bpy.types.Operator):
	bl_idname = "fmdltools.chop_head"
	bl_label = "Chop head and hair"
	bl_description = (
		"Cut the head and the hair off the converted body. "
		"The body mesh and every bone except sk_head are removed. "
		"The head stays on the same sk_head joint. "
		"face_high.fmdl from the PES19 head template is the collar"
	)
	bl_options = {"REGISTER", "UNDO"}

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		try:
			result = chop_head(context)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		self.report(
			{"INFO"},
			"Chopped %d verts into %s. Skeleton is %s. The body was removed." % (
				result["verts"], result["head"], ", ".join(result["bones"]),
			),
		)
		return {"FINISHED"}


class FMDLTOOLS_OT_outline(bpy.types.Operator):
	bl_idname = "fmdltools.add_outline"
	bl_label = "Add outline"
	bl_description = (
		"Duplicate the mesh into an inverse shell: merge the split edges, flip the normals, "
		"and solidify with a negative thickness. The shell uses the unlit shader and is one-sided. "
		"A vertex group named outline_skip is left out of the line. "
		"The shell is applied into the mesh so the FMDL export includes it"
	)
	bl_options = {"REGISTER", "UNDO"}

	thickness: bpy.props.FloatProperty(
		name="Thickness",
		description="Negative thickness. The shell grows outward after the normals are flipped",
		default=-0.002,
		min=-0.05,
		max=-0.0001,
	)

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		try:
			result = add_outline(context, self.thickness)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		self.report(
			{"INFO"},
			"Added an outline on %s at thickness %.4f." % (
				", ".join(result["shells"]), result["thickness"],
			),
		)
		return {"FINISHED"}


class FMDLTOOLS_OT_anime_shader(bpy.types.Operator):
	bl_idname = "fmdltools.anime_shader"
	bl_label = "Use anime shader"
	bl_description = (
		"Switch the selected mesh to the unlit constant shader from the shadeless PES head. "
		"The texture is drawn as painted, without the blin light and specular that wash the color out"
	)
	bl_options = {"REGISTER", "UNDO"}

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		try:
			result = use_anime_shader(context)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		self.report(
			{"INFO"},
			"Anime shader on %s." % ", ".join(result["materials"][:6]),
		)
		return {"FINISHED"}


class FMDLTOOLS_OT_atlas(bpy.types.Operator):
	bl_idname = "fmdltools.build_atlas"
	bl_label = "Build body texture atlas"
	bl_description = (
		"Pack the color textures into one 2048 body.dds and move the UVs. "
		"Fewer textures get larger cells. Body, face, and clothes outrank hair. "
		"Run it while the UVs still sit in each texture's own 0-1 space"
	)
	bl_options = {"REGISTER", "UNDO"}

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		try:
			result = build_body_atlas(context)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		sizes = ", ".join("%s %d" % (name, dst) for name, _src, dst in result["slots"][:6])
		note = (" " + result["note"]) if result["note"] else ""
		skipped = ""
		if result["missing"]:
			skipped = " Skipped materials with no texture: %s." % ", ".join(result["missing"][:4])
		left_out = ""
		if result.get("dropped"):
			left_out = " Left out %s." % ", ".join(result["dropped"][:4])
		if result.get("skipped"):
			left_out += " Not a head or hair texture: %s." % ", ".join(result["skipped"][:4])
		self.report(
			{"INFO"},
			"Packed %d texture(s) into %s. %s.%s%s%s" % (
				result["textures"], result["dds"], sizes, skipped, left_out, note,
			),
		)
		return {"FINISHED"}


class FMDLTOOLS_OT_head_atlas(bpy.types.Operator):
	bl_idname = "fmdltools.build_head_atlas"
	bl_label = "Build head texture"
	bl_description = (
		"Pack the chopped head's face and hair into one 1024 head.dds and put that material on the mesh. "
		"The body texture is left out. "
		"Run it while the UVs still sit in each texture's own 0-1 space"
	)
	bl_options = {"REGISTER", "UNDO"}

	@classmethod
	def poll(cls, context):
		return bool(context.selected_objects)

	def execute(self, context):
		try:
			result = build_head_atlas(context)
		except (ValueError, RuntimeError) as error:
			self.report({"ERROR"}, str(error))
			return {"CANCELLED"}
		sizes = ", ".join("%s %d" % (name, dst) for name, _src, dst in result["slots"][:6])
		note = (" " + result["note"]) if result["note"] else ""
		left_out = ""
		if result.get("skipped"):
			left_out = " Not a head or hair texture: %s." % ", ".join(result["skipped"][:4])
		self.report(
			{"INFO"},
			"Packed %d texture(s) into %s (%d). %s.%s%s" % (
				result["textures"], result["dds"], result["size"], sizes, left_out, note,
			),
		)
		return {"FINISHED"}


class FMDLTOOLS_PT_main(bpy.types.Panel):
	bl_idname = "FMDLTOOLS_PT_main"
	bl_label = "FMDL tools"
	bl_space_type = "VIEW_3D"
	bl_region_type = "UI"
	bl_category = "FMDL tools"

	def draw(self, context):
		layout = self.layout
		layout.label(text="Select boots, glove_l, and glove_r")
		for line in selection_summary(context):
			layout.label(text=line)
		layout.operator("fmdltools.merge_gloves", text="Merge gloves into body")
		layout.separator()
		layout.label(text="Select the model")
		prefs = context.preferences.addons[__package__].preferences
		layout.prop(prefs, "medic_fmdl", text="Medic FMDL")
		layout.prop(prefs, "clean_scene", text="Clean the scene")
		layout.operator("fmdltools.retarget_pes", text="Convert existing Skeleton to PES Skeleton")
		layout.operator("fmdltools.boneless_pes", text="Convert \U0001F171oneless Model to PES Skeleton")
		layout.operator("fmdltools.drape_cloth", text="Drape cloth onto limbs")
		layout.operator("fmdltools.chop_head", text="Chop head and hair")
		layout.operator("fmdltools.add_outline", text="Add outline")
		layout.operator("fmdltools.anime_shader", text="Use anime shader")
		layout.separator()
		layout.operator("fmdltools.build_atlas", text="Build body texture atlas")
		layout.operator("fmdltools.build_head_atlas", text="Build head texture")


CLASSES = (
	FMDLTOOLS_AddonPreferences,
	FMDLTOOLS_OT_merge_gloves,
	FMDLTOOLS_OT_retarget_pes,
	FMDLTOOLS_OT_boneless_pes,
	FMDLTOOLS_OT_drape_cloth,
	FMDLTOOLS_OT_chop_head,
	FMDLTOOLS_OT_outline,
	FMDLTOOLS_OT_anime_shader,
	FMDLTOOLS_OT_atlas,
	FMDLTOOLS_OT_head_atlas,
	FMDLTOOLS_PT_main,
)


def register():
	for cls in CLASSES:
		bpy.utils.register_class(cls)
	if hasattr(bpy.types.Scene, "fmdltools_medic_fmdl"):
		del bpy.types.Scene.fmdltools_medic_fmdl


def unregister():
	for cls in reversed(CLASSES):
		bpy.utils.unregister_class(cls)
	if hasattr(bpy.types.Scene, "fmdltools_medic_fmdl"):
		del bpy.types.Scene.fmdltools_medic_fmdl
