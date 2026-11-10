"""Drive an *interactive* Claude Code TUI through a pty against capture_server.py.

No credentials, no network: HOME is a throwaway dir (onboarding and trust pre-accepted)
and ANTHROPIC_BASE_URL points at 127.0.0.1.

Usage: python3 drive.py <claude-bin> <workdir> <out.jsonl> <env-profile> [script]
  env-profile: handover | minimal | minimal-nosmall
  script:      session (default) | resume | extra (early interrupt, then a Bash `cd`)
"""
import json
import os
import pty
import select
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BIN, WORK, OUT, PROFILE = (os.path.abspath(a) if i < 3 else a for i, a in enumerate(sys.argv[1:5]))
SCRIPT = sys.argv[5] if len(sys.argv) > 5 else "session"
PORT = int(os.environ.get("PORT", "4197"))

PROFILES = {
    # docs/HANDOVER.md next-step 1, verbatim
    "handover": {
        "ANTHROPIC_MODEL": "claude-opus-5.5",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5.5",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5.5",
        "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-4.5",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4.5",
        "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    },
    # README quick start + a main model
    "minimal": {"ANTHROPIC_MODEL": "claude-opus-5.5"},
    # helper slots mapped, but non-essential calls left on
    "mapped-essential-on": {
        "ANTHROPIC_MODEL": "claude-opus-5.5",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5.5",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5.5",
        "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-4.5",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4.5",
    },
}


def mark(label):
    with open(OUT, "a") as f:
        f.write(json.dumps({"t": time.time(), "step": label}) + "\n")


def main():
    proj = os.path.join(WORK, "proj")
    home = os.path.join(WORK, "home")
    if SCRIPT == "session":
        subprocess.run(["rm", "-rf", WORK], check=True)
        os.makedirs(os.path.join(proj, "sub"))
        os.makedirs(home)
        with open(os.path.join(proj, "notes.txt"), "w") as f:
            f.write("alpha\nbeta\ngamma\n")
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        open(OUT, "w").close()
        with open(os.path.join(home, ".claude.json"), "w") as f:
            json.dump({"hasCompletedOnboarding": True, "theme": "dark", "numStartups": 5,
                       "projects": {proj: {"hasTrustDialogAccepted": True,
                                           "hasCompletedProjectOnboarding": True}}}, f)
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "capture_server.py"), str(PORT), OUT])
    time.sleep(0.5)
    env = {"PATH": os.environ["PATH"], "HOME": home, "TERM": "xterm-256color", "COLUMNS": "120",
           "LINES": "40", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{PORT}",
           "ANTHROPIC_AUTH_TOKEN": "dummy", "DISABLE_TELEMETRY": "1", "DISABLE_AUTOUPDATER": "1",
           "DISABLE_ERROR_REPORTING": "1", **PROFILES[PROFILE]}
    args = [BIN, "--permission-mode", "acceptEdits"] + (["--continue"] if SCRIPT != "session" else [])
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(proj)
        os.execve(BIN, args, env)
    screen = open(os.path.join(WORK, f"screen-{SCRIPT}.log"), "ab")

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                screen.write(data)

    def typ(text, wait):
        mark(f"type {text!r}")
        for ch in text:
            os.write(fd, ch.encode())
            pump(0.02)
        pump(0.3)
        os.write(fd, b"\r")
        pump(wait)

    pump(8)
    if SCRIPT == "session":
        typ("hello there", 8)
        typ(f"READ_FILE:{proj}/notes.txt please", 10)
        typ("AGENT: explore the repo", 14)
        typ("SLOW write a long story", 4)
        mark("ESC")
        os.write(fd, b"\x1b")
        pump(5)
        typ("after interrupt", 8)
        typ("SLOW another long one", 4)
        mark("ESC2")
        os.write(fd, b"\x1b")
        pump(4)
    elif SCRIPT == "extra":
        typ("DELAY think first", 1.5)
        mark("ESC-early")
        os.write(fd, b"\x1b")
        pump(7)
        typ("after early interrupt", 8)
        typ(f"BASH:cd {proj}/sub", 10)
        typ("after cd", 8)
    else:
        typ("after resume", 10)
    mark("exit")
    os.write(fd, b"\x03")
    pump(1)
    os.write(fd, b"\x03")
    pump(3)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    srv.terminate()


main()
