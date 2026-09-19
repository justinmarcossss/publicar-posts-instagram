"""Passo 2: publica no Instagram os posts marcados como SIM na planilha.

Uso:
    python3 publicar.py --teste       # publica 1 post e para (teste)
    python3 publicar.py               # publica o lote do dia (8 posts)
    python3 publicar.py --simular     # mostra o que faria, sem publicar nada
    python3 publicar.py --status      # mostra o progresso

Segurança:
  - para no primeiro erro da API, sem insistir
  - registra cada publicação em publicados.json; nunca publica o mesmo post duas vezes
  - respeita horário comercial e usa intervalos variados
  - desativa os comentários de cada post e confere que ficaram desativados
"""
import argparse
import json
import os
import random
import sys
import time
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")
import requests

IG = "https://graph.instagram.com/v23.0"
# Endereço público de onde o Instagram busca as mídias (definido no GitHub Actions).
# Vazio = usa a URL original do Facebook, que expira em poucos dias.
RAW_BASE = os.environ.get("RAW_BASE", "").rstrip("/")
REGISTRO = "publicados.json"
LOG = "log.txt"

POR_DIA = 8
HORA_INICIO = 9          # não publica antes das 9h
HORA_FIM = 18            # nem depois das 18h
INTERVALO_MIN = 45 * 60  # menor espera entre posts (45 min)
INTERVALO_MAX = 90 * 60  # maior espera entre posts (1h30)
LIMITE_API_DIA = 100     # limite da Meta; ficamos bem abaixo


def log(msg):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(linha, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def load_env():
    env = {}
    if not os.path.exists(".env"):
        return env
    with open(".env") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env


def carregar_registro():
    if os.path.exists(REGISTRO):
        with open(REGISTRO, encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_registro(reg):
    with open(REGISTRO, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


class ErroAPI(Exception):
    pass


def api(metodo, caminho, token, **params):
    params["access_token"] = token
    r = requests.request(metodo, f"{IG}/{caminho}", params=params, timeout=120)
    data = r.json()
    if "error" in data:
        e = data["error"]
        raise ErroAPI(f"{e.get('type')} (código {e.get('code')}): {e.get('message')}")
    return data


def esperar_container(ig_id, cid, token, limite=300):
    """Vídeos levam alguns minutos para o Instagram processar."""
    inicio = time.time()
    while time.time() - inicio < limite:
        st = api("GET", cid, token, fields="status_code,status")
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise ErroAPI(f"processamento falhou: {st.get('status')}")
        time.sleep(15)
    raise ErroAPI("tempo esgotado esperando o processamento da mídia")


def url_da_midia(m):
    """Prefere o arquivo hospedado no repositório; cai para a URL do Facebook."""
    if RAW_BASE and m.get("arquivo"):
        return f"{RAW_BASE}/{m['arquivo']}"
    return m["url"]


def publicar_post(post, ig_id, token):
    """Cria o container, espera processar e publica. Devolve o id da mídia no IG."""
    legenda = (post["message"] or "")[:2200]
    midias = post["media"][:10]

    if len(midias) > 1:  # carrossel
        filhos = []
        for m in midias:
            campo = "video_url" if m["kind"] == "video" else "image_url"
            c = api("POST", f"{ig_id}/media", token, **{campo: url_da_midia(m)},
                    is_carousel_item="true",
                    **({"media_type": "VIDEO"} if m["kind"] == "video" else {}))
            filhos.append(c["id"])
        for f in filhos:
            esperar_container(ig_id, f, token)
        cont = api("POST", f"{ig_id}/media", token, media_type="CAROUSEL",
                   children=",".join(filhos), caption=legenda)
    elif midias[0]["kind"] == "video":  # reels
        cont = api("POST", f"{ig_id}/media", token, media_type="REELS",
                   video_url=url_da_midia(midias[0]), caption=legenda, share_to_feed="true")
    else:  # foto
        cont = api("POST", f"{ig_id}/media", token, image_url=url_da_midia(midias[0]), caption=legenda)

    esperar_container(ig_id, cont["id"], token)
    pub = api("POST", f"{ig_id}/media_publish", token, creation_id=cont["id"])
    desativar_comentarios(pub["id"], token)
    return pub["id"]


def desativar_comentarios(media_id, token):
    """Desliga os comentários do post e confere que ficou desligado mesmo."""
    api("POST", media_id, token, comment_enabled="false")
    estado = api("GET", media_id, token, fields="is_comment_enabled").get("is_comment_enabled")
    if estado is not False:
        raise ErroAPI(f"comentários continuam ativos no post {media_id} (is_comment_enabled={estado})")


def dentro_do_horario():
    return HORA_INICIO <= datetime.now().hour < HORA_FIM


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teste", action="store_true", help="publica só 1 post e para")
    ap.add_argument("--simular", action="store_true", help="não publica nada")
    ap.add_argument("--status", action="store_true", help="mostra o progresso")
    ap.add_argument("--quantidade", type=int, default=POR_DIA)
    ap.add_argument("--ignorar-horario", action="store_true")
    args = ap.parse_args()

    env = load_env()
    token = os.environ.get("IG_TOKEN") or env["IG_TOKEN"]
    with open("posts.json", encoding="utf-8") as f:
        todos = json.load(f)

    reg = carregar_registro()
    fila = [p for p in todos if p["publicar"] == "SIM" and p["fb_id"] not in reg]

    if args.status:
        print(f"Publicados: {len(reg)}    Na fila: {len(fila)}")
        if fila:
            print(f"Próximo: {fila[0]['created_time'][:10]} — {(fila[0]['message'] or '')[:60]}...")
        return

    if not fila:
        log("Nada na fila. Todos os posts marcados já foram publicados.")
        return

    ig_id = api("GET", "me", token, fields="user_id")["user_id"]
    usado = api("GET", "me/content_publishing_limit", token)["data"][0].get("quota_usage", 0)
    log(f"Conta {ig_id} | cota da Meta usada hoje: {usado}/{LIMITE_API_DIA}")

    quantidade = 1 if args.teste else min(args.quantidade, len(fila), LIMITE_API_DIA - usado)
    log(f"Lote de hoje: {quantidade} post(s) | restam {len(fila)} na fila")

    for n, post in enumerate(fila[:quantidade], 1):
        if not args.ignorar_horario and not dentro_do_horario():
            log(f"Fora do horário ({HORA_INICIO}h–{HORA_FIM}h). Parando por hoje.")
            break

        resumo = f"[{n}/{quantidade}] {post['created_time'][:10]} {post['tipo']}: " \
                 f"{(post['message'] or '')[:60].replace(chr(10), ' ')}..."
        if args.simular:
            log("SIMULAÇÃO " + resumo)
            continue

        log("Publicando " + resumo)
        try:
            media_id = publicar_post(post, ig_id, token)
        except ErroAPI as e:
            log(f"ERRO: {e}")
            log("Parando por segurança. Nada mais será publicado até você revisar.")
            sys.exit(1)
        except Exception as e:
            log(f"ERRO inesperado: {e}")
            sys.exit(1)

        link = api("GET", media_id, token, fields="permalink").get("permalink", "")
        reg[post["fb_id"]] = {"ig_media_id": media_id, "permalink": link,
                              "publicado_em": datetime.now().isoformat(timespec="seconds"),
                              "data_original": post["created_time"][:10]}
        salvar_registro(reg)
        log(f"  OK → {link}  (comentários desativados)")

        if n < quantidade:
            espera = random.randint(INTERVALO_MIN, INTERVALO_MAX)
            log(f"  aguardando {espera // 60} min até o próximo")
            time.sleep(espera)

    log(f"Lote encerrado. Publicados no total: {len(reg)} | ainda na fila: "
        f"{len([p for p in todos if p['publicar'] == 'SIM' and p['fb_id'] not in reg])}")


if __name__ == "__main__":
    main()
