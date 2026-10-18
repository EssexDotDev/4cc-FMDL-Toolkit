"""N-panel tools for PES FMDL scenes."""

bl_info = {
	"name": "FMDL tools",
	"author": "EssexDotDev",
	"version": (1, 4, 0),
	"blender": (5, 2, 0),
	"location": "View3D > Sidebar > FMDL tools",
	"description": "Merge gloves onto a body, put a mesh on the PES medic skeleton, and pack a body.dds atlas",
	"category": "Object",
}

import bpy

from .atlas import build_body_atlas
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
			result = retarget_to_pes(context, prefs.medic_fmdl)
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
			result = transfer_boneless_to_pes(context, prefs.medic_fmdl)
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
		self.report(
			{"INFO"},
			"Packed %d texture(s) into %s. %s.%s%s%s" % (
				result["textures"], result["dds"], sizes, skipped, left_out, note,
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
		layout.operator("fmdltools.retarget_pes", text="Convert existing Skeleton to PES Skeleton")
		layout.operator("fmdltools.boneless_pes", text="Convert \U0001F171oneless Model to PES Skeleton")
		layout.operator("fmdltools.drape_cloth", text="Drape cloth onto limbs")
		layout.separator()
		layout.operator("fmdltools.build_atlas", text="Build body texture atlas")


CLASSES = (
	FMDLTOOLS_AddonPreferences,
	FMDLTOOLS_OT_merge_gloves,
	FMDLTOOLS_OT_retarget_pes,
	FMDLTOOLS_OT_boneless_pes,
	FMDLTOOLS_OT_drape_cloth,
	FMDLTOOLS_OT_atlas,
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
