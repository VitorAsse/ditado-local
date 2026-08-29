[CmdletBinding()]
param(
    [string]$OrganizationName = "Misc",
    [string]$ProjectName = "Ditado Local",
    [bool]$RequireEmailConfirmation = $false
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Invoke-SupabaseManagementApi {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("Get", "Patch", "Post")]
        [string]$Method,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [object]$Body
    )

    $parameters = @{
        Method      = $Method
        Uri         = "https://api.supabase.com/v1/$Path"
        Headers     = $script:SupabaseHeaders
        ErrorAction = "Stop"
        TimeoutSec  = 30
    }
    if ($null -ne $Body) {
        $parameters.ContentType = "application/json"
        $parameters.Body = $Body | ConvertTo-Json -Compress -Depth 12
    }
    Invoke-RestMethod @parameters
}

function Invoke-SupabaseProjectApi {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("Get", "Put")]
        [string]$Method,

        [Parameter(Mandatory = $true)]
        [string]$ProjectUrl,

        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$AdminKey,

        [object]$Body
    )

    $parameters = @{
        Method      = $Method
        Uri         = "$ProjectUrl$Path"
        Headers     = @{
            apikey        = $AdminKey
            Authorization = "Bearer $AdminKey"
        }
        ErrorAction = "Stop"
        TimeoutSec  = 30
    }
    if ($null -ne $Body) {
        $parameters.ContentType = "application/json"
        $parameters.Body = $Body | ConvertTo-Json -Compress -Depth 12
    }
    Invoke-RestMethod @parameters
}

function Get-ResponseItems {
    param(
        [object]$Response,
        [string[]]$ContainerNames
    )

    foreach ($containerName in $ContainerNames) {
        $property = $Response.PSObject.Properties[$containerName]
        if ($null -ne $property) {
            return @($property.Value)
        }
    }
    return @($Response)
}

function Get-ApiKeyValue {
    param([object]$ApiKey)

    foreach ($propertyName in @("api_key", "value", "key")) {
        $property = $ApiKey.PSObject.Properties[$propertyName]
        if ($null -ne $property -and -not [string]::IsNullOrWhiteSpace($property.Value)) {
            return [string]$property.Value
        }
    }
    return ""
}

function Set-ObjectProperty {
    param(
        [Parameter(Mandatory = $true)]
        [object]$InputObject,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        [AllowNull()]
        [object]$Value
    )

    if ($null -eq $InputObject.PSObject.Properties[$Name]) {
        $InputObject | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
    else {
        $InputObject.$Name = $Value
    }
}

function Set-DitadoCloudBackend {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StatePath,

        [Parameter(Mandatory = $true)]
        [string]$Url,

        [Parameter(Mandatory = $true)]
        [string]$PublishableKey
    )

    $state = [pscustomobject][ordered]@{
        version = 1
        backend = [pscustomobject][ordered]@{
            url = ""
            publishable_key = ""
        }
        device_id = ""
        device_name = ""
        active_user_id = ""
        sessions = [pscustomobject]@{}
        master_keys = [pscustomobject]@{}
        sync = [pscustomobject]@{}
    }

    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        try {
            $encoded = [System.IO.File]::ReadAllText($StatePath).Trim()
            $encrypted = [System.Convert]::FromBase64String($encoded)
            $decoded = [System.Security.Cryptography.ProtectedData]::Unprotect(
                $encrypted,
                $null,
                [System.Security.Cryptography.DataProtectionScope]::CurrentUser
            )
            $loaded = [System.Text.Encoding]::UTF8.GetString($decoded) | ConvertFrom-Json
            if ($null -ne $loaded) {
                $state = $loaded
            }
        }
        catch {
            throw "Nao foi possivel atualizar cloud_state.dat com seguranca: $($_.Exception.Message)"
        }
    }

    Set-ObjectProperty -InputObject $state -Name "version" -Value 1
    Set-ObjectProperty -InputObject $state -Name "backend" -Value (
        [pscustomobject][ordered]@{
            url = $Url
            publishable_key = $PublishableKey
        }
    )
    if ([string]::IsNullOrWhiteSpace([string]$state.device_id)) {
        Set-ObjectProperty -InputObject $state -Name "device_id" -Value ([guid]::NewGuid().ToString())
    }
    if ([string]::IsNullOrWhiteSpace([string]$state.device_name)) {
        Set-ObjectProperty -InputObject $state -Name "device_name" -Value ([Environment]::MachineName)
    }
    foreach ($defaultProperty in @("active_user_id", "sessions", "master_keys", "sync")) {
        if ($null -eq $state.PSObject.Properties[$defaultProperty]) {
            $defaultValue = if ($defaultProperty -eq "active_user_id") { "" } else { [pscustomobject]@{} }
            Set-ObjectProperty -InputObject $state -Name $defaultProperty -Value $defaultValue
        }
    }

    $payload = $state | ConvertTo-Json -Compress -Depth 30
    $payloadBytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
    $protected = [System.Security.Cryptography.ProtectedData]::Protect(
        $payloadBytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $temporaryPath = "$StatePath.tmp"
    try {
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            [System.Convert]::ToBase64String($protected),
            [System.Text.Encoding]::ASCII
        )
        Move-Item -LiteralPath $temporaryPath -Destination $StatePath -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$schemaPath = Join-Path $repositoryRoot "supabase\ditado_cloud_schema.sql"
$installRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA "faster-whisper")
)
$resultPath = Join-Path $installRoot "supabase-bootstrap-result.json"
$secureToken = $null
$tokenPointer = [IntPtr]::Zero
$plainToken = $null
$adminKey = $null
$script:SupabaseHeaders = $null
$stage = "starting"

try {
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    [ordered]@{
        success = $false
        stage = "waiting_for_token"
        message = "Aguardando o token no terminal seguro."
    } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding utf8

    if (-not (Test-Path -LiteralPath $schemaPath -PathType Leaf)) {
        throw "Schema nao encontrado em $schemaPath"
    }

    Clear-Host
    Write-Host "Ditado Local - configuracao segura do Supabase" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Organizacao: $OrganizationName"
    Write-Host "Projeto:      $ProjectName"
    Write-Host ""
    Write-Host "Cole um Personal Access Token criado em:" -ForegroundColor Yellow
    Write-Host "https://supabase.com/dashboard/account/tokens"
    Write-Host "O token ficara somente na memoria deste processo." -ForegroundColor DarkGray
    Write-Host ""

    $stage = "reading_token"
    $secureToken = Read-Host "Personal Access Token" -AsSecureString
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(
        $secureToken
    )
    $plainToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        $tokenPointer
    )
    if ([string]::IsNullOrWhiteSpace($plainToken)) {
        throw "Nenhum token foi informado."
    }
    $script:SupabaseHeaders = @{ Authorization = "Bearer $plainToken" }

    $stage = "discovering_organization"
    Write-Host "Localizando organizacao e projeto..." -ForegroundColor Cyan
    $organizationsResponse = Invoke-SupabaseManagementApi `
        -Method Get `
        -Path "organizations"
    $organizations = Get-ResponseItems `
        -Response $organizationsResponse `
        -ContainerNames @("organizations")
    $organization = @(
        $organizations | Where-Object {
            $_.name -ieq $OrganizationName
        }
    )
    if ($organization.Count -ne 1) {
        throw "A organizacao '$OrganizationName' nao foi encontrada de forma unica para este token."
    }
    $organization = $organization[0]

    $stage = "discovering_project"
    $projectsResponse = Invoke-SupabaseManagementApi -Method Get -Path "projects"
    $projects = Get-ResponseItems `
        -Response $projectsResponse `
        -ContainerNames @("projects")
    $project = @(
        $projects | Where-Object {
            $_.name -ieq $ProjectName -and
            ($_.organization_id -eq $organization.id -or
                $_.organization_slug -eq $organization.slug)
        }
    )
    if ($project.Count -ne 1) {
        throw "O projeto '$ProjectName' nao foi encontrado de forma unica na organizacao '$OrganizationName'."
    }
    $project = $project[0]
    $projectRef = if ($project.ref) { $project.ref } else { $project.id }
    if ([string]::IsNullOrWhiteSpace($projectRef)) {
        throw "O Supabase nao retornou o identificador do projeto."
    }
    if ($project.status -and $project.status -ne "ACTIVE_HEALTHY") {
        throw "O projeto ainda nao esta pronto: $($project.status)"
    }

    $stage = "configuring_auth"
    Write-Host "Configurando o acesso por e-mail e senha..." -ForegroundColor Cyan
    $authConfig = Invoke-SupabaseManagementApi `
        -Method Get `
        -Path "projects/$projectRef/config/auth"
    $targetAutoConfirm = -not $RequireEmailConfirmation
    if ([bool]$authConfig.mailer_autoconfirm -ne $targetAutoConfirm) {
        $null = Invoke-SupabaseManagementApi `
            -Method Patch `
            -Path "projects/$projectRef/config/auth" `
            -Body @{ mailer_autoconfirm = $targetAutoConfirm }
    }
    $verifiedAuthConfig = Invoke-SupabaseManagementApi `
        -Method Get `
        -Path "projects/$projectRef/config/auth"
    if ([bool]$verifiedAuthConfig.mailer_autoconfirm -ne $targetAutoConfirm) {
        throw "A configuracao de confirmacao de e-mail nao foi aplicada."
    }

    $stage = "applying_schema"
    Write-Host "Aplicando o schema com RLS e isolamento por usuario..." -ForegroundColor Cyan
    $schemaSql = Get-Content -Raw -LiteralPath $schemaPath
    $null = Invoke-SupabaseManagementApi `
        -Method Post `
        -Path "projects/$projectRef/database/query" `
        -Body @{ query = $schemaSql }

    $stage = "verifying_schema"
    Write-Host "Verificando tabelas, RLS, politicas e funcoes..." -ForegroundColor Cyan
    $verificationSql = @'
select json_build_object(
    'tables', (
        select count(*)
        from pg_catalog.pg_tables
        where schemaname = 'public'
          and tablename in (
              'ditado_user_keys',
              'ditado_sync_items',
              'ditado_devices'
          )
    ),
    'rls_tables', (
        select count(*)
        from pg_catalog.pg_class as relation
        join pg_catalog.pg_namespace as namespace
          on namespace.oid = relation.relnamespace
        where namespace.nspname = 'public'
          and relation.relname in (
              'ditado_user_keys',
              'ditado_sync_items',
              'ditado_devices'
          )
          and relation.relrowsecurity
    ),
    'policies', (
        select count(*)
        from pg_catalog.pg_policies
        where schemaname = 'public'
          and tablename in (
              'ditado_user_keys',
              'ditado_sync_items',
              'ditado_devices'
          )
    ),
    'revoke_rpc', to_regprocedure('public.ditado_revoke_device(uuid)') is not null,
    'session_guard', to_regprocedure('private.ditado_session_is_active()') is not null
) as verification;
'@
    $verificationResponse = Invoke-SupabaseManagementApi `
        -Method Post `
        -Path "projects/$projectRef/database/query" `
        -Body @{ query = $verificationSql }
    $verificationRows = Get-ResponseItems `
        -Response $verificationResponse `
        -ContainerNames @("result")
    $verification = $verificationRows[0].verification
    if (
        [int]$verification.tables -ne 3 -or
        [int]$verification.rls_tables -ne 3 -or
        [int]$verification.policies -lt 10 -or
        -not [bool]$verification.revoke_rpc -or
        -not [bool]$verification.session_guard
    ) {
        throw "A verificacao do schema retornou um estado incompleto."
    }

    $stage = "fetching_api_keys"
    Write-Host "Buscando as chaves do projeto para a configuracao segura..." -ForegroundColor Cyan
    $apiKeysResponse = Invoke-SupabaseManagementApi `
        -Method Get `
        -Path "projects/$projectRef/api-keys?reveal=true"
    $apiKeys = Get-ResponseItems `
        -Response $apiKeysResponse `
        -ContainerNames @("api_keys", "keys")
    $publishableKey = ""
    foreach ($apiKey in $apiKeys) {
        $keyType = [string]$apiKey.type
        $keyName = [string]$apiKey.name
        $candidate = Get-ApiKeyValue -ApiKey $apiKey
        if ($keyType -ieq "publishable" -or $keyName -ieq "anon") {
            if ($candidate.StartsWith("sb_publishable_") -or $candidate.StartsWith("eyJ")) {
                if (
                    [string]::IsNullOrWhiteSpace($publishableKey) -or
                    $candidate.StartsWith("sb_publishable_")
                ) {
                    $publishableKey = $candidate
                }
            }
        }
        if ($keyType -ieq "secret" -or $keyName -ieq "service_role") {
            if ($candidate.StartsWith("sb_secret_") -or $candidate.StartsWith("eyJ")) {
                if (
                    [string]::IsNullOrWhiteSpace($adminKey) -or
                    $candidate.StartsWith("sb_secret_")
                ) {
                    $adminKey = $candidate
                }
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($publishableKey)) {
        throw "Nenhuma chave publicavel ativa foi encontrada no projeto."
    }

    $projectUrl = "https://$projectRef.supabase.co"
    $confirmedPendingAccount = $false
    $pendingAccountCount = 0
    if (-not $RequireEmailConfirmation) {
        if ([string]::IsNullOrWhiteSpace($adminKey)) {
            throw "Nenhuma chave administrativa temporaria foi encontrada para regularizar a conta pendente."
        }
        $stage = "confirming_pending_account"
        Write-Host "Regularizando a conta que ficou aguardando confirmacao..." -ForegroundColor Cyan
        $usersResponse = Invoke-SupabaseProjectApi `
            -Method Get `
            -ProjectUrl $projectUrl `
            -Path "/auth/v1/admin/users?page=1&per_page=1000" `
            -AdminKey $adminKey
        $pendingUsers = @(
            $usersResponse.users | Where-Object {
                -not [string]::IsNullOrWhiteSpace([string]$_.email) -and
                [string]::IsNullOrWhiteSpace([string]$_.email_confirmed_at)
            }
        )
        $pendingAccountCount = $pendingUsers.Count
        if ($pendingAccountCount -eq 1) {
            $confirmedUser = Invoke-SupabaseProjectApi `
                -Method Put `
                -ProjectUrl $projectUrl `
                -Path "/auth/v1/admin/users/$($pendingUsers[0].id)" `
                -AdminKey $adminKey `
                -Body @{ email_confirm = $true }
            if ([string]::IsNullOrWhiteSpace([string]$confirmedUser.email_confirmed_at)) {
                throw "A conta pendente nao foi confirmada pelo Auth."
            }
            $confirmedPendingAccount = $true
        }
        elseif ($pendingAccountCount -gt 1) {
            throw "Ha mais de uma conta pendente; nenhuma foi confirmada automaticamente por seguranca."
        }
    }

    $stage = "configuring_application"
    $cloudStatePath = Join-Path $installRoot "cloud_state.dat"
    Set-DitadoCloudBackend `
        -StatePath $cloudStatePath `
        -Url $projectUrl `
        -PublishableKey $publishableKey

    $stage = "running_security_advisor"
    Write-Host "Executando o Security Advisor..." -ForegroundColor Cyan
    $securityAdvisors = Invoke-SupabaseManagementApi `
        -Method Get `
        -Path "projects/$projectRef/advisors/security"

    $stage = "running_performance_advisor"
    Write-Host "Executando o Performance Advisor..." -ForegroundColor Cyan
    $performanceAdvisors = Invoke-SupabaseManagementApi `
        -Method Get `
        -Path "projects/$projectRef/advisors/performance"

    $result = [ordered]@{
        success           = $true
        completed_at      = [DateTimeOffset]::UtcNow.ToString("o")
        organization_id   = $organization.id
        organization_name = $organization.name
        project_id        = $projectRef
        project_name      = $project.name
        project_url       = $projectUrl
        schema_applied    = $true
        app_configured    = $true
        email_confirmation_required = $RequireEmailConfirmation
        pending_accounts_found = $pendingAccountCount
        pending_account_confirmed = $confirmedPendingAccount
        verification      = $verification
        security_advisors = $securityAdvisors
        performance_advisors = $performanceAdvisors
    }
    $result | ConvertTo-Json -Depth 12 | Set-Content `
        -LiteralPath $resultPath `
        -Encoding utf8

    Write-Host ""
    Write-Host "Configuracao concluida com sucesso." -ForegroundColor Green
    Write-Host "O token nao foi salvo."
    Write-Host "Projeto: $projectUrl"
    Write-Host ""
} catch {
    $safeError = $_.Exception.Message
    if (-not [string]::IsNullOrWhiteSpace($plainToken)) {
        $safeError = $safeError -replace [regex]::Escape($plainToken), "[REDACTED]"
    }
    if (-not [string]::IsNullOrWhiteSpace($adminKey)) {
        $safeError = $safeError -replace [regex]::Escape($adminKey), "[REDACTED]"
    }
    [ordered]@{
        success      = $false
        completed_at = [DateTimeOffset]::UtcNow.ToString("o")
        stage        = $stage
        error        = $safeError
    } | ConvertTo-Json -Depth 5 | Set-Content `
        -LiteralPath $resultPath `
        -Encoding utf8
    Write-Host ""
    Write-Host "Nao foi possivel concluir a configuracao:" -ForegroundColor Red
    Write-Host $safeError -ForegroundColor Red
    Write-Host ""
    exit 1
} finally {
    $script:SupabaseHeaders = $null
    $adminKey = $null
    $plainToken = $null
    $secureToken = $null
    if ($tokenPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }
}
