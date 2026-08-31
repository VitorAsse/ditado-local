# Changelog

Todas as alterações relevantes serão documentadas neste arquivo.

## 0.3.1 - 2026-08-31

- Correções ortográficas adicionadas ou removidas passam a iniciar uma sincronização
  imediata quando a conta Supabase está conectada.
- A interface informa quando a alteração foi enviada à nuvem, ficou salva somente no
  computador ou aguardará a próxima sincronização.

## 0.3.0 - 2026-08-29

- `Ctrl + Espaço` passa a ter uma sessão própria e sempre prevalece sobre eventos
  antigos do atalho do agente.
- A revisão gramatical aceita somente JSON estruturado e preserva perguntas literais,
  impedindo que o modelo responda à fala no modo de transcrição.
- Nova aba `Nuvem` com contas separadas, troca de perfil, fila offline e sincronização
  automática de correções, regras, skills, preferências e histórico.
- Conteúdo sincronizado recebe criptografia ponta a ponta AES-256-GCM, chave de
  recuperação e proteção local pelo DPAPI do Windows.
- Dispositivos ficam associados à sessão Supabase correspondente; a remoção revoga a
  sessão e as políticas RLS rejeitam tokens cuja sessão não está mais ativa.
- Schema dedicado inclui grants explícitos, RLS por `auth.uid()`, tombstones e
  resolução determinística de conflitos.
- A saída de áudio padrão é memorizada antes de abrir o microfone e restaurada somente
  depois de fechar a captura, evitando trocas de endpoint causadas por perfis Bluetooth.
- Novo bootstrap de terminal lê o token de administração como `SecureString`, aplica e
  verifica o schema e configura somente a chave publicável no aplicativo.
- O instalador passa a habilitar a inicialização com o Windows por padrão e remove o
  atalho quando essa opção é desativada explicitamente.

## 0.2.2 - 2026-08-14

- A revisão gramatical trata a fala transcrita como texto literal, inclusive quando contém frases no imperativo.
- Respostas que resumem, explicam, mudam números ou transformam o formato são descartadas antes de substituir a transcrição.
- Quando a revisão local se afasta do conteúdo falado, o aplicativo preserva automaticamente o texto bruto do Whisper.

## 0.2.1 - 2026-08-12

- `Ctrl + Espaço` sempre inicia o ditado, mesmo quando existe um estado antigo de `Alt`.
- A gravação do agente muda com segurança para ditado quando o atalho de ditado prevalece.
- Eventos de teclado gerados pelo próprio aplicativo não reativam os atalhos globais.
- A captura de texto selecionado é cancelada sem publicar conteúdo antigo ao trocar de modo.

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
