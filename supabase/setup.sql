-- NephroWoche – Datenbank
-- Einmal komplett im SQL Editor des eigenen Supabase-Projekts "nephrowoche" ausführen.
-- Das Skript kann gefahrlos mehrfach laufen.
-- Am Ende wird der Leser angelegt; die Abfrage ganz unten zeigt seinen persönlichen Link-Schlüssel.

-- ---------- Tabellen ----------
create table if not exists nw_readers (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  token text not null unique default replace(gen_random_uuid()::text, '-', ''),
  created_at timestamptz not null default now()
);

-- Eine Ausgabe pro Woche, z. B. id = '2026-W48'
create table if not exists nw_issues (
  id text primary key,
  week_label text not null default '',
  published_at timestamptz,
  lead jsonb not null default '[]',      -- die drei wichtigsten Punkte
  trends jsonb not null default '[]',    -- Themen im Trend
  stats jsonb not null default '{}',     -- Zahlen zum Lauf (Quellen, Kandidaten, Kosten)
  created_at timestamptz not null default now()
);

create table if not exists nw_items (
  id text primary key,                   -- z. B. 'pmid:41234567' oder 'doi:10.…'
  issue_id text not null references nw_issues(id) on delete cascade,
  tier text not null check (tier in ('top','list')),
  rank int not null default 0,
  score numeric not null default 0,
  trend numeric not null default 0,
  tags text[] not null default '{}',     -- pd, peri, hd, drug, tx, gn, ckd, aki, congress, guideline
  topic text not null default '',
  badges jsonb not null default '[]',
  title_de text not null,
  title_en text not null default '',
  source_line text not null default '',
  summary_de text not null default '',
  why_de text not null default '',
  oneliner_de text not null default '',
  facts jsonb not null default '[]',
  drugs text[] not null default '{}',
  abstract_en text not null default '',
  url text not null,
  fulltext_url text not null default '',
  open_access boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists nw_items_issue on nw_items(issue_id, tier, rank);

-- Online-Fortbildungen der Woche
create table if not exists nw_events (
  id uuid primary key default gen_random_uuid(),
  issue_id text not null references nw_issues(id) on delete cascade,
  title text not null,
  organizer text not null default '',
  starts_at timestamptz,
  ends_at timestamptz,
  cme text not null default '',
  matches text not null default '',
  url text not null
);

-- Gemerkt / gelesen, je Leser
create table if not exists nw_state (
  reader_id uuid not null references nw_readers(id) on delete cascade,
  item_id text not null references nw_items(id) on delete cascade,
  starred boolean not null default false,
  read boolean not null default false,
  updated_at timestamptz not null default now(),
  primary key (reader_id, item_id)
);

-- "Fehlt etwas?"-Hinweise
create table if not exists nw_missing (
  id uuid primary key default gen_random_uuid(),
  reader_id uuid references nw_readers(id) on delete set null,
  text text not null,
  done boolean not null default false,
  created_at timestamptz not null default now()
);

-- Kein direkter Zugriff von außen: alles läuft über die nw_*-Funktionen.
-- Der Wochenlauf schreibt mit dem geheimen service_role-Schlüssel.
alter table nw_readers enable row level security;
alter table nw_issues  enable row level security;
alter table nw_items   enable row level security;
alter table nw_events  enable row level security;
alter table nw_state   enable row level security;
alter table nw_missing enable row level security;
revoke all on nw_readers, nw_issues, nw_items, nw_events, nw_state, nw_missing from anon, authenticated;
-- Der Wochenlauf (geheimer service_role-Schlüssel) braucht Vollzugriff, auch wenn
-- "Grant API access to new tables automatically" beim Anlegen des Projekts aus war.
grant usage on schema public to anon, authenticated, service_role;
grant all on nw_readers, nw_issues, nw_items, nw_events, nw_state, nw_missing to service_role;

-- ---------- Funktionen ----------
create or replace function nw_who(p_token text) returns uuid
language plpgsql security definer set search_path = public as $$
declare v uuid;
begin
  select id into v from nw_readers where token = p_token;
  if v is null then raise exception 'Ungültiger Link'; end if;
  return v;
end $$;

create or replace function nw_item_json(p_reader uuid, i nw_items) returns json
language sql stable security definer set search_path = public as $$
  select json_build_object(
    'id', i.id, 'issue', i.issue_id, 'tier', i.tier, 'rank', i.rank, 'score', i.score, 'trend', i.trend,
    'tags', i.tags, 'topic', i.topic, 'badges', i.badges, 'title', i.title_de, 'orig', i.title_en,
    'src', i.source_line, 'sum', i.summary_de, 'why', i.why_de, 'one', i.oneliner_de, 'facts', i.facts,
    'drugs', i.drugs, 'abstract', i.abstract_en, 'url', i.url, 'full', i.fulltext_url, 'oa', i.open_access,
    'starred', coalesce(s.starred, false), 'read', coalesce(s.read, false))
  from (select 1) x left join nw_state s on s.reader_id = p_reader and s.item_id = i.id
$$;

-- Eine Ausgabe (null = neueste veröffentlichte) samt Einträgen und Fortbildungen
create or replace function nw_issue(p_token text, p_issue text default null) returns json
language plpgsql security definer set search_path = public as $$
declare r uuid := nw_who(p_token); v nw_issues;
begin
  if p_issue is null then
    select * into v from nw_issues where published_at is not null order by published_at desc limit 1;
  else
    select * into v from nw_issues where id = p_issue and published_at is not null;
  end if;
  if v.id is null then return json_build_object('issue', null); end if;
  return json_build_object(
    'reader', (select name from nw_readers where id = r),
    'issue', json_build_object('id', v.id, 'label', v.week_label, 'published', v.published_at,
                               'lead', v.lead, 'trends', v.trends, 'stats', v.stats),
    'items', coalesce((select json_agg(nw_item_json(r, i) order by i.tier desc, i.rank) from nw_items i where i.issue_id = v.id), '[]'),
    'events', coalesce((select json_agg(json_build_object('title', e.title, 'org', e.organizer, 'start', e.starts_at,
                         'end', e.ends_at, 'cme', e.cme, 'matches', e.matches, 'url', e.url) order by e.starts_at nulls last)
                        from nw_events e where e.issue_id = v.id), '[]'));
end $$;

-- Liste aller Ausgaben fürs Archiv
create or replace function nw_archive(p_token text) returns json
language plpgsql security definer set search_path = public as $$
declare r uuid := nw_who(p_token);
begin
  return coalesce((select json_agg(json_build_object('id', v.id, 'label', v.week_label, 'published', v.published_at,
      'lead', v.lead, 'count', (select count(*) from nw_items i where i.issue_id = v.id)) order by v.published_at desc)
    from nw_issues v where v.published_at is not null), '[]');
end $$;

-- Alle gemerkten Einträge (über alle Wochen)
create or replace function nw_starred(p_token text) returns json
language plpgsql security definer set search_path = public as $$
declare r uuid := nw_who(p_token);
begin
  return coalesce((select json_agg(nw_item_json(r, i) order by s.updated_at desc)
    from nw_state s join nw_items i on i.id = s.item_id where s.reader_id = r and s.starred), '[]');
end $$;

-- Volltextsuche im Archiv (einfach, über Titel und Zusammenfassung)
create or replace function nw_search(p_token text, p_q text) returns json
language plpgsql security definer set search_path = public as $$
declare r uuid := nw_who(p_token); q text := '%' || replace(replace(trim(p_q), '%', ''), '_', '') || '%';
begin
  if length(trim(p_q)) < 3 then return '[]'; end if;
  return coalesce((select json_agg(x) from (
    select nw_item_json(r, i) x from nw_items i join nw_issues v on v.id = i.issue_id
    where v.published_at is not null
      and (i.title_de ilike q or i.title_en ilike q or i.summary_de ilike q or i.oneliner_de ilike q
           or i.topic ilike q or array_to_string(i.drugs, ' ') ilike q)
    order by v.published_at desc, i.tier desc, i.rank limit 60) t), '[]');
end $$;

create or replace function nw_set(p_token text, p_item text, p_starred boolean default null, p_read boolean default null) returns void
language plpgsql security definer set search_path = public as $$
declare r uuid := nw_who(p_token);
begin
  insert into nw_state(reader_id, item_id, starred, read)
  values (r, p_item, coalesce(p_starred, false), coalesce(p_read, false))
  on conflict (reader_id, item_id) do update
    set starred = coalesce(p_starred, nw_state.starred),
        read = coalesce(p_read, nw_state.read),
        updated_at = now();
end $$;

create or replace function nw_report_missing(p_token text, p_text text) returns void
language plpgsql security definer set search_path = public as $$
declare r uuid := nw_who(p_token);
begin
  if length(trim(p_text)) between 3 and 2000 then
    insert into nw_missing(reader_id, text) values (r, trim(p_text));
  end if;
end $$;

revoke all on function nw_who(text), nw_item_json(uuid, nw_items) from public, anon, authenticated;
grant execute on function nw_issue(text, text), nw_archive(text), nw_starred(text), nw_search(text, text),
  nw_set(text, text, boolean, boolean), nw_report_missing(text, text) to anon, authenticated;

-- ---------- Leser anlegen ----------
insert into nw_readers(name) values ('Dr. Brockmann') on conflict (name) do nothing;
select name, token from nw_readers;
