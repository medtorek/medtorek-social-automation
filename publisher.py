#!/usr/bin/env python3
"""Safe-by-default scientific publishing workflow. Uses only public research metadata."""
import datetime as dt
import json
import os
import re
import sys
import textwrap
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from PIL import Image, ImageDraw, ImageFont

BASE = "https://api.buffer.com"
ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
PUBLIC = ROOT / "public"
TODAY = dt.datetime.now(ZoneInfo("Europe/Copenhagen")).date().isoformat()
TOPICS = [
    '"artificial intelligence"[Title/Abstract] AND (clinical OR physician OR diagnosis)',
    '"telemedicine"[Title/Abstract] AND ("primary care" OR cardiovascular)',
    '("cardiovascular risk" OR electrocardiogram) AND ("primary care" OR validation)',
    '("clinical decision support" OR interoperability) AND (safety OR evaluation)',
]
TARGETS = {"twitter", "linkedin", "instagram"}
UA = {"User-Agent": "medtorek-research-publisher/0.1 (personal research workflow)"}

def request(method, url, **kw):
    r = requests.request(method, url, timeout=35, headers={**UA, **kw.pop("headers", {})}, **kw)
    r.raise_for_status()
    return r

def text_of(elem, path):
    child = elem.find(path)
    return " ".join("".join(child.itertext()).split()) if child is not None else ""

def pubmed_candidates():
    start = (dt.date.fromisoformat(TODAY) - dt.timedelta(days=14)).isoformat()
    terms = TOPICS[dt.date.fromisoformat(TODAY).toordinal() % len(TOPICS):] + TOPICS[:dt.date.fromisoformat(TODAY).toordinal() % len(TOPICS)]
    seen = set()
    for term in terms:
        query = f"({term}) AND ({start}[Date - Publication] : {TODAY}[Date - Publication]) AND journal article[Publication Type]"
        resp = request("GET", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                       params={"db": "pubmed", "term": query, "retmode": "json", "retmax": 12, "sort": "pub date"}).json()
        ids = resp.get("esearchresult", {}).get("idlist", [])
        if not ids:
            continue
        xml = request("GET", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                      params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}).content
        for article in ET.fromstring(xml).findall(".//PubmedArticle"):
            pmid = text_of(article, ".//MedlineCitation/PMID")
            if pmid in seen:
                continue
            seen.add(pmid)
            doi = ""
            for node in article.findall(".//ArticleId"):
                if node.get("IdType") == "doi":
                    doi = "".join(node.itertext()).strip()
                    break
            title = text_of(article, ".//Article/ArticleTitle")
            abstracts = [" ".join("".join(n.itertext()).split()) for n in article.findall(".//Article/Abstract/AbstractText")]
            abstract = " ".join(abstracts)
            journal = text_of(article, ".//Article/Journal/Title")
            pub_year = text_of(article, ".//Article/Journal/JournalIssue/PubDate/Year")
            if not doi or len(abstract) < 450 or not title:
                continue
            yield {"pmid": pmid, "doi": doi, "title": title, "abstract": abstract[:9000],
                   "journal": journal, "pub_year": pub_year}

def verify_crossref(item):
    try:
        data = request("GET", "https://api.crossref.org/works/" + quote(item["doi"], safe="")).json()["message"]
        found = re.sub(r"\W", "", " ".join(data.get("title", []))).lower()
        expected = re.sub(r"\W", "", item["title"]).lower()
        if not found or not expected or (found[:60] != expected[:60]):
            return False
        dates = data.get("published", {}).get("date-parts", [[]])[0]
        if len(dates) >= 3:
            published = dt.date(*dates[:3])
            if not (dt.date.fromisoformat(TODAY)-dt.timedelta(days=14) <= published <= dt.date.fromisoformat(TODAY)):
                return False
        else:
            # Exact recent publication date not independently verified: skip.
            return False
        item["published"] = published.isoformat()
        item["url"] = "https://doi.org/" + item["doi"]
        return True
    except Exception:
        return False

def choose_article():
    previous = set()
    for path in STATE.glob("*.json"):
        try:
            previous.add(json.loads(path.read_text(encoding="utf-8")).get("doi", ""))
        except (OSError, ValueError):
            pass
    for item in pubmed_candidates():
        if item["doi"] not in previous and verify_crossref(item):
            return item
    raise RuntimeError("No se encontró artículo reciente con DOI, fecha completa y abstract verificables. No publicar.")

def gemini_write(item):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Falta GEMINI_API_KEY. No publicar.")
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    prompt = (
        "Actúas como editor científico médico en español. SOLO puedes usar el título, DOI y resumen suministrados. "
        "No debes inventar resultados, tamaños muestrales, valores, efectos ni conclusiones. "
        "Si no hay información suficiente para una interpretación clínica prudente, responde exactamente REJECT. "
        "Devuelve SOLO objeto JSON sin markdown con claves: x, linkedin, instagram, visual_title, visual_subtitle. "
        "X máximo 240 caracteres INCLUYENDO el enlace DOI; Linkedin máximo 1000; Instagram máximo 800. "
        "Todos los textos deben incluir el enlace DOI exacto, un hallazgo concreto y una limitación real. "
        "No presentar asociación como causalidad, no incluir hashtags, no consejos personalizados. "
        "El texto visual es conceptual y no debe incluir cifras ni representar gráficos clínicos como datos reales. "
        "En Instagram invita a leer DOI en descripción, sin prometer enlace clicable. "
        "El resumen puede ser insuficiente para verificar todas las cifras: evita cifras no esenciales.\n\n"
        + json.dumps(item, ensure_ascii=False)
    )
    print("Gemini model:", model)
    limits = {"x": 240, "linkedin": 1000, "instagram": 800}
    # The model may omit or rewrite the DOI and exceed its requested length.
    # Request a correction rather than silently truncating clinical claims.
    for attempt in range(3):
        result = request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                         headers={"x-goog-api-key": key},
                         json={"contents":[{"parts":[{"text":prompt}]}],
                               "generationConfig":{"temperature":0.1,"responseMimeType":"application/json"}}).json()
        candidate = result.get("candidates", [{}])[0]
        if candidate.get("finishReason") != "STOP":
            raise RuntimeError("Gemini no completó la salida correctamente.")
        raw = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))
        if raw.strip().upper() == "REJECT":
            raise RuntimeError("El modelo rechazó el artículo por falta de evidencia.")
        data = json.loads(raw)
        errors = []
        for field, limit in limits.items():
            value = data.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(field + ": vacío")
                continue
            # Use the canonical, independently checked DOI instead of a model-generated URL.
            value = re.sub(r"https?://(?:dx\.)?doi\.org/\S+", "", value).strip()
            value = value.rstrip(" .,;") + "\n" + item["url"]
            data[field] = value
            if len(value) > limit:
                errors.append(f"{field}: {len(value)} caracteres (máximo {limit})")
        for field in ("visual_title", "visual_subtitle"):
            if not isinstance(data.get(field), str) or len(data[field]) > 65:
                errors.append(field + ": longitud o tipo inválido")
        if not errors:
            return data
        print(f"Validación intento {attempt+1}: " + "; ".join(errors), file=sys.stderr)
        prompt += (
            "\nCORRECCIÓN OBLIGATORIA: " + "; ".join(errors) +
            ". Reescribe todos los campos del objeto JSON. Reduce la extensión sin omitir "
            "hallazgo concreto ni limitación y sin introducir hechos nuevos. "
            "No es necesario redactar el DOI: el programa lo añade al final. "
            "En X, reserva al menos " + str(len(item["url"])+2) +
            " caracteres para el DOI que añadirá el programa. Devuelve exclusivamente JSON."
        )
    raise RuntimeError("La generación no superó la validación tras 3 intentos; no publicar.")

def font(size, bold=False):
    names = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
             else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for f in names:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()

def draw_image(item, texts, w, h, filename):
    im = Image.new("RGB", (w,h), "#0b1629")
    d = ImageDraw.Draw(im)
    pad = int(w*.065)
    d.rounded_rectangle((pad, int(h*.08), w-pad, int(h*.88)), radius=32, fill="#152943")
    d.text((pad*1.35, int(h*.15)), "MEDTOREK  /  EVIDENCIA CLÍNICA", font=font(int(w*.025),True), fill="#a4d2e1")
    y=int(h*.29)
    for line in textwrap.wrap(texts["visual_title"], width=24):
        d.text((pad*1.35,y), line, font=font(int(w*.052),True), fill="white")
        y+=int(w*.071)
    y+=int(w*.035)
    for line in textwrap.wrap(texts["visual_subtitle"],width=35)[:4]:
        d.text((pad*1.35,y),line,font=font(int(w*.028)),fill="#c6d6e5")
        y+=int(w*.046)
    d.line((pad*1.35,int(h*.77),w-pad*1.35,int(h*.77)),fill="#5e9aac",width=3)
    d.text((pad*1.35,int(h*.8)),"Artículo científico · DOI en la publicación",
           font=font(int(w*.021)),fill="#c6d6e5")
    PUBLIC.mkdir(exist_ok=True)
    im.save(PUBLIC/filename,optimize=True)
    return filename

def write_state(obj):
    STATE.mkdir(exist_ok=True)
    (STATE/(TODAY+".json")).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")

def prepare():
    existing = STATE/(TODAY+".json")
    if existing.exists():
        print("Ya existe preparación para hoy. Se conserva.")
        return
    article=choose_article()
    drafts=gemini_write(article)
    stem=TODAY
    x=draw_image(article,drafts,1200,675,stem+"-x.png")
    instagram=draw_image(article,drafts,1080,1350,stem+"-instagram.png")
    write_state({**article,"copy":drafts,"images":{"twitter":x,"linkedin":x,"instagram":instagram},"posted":{}})
    print("Vista previa preparada para",article["doi"],"sin publicar.")

def gql(query):
    token=os.getenv("BUFFER_API_KEY")
    if not token:
        raise RuntimeError("Falta BUFFER_API_KEY")
    data=request("POST",BASE,headers={"Authorization":"Bearer "+token},
                 json={"query":query}).json()
    if data.get("errors"):
        raise RuntimeError("Buffer GraphQL: "+str(data["errors"])[:500])
    return data["data"]

def channels():
    orgs=gql("query { account { organizations { id name } } }")["account"]["organizations"]
    if len(orgs)!=1:
        raise RuntimeError("Se requiere exactamente una organización Buffer.")
    org_id=json.dumps(orgs[0]["id"])
    channels=gql(f"query {{ channels(input: {{ organizationId: {org_id} }}) {{ id name service }} }}")["channels"]
    selected={}
    for row in channels:
        service=row["service"].lower()
        if service in TARGETS:
            if service in selected:
                raise RuntimeError("Hay más de un canal de "+service+"; seleccionar manualmente.")
            selected[service]=row
    if set(selected)!=TARGETS:
        raise RuntimeError("No están los tres canales X, LinkedIn e Instagram en Buffer: "+str(sorted(selected)))
    return selected

def publish():
    if os.getenv("AUTO_PUBLISH","false").lower() != "true" or os.getenv("FORCE_PREVIEW","false").lower() == "true":
        print("AUTO_PUBLISH desactivado / modo vista previa. No se envía nada.")
        return
    path=STATE/(TODAY+".json")
    if not path.exists():
        raise RuntimeError("No existe vista previa; no publicar.")
    obj=json.loads(path.read_text(encoding="utf-8"))
    selected=channels()
    for service in ("twitter","linkedin","instagram"):
        if obj["posted"].get(service):
            continue
        text={"twitter":obj["copy"]["x"],"linkedin":obj["copy"]["linkedin"],"instagram":obj["copy"]["instagram"]}[service]
        if obj["url"] not in text:
            raise RuntimeError("Falta DOI en el texto.")
        url="https://raw.githubusercontent.com/"+os.environ["GITHUB_REPOSITORY"]+"/main/public/"+obj["images"][service]
        check=request("GET",url)
        if check.headers.get("Content-Type","").split(";")[0] not in ("image/png","image/jpeg","application/octet-stream"):
            raise RuntimeError("Imagen pública no accesible.")
        mutation="mutation { createPost(input: { text: "+json.dumps(text,ensure_ascii=False)+ \
            " channelId: "+json.dumps(selected[service]["id"])+ \
            " schedulingType: automatic mode: addToQueue assets: [{ image: { url: "+json.dumps(url)+" } }] })"+ \
            " { ... on PostActionSuccess { post { id status } } ... on MutationError { message } } }"
        response=gql(mutation)["createPost"]
        if not response.get("post",{}).get("id"):
            raise RuntimeError("Buffer "+service+": "+str(response))
        obj["posted"][service]=response["post"]["id"]
        write_state(obj)
        print(service,"añadido a cola Buffer",response["post"]["id"])

if __name__ == "__main__":
    try:
        command=sys.argv[1] if len(sys.argv)>1 else ""
        if command=="prepare": prepare()
        elif command=="publish": publish()
        elif command=="check-channels":
            print(json.dumps(channels(),indent=2))
        else: raise ValueError("Use prepare, publish or check-channels")
    except Exception as exc:
        print("ERROR:",str(exc),file=sys.stderr)
        sys.exit(1)
