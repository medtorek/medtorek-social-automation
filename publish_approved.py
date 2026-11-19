#!/usr/bin/env python3
"""Publish a reviewed, repository-stored scientific post only after manual approval."""
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

from publisher import channels, gql, request

ROOT = Path(__file__).resolve().parent
DOI = os.environ["APPROVED_DOI"].strip()
CONFIRM = os.environ["APPROVAL_CONFIRM"].strip()
PREVIEW = os.environ.get("PREVIEW_ONLY", "true").lower() != "false"
REPO = os.environ["GITHUB_REPOSITORY"]

def fail(msg):
    raise SystemExit(msg)

def save_receipt(path, receipt):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", str(path.relative_to(ROOT))], check=True, cwd=ROOT)
    subprocess.run(["git", "commit", "-m", "Record approved Buffer publication"], check=True, cwd=ROOT)
    subprocess.run(["git", "push"], check=True, cwd=ROOT)

def main():
    if not re.fullmatch(r"10\.\d{4,9}/[^\s]+", DOI, flags=re.I):
        fail("DOI invalido")
    if CONFIRM != "PUBLICAR " + DOI:
        fail("La aprobacion debe ser exactamente PUBLICAR seguido del DOI")
    slug = re.sub(r"[^a-z0-9]+", "-", DOI.lower()).strip("-")
    manifest_path = ROOT / "approved" / (slug + ".json")
    if not manifest_path.is_file():
        fail("No existe material revisado para este DOI: " + str(manifest_path.relative_to(ROOT)))
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("doi") != DOI or data.get("approved") is not True:
        fail("El manifiesto no coincide con el DOI o no esta marcado como aprobado")
    url = "https://doi.org/" + DOI
    copies = data.get("copy", {})
    images = data.get("images", {})
    for service, field in (("twitter", "x"), ("linkedin", "linkedin"), ("instagram", "instagram")):
        value = copies.get(field)
        if not isinstance(value, str) or url not in value:
            fail("Falta el texto aprobado con DOI en " + service)
        if service == "twitter" and len(value) > 280:
            fail("El texto de X supera 280 caracteres")
        image = images.get(service)
        if not isinstance(image, str) or not re.fullmatch(r"public/[a-zA-Z0-9_.-]+\.(?:png|jpg|jpeg)", image):
            fail("Ruta de imagen no permitida para " + service)
        if not (ROOT / image).is_file():
            fail("Falta imagen aprobada en el repositorio: " + image)
    print("Material validado para DOI:", DOI)
    print("Canales: X, LinkedIn, Instagram")
    if PREVIEW:
        print("PREVIEW_ONLY=true: NO se contacta a Buffer ni se publica.")
        return
    if not os.getenv("BUFFER_API_KEY"):
        fail("Falta el secreto BUFFER_API_KEY")
    selected = channels()
    receipt_path = ROOT / "state" / ("approved-" + slug + ".json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {"doi": DOI, "sent": {}}
    for service, field in (("twitter", "x"), ("linkedin", "linkedin"), ("instagram", "instagram")):
        if receipt["sent"].get(service):
            print(service, "ya enviado: se evita duplicado")
            continue
        media = "https://raw.githubusercontent.com/" + REPO + "/main/" + images[service]
        response = request("GET", media)
        if not response.headers.get("Content-Type", "").startswith("image/"):
            fail("Imagen no accesible: " + media)
        extra = " metadata: { instagram: { type: post, shouldShareToFeed: true, isAiGenerated: true } }" if service == "instagram" else ""
        mutation = (
            "mutation { createPost(input: { text: " + json.dumps(copies[field], ensure_ascii=False)
            + " channelId: " + json.dumps(selected[service]["id"])
            + " schedulingType: automatic mode: shareNow" + extra
            + " assets: [{ image: { url: " + json.dumps(media) + " } }] })"
            + " { ... on PostActionSuccess { post { id status sharedNow } } ... on MutationError { message } } }"
        )
        result = gql(mutation)["createPost"]
        post = result.get("post") or {}
        if not post.get("id"):
            fail("Buffer rechazo " + service + ": " + str(result))
        receipt["sent"][service] = {"buffer_post_id": post["id"], "status": post.get("status"),
            "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        save_receipt(receipt_path, receipt)
        print(service, "enviado a Buffer mediante shareNow", post["id"], post.get("status"))
    print("Envio solicitado a Buffer. Verificar estado final en Buffer Sent.")

if __name__ == "__main__":
    main()
