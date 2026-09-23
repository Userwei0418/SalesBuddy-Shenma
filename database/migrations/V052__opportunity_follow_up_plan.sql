BEGIN;
ALTER TABLE crm.opportunity ADD COLUMN follow_up_plan text;
COMMENT ON COLUMN crm.opportunity.follow_up_plan IS '人工维护的商机跟进计划；拜访中的下一步计划独立保存在当次记录，不自动覆盖';
COMMIT;
