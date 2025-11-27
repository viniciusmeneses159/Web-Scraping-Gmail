from __future__ import print_function
import os
import base64
import shutil  # Necessário para a função resetar_pastas
from bs4 import BeautifulSoup

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request


SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


# 1) Autenticação

def autenticar():
    creds = None

    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            # Adicionado o 'redirect_uri' para garantir compatibilidade
            creds = flow.run_local_server(port=0, redirect_uri='http://localhost') 

        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return creds


# 2) Função para filtrar emails por categoria

def filtrar_emails_por_tipo(subject, sender, body=""):
    subject = (subject or "").lower()
    sender = (sender or "").lower()
    body = (body or "").lower()

    if any(w in sender for w in ["inter", "itau", "caixa", "bb.com"]):
        return "banco"

    # Corrigido: Incluir "nf-e" (com hífen) na lista
    if any(w in subject for w in ["nota fiscal", "nf-e", "fatura", "boleto"]):
        return "documentos_fiscais"

    if any(w in subject for w in ["livro"]):
        return "livros"
    
    if any(w in subject for w in ["pedido", "compra", "rastreamento"]) or \
        any(site in sender for site in ["amazon", "mercadolivre", "magazineluiza"]):
        return "compras"

    # Corrigido: "[Git hub]" para "github" para ser mais flexível
    if any(w in subject for w in ["github", "trampo", "codigo"]) or \
        any(site in sender for site in ["github", "linkedin", "googleaistudio"]):
        return "codigo_trampo"
    
    if any(w in subject for w in ["guitar", "tab", "solo"]) or "ultimateguitar" in sender:
        return "guitarra"

    if any(w in subject for w in ["exercicio", "ia", "ufs", "noticia"]) or \
        any(site in sender for site in ["sigaa", "ufs"]):
        return "ufs"

    if "unsubscribe" in body or "descadastre" in body:
        return "newsletter"

    return "outros"


# 3) Extrair anexos PDF e XML

def extrair_anexos(service, msg):
    anexos = []
    # Correção: O payload pode ter partes ou ser o próprio corpo em emails simples
    payload = msg.get("payload", {})
    parts = payload.get("parts", [])

    # Se não houver 'parts', tenta analisar o payload diretamente (pode ser o caso de anexos)
    if not parts and payload.get('body'):
        parts.append(payload)

    # Função recursiva para lidar com mensagens multipart/mixed
    def processar_partes(partes_email):
        for part in partes_email:
            filename = part.get("filename")
            body = part.get("body", {})
            attach_id = body.get("attachmentId")
            mime_type = part.get("mimeType")

            # Se for uma parte aninhada (ex: multipart/related, multipart/alternative)
            if mime_type and mime_type.startswith("multipart/") and part.get("parts"):
                processar_partes(part.get("parts"))
                continue

            if not filename or not attach_id:
                continue

            # Garante que é PDF ou XML
            if not (filename.lower().endswith(".pdf") or filename.lower().endswith(".xml")):
                continue

            attachment = service.users().messages().attachments().get(
                userId="me",
                messageId=msg["id"],
                id=attach_id
            ).execute()

            data = attachment.get("data")

            if not data:
                continue

            # O base64 dos anexos do Gmail é url-safe
            file_bytes = base64.urlsafe_b64decode(data.encode("ASCII"))

            anexos.append((filename, file_bytes))
    
    processar_partes(parts)
    return anexos


# 4) Extrair corpo (texto) do email (função original está boa)

def extrair_corpo(msg):
    payload = msg.get("payload", {})
    parts = payload.get("parts", [])

    for part in parts:
        # Tenta extrair primeiro o texto puro
        if part.get("mimeType") == "text/plain":
            data = part["body"].get("data")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

        # Se não encontrar, tenta extrair do HTML
        if part.get("mimeType") == "text/html":
            data = part["body"].get("data")
            if data:
                html = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                return soup.get_text(separator="\n")
        
        # Lida com estruturas multipart aninhadas (recursivamente)
        if part.get("parts"):
            corpo_aninhado = extrair_corpo({"payload": part})
            if corpo_aninhado:
                return corpo_aninhado

    return ""


# 5) Criar pastas automaticamente

def garantir_pasta(pasta):
    """
    Cria a pasta, garantindo que a estrutura de diretórios exista.
    """
    os.makedirs(pasta, exist_ok=True)
    return pasta

# 6) Função para resetar/limpar os dados

def resetar_pastas(base_dir="emails"):
    """
    Remove o diretório base e todos os seus conteúdos para limpar os dados extraídos.
    """
    if os.path.exists(base_dir):
        try:
            # Função para remoção recursiva de diretórios
            shutil.rmtree(base_dir) 
            print(f"\n[RESET] Pasta '{base_dir}' e seu conteúdo foram removidos com sucesso.")
        except OSError as e:
            print(f"\n[ERRO] Não foi possível remover a pasta {base_dir}: {e}")
    else:
        print(f"\n[INFO] Pasta '{base_dir}' não existe. Nada a resetar.")


# 7) Processar emails

def main():
    # Opção para resetar os dados antes de iniciar
    # Descomente a linha abaixo se você quiser limpar a pasta 'emails' em cada execução
    # resetar_pastas() 
    
    creds = autenticar()
    service = build("gmail", "v1", credentials=creds)

    # Buscar emails (pode adicionar um 'q' para filtros específicos)
    print("Buscando e-mails...")
    results = service.users().messages().list(userId="me", maxResults=20).execute()
    mensagens = results.get("messages", [])

    if not mensagens:
        print("Nenhum e-mail encontrado.")
        return

    for m in mensagens:
        msg_id = m["id"]
        
        # Formato 'full' é o ideal para extrair corpo e anexos
        msg = service.users().messages().get(
            userId="me",
            id=msg_id,
            format="full"
        ).execute()

        headers = msg["payload"].get("headers", [])

        subject = ""
        sender = ""

        for h in headers:
            if h["name"] == "Subject":
                subject = h["value"]
            if h["name"] == "From":
                sender = h["value"]

        corpo = extrair_corpo(msg)
        categoria = filtrar_emails_por_tipo(subject, sender, corpo)
        
        # Estrutura: emails/categoria/ID_do_email/
        pasta_categoria = os.path.join("emails", categoria)
        pasta_email = os.path.join(pasta_categoria, msg_id)
        pasta = garantir_pasta(pasta_email)

        # 7.1) Criar arquivo info.txt
        info_path = os.path.join(pasta, "info.txt")
        with open(info_path, "w", encoding="utf-8") as f: # Use "w" para criar um novo arquivo por email
            f.write("="*50 + "\n")
            f.write(f"ID da Mensagem: {msg_id}\n")
            f.write(f"Assunto: {subject}\n")
            f.write(f"De: {sender}\n\n")
            f.write(f"Trecho: {msg.get('snippet')}\n\n")
            f.write("Corpo do email:\n")
            # Limita o corpo para evitar arquivos info.txt muito grandes
            f.write(corpo[:2000] + ("..." if len(corpo) > 2000 else "") + "\n\n") 
        
        # 7.2) Anexos PDF/XML
        anexos = extrair_anexos(service, msg)
        for nome, dados in anexos:
            anexo_path = os.path.join(pasta, nome)
            with open(anexo_path, "wb") as f:
                f.write(dados)
        
        print(f"[OK] E-mail {msg_id} salvo na categoria '{categoria}' em: {pasta}")


if __name__ == "__main__":
    # Exemplo de uso da função de reset
    # Se você quiser apenas rodar o reset, chame-a diretamente:
    # resetar_pastas()
    
    main()