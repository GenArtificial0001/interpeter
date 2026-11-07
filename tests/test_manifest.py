"""Protege la extensión del navegador: ID estable, patrones de URL y permisos mínimos."""

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from interpreter.audio.webmic import EXTENSION_IDS

MANIFEST = Path(__file__).resolve().parents[1] / "browser-extension" / "manifest.json"


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def extension_id(key_b64: str) -> str:
    """ID que Chrome/Edge derivan de la clave pública: SHA-256, 16 bytes, hex mapeado a a-p."""
    digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def match_pattern(pattern: str, url: str) -> bool:
    """Subconjunto de los patrones de coincidencia de Chrome: esquema, host (con *.) y ruta con *."""
    m = re.fullmatch(r"(https|http|\*)://([^/]+)(/.*)", pattern)
    assert m, f"patrón no válido: {pattern}"
    scheme, host, path = m.groups()
    u = re.fullmatch(r"(https?)://([^/:]+)(?::\d+)?(/.*)?", url)
    assert u, url
    u_scheme, u_host, u_path = u.group(1), u.group(2).lower(), u.group(3) or "/"
    if scheme != "*" and scheme != u_scheme:
        return False
    if scheme == "*" and u_scheme not in ("http", "https"):
        return False
    if host.startswith("*."):
        base = host[2:]
        if not (u_host == base or u_host.endswith("." + base)):
            return False
    elif host != "*" and host != u_host:
        return False
    return re.fullmatch(".*".join(re.escape(p) for p in path.split("*")), u_path) is not None


def cubre(manifest, url: str) -> bool:
    return all(any(match_pattern(p, url) for p in cs["matches"]) for cs in manifest["content_scripts"])


def test_el_id_derivado_de_la_clave_es_el_que_acepta_el_servidor(manifest):
    assert extension_id(manifest["key"]) in EXTENSION_IDS
    assert (MANIFEST.parent / "EXTENSION_ID.txt").read_text().strip() in EXTENSION_IDS


@pytest.mark.parametrize(
    "url",
    [
        "https://teams.microsoft.com/v2/",
        "https://teams.cloud.microsoft/",
        "https://teams.live.com/",
        "https://meet.google.com/abc-defg-hij",
        "https://app.zoom.us/wc/123456789/join",
        "https://zoom.us/wc/join/123456789",
        "https://us02web.zoom.us/wc/123456789/join",
        "https://empresa.zoomgov.com/wc/123/join",
        "https://app.zoom.us/wc/95416837551/start?ref_from=launch&fromPWA=1",
    ],
)
def test_las_reuniones_reales_estan_cubiertas(manifest, url):
    assert cubre(manifest, url)


@pytest.mark.parametrize(
    "url",
    [
        "https://meet.google.com.evil.com/abc",
        "https://evilzoom.us/wc/1/join",
        "https://teams.microsoft.com.evil.com/",
        "http://meet.google.com/abc",  # solo https
        "https://example.com/",
    ],
)
def test_lo_que_no_es_una_reunion_no_se_toca(manifest, url):
    assert not cubre(manifest, url)


def test_permisos_minimos(manifest):
    assert manifest["manifest_version"] == 3
    assert "permissions" not in manifest or not manifest["permissions"]
    assert manifest["host_permissions"] == ["ws://127.0.0.1:8765/*"]
    texto = json.dumps(manifest)
    assert "<all_urls>" not in texto and "http://*/*" not in texto and "https://*/*" not in texto


def test_los_dos_scripts_cubren_las_mismas_paginas_y_mundos(manifest):
    principal, aislado = manifest["content_scripts"]
    assert principal["world"] == "MAIN" and aislado["world"] == "ISOLATED"
    assert principal["matches"] == aislado["matches"]
    for cs in (principal, aislado):
        assert cs["run_at"] == "document_start" and cs["all_frames"] is True
        assert cs["match_about_blank"] is True and cs["match_origin_as_fallback"] is True
    assert int(manifest["minimum_chrome_version"]) >= 119  # match_origin_as_fallback


def test_con_match_origin_as_fallback_toda_ruta_debe_ser_asterisco(manifest):
    """Chrome y Edge rechazan el manifest ENTERO si un patrón lleva una ruta distinta de /*."""
    for cs in manifest["content_scripts"]:
        if cs.get("match_origin_as_fallback"):
            for pattern in cs["matches"]:
                ruta = "/" + pattern.split("://", 1)[1].split("/", 1)[1]
                assert ruta == "/*", f"{pattern}: el navegador desactivaría la extensión"
