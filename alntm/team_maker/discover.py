"""Find Blender 5.2. FMDL Tools is installed for that version."""

import json
import os
import sys


def app_dir():
	"""Folder that holds settings and the log. Next to the exe when frozen."""
	if getattr(sys, "frozen", False):
		return os.path.dirname(os.path.abspath(sys.executable))
	return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def worker_path():
	if getattr(sys, "frozen", False):
		return os.path.join(sys._MEIPASS, "blender_worker.py")
	return os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_worker.py")


def _settings_path():
	return os.path.join(app_dir(), "settings.json")


def load_settings():
	path = _settings_path()
	if not os.path.isfile(path):
		return {}
	try:
		with open(path, "r", encoding="utf-8") as handle:
			data = json.load(handle)
	except (OSError, json.JSONDecodeError):
		return {}
	return data if isinstance(data, dict) else {}


def save_settings(data):
	path = _settings_path()
	with open(path, "w", encoding="utf-8") as handle:
		json.dump(data, handle, indent=2)


def blender_candidates():
	roots = [
		os.environ.get("ProgramFiles", r"C:\Program Files"),
		os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
		os.environ.get("LocalAppData", ""),
	]
	found = []
	for root in roots:
		if not root:
			continue
		base = os.path.join(root, "Blender Foundation")
		if not os.path.isdir(base):
			continue
		try:
			names = os.listdir(base)
		except OSError:
			continue
		for name in names:
			exe = os.path.join(base, name, "blender.exe")
			if os.path.isfile(exe):
				found.append(exe)
	found.sort(key=lambda path: (os.path.basename(os.path.dirname(path)) != "Blender 5.2", path))
	return found


def find_blender():
	"""Saved path wins. Otherwise the Blender 5.2 install, then any other install."""
	settings = load_settings()
	saved = settings.get("blender") or ""
	if saved and os.path.isfile(saved):
		return saved
	found = blender_candidates()
	return found[0] if found else ""
