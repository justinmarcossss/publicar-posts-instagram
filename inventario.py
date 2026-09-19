"""Passo 1: lê todos os posts da Página do Facebook e gera a planilha de revisão.

Não publica nada. Saídas:
  - posts.json        (dados completos, usados depois pelo script de publicação)
  - revisao.xlsx      (planilha para você revisar e marcar o que publicar)
"""
import difflib
import json
import warnings

warnings.filterwarnings("ignore")
import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

GRAPH = "https://graph.facebook.com/v25.0"
IG_GRAPH = "https://graph.instagram.com/v23.0"


def load_env():
    env = {}
    with open(".env") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env


def get_all(url, params):
    items = []
    while url:
        r = requests.get(url, params=params, timeout=60)
        data = r.json()
        if "error" in data:
            raise SystemExit(f"Erro da API: {data['error'].get('message')}")
        items.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = None  # a URL "next" já contém os parâmetros
    return items


MEDIA_FIELDS = "media_type,type,url,target{id},media{image{src,width,height},source}"
FIELDS = (
    "id,created_time,message,permalink_url,status_type,"
    f"attachments{{{MEDIA_FIELDS},subattachments.limit(20){{{MEDIA_FIELDS}}}}}"
)


# tipos de attachment que são mudanças na Página, não conteúdo de feed
TIPOS_NAO_CONTEUDO = {"cover_photo", "profile_media", "avatar", "new_album"}


def extract_media(att):
    """Converte um attachment do Facebook numa lista de mídias {kind, url, w, h}."""
    att_type = att.get("type", "")
    subs = att.get("subattachments", {}).get("data")
    nodes = subs if subs else [att]
    out = []
    for n in nodes:
        mt = (n.get("media_type") or n.get("type") or "").lower()
        media = n.get("media", {})
        img = media.get("image", {})
        if "video" in mt:
            out.append({"kind": "video", "url": media.get("source"), "thumb": img.get("src"),
                        "w": img.get("width"), "h": img.get("height"), "att_type": att_type})
        elif img.get("src"):
            out.append({"kind": "image", "url": img["src"], "w": img.get("width"), "h": img.get("height"),
                        "photo_id": n.get("target", {}).get("id"), "att_type": att_type})
    return out


def upgrade_resolution(media, token):
    """Troca a imagem de 720px pela maior versão disponível da foto."""
    for m in media:
        if m["kind"] != "image" or not m.get("photo_id") or m.get("att_type") in TIPOS_NAO_CONTEUDO:
            continue
        r = requests.get(f"{GRAPH}/{m['photo_id']}", params={"fields": "images", "access_token": token},
                         timeout=60).json()
        imgs = r.get("images")
        if imgs:
            best = max(imgs, key=lambda i: i["width"])
            m.update(url=best["source"], w=best["width"], h=best["height"])


def classify(post):
    media = post["media"]
    notes = []
    if not media:
        return "SÓ TEXTO", "Instagram não aceita post sem imagem", notes
    if all(m.get("att_type") in TIPOS_NAO_CONTEUDO for m in media):
        return "FOTO DE CAPA/PERFIL", "NÃO É POST", notes
    videos = [m for m in media if m["kind"] == "video"]
    if any(v["url"] is None for v in videos):
        notes.append("vídeo sem link de download (pode ser vídeo compartilhado de outra página)")
    for m in media:
        if m["kind"] == "image" and m["w"] and m["h"]:
            ratio = m["w"] / m["h"]
            if ratio < 0.8 or ratio > 1.91:
                notes.append(f"proporção {m['w']}x{m['h']} fora do padrão IG → ajustar com bordas")
    if len(media) > 10:
        notes.append(f"{len(media)} mídias → IG aceita no máx. 10 (usar as 10 primeiras)")
    if len(post.get("message") or "") > 2200:
        notes.append("legenda > 2200 caracteres → será cortada")
    if post.get("message", "").count("#") > 30:
        notes.append("mais de 30 hashtags")
    if len(media) > 1:
        tipo = "CARROSSEL"
    elif videos:
        tipo = "REELS"
    else:
        tipo = "FOTO"
    return tipo, "OK" if not notes else "OK com ajuste", notes


def load_excluidos():
    """IDs listados em excluir.txt nunca vão para o Instagram."""
    ids = set()
    try:
        with open("excluir.txt") as f:
            for line in f:
                line = line.split("#")[0].strip()
                if line:
                    ids.add(line)
    except FileNotFoundError:
        pass
    return ids


def norm(s):
    """Texto normalizado: sem quebras de linha, sem emoji de marcação, minúsculo."""
    return " ".join((s or "").split()).lower()


def parecido(a, b):
    """Duas legendas são o mesmo post se uma contém a outra ou se são 80% iguais.

    O Instagram muitas vezes tem uma versão levemente editada do texto do Facebook
    (um parágrafo a mais no início, por exemplo), por isso não basta comparar o começo.
    """
    if not a or not b:
        return False
    if len(a) >= 80 and len(b) >= 80 and (a in b or b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.80


def main():
    env = load_env()
    print("Lendo posts da Página...")
    raw = get_all(f"{GRAPH}/me/posts", {"fields": FIELDS, "limit": 50, "access_token": env["FB_TOKEN"]})
    print(f"  {len(raw)} posts encontrados")

    print("Lendo posts já existentes no Instagram...")
    ig = get_all(f"{IG_GRAPH}/me/media", {"fields": "id,caption,timestamp,permalink", "limit": 50,
                                          "access_token": env["IG_TOKEN"]})
    ig_caps = [(norm(m.get("caption")), m.get("permalink")) for m in ig if m.get("caption")]
    print(f"  {len(ig)} posts no Instagram")

    excluidos = load_excluidos()
    posts = []
    for p in sorted(raw, key=lambda x: x["created_time"]):  # mais antigo primeiro
        media = []
        for att in p.get("attachments", {}).get("data", []):
            media.extend(extract_media(att))
        upgrade_resolution(media, env["FB_TOKEN"])
        post = {
            "fb_id": p["id"],
            "created_time": p["created_time"],
            "message": p.get("message", ""),
            "permalink": p.get("permalink_url"),
            "status_type": p.get("status_type"),
            "media": media,
        }
        tipo, status, notes = classify(post)
        alvos = {m.get("photo_id") for m in media} | set(post["fb_id"].split("_"))
        if alvos & excluidos:
            status = "EXCLUÍDO POR VOCÊ"
            notes.append("consta em excluir.txt")
        alvo = norm(post["message"])
        dup = next((link for cap, link in ig_caps if parecido(alvo, cap)), None)
        if dup:
            status = "JÁ NO INSTAGRAM"
            notes.append(f"legenda igual a {dup}")
        post.update(tipo=tipo, status=status, notes=notes,
                    publicar="NÃO" if status != "OK" and status != "OK com ajuste" else "SIM")
        posts.append(post)

    with open("posts.json", "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)

    wb = Workbook()
    ws = wb.active
    ws.title = "Revisão"
    headers = ["#", "Data original", "Tipo", "Nº mídias", "Status", "PUBLICAR? (SIM/NÃO)",
               "Legenda (início)", "Observações", "Link no Facebook", "fb_id"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    fills = {"SIM": "E2EFDA", "NÃO": "FCE4D6"}
    for i, p in enumerate(posts, 1):
        ws.append([i, p["created_time"][:10], p["tipo"], len(p["media"]), p["status"], p["publicar"],
                   (p["message"] or "")[:150], "; ".join(p["notes"]), p["permalink"], p["fb_id"]])
        ws.cell(row=i + 1, column=6).fill = PatternFill("solid", fgColor=fills[p["publicar"]])
    widths = [5, 12, 12, 9, 16, 18, 60, 50, 40, 36]
    for col, w in zip("ABCDEFGHIJ", widths):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=c.column in (7, 8))
    ws.freeze_panes = "A2"
    wb.save("revisao.xlsx")

    from collections import Counter
    print("\nResumo:")
    for k, v in Counter(p["tipo"] for p in posts).items():
        print(f"  {k}: {v}")
    for k, v in Counter(p["status"] for p in posts).items():
        print(f"  status {k}: {v}")
    print(f"  Marcados para publicar: {sum(p['publicar'] == 'SIM' for p in posts)}")
    print("\nArquivos gerados: posts.json, revisao.xlsx")


if __name__ == "__main__":
    main()
