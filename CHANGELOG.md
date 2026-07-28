# Changelog

Todas as alterações relevantes serão documentadas neste arquivo.

## 0.2.0 - 2026-07-28

- Conversas do agente podem ser retomadas pelo Histórico em um mini chat local.
- O overlay da primeira resposta oferece o botão clicável `Continuar no chat`.
- A continuação acontece no mini chat digitado, sem exigir a janela principal.
- O menu da bandeja abre diretamente a conversa mais recente em `Conversar com o agente`.
- Skills específicas continuam fora do prompt até que um gatilho explícito corresponda.
- O histórico criptografado preserva o texto original e os turnos da conversa.
- A interface passa a carregar DM Sans, a mesma família dos apps Agencify e Agencify Ops.
- Cabeçalho, navegação, estados e mini chat recebem uma paleta mais consistente.

## 0.1.0

- Primeira versão pública do Ditado Local.
- Ditado global com Faster Whisper.
- Ações por voz em texto selecionado usando Ollama.
- Idioma automático ou selecionável.
- Correções personalizadas, regras permanentes e skills acionáveis.
- Histórico local protegido pelo Windows.
- Captura de conteúdo copiado por outros aplicativos desativada por padrão.
- Instalador local sem inclusão de modelos ou dados pessoais.
- Dependências reproduzíveis verificadas por hashes no instalador e na CI.
