-- Sending capacity as managed inventory.
--
-- `zolts/deliverability.py` has modelled this since the reference core existed:
-- warm-up curves, reputation factors, the docs/09 thresholds, per-provider
-- segregation, a fleet that refuses to hand out a mailbox it cannot afford.
-- Nothing in the runtime imported it. The `mailbox` table has been here since
-- migration 002 with columns for warm-up and four rates, and no code has ever
-- written or read a single one of them.
--
-- docs/15 ranks deliverability collapse as the highest-impact risk in the
-- product and names the mitigation "sending capacity as managed inventory".
-- The inventory existed as a specification and as a table, and was managed by
-- nothing.

-- Which mailbox sent it. Without this column there is no per-mailbox
-- accounting at all, whatever the connector's docstring claimed.
alter table touch add column mailbox_id uuid references mailbox(id) on delete set null;

-- A spam complaint and an unsubscribe were the same event to this runtime,
-- both routed through one OPT_OUT set. For suppression they are the same and
-- the code was right. For reputation they are nothing alike: 0.3% complaints
-- pauses a domain, 2% unsubscribes triggers a review. Conflated, the cut-off
-- that matters most could never fire.
--
-- Timestamps rather than statuses: `status` is the delivery lifecycle and has
-- one terminal value, while a complaint is an additional fact about a touch
-- that was delivered. Overloading the lifecycle would lose the delivery.
alter table touch add column complained_at   timestamptz;
alter table touch add column unsubscribed_at timestamptz;

create index touch_mailbox_ix on touch (tenant_id, mailbox_id, sent_at desc)
  where mailbox_id is not null;

-- The four rate columns are dropped rather than populated. A stored rate is a
-- number that is right when it is written and wrong from then on; every one of
-- these is derivable from the touches the mailbox actually sent, and derived is
-- the only version that cannot go stale.
alter table mailbox drop column sent_30d;
alter table mailbox drop column bounce_rate;
alter table mailbox drop column complaint_rate;
alter table mailbox drop column reply_rate;

-- Same argument, one step further. `warmed_days` is a counter something has to
-- increment every day, which means one missed run makes a mailbox permanently
-- younger than it is and sending under its real capacity forever. A date is
-- written once and today does the arithmetic.
alter table mailbox drop column warmed_days;
alter table mailbox add column warmup_started_on date not null default current_date;

-- Reputation is scored per recipient provider, so a mailbox that mixes them
-- hides a problem at one behind health at the other.
alter table mailbox add column provider text not null default 'other'
  check (provider in ('google', 'microsoft', 'other'));
alter table mailbox add column paused_reason text;

-- The domain is the unit that burns: a domain past the complaint cut-off has
-- zero capacity including its healthy mailboxes (docs/09).
create table sending_domain (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             uuid not null references tenant(id) on delete cascade,
  name                  text not null,
  -- Authentication defaults to absent, so a domain has no capacity until an
  -- operator states otherwise. Missing authentication is not a reputation
  -- problem to recover from, it is mail filtered on arrival, and the rest of
  -- this runtime fails closed for less.
  spf                   boolean not null default false,
  dkim                  boolean not null default false,
  dmarc_policy          text    not null default 'none',
  one_click_unsubscribe boolean not null default false,
  paused                boolean not null default false,
  paused_reason         text,
  paused_at             timestamptz,
  created_at            timestamptz not null default now(),
  unique (tenant_id, name)
);

alter table sending_domain enable row level security;
alter table sending_domain force row level security;
create policy sending_domain_tenant_isolation on sending_domain
  using (tenant_id = zolts_internal.current_tenant())
  with check (tenant_id = zolts_internal.current_tenant());
