-- The team inbox lists newest first, usually filtered by temperature or status.
create index leads_created_at_idx on public.leads (created_at desc);
create index leads_temperature_created_idx on public.leads (temperature, created_at desc);
create index leads_unclassified_idx on public.leads (created_at) where status = 'new';
