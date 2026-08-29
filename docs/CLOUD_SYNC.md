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
- Criptografia ponta a ponta com AES-256-GCM. O servidor recebe somente ciphertext,
  tipo do registro, identificador, horário e identificador do dispositivo.
- Chave mestra aleatória por usuário, protegida localmente pelo DPAPI do Windows.
- Chave de recuperação de alta entropia para abrir os dados em outro computador. A
  chave mestra armazenada no Supabase é cifrada com PBKDF2-HMAC-SHA256 (600.000
  iterações) e AES-256-GCM.
- Fila offline, IDs estáveis, sincronização por registro, tombstones de exclusão e
  resolução determinística de conflitos pelo par `updated_at` + `device_id`.
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
existentes e os combina por registro com os dados daquela conta.

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
restauração simulada em um segundo dispositivo, isolamento por usuário, exclusões por
tombstone e presença das políticas RLS. A validação final de um ambiente real deve
também criar duas contas de teste e confirmar que nenhuma delas consulta registros da
outra.
