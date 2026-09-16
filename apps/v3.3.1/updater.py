"""IELTS CLI 稳定版后台检查、可信下载、分阶段激活与学习数据备份。"""

import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import ssl
import stat
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile


LATEST_RELEASE_URL = "https://api.github.com/repos/RoreProMAX/ielts-cli/releases/latest"
ALL_RELEASES_URL = "https://api.github.com/repos/RoreProMAX/ielts-cli/releases?per_page=100"
REPOSITORY = "RoreProMAX/ielts-cli"
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
MAX_METADATA_BYTES = 1024 * 1024
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_FILES = 4096
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
NETWORK_TIMEOUT = 12
_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-beta\.(0|[1-9][0-9]*))?$")
_DIGEST_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SUM_LINE_RE = re.compile(r"^([0-9a-f]{64})  (.+)$")


class UpdateError(Exception):
    """An update failed validation or could not be completed safely."""


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        _validate_https_url(new_url, ALLOWED_DOWNLOAD_HOSTS)
        redirected = super().redirect_request(request, file_pointer, code, message, headers, new_url)
        if redirected is not None:
            redirected.remove_header("Authorization")
            redirected.remove_header("Proxy-Authorization")
        return redirected


def _open_with_context(request, timeout, context=None):
    handlers = [_SafeRedirectHandler()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=timeout)


def _retryable_ssl_error(error):
    """Recognize TLS transport failures without retrying certificate rejection."""
    pending = [error]
    seen = set()
    found_ssl = False
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return False
        if isinstance(current, ssl.SSLError):
            found_ssl = True
        for attribute in ("reason", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    return found_ssl


def _tls12_open(request, timeout):
    context = ssl.create_default_context()
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return _open_with_context(request, timeout, context)


def _default_open(request, timeout):
    try:
        return _open_with_context(request, timeout)
    except (urllib.error.URLError, OSError) as error:
        if not _retryable_ssl_error(error):
            raise
        return _tls12_open(request, timeout)


def _parse_version(value):
    if not isinstance(value, str) or _SEMVER_RE.fullmatch(value) is None:
        return None
    match = _SEMVER_RE.fullmatch(value)
    major, minor, patch, beta = match.groups()
    return (int(major), int(minor), int(patch), 0 if beta is not None else 1,
            int(beta or 0))


def _validate_https_url(value, hosts):
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise UpdateError("发布地址无效") from error
    if (parsed.scheme != "https" or parsed.hostname not in hosts or
            parsed.username is not None or parsed.password is not None or
            port not in (None, 443) or parsed.fragment):
        raise UpdateError("发布地址不在官方 HTTPS 允许范围内")
    return parsed


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _safe_member_name(name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise UpdateError("更新包包含非法文件路径")
    raw_parts = name.split("/")
    if len(name) > 2048 or any(not part or part in (".", "..") or len(part) > 240 for part in raw_parts):
        raise UpdateError("更新包包含越界文件路径")
    path = PurePosixPath(name)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        raise UpdateError("更新包包含越界文件路径")
    if re.match(r"^[A-Za-z]:", parts[0]):
        raise UpdateError("更新包包含绝对文件路径")
    windows_devices = {"con", "prn", "aux", "nul"}
    windows_devices.update("com%d" % value for value in range(1, 10))
    windows_devices.update("lpt%d" % value for value in range(1, 10))
    for part in parts:
        stem = part.split(".", 1)[0].casefold()
        if ":" in part or part.endswith((".", " ")) or stem in windows_devices:
            raise UpdateError("更新包包含跨平台不安全的文件名")
    return path


def _read_json_bytes(payload, label):
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise UpdateError(label + " 不是有效 JSON") from error
    if not isinstance(value, dict):
        raise UpdateError(label + " 顶层必须是对象")
    return value


def _parse_sums(payload):
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise UpdateError("SHA256SUMS 编码无效") from error
    if not lines:
        raise UpdateError("SHA256SUMS 为空")
    result = {}
    folded = set()
    for line in lines:
        match = _SUM_LINE_RE.fullmatch(line)
        if match is None:
            raise UpdateError("SHA256SUMS 格式无效")
        name = match.group(2)
        _safe_member_name(name)
        key = name.casefold()
        if name in result or key in folded:
            raise UpdateError("SHA256SUMS 含重复文件")
        result[name] = match.group(1)
        folded.add(key)
    return result


def _validate_manifest(manifest, version, payload_names):
    files = manifest.get("files")
    if manifest.get("default_app") != version or not isinstance(files, dict):
        raise UpdateError("BUNDLE_MANIFEST 版本或文件表无效")
    expected_app_version = "apps/v%s/VERSION" % version
    if "launcher.py" not in payload_names or expected_app_version not in payload_names:
        raise UpdateError("更新包缺少目标版本入口")
    normalized = {}
    folded = set()
    for name, digest in files.items():
        _safe_member_name(name)
        key = name.casefold()
        if key in folded or not isinstance(digest, str) or _HASH_RE.fullmatch(digest) is None:
            raise UpdateError("BUNDLE_MANIFEST 文件表无效")
        normalized[name] = digest
        folded.add(key)
    expected = payload_names - {"BUNDLE_MANIFEST.json", "SHA256SUMS"}
    if set(normalized) != expected:
        raise UpdateError("BUNDLE_MANIFEST 与更新包文件不一致")
    return normalized


class UpdateManager:
    """Manage opt-in staged updates for a profile and its selected channel."""

    def __init__(self, data_dir, current_version, *, transport=None):
        parsed = _parse_version(current_version)
        if parsed is None:
            raise ValueError("current_version must be X.Y.Z or X.Y.Z-beta.N")
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.current_version = current_version
        self._current_tuple = parsed
        self._transport = transport or _default_open
        self._lock = threading.Lock()
        self._thread = None
        self._closed = threading.Event()
        self._candidate = None
        self._ready = None
        self._generation = 0
        self._status = {"state": "idle", "version": current_version, "channel": "stable", "message": "尚未检查更新"}
        self.settings_path = self.data_dir / "update_settings.json"
        self.updates_dir = self.data_dir / "updates"
        self.downloads_dir = self.updates_dir / "downloads"
        self.releases_dir = self.updates_dir / "releases"
        self.backups_dir = self.updates_dir / "backups"
        self.active_path = self.updates_dir / "active.json"
        self.channel, self.auto_check = self._load_settings()
        self._status["channel"] = self.channel

    def _load_settings(self):
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "stable", True
        if not isinstance(value, dict):
            return "stable", True
        channel = value.get("channel") if value.get("channel") in ("stable", "beta") else "stable"
        enabled = value.get("auto_check")
        return channel, enabled if isinstance(enabled, bool) else True

    def _save_settings(self):
        self._atomic_json(self.settings_path, {"schema": 2, "auto_check": self.auto_check, "channel": self.channel})

    def set_auto_check(self, enabled):
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")
        with self._lock:
            self._atomic_json(self.settings_path, {"schema": 2, "auto_check": enabled, "channel": self.channel})
            self.auto_check = enabled

    def set_channel(self, channel):
        if channel not in ("stable", "beta"):
            raise ValueError("channel must be stable or beta")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("更新正在进行，暂不能切换通道")
            if channel == self.channel:
                return
            self._atomic_json(self.settings_path, {"schema": 2, "auto_check": self.auto_check, "channel": channel})
            self.channel = channel
            self._generation += 1
            self._candidate = None
            self._ready = None
        self._set_status("idle", self.current_version, "已切换到%s通道" % ("Beta" if channel == "beta" else "稳定"))

    def _atomic_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        descriptor, temporary = tempfile.mkstemp(prefix=".%s-" % path.name, dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(value, target, ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def poll(self):
        with self._lock:
            return dict(self._status)

    def _set_status(self, state, version, message):
        with self._lock:
            self._status = {"state": state, "version": version, "channel": self.channel, "message": message}

    def _start(self, state, target):
        with self._lock:
            if self._closed.is_set() or (self._thread is not None and self._thread.is_alive()):
                return False
            version = self._candidate["version"] if state == "downloading" and self._candidate else self.current_version
            message = "正在下载并校验更新" if state == "downloading" else "正在检查%s版" % ("Beta" if self.channel == "beta" else "稳定")
            generation = self._generation
            self._status = {"state": state, "version": version, "message": message}
            self._status["channel"] = self.channel
            self._thread = threading.Thread(target=self._run_task, args=(target, generation), daemon=True,
                                            name="ielts-update-" + state)
            self._thread.start()
            return True

    def _run_task(self, target, generation):
        try:
            target(generation)
        except Exception as error:
            with self._lock:
                version = self._candidate["version"] if self._candidate else self.current_version
            self._set_status("error", version, self._friendly_error(error))

    @staticmethod
    def _friendly_error(error):
        if isinstance(error, UpdateError):
            return str(error)[:240]
        if isinstance(error, (urllib.error.URLError, TimeoutError, OSError)):
            return "网络或文件操作失败，请稍后重试"
        return "更新处理失败，当前练习不受影响"

    def check_async(self):
        return self._start("checking", self._check)

    def install_async(self):
        with self._lock:
            available = self._candidate is not None and self._status["state"] in ("available", "error")
        if not available:
            return False
        return self._start("downloading", self._install)

    def _request(self, url, hosts, accept, transport=None):
        _validate_https_url(url, hosts)
        request = urllib.request.Request(url, headers={
            "Accept": accept,
            "User-Agent": "IELTS-CLI-Updater/%s" % self.current_version,
        })
        response = (transport or self._transport)(request, NETWORK_TIMEOUT)
        final_url = response.geturl() if hasattr(response, "geturl") else url
        _validate_https_url(final_url, hosts)
        return response

    def _check(self, generation):
        if self._closed.is_set():
            return
        with self._lock:
            if generation != self._generation:
                return
            channel = self.channel
            self._candidate = None
        payload = None
        for attempt in range(2):
            retry_transport = _tls12_open if attempt and self._transport is _default_open else None
            try:
                response = self._request(LATEST_RELEASE_URL if channel == "stable" else ALL_RELEASES_URL, {"api.github.com"},
                                         "application/vnd.github+json", retry_transport)
                with response:
                    payload = response.read(MAX_METADATA_BYTES + 1)
                break
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == 0 and not self._closed.is_set() and _retryable_ssl_error(error):
                    continue
                raise UpdateError("无法连接 GitHub 检查更新") from error
        if len(payload) > MAX_METADATA_BYTES:
            raise UpdateError("GitHub 发布信息超过大小限制")
        try:
            release = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise UpdateError("GitHub 发布信息不是有效 JSON") from error
        if not isinstance(release, (dict, list)):
            raise UpdateError("GitHub 发布信息格式无效")
        if self._closed.is_set():
            return
        releases = [release] if channel == "stable" and isinstance(release, dict) else release if channel == "beta" and isinstance(release, list) else []
        valid = []
        for item in releases:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag_name")
            version = tag[1:] if isinstance(tag, str) and tag.startswith("v") else None
            parsed = _parse_version(version)
            is_beta = isinstance(version, str) and "-beta." in version
            if (item.get("draft") is not False or parsed is None or tag != "v" + version or
                    type(item.get("prerelease")) is not bool or is_beta != item.get("prerelease") or
                    (channel == "stable" and is_beta)):
                continue
            valid.append((parsed, version, item))
        if not valid:
            self._candidate = None
            self._set_status("current", self.current_version, "当前通道没有更新")
            return
        parsed, version, release = max(valid, key=lambda item: item[0])
        if parsed <= self._current_tuple:
            self._candidate = None
            self._set_status("current", self.current_version, "当前通道暂无更高版本")
            return
        expected_name = "IELTS-CLI-Portable-%s.zip" % version
        assets = release.get("assets")
        matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == expected_name] if isinstance(assets, list) else []
        if len(matches) != 1:
            raise UpdateError("发布版本缺少唯一的官方更新包")
        asset = matches[0]
        digest_match = _DIGEST_RE.fullmatch(asset.get("digest", ""))
        size = asset.get("size")
        if digest_match is None:
            raise UpdateError("官方更新包缺少 SHA-256 摘要")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > MAX_DOWNLOAD_BYTES:
            raise UpdateError("官方更新包大小无效或超过限制")
        url = asset.get("browser_download_url")
        parsed_url = _validate_https_url(url, {"github.com"})
        expected_path = "/%s/releases/download/v%s/%s" % (REPOSITORY, version, expected_name)
        if parsed_url.path != expected_path or parsed_url.query:
            raise UpdateError("官方更新包地址与发布版本不匹配")
        candidate = {"version": version, "url": url, "digest": digest_match.group(1).lower(),
                     "size": size, "name": expected_name}
        with self._lock:
            if generation != self._generation or channel != self.channel:
                return
            self._candidate = candidate
        if self._candidate_is_staged(candidate):
            with self._lock:
                self._ready = {"version": version, "digest": candidate["digest"],
                               "path": self.releases_dir / version}
            self._set_status("ready", version, "版本 %s 已校验并暂存，等待你确认切换" % version)
            return
        label = "Beta 版" if "-beta." in version else "稳定版"
        self._set_status("available", version, "发现%s %s，可由你选择下载" % (label, version))

    def _candidate_is_staged(self, candidate):
        archive = self.downloads_dir / candidate["name"]
        release = self.releases_dir / candidate["version"]
        if archive.is_symlink() or not archive.is_file() or not release.is_dir():
            return False
        try:
            if _sha256_file(archive) != candidate["digest"]:
                return False
            self._verify_stage_binding(archive, release, candidate["version"])
            return True
        except (OSError, UpdateError, zipfile.BadZipFile):
            return False

    def _install(self, generation):
        with self._lock:
            if generation != self._generation:
                return
            candidate = dict(self._candidate) if self._candidate else None
        if candidate is None:
            raise UpdateError("请先检查可用更新")
        version = candidate["version"]
        self._ensure_private_dir(self.updates_dir)
        self._ensure_private_dir(self.downloads_dir)
        self._ensure_private_dir(self.releases_dir)
        archive_path = self.downloads_dir / candidate["name"]
        if archive_path.is_symlink():
            raise UpdateError("更新包缓存路径不是受控普通文件")
        if not archive_path.is_file() or _sha256_file(archive_path) != candidate["digest"]:
            self._download(candidate, archive_path)
        if _sha256_file(archive_path) != candidate["digest"]:
            raise UpdateError("下载文件的 SHA-256 与 GitHub 摘要不符")
        self._verify_zip(archive_path, version)
        release_path = self.releases_dir / version
        if release_path.exists():
            self._verify_stage_binding(archive_path, release_path, version)
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".%s-" % version, dir=str(self.releases_dir)))
            try:
                self._extract_zip(archive_path, temporary, version)
                self._verify_stage_binding(archive_path, temporary, version)
                os.replace(temporary, release_path)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        with self._lock:
            self._ready = {"version": version, "digest": candidate["digest"], "path": release_path}
        self._set_status("ready", version, "版本 %s 已校验并暂存，等待你确认切换" % version)

    @staticmethod
    def _ensure_private_dir(directory):
        if directory.is_symlink():
            raise UpdateError("更新目录不能是符号链接")
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir() or directory.is_symlink():
            raise UpdateError("更新目录不是受控目录")
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass

    def _download(self, candidate, destination):
        descriptor, temporary_name = tempfile.mkstemp(prefix=".%s-" % candidate["name"],
                                                       suffix=".part", dir=str(self.downloads_dir))
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            total = 0
            digest = hashlib.sha256()
            for attempt in range(2):
                retry_transport = _tls12_open if attempt and self._transport is _default_open else None
                try:
                    response = self._request(candidate["url"], ALLOWED_DOWNLOAD_HOSTS,
                                             "application/octet-stream", retry_transport)
                    total = 0
                    digest = hashlib.sha256()
                    with response, temporary.open("wb") as target:
                        while True:
                            if self._closed.is_set():
                                raise UpdateError("更新下载已取消")
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            total += len(block)
                            if total > MAX_DOWNLOAD_BYTES or total > candidate["size"]:
                                raise UpdateError("下载文件超过发布信息声明的大小")
                            target.write(block)
                            digest.update(block)
                        target.flush()
                        os.fsync(target.fileno())
                    break
                except (urllib.error.URLError, TimeoutError, OSError) as error:
                    if attempt == 0 and not self._closed.is_set() and _retryable_ssl_error(error):
                        continue
                    raise UpdateError("下载更新失败，请稍后重试") from error
            if total != candidate["size"]:
                raise UpdateError("下载文件大小与发布信息不符")
            if digest.hexdigest() != candidate["digest"]:
                raise UpdateError("下载文件的 SHA-256 与 GitHub 摘要不符")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _zip_entries(self, archive):
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise UpdateError("更新包文件数量超过限制")
        seen = set()
        file_paths = set()
        payload = {}
        total = 0
        for info in infos:
            path = _safe_member_name(info.filename.rstrip("/") if info.is_dir() else info.filename)
            folded = str(path).casefold()
            if folded in seen:
                raise UpdateError("更新包包含重复文件路径")
            ancestor = PurePosixPath()
            for part in path.parts[:-1]:
                ancestor /= part
                if str(ancestor).casefold() in file_paths:
                    raise UpdateError("更新包的文件与目录路径冲突")
            if not info.is_dir() and any(existing.startswith(folded + "/") for existing in seen):
                raise UpdateError("更新包的文件与目录路径冲突")
            seen.add(folded)
            if info.flag_bits & 0x1:
                raise UpdateError("更新包包含加密文件")
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode) or (kind not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise UpdateError("更新包包含符号链接或特殊文件")
            if info.file_size > MAX_MEMBER_BYTES:
                raise UpdateError("更新包单个文件超过限制")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise UpdateError("更新包解压后大小超过限制")
            if info.file_size and info.compress_size == 0:
                raise UpdateError("更新包压缩信息无效")
            if info.compress_size and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
                raise UpdateError("更新包压缩比超过限制")
            if not info.is_dir():
                payload[str(path)] = info
                file_paths.add(folded)
        return payload

    def _verify_zip(self, path, version):
        try:
            with zipfile.ZipFile(path) as archive:
                payload = self._zip_entries(archive)
                if "BUNDLE_MANIFEST.json" not in payload or "SHA256SUMS" not in payload:
                    raise UpdateError("更新包缺少完整性清单")
                manifest_bytes = archive.read(payload["BUNDLE_MANIFEST.json"])
                sums_bytes = archive.read(payload["SHA256SUMS"])
                manifest = _read_json_bytes(manifest_bytes, "BUNDLE_MANIFEST")
                manifest_files = _validate_manifest(manifest, version, set(payload))
                sums = _parse_sums(sums_bytes)
                if set(sums) != set(payload) - {"SHA256SUMS"}:
                    raise UpdateError("SHA256SUMS 与更新包文件不一致")
                for name, info in payload.items():
                    if name == "SHA256SUMS":
                        continue
                    digest = hashlib.sha256()
                    with archive.open(info) as source:
                        while True:
                            block = source.read(1024 * 1024)
                            if not block:
                                break
                            digest.update(block)
                    actual = digest.hexdigest()
                    if actual != sums[name] or (name in manifest_files and actual != manifest_files[name]):
                        raise UpdateError("更新包内部文件 SHA-256 校验失败")
                version_text = archive.read(payload["apps/v%s/VERSION" % version]).decode("utf-8").strip()
                if version_text != version:
                    raise UpdateError("更新包 VERSION 与发布版本不一致")
                if archive.testzip() is not None:
                    raise UpdateError("更新包 CRC 校验失败")
        except (zipfile.BadZipFile, RuntimeError, UnicodeError, KeyError) as error:
            if isinstance(error, UpdateError):
                raise
            raise UpdateError("更新包 ZIP 结构无效") from error

    def _extract_zip(self, path, destination, version):
        with zipfile.ZipFile(path) as archive:
            payload = self._zip_entries(archive)
            for name, info in payload.items():
                target = destination.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
        for directory in [destination] + [item for item in destination.rglob("*") if item.is_dir()]:
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass

    def _verify_release_dir(self, directory, version):
        root = directory.resolve()
        if not directory.is_dir() or directory.is_symlink():
            raise UpdateError("暂存更新目录无效")
        try:
            manifest = json.loads((root / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
            sums = _parse_sums((root / "SHA256SUMS").read_bytes())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise UpdateError("暂存更新缺少有效完整性清单") from error
        files = set()
        for item in root.rglob("*"):
            if item.is_symlink():
                raise UpdateError("暂存更新包含符号链接")
            if item.is_file():
                files.add(item.relative_to(root).as_posix())
        manifest_files = _validate_manifest(manifest, version, files)
        if set(sums) != files - {"SHA256SUMS"}:
            raise UpdateError("暂存更新与 SHA256SUMS 不一致")
        for name, expected in sums.items():
            actual = _sha256_file(root.joinpath(*PurePosixPath(name).parts))
            if actual != expected or (name in manifest_files and actual != manifest_files[name]):
                raise UpdateError("暂存更新文件 SHA-256 校验失败")
        try:
            installed_version = (root / ("apps/v%s/VERSION" % version)).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise UpdateError("暂存更新缺少 VERSION") from error
        if installed_version != version:
            raise UpdateError("暂存更新 VERSION 不匹配")

    def _verify_stage_binding(self, archive_path, release_path, version):
        """Bind the extracted tree to the authenticated ZIP, not merely to self-consistent lists."""
        self._verify_zip(archive_path, version)
        self._verify_release_dir(release_path, version)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                zip_manifest = archive.read("BUNDLE_MANIFEST.json")
                zip_sums = archive.read("SHA256SUMS")
            if ((release_path / "BUNDLE_MANIFEST.json").read_bytes() != zip_manifest or
                    (release_path / "SHA256SUMS").read_bytes() != zip_sums):
                raise UpdateError("暂存目录与已验证更新包不一致")
        except (OSError, KeyError, zipfile.BadZipFile) as error:
            raise UpdateError("无法绑定暂存目录与已验证更新包") from error

    def activate_ready(self):
        with self._lock:
            ready = dict(self._ready) if self._ready else None
            generation = self._generation
            channel = self.channel
        if ready is None or _parse_version(ready["version"]) <= self._current_tuple:
            self._set_status("error", self.current_version, "没有可激活的新版本")
            return None
        try:
            archive = self.downloads_dir / ("IELTS-CLI-Portable-%s.zip" % ready["version"])
            if not archive.is_file() or _sha256_file(archive) != ready["digest"]:
                raise UpdateError("已下载更新包的摘要不再匹配")
            self._verify_stage_binding(archive, ready["path"], ready["version"])
            self._backup_profile(ready["version"])
            pointer = {"schema": 1, "version": ready["version"], "digest": ready["digest"],
                       "activated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
            with self._lock:
                if (generation != self._generation or channel != self.channel or
                        self._ready is None or self._ready.get("version") != ready["version"] or
                        self._ready.get("digest") != ready["digest"]):
                    raise UpdateError("更新通道已变化，取消激活旧候选")
                self._atomic_json(self.active_path, pointer)
                self._ready = None
        except Exception as error:
            self._set_status("error", ready["version"], self._friendly_error(error))
            return None
        launcher = ready["path"] / "launcher.py"
        self._set_status("ready", ready["version"], "版本 %s 已设为下次启动版本" % ready["version"])
        return launcher

    def _backup_profile(self, target_version):
        self._ensure_private_dir(self.updates_dir)
        self._ensure_private_dir(self.backups_dir)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        final = self.backups_dir / ("before-%s-%s" % (target_version, stamp))
        temporary = Path(tempfile.mkdtemp(prefix=".backup-", dir=str(self.backups_dir)))
        copied = []
        try:
            fixed = ["library.json", "progress.json", "settings.json", "migration.json", "version_origin.json",
                     "routine.json", "practice_settings.json", "active_round.json", "extra_round.json", "update_settings.json"]
            candidates = [self.data_dir / name for name in fixed]
            candidates.extend(sorted(self.data_dir.glob("learn_round-*.json")))
            seen = set()
            for source in candidates:
                if source.name in seen or not source.is_file() or source.is_symlink():
                    continue
                seen.add(source.name)
                destination = temporary / source.name
                shutil.copyfile(source, destination)
                try:
                    os.chmod(destination, 0o600)
                except OSError:
                    pass
                copied.append({"name": source.name, "sha256": _sha256_file(destination)})
            database = self.data_dir / "learning.sqlite3"
            if database.is_file() and not database.is_symlink():
                destination = temporary / database.name
                source_connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
                target_connection = sqlite3.connect(destination)
                try:
                    source_connection.backup(target_connection)
                finally:
                    source_connection.close()
                    target_connection.close()
                try:
                    os.chmod(destination, 0o600)
                except OSError:
                    pass
                copied.append({"name": database.name, "sha256": _sha256_file(destination)})
            self._atomic_json(temporary / "BACKUP.json", {
                "schema": 1,
                "target_version": target_version,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "files": copied,
            })
            os.replace(temporary, final)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return final

    def active_launcher(self):
        try:
            if self.active_path.is_symlink() or self.downloads_dir.is_symlink() or self.releases_dir.is_symlink():
                return None
            pointer = json.loads(self.active_path.read_text(encoding="utf-8"))
            if not isinstance(pointer, dict) or set(pointer) != {"schema", "version", "digest", "activated_at"}:
                return None
            version = pointer["version"]
            parsed = _parse_version(version)
            digest = pointer["digest"]
            if pointer["schema"] != 1 or parsed is None or parsed <= self._current_tuple:
                return None
            if not isinstance(pointer["activated_at"], str):
                return None
            if not isinstance(digest, str) or _HASH_RE.fullmatch(digest) is None:
                return None
            release = self.releases_dir / version
            archive = self.downloads_dir / ("IELTS-CLI-Portable-%s.zip" % version)
            if not archive.is_file() or _sha256_file(archive) != digest:
                return None
            self._verify_stage_binding(archive, release, version)
            launcher = release / "launcher.py"
            return launcher if launcher.is_file() and not launcher.is_symlink() else None
        except (OSError, ValueError, TypeError, UpdateError, json.JSONDecodeError):
            return None

    def close(self):
        self._closed.set()
