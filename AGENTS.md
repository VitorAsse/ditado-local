# Ditado Local Agent Router

Este repositório contém o Ditado Local, o aplicativo Windows de voz desenvolvido com Faster Whisper e Ollama.

## Identidade e roteamento

- Caminho canônico: `C:\Users\Vitor\Desktop\ditado-local`.
- Repositório GitHub: `VitorAsse/ditado-local`.
- Termos como `Ditado`, `Ditado Local`, `app de voz` e `aplicativo de ditado` apontam para este projeto.
- Ditado Local é separado da Agencify. Nunca publique arquivos da Agencify quando o pedido nomear este aplicativo.

## Verificação

Antes de publicar alterações:

```powershell
.\.venv\Scripts\python.exe -m py_compile ditado_ai.py ditado_audio.py ditado_local.pyw ditado_storage.py
.\.venv\Scripts\python.exe -m unittest discover -v
```

Para validar o pacote destinado a novos usuários:

```powershell
powershell -File .\scripts\package-release.ps1 -Version <versão>
```

## Entrega

- Não faça commit, push, tag ou Release sem pedido explícito.
- Revise o diff e exclua dados do usuário, configurações locais, modelos e artefatos gerados.
- Atualize `CHANGELOG.md` e a versão padrão do empacotamento quando publicar uma nova Release.
- Uma publicação para novos usuários só termina quando a Release do GitHub contém o arquivo `DitadoLocal-<versão>.zip` e a CI está aprovada.
