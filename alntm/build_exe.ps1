# Rebuild ALNTM.exe from this folder. The recipient does not need this.
Set-Location $PSScriptRoot
py -3 -m PyInstaller `
	--noconfirm `
	--windowed `
	--onefile `
	--name ALNTM `
	--paths $PSScriptRoot `
	--collect-all tkinterdnd2 `
	--add-data "team_maker\blender_worker.py;." `
	"team_maker\__main__.py"
Write-Host "Exe: $PSScriptRoot\dist\ALNTM.exe"
