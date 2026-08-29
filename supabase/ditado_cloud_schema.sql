-- Ditado Local cloud schema (Supabase / PostgreSQL 17)
-- Apply this file once to the dedicated Ditado Local Supabase project.

create schema if not exists private;
revoke all on schema private from public;

-- New Supabase projects can include the documented event trigger that enables
-- RLS automatically. It must remain installed, but no Data API role needs to
-- invoke its SECURITY DEFINER function directly.
do $$
declare
    auto_rls_function_oid oid := to_regprocedure('public.rls_auto_enable()');
begin
    if auto_rls_function_oid is null then
        return;
    end if;
    if not exists (
        select 1
        from pg_catalog.pg_proc as procedure
        where procedure.oid = auto_rls_function_oid
          and procedure.prosecdef
          and procedure.prorettype = 'event_trigger'::regtype
    ) then
        return;
    end if;
    if not exists (
        select 1
        from pg_catalog.pg_event_trigger as event_trigger
        where event_trigger.evtfoid = auto_rls_function_oid
    ) then
        return;
    end if;

    revoke execute on function public.rls_auto_enable() from public;
    revoke execute on function public.rls_auto_enable() from anon;
    revoke execute on function public.rls_auto_enable() from authenticated;
end;
$$;

create table if not exists public.ditado_user_keys (
    user_id uuid primary key references auth.users(id) on delete cascade,
    wrapped_key jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint ditado_user_keys_wrapped_key_object
        check (jsonb_typeof(wrapped_key) = 'object')
);

create table if not exists public.ditado_sync_items (
    user_id uuid not null references auth.users(id) on delete cascade,
    item_type text not null,
    item_id text not null,
    ciphertext text not null default '',
    updated_at timestamptz not null,
    deleted_at timestamptz,
    device_id uuid not null,
    primary key (user_id, item_type, item_id),
    constraint ditado_sync_items_type
        check (item_type in ('preference', 'correction', 'rule', 'skill', 'history')),
    constraint ditado_sync_items_id_length
        check (length(item_id) between 1 and 160),
    constraint ditado_sync_items_ciphertext_length
        check (length(ciphertext) <= 2000000),
    constraint ditado_sync_items_tombstone_shape
        check (
            (deleted_at is null and length(ciphertext) > 0)
            or (deleted_at is not null and ciphertext = '')
        )
);

create index if not exists ditado_sync_items_user_updated_idx
    on public.ditado_sync_items (user_id, updated_at);

create table if not exists public.ditado_devices (
    user_id uuid not null references auth.users(id) on delete cascade,
    device_id uuid not null,
    name text not null,
    platform text not null default '',
    session_id uuid,
    last_seen timestamptz not null default now(),
    revoked_at timestamptz,
    primary key (user_id, device_id),
    constraint ditado_devices_name_length check (length(name) between 1 and 120),
    constraint ditado_devices_platform_length check (length(platform) <= 200)
);

alter table public.ditado_devices
    add column if not exists session_id uuid;

create or replace function private.ditado_session_is_active()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select
        (select auth.uid()) is not null
        and nullif((select auth.jwt() ->> 'session_id'), '') is not null
        and exists (
            select 1
            from auth.sessions as session
            where session.id = nullif(
                (select auth.jwt() ->> 'session_id'),
                ''
            )::uuid
            and session.user_id = (select auth.uid())
        );
$$;

revoke all on function private.ditado_session_is_active() from public;
grant usage on schema private to authenticated;
grant execute on function private.ditado_session_is_active() to authenticated;

create or replace function private.ditado_revoke_owned_device(
    p_device_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_user_id uuid := (select auth.uid());
    target_session_id uuid;
begin
    if current_user_id is null then
        raise insufficient_privilege using message = 'Authentication required';
    end if;

    if not private.ditado_session_is_active() then
        raise insufficient_privilege using message = 'Active session required';
    end if;

    select device.session_id
    into target_session_id
    from public.ditado_devices as device
    where device.user_id = current_user_id
      and device.device_id = p_device_id
      and device.revoked_at is null;

    if not found then
        return false;
    end if;

    if target_session_id = nullif(
        (select auth.jwt() ->> 'session_id'),
        ''
    )::uuid then
        raise check_violation using message = 'Current device cannot be revoked';
    end if;

    if target_session_id is not null then
        delete from auth.sessions
        where id = target_session_id
          and user_id = current_user_id;
    end if;

    update public.ditado_devices
    set revoked_at = now()
    where user_id = current_user_id
      and device_id = p_device_id;

    return true;
end;
$$;

revoke all on function private.ditado_revoke_owned_device(uuid) from public;
grant execute on function private.ditado_revoke_owned_device(uuid) to authenticated;

create or replace function public.ditado_revoke_device(p_device_id uuid)
returns boolean
language sql
security invoker
set search_path = ''
as $$
    select private.ditado_revoke_owned_device(p_device_id);
$$;

revoke all on function public.ditado_revoke_device(uuid) from public;
revoke all on function public.ditado_revoke_device(uuid) from anon;
grant execute on function public.ditado_revoke_device(uuid) to authenticated;

create or replace function public.ditado_keep_newest_sync_item()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if (new.updated_at, new.device_id::text)
        < (old.updated_at, old.device_id::text) then
        return old;
    end if;
    return new;
end;
$$;

revoke all on function public.ditado_keep_newest_sync_item() from public;
revoke all on function public.ditado_keep_newest_sync_item() from anon;
revoke all on function public.ditado_keep_newest_sync_item() from authenticated;

drop trigger if exists ditado_sync_items_keep_newest
    on public.ditado_sync_items;
create trigger ditado_sync_items_keep_newest
before update on public.ditado_sync_items
for each row execute function public.ditado_keep_newest_sync_item();

alter table public.ditado_user_keys enable row level security;
alter table public.ditado_sync_items enable row level security;
alter table public.ditado_devices enable row level security;

drop policy if exists "ditado_user_keys_select_own" on public.ditado_user_keys;
create policy "ditado_user_keys_select_own"
on public.ditado_user_keys for select
to authenticated
using (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_user_keys_insert_own" on public.ditado_user_keys;
create policy "ditado_user_keys_insert_own"
on public.ditado_user_keys for insert
to authenticated
with check (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_user_keys_update_own" on public.ditado_user_keys;
create policy "ditado_user_keys_update_own"
on public.ditado_user_keys for update
to authenticated
using (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
)
with check (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_sync_items_select_own" on public.ditado_sync_items;
create policy "ditado_sync_items_select_own"
on public.ditado_sync_items for select
to authenticated
using (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_sync_items_insert_own" on public.ditado_sync_items;
create policy "ditado_sync_items_insert_own"
on public.ditado_sync_items for insert
to authenticated
with check (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_sync_items_update_own" on public.ditado_sync_items;
create policy "ditado_sync_items_update_own"
on public.ditado_sync_items for update
to authenticated
using (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
)
with check (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_sync_items_delete_own" on public.ditado_sync_items;
create policy "ditado_sync_items_delete_own"
on public.ditado_sync_items for delete
to authenticated
using (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_devices_select_own" on public.ditado_devices;
create policy "ditado_devices_select_own"
on public.ditado_devices for select
to authenticated
using (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_devices_insert_own" on public.ditado_devices;
create policy "ditado_devices_insert_own"
on public.ditado_devices for insert
to authenticated
with check (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_devices_update_own" on public.ditado_devices;
create policy "ditado_devices_update_own"
on public.ditado_devices for update
to authenticated
using (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
)
with check (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

drop policy if exists "ditado_devices_delete_own" on public.ditado_devices;
create policy "ditado_devices_delete_own"
on public.ditado_devices for delete
to authenticated
using (
    (select auth.uid()) = user_id
    and private.ditado_session_is_active()
);

-- New Supabase projects no longer expose public tables automatically.
-- The publishable client receives table access, then RLS restricts every row.
revoke all on table public.ditado_user_keys from anon;
revoke all on table public.ditado_sync_items from anon;
revoke all on table public.ditado_devices from anon;

grant select, insert, update on table public.ditado_user_keys to authenticated;
grant select, insert, update, delete on table public.ditado_sync_items to authenticated;
grant select, insert, update, delete on table public.ditado_devices to authenticated;
