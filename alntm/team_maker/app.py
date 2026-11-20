"""Window for dropping models and building Boots folders."""

import os
import queue
import shutil
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from team_maker import APP_NAME
from team_maker.discover import app_dir, find_blender, load_settings, save_settings, worker_path
from team_maker.naming import BootIdError, accepted_model, parse_boot_id, parse_face_id
from team_maker.runner import run_blender

try:
	from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
	DND_FILES = None
	TkinterDnD = None

_BG = "#ffffff"
_GREEN = "#2e7d32"
_GREEN_ACTIVE = "#1b5e20"
_RED = "#c62828"
_RED_ACTIVE = "#8e0000"
_MUTED = "#666666"
_FONT = ("Segoe UI", 10)
_FILETYPES = (
	("Models", "*.blend *.fbx *.glb *.gltf *.obj"),
	("Blender", "*.blend"),
	("FBX", "*.fbx"),
	("glTF", "*.glb *.gltf"),
	("OBJ", "*.obj"),
	("All files", "*.*"),
)


def _dpi():
	try:
		from ctypes import windll
		windll.shcore.SetProcessDpiAwareness(1)
	except Exception:
		pass


def _split_drop(widget, data):
	try:
		return [item for item in widget.tk.splitlist(data) if item]
	except tk.TclError:
		return [data] if data else []


class ModelRow:
	def __init__(self, app, parent):
		self.app = app
		self.path = ""
		self.placeholder = True
		self.frame = tk.Frame(parent, bg=_BG)
		self.frame.pack(fill="x", padx=18, pady=7)

		self.remove_button = tk.Button(
			self.frame,
			text="X",
			command=self.remove,
			fg="white",
			bg=_RED,
			activeforeground="white",
			activebackground=_RED_ACTIVE,
			font=("Segoe UI", 9, "bold"),
			width=3,
			relief="solid",
			bd=1,
			cursor="hand2",
		)
		self.remove_button.pack(side="left")

		self.file_box = tk.Label(
			self.frame,
			text="File Goes Here",
			anchor="w",
			width=40,
			bg=_BG,
			fg=_MUTED,
			font=_FONT,
			relief="solid",
			bd=1,
			padx=8,
			pady=5,
			cursor="hand2",
		)
		self.file_box.pack(side="left", padx=(10, 0))
		self.file_box.bind("<Button-1>", self.browse)

		tk.Label(self.frame, text="——", bg=_BG, font=("Segoe UI", 12)).pack(side="left", padx=8)

		self.head_only = tk.BooleanVar(value=False)
		self.boot = tk.Entry(
			self.frame,
			width=22,
			font=_FONT,
			relief="solid",
			bd=1,
			fg=_MUTED,
		)
		self.boot.pack(side="left", ipady=4)
		self._show_id_hint()
		self.boot.bind("<FocusIn>", self._boot_focus_in)
		self.boot.bind("<FocusOut>", self._boot_focus_out)

		tk.Checkbutton(
			self.frame,
			text="Head only",
			variable=self.head_only,
			command=self._on_head_only,
			bg=_BG,
			activebackground=_BG,
			font=_FONT,
		).pack(side="left", padx=(12, 0))

		self.anime_shader = tk.BooleanVar(value=False)
		tk.Checkbutton(
			self.frame,
			text="Anime shader",
			variable=self.anime_shader,
			bg=_BG,
			activebackground=_BG,
			font=_FONT,
		).pack(side="left", padx=(8, 0))

		if DND_FILES is not None:
			self.file_box.drop_target_register(DND_FILES)
			self.file_box.dnd_bind("<<Drop>>", self._on_drop)

	def _id_hint(self):
		return "Head ##" if self.head_only.get() else "Boot ID"

	def _show_id_hint(self):
		self.placeholder = True
		self.boot.delete(0, "end")
		self.boot.insert(0, self._id_hint())
		self.boot.configure(fg=_MUTED)

	def _on_head_only(self):
		if self.placeholder:
			self._show_id_hint()
		self.app._ensure_blank()

	def _boot_focus_in(self, _event):
		if self.placeholder:
			self.placeholder = False
			self.boot.delete(0, "end")
			self.boot.configure(fg="black")

	def _boot_focus_out(self, _event):
		if not self.boot.get().strip():
			self._show_id_hint()

	def boot_text(self):
		if self.placeholder:
			return ""
		return self.boot.get().strip()

	def browse(self, _event=None):
		if self.app.running:
			return
		paths = filedialog.askopenfilenames(title="Model", filetypes=_FILETYPES)
		if paths:
			self.app.accept_files(paths, self)

	def _on_drop(self, event):
		if self.app.running:
			return
		self.app.accept_files(_split_drop(self.file_box, event.data), self)

	def set_path(self, path):
		self.path = path
		shown = path if len(path) <= 52 else "..." + path[-49:]
		self.file_box.configure(text=shown, fg="black")

	def clear(self):
		self.path = ""
		self.file_box.configure(text="File Goes Here", fg=_MUTED)
		self.head_only.set(False)
		self.anime_shader.set(False)
		self._show_id_hint()

	def remove(self):
		if self.app.running:
			return
		self.app.remove_row(self)


class App:
	def __init__(self):
		_dpi()
		if TkinterDnD is not None:
			self.root = TkinterDnD.Tk()
		else:
			self.root = tk.Tk()
		self.root.title(APP_NAME)
		self.root.configure(bg=_BG)
		self.root.geometry("1080x700")
		self.root.minsize(960, 520)

		self.team_path = ""
		self.rows = []
		self.running = False
		self.cancel = False
		self.proc_slot = []
		self.events = queue.Queue()

		self._build()
		self.add_row()
		self._show_blender()
		self.root.protocol("WM_DELETE_WINDOW", self.close)
		self.root.after(100, self._drain)

	def _build(self):
		top = tk.Frame(self.root, bg=_BG)
		top.pack(fill="x", padx=16, pady=(14, 8))
		top.columnconfigure(1, weight=1)

		team = tk.Button(
			top,
			text="Team Path",
			command=self.pick_team,
			font=_FONT,
			relief="solid",
			bd=1,
			bg=_BG,
			padx=8,
			pady=4,
			cursor="hand2",
		)
		team.grid(row=0, column=0, sticky="w")

		self.team_var = tk.StringVar()
		self.team_entry = tk.Entry(
			top,
			textvariable=self.team_var,
			font=_FONT,
			relief="solid",
			bd=1,
			state="readonly",
			readonlybackground=_BG,
		)
		self.team_entry.grid(row=0, column=1, sticky="ew", padx=8)
		self.team_entry.bind("<Button-1>", lambda _event: self.pick_team())

		tk.Label(top, text=APP_NAME, bg=_BG, font=("Segoe UI", 14, "bold")).grid(
			row=0, column=2, padx=8
		)

		self.make = tk.Button(
			top,
			text="Make A Team",
			command=self.make_team,
			fg="white",
			bg=_GREEN,
			activeforeground="white",
			activebackground=_GREEN_ACTIVE,
			font=("Segoe UI", 11, "bold"),
			relief="solid",
			bd=1,
			padx=12,
			pady=4,
			cursor="hand2",
		)
		self.make.grid(row=0, column=3, sticky="e")

		middle = tk.Frame(self.root, bg=_BG)
		middle.pack(fill="both", expand=True)
		self.scroll = ttk.Scrollbar(middle, orient="vertical")
		self.canvas = tk.Canvas(
			middle,
			bg=_BG,
			highlightthickness=0,
			yscrollcommand=self.scroll.set,
		)
		self.scroll.configure(command=self.canvas.yview)
		self.scroll.pack(side="right", fill="y")
		self.canvas.pack(side="left", fill="both", expand=True)
		self.inner = tk.Frame(self.canvas, bg=_BG)
		self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
		self.inner.bind("<Configure>", self._fit_scroll)
		self.canvas.bind("<Configure>", self._fit_width)
		self.canvas.bind_all("<MouseWheel>", self._wheel)
		if DND_FILES is not None:
			self.inner.drop_target_register(DND_FILES)
			self.inner.dnd_bind("<<Drop>>", self._drop_on_list)

		self.progress = ttk.Progressbar(self.root, maximum=1000, mode="determinate")
		self.progress.pack(fill="x", padx=18, pady=(8, 2))

		self.status = tk.StringVar(value="Drop a model on a row, or click the row to browse.")
		tk.Label(self.root, textvariable=self.status, bg=_BG, fg="#222222", font=_FONT, anchor="w").pack(
			fill="x", padx=18
		)

		self.blender_var = tk.StringVar()
		blender_line = tk.Label(
			self.root,
			textvariable=self.blender_var,
			bg=_BG,
			fg=_MUTED,
			font=("Segoe UI", 9),
			anchor="w",
			cursor="hand2",
		)
		blender_line.pack(fill="x", padx=18, pady=(2, 0))
		blender_line.bind("<Button-1>", lambda _event: self.pick_blender())

		self.log = tk.Text(
			self.root,
			height=8,
			wrap="word",
			font=("Consolas", 9),
			bg="#fafafa",
			relief="solid",
			bd=1,
			state="disabled",
		)
		self.log.pack(fill="x", padx=18, pady=(6, 14))

	def _fit_scroll(self, _event):
		self.canvas.configure(scrollregion=self.canvas.bbox("all"))

	def _fit_width(self, event):
		self.canvas.itemconfigure(self.window_id, width=event.width)

	def _wheel(self, event):
		self.canvas.yview_scroll(int(-event.delta / 120), "units")

	def _drop_on_list(self, event):
		if self.running:
			return
		target = next((row for row in self.rows if not row.path), None)
		self.accept_files(_split_drop(self.inner, event.data), target)

	def add_row(self):
		row = ModelRow(self, self.inner)
		self.rows.append(row)
		return row

	def remove_row(self, row):
		if len(self.rows) == 1:
			row.clear()
			return
		row.frame.destroy()
		self.rows.remove(row)
		self._ensure_blank()

	def accept_files(self, paths, target):
		rejected = []
		for path in paths:
			if os.path.isdir(path) or not accepted_model(path):
				rejected.append(os.path.basename(path))
				continue
			row = target if target is not None and not target.path else None
			if row is None:
				row = next((item for item in self.rows if not item.path), None)
			if row is None:
				row = self.add_row()
			row.set_path(os.path.abspath(path))
			target = None
		self._ensure_blank()
		if rejected:
			self.status.set("Skipped %s. Use a blend, fbx, glb, gltf, or obj file." % ", ".join(rejected))
		elif paths:
			self.status.set("Ready.")

	def _ensure_blank(self):
		if not self.rows or self.rows[-1].path:
			self.add_row()
		while (
			len(self.rows) > 1
			and not self.rows[-1].path
			and not self.rows[-1].boot_text()
			and not self.rows[-1].head_only.get()
			and not self.rows[-2].path
			and not self.rows[-2].boot_text()
			and not self.rows[-2].head_only.get()
		):
			extra = self.rows.pop()
			extra.frame.destroy()

	def pick_team(self):
		if self.running:
			return
		path = filedialog.askdirectory(title="Team folder")
		if not path:
			return
		self.team_path = os.path.abspath(path)
		self.team_var.set(self.team_path)

	def pick_blender(self):
		if self.running:
			return
		path = filedialog.askopenfilename(
			title="blender.exe",
			filetypes=(("Blender", "blender.exe"), ("Programs", "*.exe")),
		)
		if not path:
			return
		settings = load_settings()
		settings["blender"] = os.path.abspath(path)
		save_settings(settings)
		self._show_blender()

	def _show_blender(self):
		path = find_blender()
		if path:
			self.blender_var.set("Blender: %s    (click to change)" % path)
		else:
			self.blender_var.set("Blender was not found. Click here and choose blender.exe")

	def _log(self, text):
		self.log.configure(state="normal")
		self.log.insert("end", text + "\n")
		self.log.see("end")
		self.log.configure(state="disabled")
		try:
			with open(os.path.join(app_dir(), "alntm.log"), "a", encoding="utf-8") as handle:
				handle.write(text + "\n")
		except OSError:
			pass

	def _jobs(self):
		jobs = []
		seen = {}
		for index, row in enumerate(self.rows, start=1):
			if not row.path and not row.boot_text():
				continue
			head_only = bool(row.head_only.get())
			id_name = "head number" if head_only else "Boot ID"
			if not row.path:
				raise BootIdError("Row %d has a %s and no model." % (index, id_name))
			if not os.path.isfile(row.path):
				raise BootIdError("Row %d is missing its file." % index)
			try:
				if head_only:
					folder, model_id = parse_face_id(row.boot_text(), row.path)
				else:
					folder, model_id = parse_boot_id(row.boot_text(), row.path)
			except BootIdError as error:
				raise BootIdError("Row %d: %s" % (index, error)) from error
			key = ("face", model_id[-2:]) if head_only else ("boot", model_id)
			if key in seen:
				raise BootIdError("%s is already used by %s." % (folder, seen[key]))
			seen[key] = folder
			jobs.append({
				"model": row.path,
				"folder": folder,
				"model_id": model_id,
				"head_only": head_only,
				"anime_shader": bool(row.anime_shader.get()),
			})
		if not jobs:
			raise BootIdError("Add a model and an ID first.")
		return jobs

	def make_team(self):
		if self.running:
			return
		if not self.team_path or not os.path.isdir(self.team_path):
			self.status.set("Choose the team folder first.")
			return
		blender = find_blender()
		if not blender:
			self.status.set("Choose blender.exe. The line under this message opens the file picker.")
			self.pick_blender()
			blender = find_blender()
			if not blender:
				return
		script = worker_path()
		if not os.path.isfile(script):
			self.status.set("blender_worker.py is missing next to the program.")
			return
		try:
			jobs = self._jobs()
		except BootIdError as error:
			self.status.set(str(error))
			return

		self.running = True
		self.cancel = False
		self.make.configure(state="disabled", bg="#9e9e9e")
		self.progress["value"] = 0
		self.status.set("Checking Blender, FMDL Tools, and PES FMDL.")
		self._log("——")
		thread = threading.Thread(
			target=self._run,
			args=(blender, script, jobs),
			daemon=True,
		)
		thread.start()

	def _run(self, blender, script, jobs):
		made = []
		failed = []
		try:
			self._preflight(blender, script)
			if self.cancel:
				return
			total = len(jobs)
			for index, job in enumerate(jobs):
				if self.cancel:
					break
				self._one(blender, script, job, index, total, made, failed)
		except Exception as error:
			failed.append(("", str(error)))
			self.events.put(("log", str(error)))
		finally:
			self.events.put(("finished", (made, failed)))

	def _preflight(self, blender, script):
		self.events.put(("status", "Checking Blender, FMDL Tools, and PES FMDL."))
		self.events.put(("progress", 0.02))

		def on_line(text):
			self._note_line(text, 0, 1, preflight=True)

		ok, detail = run_blender(blender, script, {"check": True}, on_line, lambda: self.cancel, self.proc_slot)
		if self.cancel:
			return
		if not ok:
			raise RuntimeError(detail or "Blender, FMDL Tools, or PES FMDL is not ready.")

	def _one(self, blender, script, job, index, total, made, failed):
		root = tempfile.mkdtemp(prefix="alntm-")
		out = os.path.join(root, "out")
		work = os.path.join(root, "work")
		payload = {
			"model": job["model"],
			"model_id": job["model_id"],
			"output_dir": out,
			"work_dir": work,
			"head_only": bool(job.get("head_only")),
			"anime_shader": bool(job.get("anime_shader")),
		}
		label = "%d/%d  %s" % (index + 1, total, job["folder"])
		self.events.put(("status", label))
		self.events.put(("log", label + "  " + job["model"]))

		def on_line(text):
			self._note_line(text, index, total, preflight=False)

		try:
			ok, detail = run_blender(
				blender, script, payload, on_line, lambda: self.cancel, self.proc_slot
			)
			if self.cancel:
				return
			if not ok:
				failed.append((job["folder"], detail))
				self.events.put(("log", "Failed %s: %s" % (job["folder"], detail)))
				return
			kind = "Faces" if job.get("head_only") else "Boots"
			final = os.path.join(self.team_path, kind, job["folder"])
			os.makedirs(os.path.dirname(final), exist_ok=True)
			if os.path.isdir(final):
				shutil.rmtree(final)
			shutil.move(out, final)
			made.append(final)
			self.events.put(("log", "Wrote %s" % final))
			self.events.put(("progress", (index + 1) / total))
		finally:
			shutil.rmtree(root, ignore_errors=True)

	def _note_line(self, text, index, total, preflight):
		self.events.put(("log", text))
		if not text.startswith("ALNTM\t"):
			return
		parts = text.split("\t", 2)
		if len(parts) < 3:
			return
		kind, message = parts[1], parts[2]
		if kind == "STEP" and not preflight:
			fraction_text, _, detail = message.partition(" ")
			try:
				fraction = float(fraction_text)
			except ValueError:
				fraction = 0.0
			self.events.put(("progress", (index + fraction) / total))
			self.events.put(("status", "%d/%d  %s" % (index + 1, total, detail)))
		elif kind in ("INFO", "ERROR"):
			self.events.put(("status", message))

	def _drain(self):
		if not self.root.winfo_exists():
			return
		try:
			while True:
				kind, payload = self.events.get_nowait()
				if kind == "log":
					self._log(payload)
				elif kind == "status":
					self.status.set(payload)
				elif kind == "progress":
					self.progress["value"] = max(0, min(1000, int(payload * 1000)))
				elif kind == "finished":
					self._finished(*payload)
		except queue.Empty:
			pass
		if self.root.winfo_exists():
			self.root.after(100, self._drain)

	def _finished(self, made, failed):
		self.running = False
		self.make.configure(state="normal", bg=_GREEN)
		if self.cancel:
			self.status.set("Stopped.")
			return
		if failed and made:
			self.status.set("Made %d. %d failed. %s" % (len(made), len(failed), failed[0][1]))
		elif failed:
			self.status.set(failed[0][1])
		else:
			self.progress["value"] = 1000
			faces = [path for path in made if os.path.basename(os.path.dirname(path)) == "Faces"]
			boots = [path for path in made if path not in faces]
			if faces and boots:
				self.status.set("Made %d boots and %d heads." % (len(boots), len(faces)))
			elif faces:
				word = "folder" if len(faces) == 1 else "folders"
				self.status.set("Made %d head %s." % (len(faces), word))
			else:
				word = "folder" if len(boots) == 1 else "folders"
				self.status.set("Made %d boots %s." % (len(boots), word))

	def close(self):
		self.cancel = True
		proc = self.proc_slot[0] if self.proc_slot else None
		if proc is not None and proc.poll() is None:
			subprocess_kill(proc)
		self.root.destroy()

	def run(self):
		self.root.mainloop()


def subprocess_kill(proc):
	import subprocess
	subprocess.run(
		["taskkill", "/F", "/T", "/PID", str(proc.pid)],
		capture_output=True,
		text=True,
	)


def main():
	App().run()
