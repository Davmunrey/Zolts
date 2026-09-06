-- The role the application connects as. It owns nothing, has no DDL rights and
-- cannot bypass row-level security; the migrations grant it exactly the table
-- privileges it needs. Password is for local development only.
create role zolts_app login password 'zolts_app';
