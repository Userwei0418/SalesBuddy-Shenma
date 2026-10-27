BEGIN;
-- Customer V035–V161 remain immutable. The independent V300+ chain carries
-- upstream V152–V250 and never adopts their colliding version numbers.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V161') THEN
  RAISE EXCEPTION 'Shenma V161 must be applied before whole-version bridge';
 END IF;
 IF to_regclass('crm.customer_creation_request') IS NULL
   OR to_regclass('crm.opportunity_saved_view') IS NULL THEN
  RAISE EXCEPTION 'Shenma S2/S3 compatibility tables are missing';
 END IF;
END $$;
-- Freeze the pre-upgrade company set; subsequent new companies follow the
-- upstream provisioning flow. No user/credential/business values are copied.
CREATE TABLE ops.shenma_121_legacy_workspace(workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id));
INSERT INTO ops.shenma_121_legacy_workspace SELECT id FROM platform.workspace;
REVOKE ALL ON ops.shenma_121_legacy_workspace FROM PUBLIC;
-- Upstream V215 reuses an earlier view table but expects these columns.
ALTER TABLE crm.opportunity_saved_view ADD COLUMN visibility text NOT NULL DEFAULT 'private';
ALTER TABLE crm.opportunity_saved_view ADD COLUMN team_id uuid;
ALTER TABLE crm.opportunity_saved_view ADD CONSTRAINT shenma_view_team_visibility CHECK((visibility='team')=(team_id IS NOT NULL));
ALTER TABLE crm.opportunity_saved_view ADD CONSTRAINT shenma_view_team_fk FOREIGN KEY(team_id,workspace_id) REFERENCES platform.team(id,workspace_id);
INSERT INTO ops.schema_migration(version,description) VALUES('V299','Shenma whole 1.2.1 compatibility prelude; retain historical rows and ledger');
COMMIT;
