# Sincronização na nuvem

O Ditado Local usa uma arquitetura local-first: toda alteração é salva primeiro no
PC, entra em uma fila persistente e pode ser enviada ao Supabase quando houver rede.
A conta da nuvem é opcional e o ditado continua funcionando sem ela.

## O que foi implementado

- Contas separadas por `auth.users.id`, inclusive troca entre contas salvas no mesmo
  Windows.
- Perfis locais independentes por conta.
- Sincronização de correções, regras, skills, preferências selecionadas, transcrições,
  resultados do agente e o contexto necessário para continuar conversas.
- Skills incluem nome, descrição, gatilhos, instruções, exemplos, tipo, formato padrão e estado ativo ou
  pausado. Criar, editar, pausar ou excluir uma skill ou regra solicita sincronização
  imediata quando a conta está conectada; a sincronização automática a cada 30 segundos
  também cobre essas alterações e o histórico. Sem conexão, os dados continuam locais
  até uma sincronização bem-sucedida.
- Preferências da conta: colagem automática, revisão gramatical, captura do histórico
  da área de transferência, silenciamento durante gravação, idioma de transcrição e
  limite do histórico, além do nome e aliases usados pelo agente para reconhecer o autor
  (`user_identity`) e do atalho editável para abrir o chat (`agent_chat_hotkey`).
  O gesto de correção rápida (`quick_correction_gesture`) também sincroniza,
  inclusive quando desativado, e é aplicado sem reiniciar o aplicativo.
  Salvar identificação ou atalho solicita sincronização imediata. Em outro PC,
  o atalho restaurado é registrado localmente; se estiver ocupado, o app avisa e
  mantém o atalho que já estava funcionando naquele PC.
  O limite também determina a retenção do histórico sincronizado;
  não é um arquivo ilimitado de todas as transcrições anteriores.
- Criptografia ponta a ponta com AES-256-GCM. O servidor recebe somente ciphertext,
  tipo do registro, identificador, horário e identificador do dispositivo.
- Chave mestra aleatória por usuário, protegida localmente pelo DPAPI do Windows.
- Chave de recuperação de alta entropia para abrir os dados em outro computador. A
  chave mestra armazenada no Supabase é cifrada com PBKDF2-HMAC-SHA256 (600.000
  iterações) e AES-256-GCM.
- Fila offline, IDs estáveis, sincronização por registro, tombstones de exclusão e
  resolução determinística de conflitos pelo par `updated_at` + `device_id`.
- Fila salva antes das requisições e preservada em falhas. Leitura paginada inclui
  registros ativos e exclusões mesmo quando ultrapassam o limite de uma resposta da API.
  Alterações recebidas na primeira leitura também atualizam a interface; edições locais
  não disputam a gravação do merge. Preferências desconhecidas de versões mais novas
  são ignoradas, sem gerar exclusões para elas.
- Cadastro de dispositivos e revogação da sessão Supabase ligada a cada PC. As
  políticas também verificam se o `session_id` continua em `auth.sessions`, de modo
  que um token de uma sessão removida não acessa os dados mesmo antes de expirar.
- RLS em todas as tabelas expostas, com políticas baseadas em `auth.uid()` e grants
  explícitos apenas para `authenticated`.

## O que nunca é enviado

- áudio bruto;
- nome do microfone;
- configuração de inicialização com o Windows;
- perfil/modelo de transcrição, cache de modelos e bibliotecas de GPU;
- tokens ou a chave mestra sem a proteção local do Windows.

Nome do microfone, inicialização com o Windows e perfil de transcrição são escolhas
específicas do dispositivo e ficam locais. Áudio bruto, buffers de gravação, rascunhos
ainda não enviados/salvos e estado temporário das janelas não fazem parte do histórico.
O código do app, incluindo o prompt base, é distribuído por atualização/release;
preferências editáveis de redação devem ser cadastradas como regras ou skills.

## Provisionamento do Supabase

Use um projeto dedicado ao Ditado Local. Não reutilize um banco de outro produto.

1. Crie o projeto e mantenha o provedor Email/Senha habilitado em Auth.
2. Execute [`supabase/ditado_cloud_schema.sql`](../supabase/ditado_cloud_schema.sql)
   no projeto.
3. Rode os Security e Performance Advisors e corrija qualquer alerta antes de
   liberar usuários.
4. Copie a Project URL e uma chave publicável `sb_publishable_...`. Nunca use
   `service_role` ou uma secret key no aplicativo.
5. No Ditado Local, abra `Nuvem`, informe os dois valores e clique em
   `Salvar conexão`.
6. Crie a conta, confirme o e-mail se o projeto exigir, entre e salve imediatamente
   a chave de recuperação exibida.

Para provisionar um projeto existente sem expor um Personal Access Token no
histórico do terminal, execute `scripts/configure-supabase-secure.ps1`. O script
usa `Read-Host -AsSecureString`, mantém o token somente na memória, aplica e
verifica o schema, executa os Security e Performance Advisors e salva no
aplicativo apenas a URL e a chave publicável. O hardening preserva o event
trigger oficial `rls_auto_enable`, quando presente, mas remove sua execução
direta por `anon` e `authenticated`. Secret keys e `service_role` nunca são
copiadas para o PC.

Projetos Supabase recentes não expõem tabelas novas automaticamente. O schema já
inclui os grants necessários; eles não substituem RLS, e ambos são obrigatórios.

## Multiusuário

Cada conta lê somente linhas cujo `user_id` corresponde ao `auth.uid()` do JWT. A
interface permite alternar entre contas já autenticadas no mesmo PC e carrega o
perfil local correto. Entrar pela primeira vez em uma conta importa os dados locais
existentes do modo sem conta e os combina por registro com os dados daquela conta.
Entrar ou alternar a partir de outra conta nunca importa as skills, regras, correções
ou histórico dessa outra pessoa, mesmo quando o perfil de destino ainda não existe.

Esta versão trata multiusuário como contas privadas e isoladas. Ela não compartilha
histórico ou dicionários entre pessoas e não implementa espaços colaborativos; isso
evita tornar transcrições pessoais visíveis por acidente.

Remover outro dispositivo chama uma função pública `SECURITY INVOKER`, que delega a
revogação a uma função `SECURITY DEFINER` mantida no schema não exposto `private`. A
função interna confirma `auth.uid()`, limita a busca ao dono da linha e apaga somente
a sessão associada àquele dispositivo.

## Recuperação e perda de chave

O Supabase não recebe a chave de recuperação nem a chave mestra em claro. Em um novo
PC, a senha da conta autentica o usuário, mas não decifra o conteúdo: a chave de
recuperação também é obrigatória. Se todos os dispositivos forem perdidos e a chave
de recuperação não tiver sido guardada, os ciphertexts não podem ser recuperados.

## Verificação

Os testes em `test_ditado_cloud.py` cobrem autenticação do ciphertext, chave incorreta,
restauração simulada em um segundo dispositivo (skills completas, regras pausadas,
preferências e continuação do agente), isolamento na troca de contas, exclusões,
recuperação após falhas de rede, paginação e presença das políticas RLS. A validação
final de um ambiente real deve
também criar duas contas de teste e confirmar que nenhuma delas consulta registros da
outra.
