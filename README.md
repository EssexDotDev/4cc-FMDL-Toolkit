# 4cc FMDL Toolkit

For Blender 5.2 LTS

The sidebar tab is **FMDL tools**.

- Merge glove meshes onto a body.
- Convert an existing skeleton onto the PES medic skeleton.
- Convert a mesh with no vertex groups onto the PES medic skeleton by copying weights from the medic body.
- Pack each material's color texture into one 2048 `body.dds` and move the UVs into that sheet.

## Install

Copy the `fmdl_tools` folder to:

`%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\fmdl_tools`

Enable **FMDL tools** in Blender's add-on preferences. **PESfmdl has to be installed.** The panel uses it to import the medic FMDL when that skeleton is not already in the scene.

Set the Medic FMDL path on the panel. A successful convert saves that path in Blender's user preferences.

## Patch notes

### 1.5.0

**Rigged models scale on their bones first.** Convert existing Skeleton poses the skeleton that came with the mesh, then bakes it. The body is scaled once so the neck and the soles match the medic and the hip lines up. The limbs then rotate toward the next medic joint. Mesh matching is the fallback: it runs only if that posed body is still off the medic, and it is still the whole path for Convert boneless Model.

**Narrow arms are no longer stopped at 1.2.** The body scale already set the height. The old ceiling applied that 1.2 again to the arms, so a narrow shoulder left the wrist about 10 cm short and the medic wrist sat in the palm. The upper arm and the forearm now share one extra scale along the arm, whatever reach the wrist still needs. The fingers are not scaled. The shoulder can still sit off the medic shoulder. That deformation is left for a later pass.

**The body atlas is a PES material.** `body.dds` is DXT5. The old RXGB codec was skipped by the compiler. The body material is `fox3ddf_blin`, with the common dummy normal and specular, so an empty shader is not drawn red.
