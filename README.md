# Publicação automática: Facebook → Instagram (@documentonobr)

Publica no Instagram, um por vez, os posts já existentes na Página do Facebook "Doc no Brasil".

## Como funciona

O GitHub Actions dispara 8 vezes por dia, das 9h às 18h (horário de Brasília), e cada
execução publica **1 post**. O progresso fica em `publicados.json`, então nada é
publicado duas vezes, mesmo que uma execução falhe.

| Arquivo | O que faz |
|---|---|
| `inventario.py` | Lê a Página do Facebook e gera `posts.json` e `revisao.xlsx` |
| `publicar.py` | Publica no Instagram os posts marcados como SIM |
| `posts.json` | Todos os posts, com status e marcação de publicar ou não |
| `excluir.txt` | IDs que nunca devem ser publicados |
| `midia/` | Imagens e vídeos hospedados para o Instagram buscar |
| `publicados.json` | Registro do que já foi publicado, com data e link |

Todo post publicado sai com **comentários desativados**. O script desliga os comentários
logo após publicar e confere o resultado; se por algum motivo continuarem ativos, ele
trata como erro e para.

## Comandos

```bash
python3 publicar.py --status            # ver o progresso
python3 publicar.py --simular           # ver o que seria publicado
python3 publicar.py --quantidade 1      # publicar 1 post
python3 inventario.py                   # reler a Página (precisa do FB_TOKEN válido)
```

## Como parar tudo

No site do GitHub: **Settings → Actions → Disable Actions**. Ou apague o arquivo
`.github/workflows/publicar.yml`.

## Segredos

O token do Instagram fica em **Settings → Secrets → Actions**, com o nome `IG_TOKEN`.
Nunca colocar token no código. O `.env` está no `.gitignore`.
