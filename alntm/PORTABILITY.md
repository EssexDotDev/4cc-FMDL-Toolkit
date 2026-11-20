# Aqua's Lazy Nyagger Team Maker

A small window that drives Blender. It does not contain the converter. Each model is opened in a headless Blender, then the installed add-ons do the work:

1. FMDL Tools converts the skeleton (`fmdltools.retarget_pes`, or `fmdltools.boneless_pes` when the mesh has no vertex groups).
2. FMDL Tools builds the body atlas (`fmdltools.build_atlas`).
3. PES FMDL exports `boots.fmdl` (`fmdl.export_fmdl`).

When FMDL Tools changes, install the new add-on in Blender. This program does not need a new build unless those operator names change.

## What the person running the exe installs

1. Blender 5.2, from the Blender installer. The usual location is `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`.
2. FMDL Tools. Copy the `fmdl_tools` folder to `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\fmdl_tools` and enable **FMDL tools** in Blender's add-on preferences.
3. PES FMDL. Install it for the same Blender 5.2 and enable **PESfmdl**.
4. NVIDIA Texture Tools. The current atlas step runs `C:\Program Files\NVIDIA Corporation\NVIDIA Texture Tools\nvtt_export.exe` to write `body.dds`. That program is part of NVIDIA Texture Tools Exporter. If a future FMDL Tools writes the DDS itself, this extra install stops mattering. This window only checks that `body.dds` appeared.

In Blender's FMDL tools panel, set **Medic FMDL** once and leave **Clean the scene** as you want it. The path is saved in Blender's preferences. This program reads that saved path. It does not keep its own copy.

Python is not required for the person who only runs `ALNTM.exe`.

## Sharing the program

Copy `dist\ALNTM.exe`. The first run writes two files next to the exe:

- `settings.json` only if they click the Blender line and pick a `blender.exe` themselves. Until then the program looks for Blender 5.2 under Program Files.
- `alntm.log`, a transcript of each run.

Do not ship the `team_maker` source, Blender, or the add-ons inside the exe. Each person uses the Blender and the add-ons on their own machine, so an FMDL Tools update is an add-on update.

## Using it

1. Click **Team Path** and choose the team folder. The program creates `Boots` inside it.
2. Click a **File Goes Here** box, or drop a file on it. A new empty row appears under a filled one. The red X removes a row.
3. Type the Boot ID. Four digits, with an optional leading `k` and an optional label. `1901`, `k1901`, and `k1901 HONEY` are the same id. When the label is left off, the model file name is used. The folder is `Boots\k1901 HONEY`.
4. Click **Make A Team**. The bar follows import, convert, atlas, and export.

A successful row replaces an existing folder of the same Boot ID. A failed row leaves the old folder where it was.

Each boots folder receives:

- `boots.fmdl` from PES FMDL
- `body.dds` from the atlas step
- `boots.fpk.xml` copied from the Medic FMDL's folder, or written as the standard boots archive listing if that file is not there
- `boots.skl` copied from the Medic FMDL's folder when it sits next to that FMDL

The FMDL's color texture is given the asset path `/Assets/pes16/model/character/boots/k####/body.dds`. The 4cc compiler rewrites `k####` from the folder name.

Files it will open: `.blend`, `.fbx`, `.glb`, `.gltf`, `.obj`. A PMX model has to be saved as a `.blend` from Blender first. This program does not add importers.

It does not press **Merge gloves into body** or **Drape cloth onto limbs**.

The Blender on PATH is ignored when it is not the 5.2 install, because FMDL Tools is installed for 5.2. Click the Blender line at the bottom of the window to point at a different `blender.exe`.

## Rebuilding the exe

On a machine with Python 3.10 or newer:

```
cd C:\AI\4ccAesTools\alntm
py -3 -m pip install -r requirements.txt
py -3 -m PyInstaller --noconfirm --windowed --onefile --name ALNTM --paths . --collect-all tkinterdnd2 --add-data "team_maker\blender_worker.py;." team_maker\__main__.py
```

Or run `build_exe.ps1`. The exe is `dist\ALNTM.exe`.

To try the window without building:

```
cd C:\AI\4ccAesTools\alntm
py -3 -m team_maker
```

`blender_worker.py` is not imported by the window. Blender runs it. The PyInstaller `--add-data` line is what puts that script inside the exe. Without it, the exe has no job to hand Blender.
