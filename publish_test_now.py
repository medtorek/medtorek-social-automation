#!/usr/bin/env python3
"""Manually triggered PUBLIC TEST. Not used by daily publishing workflow."""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from publisher import channels, gql, request

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
STATE = ROOT / "state"
TEST_ID = "test-envio-2026-11-17"
DOI = "https://doi.org/10.1002/puh2.70438"
TEXT = (
    "PRUEBA TÉCNICA · @medtorek\n"
    "Estamos comprobando el envío automatizado de publicaciones científicas "
    "a través de Buffer.\n"
    "Artículo de referencia sobre telesalud en Atención Primaria:\n" + DOI
)

def save_receipt(path, receipt):
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Save after each network so retries skip confirmed successes.
    subprocess.run(["git", "add", str(path.relative_to(ROOT))], check=True, cwd=ROOT)
    subprocess.run(["git", "commit", "-m", "Registrar prueba de publicación en Buffer"], check=True, cwd=ROOT)
    subprocess.run(["git", "push"], check=True, cwd=ROOT)

def main():
    if os.getenv("CONFIRM_PUBLIC_TEST") != "PUBLISH_TEST_NOW":
        raise RuntimeError("Prueba real no confirmada. No publicar.")
    if not os.getenv("BUFFER_API_KEY"):
        raise RuntimeError("No hay BUFFER_API_KEY")
    # Use verified previous media, not model-created clinical results.
    for fname in ("2026-11-17-x.png", "2026-11-17-instagram.png"):
        if not (PUBLIC/fname).exists():
            raise RuntimeError("Falta una imagen de prueba: " + fname)
    selected = channels()
    STATE.mkdir(exist_ok=True)
    path = STATE/(TEST_ID + ".json")
    receipt = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"test_id":TEST_ID, "sent":{}}
    for service in ("twitter","linkedin","instagram"):
        if receipt["sent"].get(service):
            print(service, "ya registrado; se omite")
            continue
        name = "2026-11-17-instagram.png" if service=="instagram" else "2026-11-17-x.png"
        url = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/main/public/{name}"
        r = request("GET", url)
        if not r.headers.get("Content-Type", "").startswith("image/"):
            raise RuntimeError("La imagen no es accesible para " + service)
        instagram = " metadata: { instagram: { type: post, shouldShareToFeed: true, isAiGenerated: true } }" if service=="instagram" else ""
        mutation = (
            "mutation { createPost(input: { text: " + json.dumps(TEXT, ensure_ascii=False) +
            " channelId: " + json.dumps(selected[service]["id"]) +
            " schedulingType: automatic mode: shareNow" + instagram +
            " assets: [{ image: { url: " + json.dumps(url) + " } }] })"
            " { ... on PostActionSuccess { post { id status sharedNow } } ... on MutationError { message } } }"
        )
        result = gql(mutation)["createPost"]
        post = result.get("post") or {}
        if not post.get("id"):
            raise RuntimeError(f"Buffer no aceptó {service}: {result}")
        # shareNow means submitted to publishing; status may be pending until external network confirms.
        receipt["sent"][service] = {
            "buffer_post_id":post["id"], "buffer_status":post.get("status"),
            "created_at":dt.datetime.now(ZoneInfo("Europe/Copenhagen")).isoformat(),
            "media_url":url,
        }
        save_receipt(path, receipt)
        print(service, "entregado a Buffer para publicación inmediata:", post["id"], post.get("status"))
    print("Solicitud de prueba enviada a Buffer. Verificar publicación real en las redes.")

if __name__=="__main__":
    try:
        main()
    except Exception as err:
        print("ERROR:", err, file=sys.stderr)
        sys.exit(1)
