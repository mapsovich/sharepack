"""sharepack: nickname + pack code -> download the game and mods -> launch.

The UI lives in ui/ (pywebview), the logic is here. Settings and profiles are kept in %APPDATA%\\sharepack.
"""
import contextlib
import ctypes
import glob
import hashlib
import hmac
import json
import math
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

import minecraft_launcher_lib as mll
import requests
import webview

import i18n
from i18n import t

TITLE = "sharepack"
VERSION = "v2026.b3"
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, same as the server
SECRET_BYTES = 12
LINKS = {"github": "https://github.com/mapsovich", "m": "https://mstudio.one/game"}
RELEASES_API = "https://api.github.com/repos/mapsovich/sharepack/releases/latest"
RELEASES_PAGE = "https://github.com/mapsovich/sharepack/releases/latest"
RELEASES_DOWNLOAD = "https://github.com/mapsovich/sharepack/releases/download/"

# former launcher names, their data is moved by migrate_old_data
OLD_NAMES = ("joinpack", "minejoin")  # newest first
EXE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
# in the exe, ui/ is unpacked to the PyInstaller temp folder
UI_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "ui")
APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
HOME_DIR = os.environ.get("SHAREPACK_HOME") or os.path.join(APPDATA, "sharepack")
OLD_HOME_DIRS = [os.path.join(APPDATA, name) for name in OLD_NAMES]

# minecraft-launcher-lib sets no timeouts, so a stalled connection used to hang the download forever.
# With a timeout it becomes a regular network error, which install_game retries. (connect, read) in seconds
NET_TIMEOUT = (10, 30)
_session_request = requests.Session.request


def _request_with_timeout(self, method, url, **kwargs):
    kwargs.setdefault("timeout", NET_TIMEOUT)
    return _session_request(self, method, url, **kwargs)


requests.Session.request = _request_with_timeout
STALL_WARNING_SECONDS = 25  # warn after this many seconds without progress
DEFAULT_DATA_DIR = os.path.join(HOME_DIR, "profiles")
SETTINGS_PATH = os.path.join(HOME_DIR, "launcher.json")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# kept on redownload
KEEP_ON_REDOWNLOAD = {"saves", "screenshots", "options.txt"}
# managed_dirs can't point here: player data and the installed game
PROTECTED_DIRS = {"saves", "screenshots", "options.txt", "versions", "libraries", "assets", "runtime", "logs",
                  "crash-reports"}
MAX_NICKS = 10

# share of each step in the progress bar; assets take the longest, pack files are the last 15%
PROGRESS_VANILLA = {"Download Libraries": (0.0, 0.15), "Download Assets": (0.15, 0.55),
                    "Install java runtime": (0.55, 0.70)}
PROGRESS_LOADER = (0.70, 0.85)

WINDOW_SIZE = (426, 360)
CONSOLE_HIDDEN_HEIGHT = 295  # window height with the console collapsed
SPLASH_SIZE = (426, 119)
UPDATE_SIZE = (426, 170)


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def migrate_old_data():
    """One-time move of data from %APPDATA%\\joinpack, %APPDATA%\\minejoin or the folder next to the exe.
    If the profiles can't be moved (another drive, files in use), they stay in place
    and the old folder is written to the settings."""
    if os.path.isfile(SETTINGS_PATH):
        return
    sources = [(os.path.join(d, "launcher.json"), os.path.join(d, "profiles")) for d in OLD_HOME_DIRS]
    sources.append((os.path.join(EXE_DIR, "launcher.json"), os.path.join(EXE_DIR, "data")))
    for old_settings, old_data in sources:
        if os.path.isfile(old_settings):
            _migrate(old_settings, old_data)
            break
    for d in OLD_HOME_DIRS:
        try:
            os.rmdir(d)  # only if empty
        except OSError:
            pass


def _migrate(old_settings, old_data):
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        os.replace(old_settings, SETTINGS_PATH)
    except OSError:
        return
    if not os.path.isdir(old_data):
        return
    settings = load_settings()
    user = settings.setdefault("user", {})
    if user.get("profiles_dir"):
        return
    try:
        if os.path.exists(DEFAULT_DATA_DIR):
            raise OSError("profiles already exist")
        os.replace(old_data, DEFAULT_DATA_DIR)
    except OSError:
        user["profiles_dir"] = old_data
        save_settings(settings)


def version_key(tag):
    """v2026.b2 -> [2026, 2]"""
    return [int(n) for n in re.findall(r"\d+", tag)]


def check_update():
    """Latest GitHub release if it is newer: {"version", "url", "asset"} or None.
    SHAREPACK_FAKE_UPDATE=v2026.b2 shows the update screen for testing."""
    fake = os.environ.get("SHAREPACK_FAKE_UPDATE")
    if fake:
        return {"version": fake, "url": RELEASES_PAGE, "asset": None}
    try:
        r = requests.get(RELEASES_API, timeout=(3, 5), headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        release = r.json()
    except (requests.RequestException, ValueError):
        return None
    tag = str(release.get("tag_name") or "")
    if not tag or version_key(tag) <= version_key(VERSION):
        return None
    asset = next((a.get("browser_download_url") for a in release.get("assets", []) if a.get("name") == "sharepack.exe"),
                 None)
    if not str(asset).startswith(RELEASES_DOWNLOAD):
        asset = None
    return {"version": tag, "url": release.get("html_url") or RELEASES_PAGE, "asset": asset}


def replace_exe(url):
    """Downloads the new exe next to the running one and swaps them. Windows allows renaming a running exe,
    so the old one becomes sharepack.exe.old and is deleted on the next start."""
    exe = sys.executable
    new, old = exe + ".new", exe + ".old"
    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(new, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    if os.path.getsize(new) < 1 << 20:
        os.remove(new)
        raise OSError("downloaded file is too small")
    if os.path.exists(old):
        os.remove(old)
    os.replace(exe, old)
    os.replace(new, exe)
    return exe


def remove_old_exe():
    if getattr(sys, "frozen", False):
        for suffix in (".old", ".new"):
            try:
                os.remove(sys.executable + suffix)
            except OSError:
                pass


GPU_PREFS_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"


def prefer_discrete_gpu(exe):
    """On laptops Windows runs java on the integrated GPU by default. Sets "High performance"
    (same as Settings > Display > Graphics) unless something is already chosen for this file."""
    if os.name != "nt" or not os.path.isabs(exe) or not os.path.isfile(exe):
        return
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, GPU_PREFS_KEY) as k:
            try:
                winreg.QueryValueEx(k, exe)
            except FileNotFoundError:
                winreg.SetValueEx(k, exe, 0, winreg.REG_SZ, "GpuPreference=2;")
    except OSError:
        pass


def open_path(path):
    os.makedirs(path, exist_ok=True)
    os.startfile(path)


# network errors in the Forge/NeoForge installer output
NETWORK_ERROR_RE = re.compile(r"SSLException|SSLHandshake|SocketException|SocketTimeout|ConnectException|"
                              r"UnknownHost|Connection reset|timed out|NoRouteToHost|EOFException|"
                              r"Server returned HTTP response code: 5\d\d", re.I)


def installer_failure(e):
    """(step, error line) from the output of a failed installer."""
    cmd = list(e.cmd) if isinstance(e.cmd, (list, tuple)) else [str(e.cmd)]
    task = cmd[cmd.index("--task") + 1] if "--task" in cmd[:-1] else (cmd[3] if len(cmd) > 3 else "?")
    lines = [l.strip() for l in (e.stderr or b"").decode(errors="replace").splitlines() if l.strip()]
    error = next((l for l in lines if not l.startswith("at ") and not l.startswith("...")), "") or t("exit_code", code=e.returncode)
    return task, error[:300]


class LauncherError(Exception):
    """Error shown to the player: short goes to the button, detail to the console."""

    def __init__(self, short, detail):
        super().__init__(detail)
        self.problem = {"short": short, "detail": detail}


def derive_key(label, secret):
    return hashlib.sha256(label + secret).digest()


def decode_code(code):
    """Code -> (host, port, access key, signing key) or None.
    The secret from the code never leaves this computer: the server is asked by the access key, and the manifest
    is checked with the signing key (see Code.java on the server side)."""
    s = code.upper().replace("-", "").replace(" ", "").replace("O", "0").replace("I", "1").replace("L", "1")
    if not s or any(c not in CODE_ALPHABET for c in s):
        return None
    bits = "".join(format(CODE_ALPHABET.index(c), "05b") for c in s)
    data = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - 7, 8))
    try:
        if data[0] == 1:
            host, rest = ".".join(str(b) for b in data[1:5]), data[5:]
        elif data[0] == 2:
            n = data[1]
            host, rest = data[2:2 + n].decode("ascii"), data[2 + n:]
        else:
            return None
        n = 2 + SECRET_BYTES
        if len(rest) < n + 1:
            return None
        body = data[:len(data) - len(rest) + n]
        checksum = 0
        for b in body:
            checksum = (checksum * 31 + b) & 0xFF
        if checksum != rest[n]:
            return None
        secret = bytes(rest[2:n])
        access = derive_key(b"sharepack/access", secret)[:8].hex()
        return host, (rest[0] << 8) | rest[1], access, derive_key(b"sharepack/sign", secret)
    except (IndexError, UnicodeDecodeError):
        return None


def validate_nick(nick):
    if not (3 <= len(nick) <= 16) or not nick.isascii() or not all(c.isalnum() or c == "_" for c in nick):
        return t("nick_invalid")
    return None


def parse_memory(value):
    """'4096', '4096M', '4G' -> megabytes, or None."""
    v = value.strip().upper().replace(" ", "").rstrip("B")
    try:
        if v.endswith("G"):
            return int(float(v[:-1]) * 1024)
        return int(v.rstrip("M"))
    except ValueError:
        return None


def work_area():
    """(x, y, w, h) of the primary monitor work area in logical pixels."""
    try:
        user32 = ctypes.windll.user32
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        rect = (ctypes.c_long * 4)()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        scale = user32.GetDpiForSystem() / 96
        return rect[0] / scale, rect[1] / scale, (rect[2] - rect[0]) / scale, (rect[3] - rect[1]) / scale
    except (AttributeError, OSError):
        return None


def ui_zoom():
    """Window scale. The 426x360 mockup is small, so it is scaled to about half the screen height:
    1080p x1.5, 1440p x2, 4K x3. Windows display scaling is already accounted for."""
    area = work_area()
    if not area:
        return 1.5
    screen_height = area[3] + 48  # + taskbar
    return max(1.0, min(3.0, math.floor(screen_height / 720 * 4) / 4))


def enable_minimize_animation(title):
    """A frameless window has no minimize animation and can't be minimized from the taskbar.
    Restores WS_MINIMIZEBOX and WS_SYSMENU; this doesn't bring the frame back."""
    try:
        user32 = ctypes.windll.user32
        pid = os.getpid()
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def on_window(hwnd, _):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 256)
            if owner.value == pid and buf.value == title:
                found.append(hwnd)
            return True

        user32.EnumWindows(on_window, 0)
        GWL_STYLE, WS_MINIMIZEBOX, WS_SYSMENU = -16, 0x00020000, 0x00080000
        for hwnd in found:
            style = user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_STYLE)
            user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_STYLE, style | WS_MINIMIZEBOX | WS_SYSMENU)
        return bool(found)
    except (AttributeError, OSError):
        return False


def total_ram_mb():
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        s = MemoryStatus()
        s.dwLength = ctypes.sizeof(s)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return s.ullTotalPhys // (1024 * 1024)
    except (AttributeError, OSError):
        return 8192


def java_info(java_exe):
    """Java version and vendor from the release file, or from java -version."""
    home = os.path.dirname(os.path.dirname(java_exe))
    version = vendor = ""
    try:
        with open(os.path.join(home, "release"), encoding="utf-8", errors="replace") as f:
            for line in f:
                key, _, value = line.partition("=")
                value = value.strip().strip('"')
                if key == "JAVA_VERSION":
                    version = value
                elif key == "IMPLEMENTOR":
                    vendor = value
    except OSError:
        pass
    if not version:
        try:
            out = subprocess.run([java_exe, "-version"], capture_output=True, text=True, timeout=5,
                                 creationflags=NO_WINDOW).stderr
            m = re.search(r'version "([^"]+)"', out)
            version = m.group(1) if m else ""
        except (OSError, subprocess.SubprocessError):
            return None
    if not version:
        return None
    major = version.split(".")[1] if version.startswith("1.") else version.split(".")[0].split("+")[0].split("-")[0]
    label = f"Java {major}" + (f" ({vendor})" if vendor else "")
    if "\\.minecraft\\runtime\\" in java_exe.lower() or "\\minecraft launcher\\" in java_exe.lower():
        label = t("java_from_launcher", major=major)
    elif not vendor and "java-runtime" in java_exe.lower():
        label = f"Java {major} (Mojang)"
    return {"path": java_exe, "version": version, "major": int(major) if major.isdigit() else 0, "label": label}


def find_javas():
    """Looks for Java in JAVA_HOME, PATH, Program Files, the registry and other launchers."""
    env = os.environ.get
    pf = [p for p in {env("ProgramFiles"), env("ProgramFiles(x86)"), env("ProgramW6432")} if p]
    patterns = []
    if env("JAVA_HOME"):
        patterns.append(os.path.join(env("JAVA_HOME"), "bin", "java.exe"))
    for d in (env("PATH") or "").split(os.pathsep):
        if d:
            patterns.append(os.path.join(d, "java.exe"))
    for p in pf:
        for vendor in ("Java", "Eclipse Adoptium", "Eclipse Foundation", "AdoptOpenJDK", "Microsoft", "Zulu",
                       "BellSoft", "Amazon Corretto", "Semeru", "OpenJDK", "GraalVM", "Liberica"):
            patterns.append(os.path.join(p, vendor, "*", "bin", "java.exe"))
        patterns.append(os.path.join(p, "Minecraft Launcher", "runtime", "*", "windows-x64", "*", "bin", "java.exe"))
    if env("APPDATA"):
        patterns.append(os.path.join(env("APPDATA"), ".minecraft", "runtime", "*", "windows-x64", "*", "bin", "java.exe"))
        patterns.append(os.path.join(env("APPDATA"), "PrismLauncher", "java", "*", "bin", "java.exe"))
    if env("USERPROFILE"):
        patterns.append(os.path.join(env("USERPROFILE"), ".jdks", "*", "bin", "java.exe"))

    paths = []
    for pattern in patterns:
        paths.extend(glob.glob(pattern))
    paths.extend(_java_from_registry())

    found, seen = [], set()
    for path in paths:
        real = os.path.normcase(os.path.realpath(path))
        # javapath holds Oracle shims; the real Java is in Program Files
        if real in seen or not os.path.isfile(real) or "javapath" in real:
            continue
        seen.add(real)
        info = java_info(os.path.realpath(path))
        if info:
            found.append(info)
    found.sort(key=lambda j: -j["major"])
    return found


def _java_from_registry():
    try:
        import winreg
    except ImportError:
        return []
    result = []
    roots = [r"SOFTWARE\JavaSoft\JDK", r"SOFTWARE\JavaSoft\JRE", r"SOFTWARE\JavaSoft\Java Runtime Environment",
             r"SOFTWARE\JavaSoft\Java Development Kit", r"SOFTWARE\Eclipse Adoptium\JDK", r"SOFTWARE\Eclipse Adoptium\JRE"]
    for root in roots:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    sub = winreg.EnumKey(key, i)
                    for path in (sub, sub + r"\hotspot\MSI"):
                        try:
                            with winreg.OpenKey(key, path) as k:
                                for name in ("JavaHome", "Path"):
                                    try:
                                        home = winreg.QueryValueEx(k, name)[0]
                                        if isinstance(home, str) and home:
                                            result.append(os.path.join(home, "bin", "java.exe"))
                                    except OSError:
                                        pass
                        except OSError:
                            pass
        except OSError:
            pass
    return result


def offline_uuid(name):
    # same as UUID.nameUUIDFromBytes("OfflinePlayer:" + name) on an offline-mode server
    h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    return h.hex()


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_servers_dat(path, name, ip):
    """Writes servers.dat with a single server."""
    def tag_string(key, value):
        k, v = key.encode(), value.encode()
        return b"\x08" + struct.pack(">H", len(k)) + k + struct.pack(">H", len(v)) + v

    entry = tag_string("name", name) + tag_string("ip", ip) + b"\x00"
    servers = b"\x09" + struct.pack(">H", 7) + b"servers" + b"\x0a" + struct.pack(">i", 1) + entry
    with open(path, "wb") as f:
        f.write(b"\x0a\x00\x00" + servers + b"\x00")


class Installer:
    def __init__(self, host, port, key, signing_key, data_dir, status, progress, java_path=None):
        self.host = host
        self.signing_key = signing_key
        self.status = status
        self.progress = progress
        self.java_path = java_path
        # folder per key, not per code, so a new owner IP keeps the downloaded files
        self.game_dir = os.path.join(data_dir, key)
        self.pack_url = f"http://{host}:{port}/{key}"

    def callback(self, span=None):
        """Callback for minecraft-launcher-lib. The library reports progress per step; this maps it to one bar
        (PROGRESS_*). span=None: vanilla, steps are known by name. span=(from, to): mod loader, the number of
        steps is unknown, so each new setMax takes half of the remaining space."""
        state = {"max": 1, "range": PROGRESS_VANILLA["Download Libraries"], "steps": 0}

        def set_status(s):
            # the library reports every file; only step changes are shown
            if s.startswith("Install java runtime"):
                self.status(t("installing_java"))
            if span is None and s in PROGRESS_VANILLA:
                state["range"] = PROGRESS_VANILLA[s]

        def set_max(m):
            state["max"] = max(m, 1)
            if span is not None:
                lo, hi = span
                left = 0.5 ** state["steps"]
                state["range"] = (hi - (hi - lo) * left, hi - (hi - lo) * left / 2)
                state["steps"] += 1

        def set_progress(p):
            lo, hi = state["range"]
            self.progress(lo + (hi - lo) * min(p / state["max"], 1))

        return {"setStatus": set_status, "setMax": set_max, "setProgress": set_progress}

    def fetch_manifest(self, timeout=15):
        r = requests.get(f"{self.pack_url}/manifest.json", timeout=timeout)
        if r.status_code == 404:
            raise LauncherError(t("e_code_not_found"), t("d_code_not_found"))
        if r.status_code >= 500:
            raise LauncherError(t("e_pack"), t("d_pack_server_failed"))
        r.raise_for_status()
        # Plain HTTP can be modified on the way. The signature is made with a key only the code holder knows.
        expected = hmac.new(self.signing_key, r.content, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, r.headers.get("X-Sharepack-Signature", "").lower()):
            raise LauncherError(t("e_tampered"), t("d_bad_signature"))
        try:
            data = r.json()
            if not isinstance(data.get("pack"), dict):
                raise ValueError
        except ValueError:
            raise LauncherError(t("e_pack"), t("d_pack_bad_json"))
        m = data["pack"]
        # pack.json may say "Forge" or " fabric "
        m["loader"] = str(m.get("loader") or "vanilla").strip().lower()
        for k in ("minecraft", "loader_version"):
            if isinstance(m.get(k), str):
                m[k] = m[k].strip()
        m["files"] = data["files"]
        m.setdefault("server", f"{self.host}:{data.get('server_port', 25565)}")
        return m

    def install_game(self, m, max_stalls=3):
        """Installs the game, retrying on network errors (downloaded files are verified by hash and not fetched
        again). Gives up after several attempts in a row without progress."""
        stalls, attempt = 0, 1
        while True:
            before = self._downloaded_files()
            try:
                return self._install_game(m)
            except (requests.RequestException, subprocess.CalledProcessError) as e:
                if isinstance(e, subprocess.CalledProcessError):
                    # the Forge/NeoForge installer downloads too: retry network errors, report the rest
                    task, error = installer_failure(e)
                    if not NETWORK_ERROR_RE.search(error):
                        loader = LOADER_NAMES.get(str(m.get("loader")).lower(), t("loader_fallback"))
                        raise LauncherError(t("e_loader_install", loader=loader.lower()),
                                            t("d_loader_install", loader=loader, task=task, error=error)) from e
                stalls = 0 if self._downloaded_files() > before else stalls + 1
                if stalls >= max_stalls:
                    url = getattr(getattr(e, "request", None), "url", "") or ""
                    host = urllib.parse.urlparse(url).hostname or t("mojang_servers")
                    raise LauncherError(t("e_download"), t("d_download", host=host)) from e
                attempt += 1
                self.status(t("net_retry", attempt=attempt))
                time.sleep(min(2 ** stalls, 8))

    def _downloaded_files(self):
        return sum(len(names) for _, _, names in os.walk(self.game_dir))

    def _install_game(self, m):
        mc = m["minecraft"]
        loader = m.get("loader", "vanilla")
        os.makedirs(self.game_dir, exist_ok=True)
        self.status(t("installing_mc", mc=mc))
        # vanilla first; it also brings the right Java
        mll.install.install_minecraft_version(mc, self.game_dir, callback=self.callback())
        if loader == "vanilla":
            self.progress(PROGRESS_LOADER[1])
            return mc
        java = self.java_path
        if not java:
            version_json = os.path.join(self.game_dir, "versions", mc, f"{mc}.json")
            with open(version_json, encoding="utf-8") as f:
                component = json.load(f).get("javaVersion", {}).get("component", "java-runtime-gamma")
            java = mll.runtime.get_executable_path(component, self.game_dir) or "java"

        ml = mll.mod_loader.get_mod_loader(loader)
        loader_version = m.get("loader_version") or ml.get_latest_loader_version(mc)
        version_id = ml.get_installed_version(mc, loader_version)
        # Forge writes <version>.json before the install is complete, so a marker is written at the very end
        version_dir = os.path.join(self.game_dir, "versions", version_id)
        done_marker = os.path.join(version_dir, ".sharepack-installed")
        # markers from the former launcher names count too
        markers = [done_marker] + [os.path.join(version_dir, f".{name}-installed") for name in OLD_NAMES]
        if not any(os.path.isfile(p) for p in markers):
            self.status(t("installing_loader", loader=loader, version=loader_version))
            ml.install(mc, self.game_dir, loader_version=loader_version, callback=self.callback(PROGRESS_LOADER),
                       java=java)
            with open(done_marker, "w") as f:
                f.write(loader_version)
        self.progress(PROGRESS_LOADER[1])
        return version_id

    def pack_path(self, rel, managed=False):
        """Absolute path for a path from the manifest, or None if it leaves the profile folder.
        The manifest comes from someone else's server, so it must not reach anything outside the profile,
        and managed_dirs must not point at folders the player owns (worlds, screenshots) or at the game itself."""
        if not isinstance(rel, str) or ":" in rel or rel.startswith(("/", "\\")):
            return None
        parts = rel.replace("\\", "/").strip("/").split("/")
        if any(p in ("", ".", "..") for p in parts):
            return None
        if managed and parts[0].lower() in PROTECTED_DIRS:
            return None
        path = os.path.abspath(os.path.join(self.game_dir, *parts))
        base = os.path.abspath(self.game_dir)
        if os.path.commonpath([os.path.normcase(base), os.path.normcase(path)]) != os.path.normcase(base):
            return None
        return path

    def sync_files(self, m):
        files = m.get("files", [])
        wanted = {f["path"] for f in files}
        # remove files in managed_dirs that are not in the pack
        for d in m.get("managed_dirs", []):
            root = self.pack_path(d, managed=True)
            if not root:
                self.status(t("unsafe_path", path=d))
                continue
            for dirpath, _, names in os.walk(root):
                for n in names:
                    rel = os.path.relpath(os.path.join(dirpath, n), self.game_dir).replace("\\", "/")
                    if rel not in wanted:
                        os.remove(os.path.join(dirpath, n))

        todo = []
        for f in files:
            local = self.pack_path(f["path"])
            if not local:
                self.status(t("unsafe_path", path=f["path"]))
                continue
            if f.get("src") not in ("files", "defaults"):
                continue
            if os.path.isfile(local) and (f.get("default") or sha256_of(local) == f.get("sha256")):
                continue
            todo.append((f, local))

        if todo:
            self.status(t("downloading_files", count=len(todo)))
        total = sum(f["size"] for f, _ in todo) or 1
        done = 0
        for f, local in todo:
            os.makedirs(os.path.dirname(local), exist_ok=True)
            url = f"{self.pack_url}/{f['src']}/" + urllib.parse.quote(f["path"])
            h, size = hashlib.sha256(), 0
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(local + ".part", "wb") as out:
                    for chunk in r.iter_content(1 << 16):
                        size += len(chunk)
                        if size > f["size"]:
                            break
                        h.update(chunk)
                        out.write(chunk)
                        done += len(chunk)
                        self.progress(PROGRESS_LOADER[1] + (1 - PROGRESS_LOADER[1]) * done / total)
            # the hash comes from the signed manifest, so a file changed on the way doesn't match it
            if size != f["size"] or h.hexdigest() != f.get("sha256"):
                os.remove(local + ".part")
                raise LauncherError(t("e_tampered"), t("d_bad_file", path=f["path"]))
            os.replace(local + ".part", local)
        self.progress(1)

        if m.get("server") and not os.path.exists(os.path.join(self.game_dir, "servers.dat")):
            write_servers_dat(os.path.join(self.game_dir, "servers.dat"), m.get("name", "Server"), m["server"])

    def build_command(self, m, version_id, nick, memory=None, java_args=""):
        memory = int(memory or m.get("memory", 4096))
        options = {
            "username": nick,
            "uuid": offline_uuid(nick),
            "token": "0",
            "launcherName": TITLE,
            "gameDirectory": self.game_dir,
            "jvmArguments": [f"-Xmx{memory}M", f"-Xms{min(memory, 1024)}M"] + shlex.split(java_args or "", posix=False),
        }
        if self.java_path:
            options["executablePath"] = self.java_path
        return mll.command.get_minecraft_command(version_id, self.game_dir, options)


# ---------------- Pack and launcher checks ----------------

LOADER_NAMES = {"vanilla": "Vanilla", "fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt"}
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
_cache = {}


def cached(key, fn):
    # cache for Mojang and loader responses
    if key not in _cache:
        _cache[key] = fn()
    return _cache[key]


def problem(short, detail):
    return {"short": short, "detail": detail}


def mojang_versions():
    r = requests.get(MOJANG_MANIFEST, timeout=10)
    r.raise_for_status()
    return {v["id"]: v["url"] for v in r.json()["versions"]}


def required_java(mc):
    """Java major version required by Mojang for this Minecraft version."""
    url = cached("mojang", mojang_versions).get(mc)
    if not url:
        return None
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json().get("javaVersion", {}).get("majorVersion", 8)


def check_pack(m):
    """Problems in the owner's pack.json -> (problems, loader version)"""
    mc = str(m.get("minecraft") or "")
    loader = str(m.get("loader") or "vanilla").lower()
    if not mc:
        return [problem(t("e_pack"), t("d_no_mc_version"))], ""
    # "auto" is left only when the server couldn't detect the value
    if mc.lower() == "auto":
        return [problem(t("e_pack"), t("d_mc_auto"))], ""
    if loader == "auto":
        return [problem(t("e_pack"), t("d_loader_auto"))], ""
    if loader not in LOADER_NAMES:
        return [problem(t("e_pack"), t("d_unknown_loader", loader=loader, known=", ".join(LOADER_NAMES)))], ""
    # show the version from pack.json even if it couldn't be verified
    problems, core_version = [], str(m.get("loader_version") or "")
    if m.get("memory") not in (None, "") and not parse_memory(str(m["memory"])):
        problems.append(problem(t("e_pack"), t("d_bad_memory", value=m["memory"])))
    try:
        if mc not in cached("mojang", mojang_versions):
            problems.append(problem(t("e_pack"), t("d_mc_missing", mc=mc)))
        elif loader != "vanilla":
            name = LOADER_NAMES[loader]
            ml = mll.mod_loader.get_mod_loader(loader)
            if not cached(("supported", loader, mc), lambda: ml.is_minecraft_version_supported(mc)):
                problems.append(problem(t("e_pack"), t("d_loader_no_mc", name=name, mc=mc)))
            elif core_version:
                if core_version not in cached(("versions", loader, mc), lambda: ml.get_loader_versions(mc, False)):
                    problems.append(problem(t("e_pack"),
                                            t("d_loader_version_missing", name=name, version=core_version, mc=mc)))
            else:
                core_version = cached(("latest", loader, mc), lambda: ml.get_latest_loader_version(mc))
    except requests.RequestException:
        pass  # offline, skip the check
    return problems, core_version


def check_launcher(m, user):
    """Problems in the launcher settings for this pack."""
    problems = []
    mc = str(m.get("minecraft") or "")
    java = user.get("java_path")
    if java:
        info = java_info(java) if os.path.isfile(java) else None
        if not info:
            problems.append(problem(t("e_java_missing"), t("d_java_missing", java=java)))
        else:
            try:
                need = cached(("java", mc), lambda: required_java(mc))
            except requests.RequestException:
                need = None
            if need and info["major"] and info["major"] < need:
                problems.append(problem(t("e_java_old", need=need),
                                        t("d_java_old", mc=mc, need=need, label=info["label"])))
    folder = user.get("profiles_dir") or DEFAULT_DATA_DIR
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        problems.append(problem(t("e_dir"), t("d_dir", folder=folder, error=e)))
    total = total_ram_mb()
    memory = parse_memory(user["memory"]) if user.get("memory") else parse_memory(str(m.get("memory") or 4096))
    if memory and memory > total - 1024:
        problems.append(problem(t("e_ram"), t("d_ram", memory=memory, total=total)))
    return problems


def pack_info(m, core_version, pack_ok, launcher_ok):
    files = m.get("files", [])
    loader = str(m.get("loader") or "vanilla").lower()
    return {
        "name": m.get("name", ""),
        "version": str(m.get("version") or "—"),
        "files": len(files),
        "mods": sum(1 for f in files if f["path"].startswith("mods/") and f["path"].endswith(".jar")),
        "core": f"{LOADER_NAMES.get(loader, loader)} · MC {m.get('minecraft', '?')}",
        "core_version": core_version or ("—" if loader == "vanilla" else t("core_latest")),
        # the UI renders the status text itself
        "pack_ok": pack_ok,
        "launcher_ok": launcher_ok,
    }


class Api:
    """window.pywebview.api.*"""

    def __init__(self, zoom=1.0):
        self._settings = load_settings()
        i18n.set_lang(self._settings.get("lang") or i18n.system_lang())
        self._window = None
        self._zoom = zoom
        self._started = time.time()
        self._last_progress = 0.0
        self._best_progress = 0.0
        self._moved_at = time.time()
        self._javas = None
        self._in_main = False
        self._update = None

    # ---- window ----

    def show_window(self):
        # The window is created hidden so it doesn't flicker while scaling. The size is set again
        # because WinForms subtracts a frame the window doesn't have.
        self._resize_centered(SPLASH_SIZE)
        self._window.show()
        enable_minimize_animation(TITLE)
        self._started = time.time()
        return True

    def ready(self):
        # During the splash: remove partial downloads, check for updates, preload the Mojang version list.
        threading.Thread(target=self._warm_up, daemon=True).start()
        self._cleanup_parts()
        self._update = check_update()
        time.sleep(max(0.0, 1.0 - (time.time() - self._started)))
        if self._update:
            self._resize_centered(UPDATE_SIZE)
            return {"update": {"version": self._update["version"]}}
        self.enter_main()
        return {"update": None}

    def enter_main(self):
        self._in_main = True
        self._resize_centered(self._main_size())
        return True

    def download_update(self):
        """Replaces the exe and restarts. Without a frozen exe or a release asset, opens the release page."""
        update = self._update or {}
        if getattr(sys, "frozen", False) and update.get("asset"):
            try:
                exe = replace_exe(update["asset"])
                subprocess.Popen([exe], cwd=EXE_DIR, creationflags=subprocess.DETACHED_PROCESS)
                threading.Timer(0.5, self._window.destroy).start()
                return {"restarting": True}
            except (requests.RequestException, OSError):
                pass
        webbrowser.open(update.get("url") or RELEASES_PAGE)
        return {"restarting": False}

    def set_console(self, hidden):
        """A collapsed console makes the window shorter; the top edge stays in place."""
        self._settings["console_hidden"] = bool(hidden)
        save_settings(self._settings)
        if self._in_main:  # the splash and update screens have their own size
            w, h = self._size(self._main_size())
            self._window.resize(w, h)
        return True

    def open_link(self, name):
        if name in LINKS:
            webbrowser.open(LINKS[name])

    def open_profiles_dir(self, folder=""):
        try:
            open_path(folder or self._data_dir())
            return True
        except OSError:
            return False

    def open_profile(self, code):
        target = decode_code(code)
        if not target:
            return False
        return self.open_profiles_dir(os.path.join(self._data_dir(), target[2]))

    def _main_size(self):
        return (WINDOW_SIZE[0], CONSOLE_HIDDEN_HEIGHT) if self._console_hidden() else WINDOW_SIZE

    def _console_hidden(self):
        # collapsed by default, opens by itself on errors
        return bool(self._settings.get("console_hidden", True))

    def _resize_centered(self, size):
        w, h = self._size(size)
        self._window.resize(w, h)
        area = work_area()
        if area:
            self._window.move(int(area[0] + (area[2] - w) / 2), int(area[1] + (area[3] - h) / 2))

    def minimize(self):
        self._window.minimize()

    def close(self):
        self._window.destroy()

    # ---- data for the UI ----

    def get_state(self):
        return {
            "nick": self._settings.get("nick", ""),
            "nicks": self._nicks(),
            "code": self._settings.get("code", ""),
            "packs": self._packs(),
            "settings": self._user_settings(),
            "zoom": self._zoom,
            "version": VERSION,
            "lang": i18n.LANG,
            "console_hidden": self._console_hidden(),
            "total_ram_mb": total_ram_mb(),
            "defaults": {"profiles_dir": DEFAULT_DATA_DIR},
        }

    def set_language(self, lang):
        """Switches the language of Python messages; the UI translates itself."""
        i18n.set_lang(lang)
        self._settings["lang"] = i18n.LANG
        save_settings(self._settings)
        self._javas = None  # Java labels depend on the language
        return i18n.LANG

    def validate_nick(self, nick):
        return validate_nick(nick)

    def check_code(self, code):
        """state ok/error, button text, console details, pack info."""
        target = decode_code(code)
        if not target:
            return self._fail(problem(t("e_bad_code"), t("d_bad_code")))
        try:
            m = self._installer(target).fetch_manifest(timeout=8)
        except LauncherError as e:
            return self._fail(e.problem)
        except requests.RequestException as e:
            return self._fail(problem(t("e_server_down"), t("d_server_down", host=target[0], port=target[1])))
        pack_problems, core_version = check_pack(m)
        launcher_problems = check_launcher(m, self._user_settings())
        problems = pack_problems + launcher_problems
        return {
            "state": "error" if problems else "ok",
            "button": problems[0]["short"] if problems else t("play"),
            "details": [p["detail"] for p in problems],
            "info": pack_info(m, core_version, not pack_problems, not launcher_problems),
        }

    @staticmethod
    def _fail(p):
        return {"state": "error", "button": p["short"], "details": [p["detail"]], "info": None}

    def find_java(self):
        if self._javas is None:
            self._javas = find_javas()
        return [{"path": j["path"], "label": j["label"]} for j in self._javas]

    def describe_java(self, path):
        info = java_info(path) if path and os.path.isfile(path) else None
        return info["label"] if info else ""

    def browse_java(self):
        res = self._window.create_file_dialog(webview.FileDialog.OPEN,
                                              file_types=("Java (java.exe;javaw.exe)", t("all_files")))
        if not res:
            return None
        path = res[0]
        # prefer java.exe next to javaw.exe
        alt = os.path.join(os.path.dirname(path), "java.exe")
        return alt if os.path.basename(path).lower() == "javaw.exe" and os.path.isfile(alt) else path

    def browse_folder(self):
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=self._data_dir())
        return res[0] if res else None

    def save_settings(self, values):
        memory = values.get("memory", "")
        if memory and not parse_memory(memory):
            return {"ok": False, "error": t("ram_invalid")}
        if memory:
            values["memory"] = str(parse_memory(memory))
        java = values.get("java_path", "")
        if java and not (os.path.isfile(java) and java_info(java)):
            return {"ok": False, "error": t("java_invalid")}
        folder = values.get("profiles_dir", "")
        if folder:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                return {"ok": False, "error": t("dir_create_failed")}
        self._settings["user"] = {k: values.get(k, "") for k in ("profiles_dir", "memory", "java_path", "java_args")}
        save_settings(self._settings)
        return {"ok": True, "settings": self._user_settings()}

    # ---- game ----

    def play(self, nick, code):
        threading.Thread(target=self._play, args=(nick, code), daemon=True).start()

    def redownload_pack(self, nick, code):
        """Wipes the profile (except worlds, screenshots and options.txt) and reinstalls without launching."""
        threading.Thread(target=self._play, args=(nick, code, False), daemon=True).start()

    def delete_pack(self, code):
        target = decode_code(code)
        if not target:
            return {"ok": False, "error": t("bad_pack_code"), "packs": self._packs()}
        try:
            self._wipe_profile(target[2])
        except OSError as e:
            return {"ok": False, "error": t("delete_failed", error=e), "packs": self._packs()}
        self._settings.get("packs", {}).pop(target[2], None)
        if self._settings.get("code") == code:
            self._settings["code"] = ""
        save_settings(self._settings)
        return {"ok": True, "packs": self._packs()}

    def _play(self, nick, code, launch=True):
        error = None  # {"short": ..., "detail": ...}
        self._best_progress = 0.0
        try:
            target = decode_code(code)
            inst = self._installer(target)
            self._status(t("getting_pack"))
            m = inst.fetch_manifest()
            problems = check_pack(m)[0] + check_launcher(m, self._user_settings())
            if problems:
                for p in problems[1:]:
                    self._status(p["detail"])
                raise LauncherError(problems[0]["short"], problems[0]["detail"])
            if not launch:
                # wipe only after the server has answered
                self._status(t("wiping"))
                try:
                    self._wipe_profile(target[2], keep=KEEP_ON_REDOWNLOAD)
                except OSError as e:
                    raise LauncherError(t("e_redownload"), t("d_redownload", error=e)) from e
            with self._stall_watch():
                version_id = inst.install_game(m)
                inst.sync_files(m)
            self._remember(nick, code, target[2], m.get("name", ""))
            if not launch:
                self._status(t("redownloaded"))
                return self._finished(None)
            user = self._user_settings()
            cmd = inst.build_command(m, version_id, nick, parse_memory(user["memory"]) if user["memory"] else None,
                                     user["java_args"])
            self._status(t("launching"))
            prefer_discrete_gpu(cmd[0])
            log_path = os.path.join(inst.game_dir, "launcher_log.txt")
            with open(log_path, "w", encoding="utf-8", errors="replace") as log:
                proc = subprocess.Popen(cmd, cwd=inst.game_dir, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=NO_WINDOW)
                self._js("onRunning()")
                self._window.hide()
                code_ = proc.wait()
            self._window.show()
            if code_ != 0:
                error = problem(t("e_crashed"), t("d_crashed", code=code_, log=log_path))
            else:
                self._status(t("game_closed"))
        except LauncherError as e:
            error = e.problem
        except requests.RequestException as e:
            error = problem(t("e_server_down"), t("d_server_lost", error=e))
        except Exception as e:
            error = problem(t("e_launch"), f"{type(e).__name__}: {e}")
        self._finished(error)

    def _finished(self, error):
        self._js(f"onFinished({json.dumps(error, ensure_ascii=False)}, {json.dumps(self._packs(), ensure_ascii=False)})")

    # ---- internals ----

    @staticmethod
    def _warm_up():
        try:
            cached("mojang", mojang_versions)
        except requests.RequestException:
            pass

    def _size(self, size):
        return int(size[0] * self._zoom), int(size[1] * self._zoom)

    def _user_settings(self):
        u = self._settings.get("user", {})
        s = {k: u.get(k, "") for k in ("profiles_dir", "memory", "java_path", "java_args")}
        if s["memory"]:
            s["memory"] = str(parse_memory(s["memory"]) or "")
        return s

    def _data_dir(self):
        return self._user_settings()["profiles_dir"] or DEFAULT_DATA_DIR

    def _installer(self, target):
        return Installer(*target, self._data_dir(), self._status, self._progress,
                         java_path=self._user_settings()["java_path"] or None)

    def _packs(self):
        packs = self._settings.get("packs", {})
        # codes in the old format (before v2026.b2) can't be used any more
        return [{"code": p["code"], "name": p.get("name", "")} for p in packs.values() if decode_code(p["code"])]

    def _remember(self, nick, code, key, name):
        # by key, so a new owner IP updates the entry instead of adding one
        self._settings.setdefault("packs", {})[key] = {"code": code, "name": name}
        self._settings["code"] = code
        if nick:
            # newest first, no duplicates (case-insensitive)
            nicks = [n for n in self._nicks() if n.lower() != nick.lower()]
            self._settings["nicks"] = [nick] + nicks[:MAX_NICKS - 1]
            self._settings["nick"] = nick
        save_settings(self._settings)

    def forget_nick(self, nick):
        self._settings["nicks"] = [n for n in self._nicks() if n != nick]
        save_settings(self._settings)
        return self._settings["nicks"]

    def _nicks(self):
        # old settings had a single nickname
        nicks = self._settings.get("nicks")
        if nicks is None:
            nicks = [self._settings["nick"]] if self._settings.get("nick") else []
        return nicks

    def _wipe_profile(self, key, keep=()):
        """Deletes the whole profile, or everything except keep."""
        game_dir = os.path.join(self._data_dir(), key)
        if not os.path.isdir(game_dir):
            return
        if not keep:
            shutil.rmtree(game_dir)
            return
        for name in os.listdir(game_dir):
            if name in keep:
                continue
            path = os.path.join(game_dir, name)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

    def _cleanup_parts(self):
        root = self._data_dir()
        for dirpath, _, names in os.walk(root):
            for n in names:
                if n.endswith(".part"):
                    try:
                        os.remove(os.path.join(dirpath, n))
                    except OSError:
                        pass

    def _js(self, code):
        try:
            self._window.evaluate_js(code)
        except Exception:
            pass

    def _status(self, text):
        self._js(f"onStatus({json.dumps(text)})")

    @contextlib.contextmanager
    def _stall_watch(self):
        """Warns in the console when progress stalls. Stalls themselves are handled by NET_TIMEOUT and install_game."""
        done = threading.Event()
        self._moved_at = time.time()

        def watch():
            warned = False
            while not done.wait(1):
                stalled = time.time() - self._moved_at
                if not warned and stalled >= STALL_WARNING_SECONDS:
                    warned = True
                    self._js(f"onWarning({json.dumps(t('stall_warning', seconds=STALL_WARNING_SECONDS))})")
                elif warned and stalled < 1:
                    warned = False
                    self._status(t("download_resumed"))

        threading.Thread(target=watch, daemon=True).start()
        try:
            yield
        finally:
            done.set()

    def _progress(self, value):
        # the bar never goes back on retries; the UI is updated at most 10 times a second
        value = min(max(value, 0), 1)
        now = time.time()
        if value > self._best_progress:
            self._best_progress = value
            self._moved_at = now
        value = self._best_progress
        if now - self._last_progress >= 0.1 or value >= 1:
            self._last_progress = now
            self._js(f"onProgress({value:.3f})")


WEBVIEW2_KEY = r"SOFTWARE\{}Microsoft\EdgeUpdate\Clients\{{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}}"
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def has_webview2():
    """The window needs WebView2. Windows 10/11 almost always has it."""
    import winreg
    for root, wow in ((winreg.HKEY_LOCAL_MACHINE, "WOW6432Node\\"), (winreg.HKEY_LOCAL_MACHINE, ""),
                      (winreg.HKEY_CURRENT_USER, "")):
        try:
            with winreg.OpenKey(root, WEBVIEW2_KEY.format(wow)) as k:
                if winreg.QueryValueEx(k, "pv")[0] not in ("", "0.0.0.0"):
                    return True
        except OSError:
            pass
    return False


def main():
    if not has_webview2():
        i18n.set_lang(load_settings().get("lang") or i18n.system_lang())
        answer = ctypes.windll.user32.MessageBoxW(None, t("webview2_missing"), TITLE, 0x04 | 0x30)
        if answer == 6:
            os.startfile(WEBVIEW2_URL)
        return
    # SHAREPACK_ZOOM=1.5 sets the scale manually
    try:
        zoom = float(os.environ.get("SHAREPACK_ZOOM") or 0) or ui_zoom()
    except ValueError:
        zoom = ui_zoom()
    migrate_old_data()
    remove_old_exe()
    api = Api(zoom)
    api._window = webview.create_window(
        TITLE, os.path.join(UI_DIR, "index.html"), js_api=api, hidden=True,
        width=int(SPLASH_SIZE[0] * zoom), height=int(SPLASH_SIZE[1] * zoom),
        resizable=False, frameless=True, easy_drag=False, background_color="#0b0b0b",
    )
    webview.start()


if __name__ == "__main__":
    main()
