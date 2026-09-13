from __future__ import annotations

import json

import pytest

from app.routes.health import ANDROID_APP_CERT_SHA256, ANDROID_APP_PACKAGE, android_assetlinks


@pytest.mark.asyncio
async def test_android_assetlinks_matches_signed_helper_contract():
    response = await android_assetlinks()
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload == [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "me.ailinux.workspace",
            "sha256_cert_fingerprints": [
                "3D:4B:5C:38:78:F1:95:08:53:AF:BF:20:A8:DF:F4:90:7A:BC:9C:8D:F1:D2:68:B3:F6:1E:16:DC:F4:9D:7E:21"
            ],
        },
    }]
    assert ANDROID_APP_PACKAGE == payload[0]["target"]["package_name"]
    assert ANDROID_APP_CERT_SHA256 == payload[0]["target"]["sha256_cert_fingerprints"][0]
    assert response.headers["cache-control"] == "public, max-age=3600"
