"""Start one headless Blender run and stream its log lines."""

import json
import os
import shutil
import subprocess
import tempfile


def _kill(proc):
	if proc.poll() is not None:
		return
	subprocess.run(
		["taskkill", "/F", "/T", "/PID", str(proc.pid)],
		capture_output=True,
		text=True,
	)
	try:
		proc.wait(timeout=10)
	except subprocess.TimeoutExpired:
		proc.kill()


def failure_text(lines, code):
	for text in reversed(lines):
		if text.startswith("ALNTM\tERROR\t"):
			return text.split("\t", 2)[2]
	tail = [text for text in lines if text.strip() and not text.startswith("ALNTM\t")]
	if tail:
		return "Blender failed (%s). %s" % (code, tail[-1])
	return "Blender failed (%s)." % code


def run_blender(blender, script, job, on_line, should_stop, proc_slot):
	"""Run blender_worker.py. Return True when it prints ALNTM DONE."""
	directory = tempfile.mkdtemp(prefix="alntm-job-")
	path = os.path.join(directory, "job.json")
	proc = None
	lines = []
	try:
		with open(path, "w", encoding="utf-8") as handle:
			json.dump(job, handle)
		env = os.environ.copy()
		env["PYTHONUNBUFFERED"] = "1"
		proc = subprocess.Popen(
			[blender, "--background", "--python", script, "--", path],
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			stdin=subprocess.DEVNULL,
			text=True,
			encoding="utf-8",
			errors="replace",
			bufsize=1,
			env=env,
			creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
		)
		proc_slot[:] = [proc]
		stream = proc.stdout
		if stream is not None:
			for line in stream:
				text = line.rstrip("\r\n")
				lines.append(text)
				on_line(text)
				if should_stop():
					_kill(proc)
					break
		code = proc.wait()
	finally:
		if proc is not None and proc.poll() is None:
			_kill(proc)
		proc_slot[:] = []
		shutil.rmtree(directory, ignore_errors=True)
	if should_stop():
		return False, "Stopped."
	done = any(text.startswith("ALNTM\tDONE\t") for text in lines)
	if code == 0 and done:
		return True, ""
	return False, failure_text(lines, code)
