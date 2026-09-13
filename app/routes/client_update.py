"""
AILinux/AIWindows Client Update API
===================================

Stellt kompilierte Client-Versionen bereit:
- Linux: .deb Pakete
- Windows: .exe Dateien

Linux Endpoints:
- GET /v1/client/update/version - Aktuelle Linux-Version
- GET /v1/client/update/download - .deb Download
- GET /v1/client/update/checksum - Checksum
- GET /v1/client/update/changelog - Changelog

Windows Endpoints:
- GET /v1/client/update/windows/version - Aktuelle Windows-Version
- GET /v1/client/update/windows/download - .exe Download
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from typing import Optional
import json
import logging

logger = logging.getLogger("ailinux.client_update")

router = APIRouter(prefix="/client/update", tags=["Client Update"])

# Release-Verzeichnisse
LINUX_RELEASE_DIR = Path("/home/zombie/workspace/triforce/client-releases/latest")
WINDOWS_RELEASE_DIR = Path("/home/zombie/workspace/triforce/docker/repository/repo/mirror/archive.ailinux.me/windows")


# ============================================================================
# LINUX CLIENT
# ============================================================================

def get_linux_release_info() -> dict:
    """Liest Linux Release-Infos aus Dateien"""
    info = {
        "version": "0.0.0",
        "build_date": "",
        "checksum": "",
        "available": False,
        "filename": "",
        "platform": "linux"
    }
    
    try:
        if (LINUX_RELEASE_DIR / "VERSION").exists():
            info["version"] = (LINUX_RELEASE_DIR / "VERSION").read_text().strip()
            info["available"] = True
            info["filename"] = f"ailinux-client_{info['version']}_amd64.deb"
            
        if (LINUX_RELEASE_DIR / "BUILD_DATE").exists():
            info["build_date"] = (LINUX_RELEASE_DIR / "BUILD_DATE").read_text().strip()
            
        if (LINUX_RELEASE_DIR / "CHECKSUM").exists():
            info["checksum"] = (LINUX_RELEASE_DIR / "CHECKSUM").read_text().strip()
            
        if (LINUX_RELEASE_DIR / "CHANGELOG").exists():
            info["changelog"] = (LINUX_RELEASE_DIR / "CHANGELOG").read_text().strip()
        else:
            info["changelog"] = ""
            
    except Exception as e:
        logger.error(f"Error reading Linux release info: {e}")
        
    return info


def find_deb_file() -> Optional[Path]:
    """Findet die .deb Datei"""
    repo_pool = Path("/home/zombie/workspace/triforce/docker/repository/repo/mirror/archive.ailinux.me/pool/main/a/ailinux-client")
    
    # Latest symlink
    latest = repo_pool / "ailinux-client-latest.deb"
    if latest.exists():
        resolved = latest.resolve()
        if resolved.exists():
            return resolved
    
    # Fallback: Neueste .deb finden
    debs = sorted(repo_pool.glob("ailinux-client_*.deb"), reverse=True)
    if debs:
        return debs[0]
        
    logger.warning("No .deb file found")
    return None


@router.get("/version")
async def get_linux_version():
    """
    Aktuelle Linux Client-Version.
    
    Returns:
        version, build_date, checksum, download_url, changelog, filename
    """
    info = get_linux_release_info()
    
    if not info["available"]:
        raise HTTPException(404, "Kein Linux Release verfügbar")
        
    return {
        "version": info["version"],
        "build_date": info["build_date"],
        "checksum": info["checksum"],
        "download_url": "https://repo.ailinux.me/mirror/archive.ailinux.me/pool/main/a/ailinux-client/ailinux-client-latest.deb",
        "changelog": info.get("changelog", ""),
        "filename": info["filename"],
        "platform": "linux"
    }


@router.get("/download")
async def download_linux_client():
    """Download des Linux Clients (.deb Paket)"""
    deb_file = find_deb_file()
    
    if not deb_file:
        raise HTTPException(404, "Kein Linux Release verfügbar")
        
    info = get_linux_release_info()
    filename = info.get("filename", "ailinux-client.deb")
        
    return FileResponse(
        path=str(deb_file),
        filename=filename,
        media_type="application/vnd.debian.binary-package"
    )


@router.get("/checksum")
async def get_linux_checksum():
    """SHA256 Checksum der Linux-Version"""
    info = get_linux_release_info()
    if not info["available"]:
        raise HTTPException(404, "Kein Release verfügbar")
    return {"checksum": info["checksum"], "platform": "linux"}


@router.get("/changelog")
async def get_linux_changelog():
    """Changelog der Linux-Version"""
    changelog_path = LINUX_RELEASE_DIR / "CHANGELOG"
    if changelog_path.exists():
        return {"changelog": changelog_path.read_text(), "platform": "linux"}
    return {"changelog": "Keine Changelog-Informationen verfügbar.", "platform": "linux"}


@router.get("/info")
async def get_linux_info():
    """Alle Linux Update-Informationen"""
    info = get_linux_release_info()
    info["download_url"] = "https://repo.ailinux.me/mirror/archive.ailinux.me/pool/main/a/ailinux-client/ailinux-client-latest.deb"
    return info


# ============================================================================
# WINDOWS CLIENT
# ============================================================================

def get_windows_release_info() -> dict:
    """Liest Windows Release-Infos aus version.json"""
    info = {
        "version": "0.0.0",
        "build_date": "",
        "checksum": "",
        "available": False,
        "filename": "",
        "platform": "windows",
        "download_url": "https://repo.ailinux.me/windows/AIWindows-Client-latest.exe"
    }
    
    version_json = WINDOWS_RELEASE_DIR / "version.json"
    
    try:
        if version_json.exists():
            data = json.loads(version_json.read_text())
            info.update(data)
            info["available"] = True
    except Exception as e:
        logger.error(f"Error reading Windows release info: {e}")
        
    return info


def find_exe_file() -> Optional[Path]:
    """Findet die .exe Datei"""
    # Latest
    latest = WINDOWS_RELEASE_DIR / "AIWindows-Client-latest.exe"
    if latest.exists():
        return latest
    
    # Versionierte Datei
    exes = sorted(WINDOWS_RELEASE_DIR.glob("AIWindows-Client-*.exe"), reverse=True)
    if exes:
        return exes[0]
        
    logger.warning("No .exe file found")
    return None


@router.get("/windows/version")
async def get_windows_version():
    """
    Aktuelle Windows Client-Version.
    
    Returns:
        version, build_date, checksum, download_url, changelog, filename, platform
    """
    info = get_windows_release_info()
    
    if not info["available"]:
        raise HTTPException(404, "Kein Windows Release verfügbar")
        
    return {
        "version": info.get("version", "0.0.0"),
        "build_date": info.get("build_date", ""),
        "checksum": info.get("checksum", ""),
        "download_url": info.get("download_url", "https://repo.ailinux.me/windows/AIWindows-Client-latest.exe"),
        "changelog": info.get("changelog", ""),
        "filename": info.get("filename", "AIWindows-Client.exe"),
        "platform": "windows",
        "min_windows_version": info.get("min_windows_version", "10.0.17763")
    }


@router.get("/windows/download")
async def download_windows_client():
    """Download des Windows Clients (.exe)"""
    exe_file = find_exe_file()
    
    if not exe_file:
        raise HTTPException(404, "Kein Windows Release verfügbar - Build in Vorbereitung")
        
    info = get_windows_release_info()
    filename = info.get("filename", "AIWindows-Client.exe")
        
    return FileResponse(
        path=str(exe_file),
        filename=filename,
        media_type="application/vnd.microsoft.portable-executable"
    )


@router.get("/windows/info")
async def get_windows_info():
    """Alle Windows Update-Informationen"""
    return get_windows_release_info()


# ============================================================================
# UNIFIED ENDPOINTS
# ============================================================================

@router.get("/all")
async def get_all_versions():
    """Alle verfügbaren Client-Versionen (Linux + Windows)"""
    linux_info = get_linux_release_info()
    windows_info = get_windows_release_info()
    
    return {
        "linux": {
            "version": linux_info.get("version", "0.0.0"),
            "available": linux_info.get("available", False),
            "download_url": "https://repo.ailinux.me/mirror/archive.ailinux.me/pool/main/a/ailinux-client/ailinux-client-latest.deb"
        },
        "windows": {
            "version": windows_info.get("version", "0.0.0"),
            "available": windows_info.get("available", False),
            "download_url": windows_info.get("download_url", "https://repo.ailinux.me/windows/AIWindows-Client-latest.exe")
        }
    }
