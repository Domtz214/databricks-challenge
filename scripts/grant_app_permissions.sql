-- Run in the SQL Editor after the app is created.
-- TODO(you): replace <app-sp> with the app's service principal (Compute -> Apps -> triage-console -> Authorization).
GRANT USE CATALOG ON CATALOG workspace TO `<app-sp>`;
GRANT USE SCHEMA, SELECT ON SCHEMA workspace.triage TO `<app-sp>`;
GRANT MODIFY ON TABLE workspace.triage.dispatch_decisions TO `<app-sp>`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME workspace.triage.landing TO `<app-sp>`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME workspace.triage.photos TO `<app-sp>`;
-- Also: Serving -> each endpoint used (LLM, vision, embeddings) -> Permissions -> app SP "Can Query",
-- and the Vector Search endpoint/index -> app SP "Can Use"/SELECT, and the pipeline -> "Can Run".

-- Responsible AI: mask reporter contact info for everyone except the coordinators group.
-- CREATE FUNCTION workspace.triage.mask_contact(c STRING)
--   RETURN CASE WHEN is_account_group_member('coordinators') THEN c ELSE '***' END;
-- ALTER TABLE workspace.triage.silver_reports ALTER COLUMN reporter_contact SET MASK workspace.triage.mask_contact;
-- (Pipeline-managed tables: set masks via the pipeline table definition if ALTER is not allowed.)
