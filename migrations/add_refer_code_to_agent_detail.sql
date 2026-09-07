-- Migration: Add refer_code column to AGENT_DETAIL table
-- Run this SQL against the trustagent PostgreSQL database

-- Add refer_code column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'AGENT_DETAIL' AND column_name = 'refer_code'
    ) THEN
        ALTER TABLE "AGENT_DETAIL" ADD COLUMN refer_code VARCHAR(50) UNIQUE;
    END IF;
END $$;

-- Generate unique refer_code for existing agents that don't have one
-- Format: TA-XXXX (4 random digits)
DO $$
DECLARE
    agent_record RECORD;
    new_code VARCHAR(50);
    code_exists BOOLEAN;
BEGIN
    FOR agent_record IN 
        SELECT id FROM "AGENT_DETAIL" WHERE refer_code IS NULL
    LOOP
        LOOP
            new_code := 'TA-' || LPAD(FLOOR(RANDOM() * 9000 + 1000)::TEXT, 4, '0');
            SELECT EXISTS(
                SELECT 1 FROM "AGENT_DETAIL" WHERE refer_code = new_code
            ) INTO code_exists;
            EXIT WHEN NOT code_exists;
        END LOOP;
        
        UPDATE "AGENT_DETAIL" SET refer_code = new_code WHERE id = agent_record.id;
    END LOOP;
END $$;

-- Verify
SELECT id, username, "FULL_NAME", refer_code FROM "AGENT_DETAIL";
