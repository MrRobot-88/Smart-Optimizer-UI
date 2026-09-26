#!/usr/bin/env python3
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path


TARGET = Path(
    os.environ.get(
        "SMART_OPTIMIZER_PACKAGE_TARGET",
        os.environ.get("SYNOPKG_PKGDEST", ""),
    )
).resolve()

VAR = Path(
    os.environ.get(
        "SMART_OPTIMIZER_PACKAGE_VAR",
        os.environ.get("SYNOPKG_PKGVAR", ""),
    )
).resolve()

PACKAGE_VERSION = str(
    os.environ.get(
        "SMART_OPTIMIZER_PACKAGE_VERSION",
        "0.0.0",
    )
).strip().lstrip("vV")

PYTHON = TARGET / "runtime" / "python" / "bin" / "python3"
PACKAGED_APP = TARGET / "app"

APP_ROOT = VAR / "app"
RELEASES = APP_ROOT / "releases"
CURRENT_FILE = APP_ROOT / "current.json"

UPDATES = VAR / "updates"
REQUEST_FILE = UPDATES / "app-update-request.json"
STATUS_FILE = UPDATES / "status.json"

LOG_FILE = VAR / "smart-optimizer-supervisor.log"

STOP_REQUESTED = False


def log(message):
    VAR.mkdir(parents=True, exist_ok=True)

    with LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            "%s %s\n"
            % (
                time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                str(message),
            )
        )


def version_key(value):
    parts = []

    for piece in str(
        value or ""
    ).strip().lstrip("vV").split("."):
        try:
            parts.append(
                int(piece)
            )
        except Exception:
            parts.append(0)

        if len(parts) >= 4:
            break

    while len(parts) < 4:
        parts.append(0)

    return tuple(parts)


def atomic_json(path, value):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        path.name
        + ".tmp-%d"
        % os.getpid()
    )

    with temporary.open(
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.flush()
        os.fsync(
            handle.fileno()
        )

    os.replace(
        temporary,
        path,
    )


def read_json(path):
    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            value = json.load(
                handle
            )

        return (
            value
            if isinstance(
                value,
                dict,
            )
            else {}
        )

    except Exception:
        return {}


def write_status(
    state,
    current_version,
    target_version="",
    message="",
    filename="",
    sha256="",
):
    atomic_json(
        STATUS_FILE,
        {
            "state":
                state,

            "message":
                message,

            "current_version":
                current_version,

            "target_version":
                target_version,

            "filename":
                filename,

            "sha256":
                sha256,

            "updated_at":
                int(
                    time.time()
                ),
        },
    )


def validate_app_directory(path):
    required = (
        "smart-optimizer-ui.py",
        "radarr-smart-optimizer.py",
        "sonarr-smart-optimizer.py",
        "assets",
        "assets/fragments",
        "assets/fragments/admin-update-mode.html",
    )

    for relative in required:
        candidate = (
            path
            / relative
        )

        if not candidate.exists():
            raise RuntimeError(
                "Application payload is missing %s."
                % relative
            )

    fragment = (
        path
        / "assets"
        / "fragments"
        / "admin-update-mode.html"
    ).read_text(
        encoding="utf-8"
    )

    if (
        "const SMART_ADMIN_DEVELOPMENT_BUILD = true;"
        in fragment
    ):
        raise RuntimeError(
            "Application update bundle is marked as a development build."
        )

    for name in (
        "smart-optimizer-ui.py",
        "radarr-smart-optimizer.py",
        "sonarr-smart-optimizer.py",
    ):
        subprocess.run(
            [
                str(PYTHON),
                "-m",
                "py_compile",
                str(
                    path
                    / name
                ),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )


def seed_packaged_release():
    if (
        not TARGET
        or not VAR
    ):
        raise RuntimeError(
            "Package paths are unavailable."
        )

    if not PYTHON.is_file():
        raise RuntimeError(
            "Bundled Python runtime is missing."
        )

    if not PACKAGED_APP.is_dir():
        raise RuntimeError(
            "Packaged application payload is missing."
        )

    RELEASES.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = (
        RELEASES
        / PACKAGE_VERSION
    )

    if not destination.exists():
        temporary = (
            RELEASES
            / (
                ".package-%s-%d"
                % (
                    PACKAGE_VERSION,
                    os.getpid(),
                )
            )
        )

        shutil.rmtree(
            temporary,
            ignore_errors=True,
        )

        shutil.copytree(
            PACKAGED_APP,
            temporary,
        )

        validate_app_directory(
            temporary
        )

        os.replace(
            temporary,
            destination,
        )

        log(
            "Seeded packaged application v%s."
            % PACKAGE_VERSION
        )

    return destination


def select_initial_release():
    packaged = (
        seed_packaged_release()
    )

    current = read_json(
        CURRENT_FILE
    )

    current_version = str(
        current.get(
            "version"
        )
        or ""
    ).strip().lstrip("vV")

    current_path = Path(
        str(
            current.get(
                "path"
            )
            or ""
        )
    )

    current_valid = bool(
        current_version
        and current_path.is_dir()
    )

    if (
        not current_valid
        or version_key(
            PACKAGE_VERSION
        )
        >
        version_key(
            current_version
        )
    ):
        current_version = (
            PACKAGE_VERSION
        )

        current_path = (
            packaged
        )

        atomic_json(
            CURRENT_FILE,
            {
                "version":
                    current_version,

                "path":
                    str(
                        current_path
                    ),

                "source":
                    "package",

                "updated_at":
                    int(
                        time.time()
                    ),
            },
        )

    validate_app_directory(
        current_path
    )

    return (
        current_version,
        current_path,
    )


def child_environment(
    version,
    app_path,
):
    env = dict(
        os.environ
    )

    env[
        "SMART_OPTIMIZER_VERSION"
    ] = version

    env[
        "SMART_OPTIMIZER_PACKAGE_VERSION"
    ] = PACKAGE_VERSION

    env[
        "SMART_OPTIMIZER_SELF_UPDATE_MODE"
    ] = "app-bundle"

    env[
        "SMART_OPTIMIZER_APP_UPDATE_REQUEST"
    ] = str(
        REQUEST_FILE
    )

    env[
        "SMART_OPTIMIZER_PACKAGE_TARGET"
    ] = str(
        TARGET
    )

    env[
        "SMART_OPTIMIZER_PACKAGE_VAR"
    ] = str(
        VAR
    )

    env[
        "SMART_UI_ASSET_DIR"
    ] = str(
        app_path
        / "assets"
    )

    env[
        "RADARR_OPTIMIZER_SCRIPT"
    ] = str(
        app_path
        / "radarr-smart-optimizer.py"
    )

    env[
        "SONARR_OPTIMIZER_SCRIPT"
    ] = str(
        app_path
        / "sonarr-smart-optimizer.py"
    )

    return env


def launch_child(
    version,
    app_path,
):
    log(
        "Starting Smart Optimizer application v%s."
        % version
    )

    return subprocess.Popen(
        [
            str(PYTHON),
            str(
                app_path
                / "smart-optimizer-ui.py"
            ),
        ],
        cwd=str(
            app_path
        ),
        env=child_environment(
            version,
            app_path,
        ),
    )


def stop_child(child):
    if (
        child is None
        or child.poll()
        is not None
    ):
        return

    child.terminate()

    try:
        child.wait(
            timeout=12
        )

    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(
            timeout=5
        )


def file_sha256(path):
    digest = hashlib.sha256()

    with path.open(
        "rb"
    ) as handle:
        while True:
            chunk = handle.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(
                chunk
            )

    return digest.hexdigest()


def safe_extract_bundle(
    bundle,
    target_version,
):
    if not bundle.is_file():
        raise RuntimeError(
            "Verified update bundle is missing."
        )

    temporary_root = Path(
        tempfile.mkdtemp(
            prefix=(
                ".update-%s-"
                % target_version
            ),
            dir=str(
                RELEASES
            ),
        )
    )

    try:
        with tarfile.open(
            bundle,
            "r:gz",
        ) as archive:
            members = archive.getmembers()

            if not members:
                raise RuntimeError(
                    "Application update bundle is empty."
                )

            for member in members:
                name = str(
                    member.name
                    or ""
                )

                pure = Path(
                    name
                )

                if (
                    pure.is_absolute()
                    or ".."
                    in pure.parts
                    or member.issym()
                    or member.islnk()
                ):
                    raise RuntimeError(
                        "Unsafe path in application update bundle."
                    )

                if (
                    not pure.parts
                    or pure.parts[0]
                    != "app"
                ):
                    raise RuntimeError(
                        "Unexpected application update bundle layout."
                    )

            archive.extractall(
                temporary_root
            )

        app = (
            temporary_root
            / "app"
        )

        validate_app_directory(
            app
        )

        destination = (
            RELEASES
            / target_version
        )

        replacement = (
            RELEASES
            / (
                ".ready-%s-%d"
                % (
                    target_version,
                    os.getpid(),
                )
            )
        )

        shutil.rmtree(
            replacement,
            ignore_errors=True,
        )

        shutil.move(
            str(app),
            str(replacement),
        )

        if destination.exists():
            shutil.rmtree(
                destination
            )

        os.replace(
            replacement,
            destination,
        )

        return destination

    finally:
        shutil.rmtree(
            temporary_root,
            ignore_errors=True,
        )


def install_request(
    request,
):
    action = str(
        request.get(
            "action"
        )
        or ""
    )

    if action != "install-app-bundle":
        raise RuntimeError(
            "Unknown update request."
        )

    target_version = str(
        request.get(
            "target_version"
        )
        or ""
    ).strip().lstrip("vV")

    filename = os.path.basename(
        str(
            request.get(
                "filename"
            )
            or ""
        ).strip()
    )

    expected_sha = str(
        request.get(
            "sha256"
        )
        or ""
    ).strip().lower()

    bundle_path = Path(
        str(
            request.get(
                "bundle_path"
            )
            or ""
        )
    ).resolve()

    if (
        not target_version
        or version_key(
            target_version
        )
        <= version_key(
            "0.0.0"
        )
    ):
        raise RuntimeError(
            "Invalid target version."
        )

    if (
        not filename.lower().startswith(
            "smartoptimizerui-app-"
        )
        or not filename.lower().endswith(
            ".tar.gz"
        )
    ):
        raise RuntimeError(
            "Invalid application update filename."
        )

    if (
        len(
            expected_sha
        )
        != 64
        or any(
            char not in "0123456789abcdef"
            for char in expected_sha
        )
    ):
        raise RuntimeError(
            "Invalid SHA-256."
        )

    try:
        bundle_path.relative_to(
            UPDATES.resolve()
        )

    except ValueError:
        raise RuntimeError(
            "Update bundle is outside package storage."
        )

    if (
        bundle_path.name
        != filename
    ):
        raise RuntimeError(
            "Update bundle filename mismatch."
        )

    actual_sha = file_sha256(
        bundle_path
    )

    if (
        actual_sha
        != expected_sha
    ):
        raise RuntimeError(
            "Application update bundle SHA-256 mismatch."
        )

    destination = safe_extract_bundle(
        bundle_path,
        target_version,
    )

    return (
        target_version,
        destination,
        filename,
        expected_sha,
    )


def handle_signal(
    _signum,
    _frame,
):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def main():
    signal.signal(
        signal.SIGTERM,
        handle_signal,
    )

    signal.signal(
        signal.SIGINT,
        handle_signal,
    )

    APP_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    RELEASES.mkdir(
        parents=True,
        exist_ok=True,
    )

    UPDATES.mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        current_version,
        current_path,
    ) = select_initial_release()

    child = launch_child(
        current_version,
        current_path,
    )

    pending_validation = None

    while not STOP_REQUESTED:
        now = time.time()

        if (
            pending_validation
            and now
            >= pending_validation[
                "deadline"
            ]
        ):
            if (
                child.poll()
                is None
            ):
                write_status(
                    "updated",
                    current_version,
                    current_version,
                    "Smart Optimizer updated successfully to v%s."
                    % current_version,
                    pending_validation[
                        "filename"
                    ],
                    pending_validation[
                        "sha256"
                    ],
                )

                log(
                    "Application update v%s passed startup validation."
                    % current_version
                )

                pending_validation = None

        if (
            child.poll()
            is not None
        ):
            return_code = (
                child.returncode
            )

            if pending_validation:
                previous_version = (
                    pending_validation[
                        "previous_version"
                    ]
                )

                previous_path = Path(
                    pending_validation[
                        "previous_path"
                    ]
                )

                log(
                    "Application v%s exited during validation; "
                    "rolling back to v%s."
                    % (
                        current_version,
                        previous_version,
                    )
                )

                atomic_json(
                    CURRENT_FILE,
                    {
                        "version":
                            previous_version,

                        "path":
                            str(
                                previous_path
                            ),

                        "source":
                            "rollback",

                        "updated_at":
                            int(
                                time.time()
                            ),
                    },
                )

                write_status(
                    "failed",
                    previous_version,
                    current_version,
                    "The new version failed to start. "
                    "Smart Optimizer rolled back automatically.",
                    pending_validation[
                        "filename"
                    ],
                    pending_validation[
                        "sha256"
                    ],
                )

                current_version = (
                    previous_version
                )

                current_path = (
                    previous_path
                )

                pending_validation = None

                child = launch_child(
                    current_version,
                    current_path,
                )

                time.sleep(1)
                continue

            log(
                "Application process exited with code %s; restarting."
                % return_code
            )

            time.sleep(2)

            child = launch_child(
                current_version,
                current_path,
            )

            continue

        request = read_json(
            REQUEST_FILE
        )

        if request:
            not_before = float(
                request.get(
                    "not_before"
                )
                or 0
            )

            if now >= not_before:
                previous_version = (
                    current_version
                )

                previous_path = (
                    current_path
                )

                target_version = str(
                    request.get(
                        "target_version"
                    )
                    or ""
                ).strip().lstrip("vV")

                filename = str(
                    request.get(
                        "filename"
                    )
                    or ""
                )

                sha256 = str(
                    request.get(
                        "sha256"
                    )
                    or ""
                )

                write_status(
                    "installing",
                    current_version,
                    target_version,
                    "Installing v%s and restarting Smart Optimizer..."
                    % target_version,
                    filename,
                    sha256,
                )

                try:
                    stop_child(
                        child
                    )

                    (
                        new_version,
                        new_path,
                        filename,
                        sha256,
                    ) = install_request(
                        request
                    )

                    atomic_json(
                        CURRENT_FILE,
                        {
                            "version":
                                new_version,

                            "path":
                                str(
                                    new_path
                                ),

                            "source":
                                "self-update",

                            "updated_at":
                                int(
                                    time.time()
                                ),
                        },
                    )

                    try:
                        REQUEST_FILE.unlink()

                    except FileNotFoundError:
                        pass

                    current_version = (
                        new_version
                    )

                    current_path = (
                        new_path
                    )

                    child = launch_child(
                        current_version,
                        current_path,
                    )

                    pending_validation = {
                        "previous_version":
                            previous_version,

                        "previous_path":
                            str(
                                previous_path
                            ),

                        "filename":
                            filename,

                        "sha256":
                            sha256,

                        "deadline":
                            time.time()
                            + 8,
                    }

                except Exception as exc:
                    log(
                        "Application update failed: %s"
                        % exc
                    )

                    try:
                        REQUEST_FILE.unlink()

                    except FileNotFoundError:
                        pass

                    write_status(
                        "failed",
                        previous_version,
                        target_version,
                        str(
                            exc
                        ),
                        filename,
                        sha256,
                    )

                    current_version = (
                        previous_version
                    )

                    current_path = (
                        previous_path
                    )

                    child = launch_child(
                        current_version,
                        current_path,
                    )

        time.sleep(
            0.35
        )

    stop_child(
        child
    )

    log(
        "Smart Optimizer supervisor stopped."
    )


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        log(
            "Supervisor fatal error: %s"
            % exc
        )
        raise
