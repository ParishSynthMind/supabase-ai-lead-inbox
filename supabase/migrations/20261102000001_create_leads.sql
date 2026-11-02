-- Leads captured from a website form, classified by Claude as Hot / Warm / Cold.

create table public.leads (
    id                    uuid primary key default gen_random_uuid(),
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),

    -- What the visitor sent
    name                  text not null check (char_length(name) between 1 and 120),
    email                 text not null check (char_length(email) <= 254
                                               and email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    company               text check (char_length(company) <= 200),
    phone                 text check (char_length(phone) <= 40),
    message               text not null check (char_length(message) between 1 and 5000),
    source                text not null default 'website' check (char_length(source) <= 60),

    -- Pipeline state, owned by the service
    status                text not null default 'new'
                          check (status in ('new', 'classified', 'contacted', 'won', 'lost', 'spam')),
    temperature           text check (temperature in ('Hot', 'Warm', 'Cold')),
    score                 smallint check (score between 0 and 100),
    reason                text check (char_length(reason) <= 1000),
    suggested_next_step   text check (char_length(suggested_next_step) <= 500),
    needs_review          boolean not null default false,
    classified_by         text,
    classified_at         timestamptz,
    classification_error  text
);

comment on table public.leads is 'Inbound leads. Anyone with the anon key may INSERT the visitor columns only; reading needs the service role.';
comment on column public.leads.needs_review is 'True when Claude could not classify the lead and the safe default (Warm) was used.';

create function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

create trigger leads_touch_updated_at
    before update on public.leads
    for each row execute function public.touch_updated_at();
