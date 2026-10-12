# 4cc FMDL Toolkit

Blender 5.2 add-on for PES FMDL scenes. Author: EssexDotDev.

The sidebar tab is **FMDL tools**.

- Merge glove meshes onto a body.
- Convert an existing skeleton onto the PES medic skeleton.
- Convert a mesh with no vertex groups onto the PES medic skeleton by copying weights from the medic body.

## Install

Copy the `fmdl_tools` folder to:

`%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\fmdl_tools`

Enable **FMDL tools** in Blender's add-on preferences. PESfmdl has to be installed. The panel uses it to import the medic FMDL when that skeleton is not already in the scene.

Set the Medic FMDL path on the panel. A successful convert saves that path in Blender's user preferences.
