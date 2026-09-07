-- Migration: Add rank column to AGENT_DETAIL table
-- Cấp bậc nhân viên. NULL = chưa xếp hạng (UNRANKED).
-- 6 cấp hợp lệ: AC, FC, FM, FD, SD, ED
-- Run this SQL against the trustagent PostgreSQL database

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'AGENT_DETAIL' AND column_name = 'rank'
    ) THEN
        ALTER TABLE "AGENT_DETAIL" ADD COLUMN rank VARCHAR(20);
    END IF;
END $$;

-- Verify
SELECT id, username, "FULL_NAME", refer_code, rank FROM "AGENT_DETAIL";
