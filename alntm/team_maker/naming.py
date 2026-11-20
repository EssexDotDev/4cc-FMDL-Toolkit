"""Boot folder names for the 4cc AET compiler.

The compiler keeps a Boots subfolder when its name starts with k and the next
four characters are digits. Anything after that is a label.
"""

import os

MODEL_EXTENSIONS = (".blend", ".fbx", ".glb", ".gltf", ".obj")
_BAD_FILENAME = '<>:"/\\|?*'


class BootIdError(ValueError):
	pass


def safe_label(text):
	cleaned = []
	for char in text:
		if char in _BAD_FILENAME or ord(char) < 32:
			continue
		cleaned.append(char)
	label = " ".join("".join(cleaned).split())
	return label[:60].rstrip(" .")


def parse_boot_id(text, model_path):
	"""Return (folder name, model id) for one row.

	model id is k plus four digits, which is what the compiler rewrites inside
	the FMDL. The folder name adds a label when the user typed one, otherwise
	the model file name.
	"""
	raw = (text or "").strip()
	if not raw:
		raise BootIdError("Enter a Boot ID.")
	try:
		raw.encode("ascii")
	except UnicodeEncodeError as error:
		raise BootIdError("Boot ID has to be plain ASCII.") from error
	token, _, label = raw.partition(" ")
	label = label.strip()
	digits = token[1:] if token[:1].lower() == "k" else token
	if len(digits) != 4 or not digits.isdigit():
		raise BootIdError("Boot ID needs four digits, for example 1901 or k1901 HONEY.")
	model_id = "k" + digits
	if not label and model_path:
		label = os.path.splitext(os.path.basename(model_path))[0]
	label = safe_label(label)
	folder = model_id if not label else "%s %s" % (model_id, label)
	return folder, model_id


def parse_face_id(text, model_path):
	"""Return (face folder, player number) for a head-only row.

	The same box takes a player number, 01 through 23. A longer id still
	works: only its last two digits are the head. The folder is XXX plus
	that number. The compiler replaces XXX with the team id.
	"""
	raw = (text or "").strip()
	if not raw:
		raise BootIdError("Enter a head number, 01 through 23.")
	try:
		raw.encode("ascii")
	except UnicodeEncodeError as error:
		raise BootIdError("Head number has to be plain ASCII.") from error
	token, _, label = raw.partition(" ")
	label = label.strip()
	lowered = token.lower()
	if lowered.startswith("xxx"):
		token = token[3:]
	elif lowered.startswith("k"):
		token = token[1:]
	if not token.isdigit():
		raise BootIdError("A head number is 01 through 23, for example 05 or 05 Fox.")
	player = token[-2:]
	number = int(player)
	if not 1 <= number <= 23:
		raise BootIdError(
			"A head number is 01 through 23. %s is outside that." % player.zfill(2)
		)
	player = "%02d" % number
	if not label and model_path:
		label = os.path.splitext(os.path.basename(model_path))[0]
	label = safe_label(label)
	face_folder = "XXX%s" % player if not label else "XXX%s %s" % (player, label)
	return face_folder, player


def accepted_model(path):
	return os.path.splitext(path)[1].lower() in MODEL_EXTENSIONS
