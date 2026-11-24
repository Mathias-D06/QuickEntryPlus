"""Fails if Quick Entry Plus could call a paid API. It must only use the CLIs on your own
account sign-in (Claude Code) and local transcription (Whisper):
- no API SDK or raw HTTP client imported anywhere in the app's code (top level or in a function),
- no API key variable read: the key names may only appear in claude_cli.KEY_VARIABLES, the list
  of variables stripped before a CLI starts,
- no SDK in the requirements or installed in the venv, and none loaded when the app is imported,
- no key reaches Claude Code's environment, even when one is set.
Run: .venv\\Scripts\\python tests\\check_no_api.py
"""

import ast
import glob
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CODE = (glob.glob(os.path.join(ROOT, "qeplus", "*.py")) + [os.path.join(ROOT, "run.py")]
        + glob.glob(os.path.join(ROOT, "packaging", "*.py")))
SDKS = ("google.genai", "google.generativeai", "google.ai", "google.cloud.aiplatform",
        "vertexai", "anthropic", "openai", "litellm", "cohere", "mistralai", "groq")
HTTP = ("requests", "httpx", "httpx2", "aiohttp", "urllib3", "urllib.request", "http.client")
PIP_NAMES = ("google-genai", "google-generativeai", "google-cloud-aiplatform", "anthropic",
             "openai", "litellm", "cohere", "mistralai", "groq")
KEY_TEXT = re.compile(r"API_KEY|AUTH_TOKEN")


def banned(module: str) -> bool:
    return any(module == m or module.startswith(m + ".") for m in SDKS + HTTP)


def problems(source: str, name: str) -> list[str]:
    """Every banned import, dynamic import, key name or api_key identifier in one file."""
    tree = ast.parse(source, name)
    allowed = set()  # the strings inside KEY_VARIABLES / KEY_SUFFIXES = (...) in claude_cli.py
    if os.path.basename(name) == "claude_cli.py":
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in ("KEY_VARIABLES", "KEY_SUFFIXES")
                    for t in node.targets):
                allowed |= {id(c) for c in ast.walk(node.value) if isinstance(c, ast.Constant)}
    found = []
    for node in ast.walk(tree):
        where = f"{os.path.relpath(name, ROOT)}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            found += [f"{where} imports {a.name}" for a in node.names if banned(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            found += [f"{where} imports {m}" for m in mods if banned(m)][:1]
        elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
              and getattr(node.func, "attr", getattr(node.func, "id", "")) in
              ("import_module", "__import__") and banned(str(node.args[0].value))):
            found.append(f"{where} imports {node.args[0].value} dynamically")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and KEY_TEXT.search(node.value) and id(node) not in allowed):
            found.append(f"{where} mentions {KEY_TEXT.search(node.value).group()}: "
                         f"{node.value[:60]!r}")
        elif isinstance(node, (ast.Name, ast.Attribute, ast.arg, ast.keyword, ast.FunctionDef)):
            ident = getattr(node, "id", None) or getattr(node, "attr", None) or getattr(
                node, "arg", None) or getattr(node, "name", None) or ""
            if "api_key" in ident.lower():
                found.append(f"{where} uses {ident}")
    return found


# 0. the checker catches what it should (so a pass below means something)
planted = ("import anthropic\nfrom google import genai\nimport os, importlib\n"
           "k = os.environ.get('GEMINI_API_KEY')\ndef api_key(): pass\n"
           "importlib.import_module('openai')\nimport requests\n")
caught = problems(planted, "planted.py")
assert len(caught) == 6, caught  # (`import os, importlib` is fine)
assert problems("KEY_VARIABLES = ('GEMINI_API_KEY',)\n", "elsewhere.py"), \
    "the allowed list only counts in claude_cli.py"

# 1. the app's code: no SDK, no HTTP client, no key read
found = []
for path in CODE:
    with open(path, encoding="utf-8") as f:
        found += problems(f.read(), path)
assert not found, "\n".join(found)

# 2. requirements and the venv carry no SDK
for path in glob.glob(os.path.join(ROOT, "requirements*.txt")):
    with open(path, encoding="utf-8") as f:
        names = {re.split(r"[\s=<>!~\[;]", line.strip())[0].lower()
                 for line in f if line.strip() and not line.startswith(("#", "-"))}
    assert not names & set(PIP_NAMES), (path, names & set(PIP_NAMES))


def installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:  # its parent package (google, say) isn't there either
        return False


assert not [m for m in SDKS if installed(m)], [m for m in SDKS if installed(m)]

# 3. importing the whole app loads no SDK
from qeplus import app, claude_cli  # noqa: E402

assert app.App and not [m for m in sys.modules if any(m == s or m.startswith(s + ".")
                                                     for s in SDKS)]

# 4. keys and cloud credentials set on this PC never reach Claude Code (its answers and its
# sign-in check) or agy (Gemini)
fake = {"ANTHROPIC_API_KEY": "sk-ant-fake", "ANTHROPIC_AUTH_TOKEN": "t", "GEMINI_API_KEY": "g",
        "GOOGLE_API_KEY": "k", "OPENAI_API_KEY": "o", "SOME_SERVICE_AUTH_TOKEN": "s",
        "GOOGLE_APPLICATION_CREDENTIALS": "c.json", "GOOGLE_CLOUD_PROJECT": "p",
        "GOOGLE_GENAI_USE_VERTEXAI": "1", "VERTEX_LOCATION": "eu", "GEMINI_MODEL": "m"}
os.environ.update(fake)
assert set(claude_cli.KEY_VARIABLES) <= set(fake)
envs = []


class Stop(OSError):
    pass


def fake_popen(args, **kw):
    envs.append(kw["env"])
    raise Stop("not really starting it")


def fake_run(args, **kw):
    envs.append(kw["env"])
    return type("Done", (), {"stdout": '{"loggedIn": true, "authMethod": "claude.ai"}'})()


real_find, real_popen, real_run = (claude_cli.find_cli, claude_cli.subprocess.Popen,
                                   claude_cli.subprocess.run)
claude_cli.find_cli = lambda: "claude.exe"
claude_cli.subprocess.Popen, claude_cli.subprocess.run = fake_popen, fake_run
from PySide6.QtCore import QCoreApplication  # noqa: E402

qt = QCoreApplication([])
session = claude_cli.ClaudeSession()
session.start()  # the answer card's process
claude_cli.auth_status("claude.exe")  # the sign-in check
from qeplus import agy_cli  # noqa: E402

real_agy = agy_cli.find_agy
agy_cli.find_agy = lambda: "agy.exe"
agy_cli.AgySession().start()  # Gemini's process (agy_cli shares the subprocess module)
agy_cli.find_agy = real_agy
claude_cli.find_cli, claude_cli.subprocess.Popen, claude_cli.subprocess.run = (
    real_find, real_popen, real_run)
for key in fake:
    os.environ.pop(key)
assert len(envs) == 3, envs
for env in envs:
    assert not {k.upper() for k in env} & set(fake), sorted(env)

# 5. only an account sign-in counts: anything else blocks the card, with a warning
ok = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
assert claude_cli.account_problem(ok, "c") == ""
for status in ({"loggedIn": False}, {**ok, "authMethod": "console"},
               {**ok, "authMethod": "api_key"}, {**ok, "apiProvider": "bedrock"}):
    assert claude_cli.account_problem(status, "c"), status
errors = []
session.failed.connect(errors.append)
session._set_problem("Claude Code is signed in with console (firstParty), not your account")
session.start()
assert errors and "not your account" in errors[-1] and session.proc is None

print("no paid APIs: all checks passed")
