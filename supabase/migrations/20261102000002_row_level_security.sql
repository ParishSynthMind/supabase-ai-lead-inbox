-- Row Level Security: the public (anon) key may only submit new leads. Nobody but the
-- service role (which bypasses RLS and is only ever used server-side) can read them.

alter table public.leads enable row level security;

-- Start from nothing, then grant the minimum.
revoke all on public.leads from anon, authenticated;

-- anon may insert only the columns a visitor fills in. Status, temperature, score and the rest
-- stay at their defaults, so a form post can't pretend to be a pre-classified Hot lead.
grant insert (name, email, company, phone, message, source) on public.leads to anon;

create policy "anon can submit new leads"
    on public.leads
    for insert
    to anon
    with check (
        status = 'new'
        and temperature is null
        and score is null
        and classified_by is null
        and needs_review = false
    );

-- No SELECT, UPDATE or DELETE policy exists for anon or authenticated, so with RLS on those
-- are denied. The service role keeps full access for the API's team endpoints.
grant all on public.leads to service_role;
