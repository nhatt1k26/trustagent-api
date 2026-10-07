-- Migration: Compensation module (Lương - Thưởng - Hoa hồng - Bổ nhiệm)
-- PostgreSQL. Idempotent: chạy lại nhiều lần không lỗi.
-- Tạo 4 bảng: contract_financial, commission_rule, payroll_result, agent_appointment.

-- ─── 1. Dữ liệu tài chính hợp đồng ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contract_financial (
    id SERIAL PRIMARY KEY,
    "CONTRACT_ID" INT NOT NULL UNIQUE,
    "AGENT_ID" INT,
    "DIRECT_REFERRER_ID" INT,
    "SUBMIT_DATE" DATE,
    "ISSUED_DATE" DATE,
    "FYP_MAIN" NUMERIC(18,2) DEFAULT 0,
    "FYP_SUPPLEMENTARY" NUMERIC(18,2) DEFAULT 0,
    "CONVERSION_FACTOR" NUMERIC(6,3) DEFAULT 1,
    "FYP_CONVERTED" NUMERIC(18,2) DEFAULT 0,
    "LEVEL_AT_CONTRACT" INT,
    "APPOINTMENT_STATUS" VARCHAR(20),
    "PAYROLL_PERIOD" VARCHAR(7),
    "NOTE" TEXT,
    "CREATED_DATETIME" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    "UPDATED_DATETIME" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cf_agent ON contract_financial ("AGENT_ID");
CREATE INDEX IF NOT EXISTS idx_cf_period ON contract_financial ("PAYROLL_PERIOD");

-- Vòng đời hợp đồng (phục vụ K2). Thêm cột nếu bảng đã tồn tại từ trước.
ALTER TABLE contract_financial ADD COLUMN IF NOT EXISTS "LIFECYCLE_STATUS" VARCHAR(20) DEFAULT 'IN_FORCE';
ALTER TABLE contract_financial ADD COLUMN IF NOT EXISTS "EFFECTIVE_DATE" DATE;
ALTER TABLE contract_financial ADD COLUMN IF NOT EXISTS "LAST_PAID_PERIOD" VARCHAR(7);
ALTER TABLE contract_financial ADD COLUMN IF NOT EXISTS "LAPSED_DATE" DATE;
ALTER TABLE contract_financial ADD COLUMN IF NOT EXISTS "LIFECYCLE_NOTE" VARCHAR(500);
CREATE INDEX IF NOT EXISTS idx_cf_lifecycle ON contract_financial ("LIFECYCLE_STATUS");

-- ─── 2. Cấu hình quy tắc chi trả ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commission_rule (
    id SERIAL PRIMARY KEY,
    "CATEGORY" VARCHAR(50) NOT NULL,
    "RULE_KEY" VARCHAR(100) NOT NULL UNIQUE,
    "LEVEL" INT,
    "THRESHOLD" NUMERIC(18,2),
    "RATE" NUMERIC(6,4),
    "NOTE" VARCHAR(500),
    "IS_ACTIVE" BOOLEAN DEFAULT TRUE,
    "CREATED_DATETIME" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cr_category ON commission_rule ("CATEGORY");

-- ─── 3. Kết quả tính thù lao ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payroll_result (
    id SERIAL PRIMARY KEY,
    "PAYROLL_PERIOD" VARCHAR(7) NOT NULL,
    "AGENT_ID" INT NOT NULL,
    "CONTRACT_ID" INT,
    "CUSTOMER_NAME" VARCHAR(200),
    "LEVEL" INT,
    "APPOINTMENT_STATUS" VARCHAR(20),
    "FYP_CONVERTED" NUMERIC(18,2) DEFAULT 0,
    "PERSONAL_RATE" NUMERIC(6,4) DEFAULT 0,
    "PERSONAL_COMMISSION" NUMERIC(18,2) DEFAULT 0,
    "SXN_BONUS" NUMERIC(18,2) DEFAULT 0,
    "SXN_WINDOW" VARCHAR(20),
    "MONTHLY_BONUS" NUMERIC(18,2) DEFAULT 0,
    "QUARTERLY_BONUS" NUMERIC(18,2) DEFAULT 0,
    "YEARLY_BONUS" NUMERIC(18,2) DEFAULT 0,
    "RECRUITMENT_BONUS" NUMERIC(18,2) DEFAULT 0,
    "GROSS_TOTAL" NUMERIC(18,2) DEFAULT 0,
    "PIT_AMOUNT" NUMERIC(18,2) DEFAULT 0,
    "FUND_AMOUNT" NUMERIC(18,2) DEFAULT 0,
    "NET_TOTAL" NUMERIC(18,2) DEFAULT 0,
    "NOTE" VARCHAR(500),
    "CALCULATED_AT" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pr_period ON payroll_result ("PAYROLL_PERIOD");
CREATE INDEX IF NOT EXISTS idx_pr_agent ON payroll_result ("AGENT_ID");
CREATE INDEX IF NOT EXISTS idx_pr_contract ON payroll_result ("CONTRACT_ID");

-- ─── 4. Bổ nhiệm / Onboard ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_appointment (
    id SERIAL PRIMARY KEY,
    "AGENT_ID" INT NOT NULL,
    "TARGET_LEVEL" INT NOT NULL,
    "STATUS" VARCHAR(20) DEFAULT 'PENDING',
    "ASSIGNED_BY" VARCHAR(100),
    "ASSIGNED_AT" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    "CHALLENGE_START" DATE,
    "CHALLENGE_END" DATE,
    "CONFIRMED_AT" TIMESTAMP,
    "ACHIEVED_LEVEL" INT,
    "IS_EVALUATED" BOOLEAN DEFAULT FALSE,
    "NOTE" VARCHAR(500),
    "CREATED_DATETIME" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aa_agent ON agent_appointment ("AGENT_ID");
CREATE INDEX IF NOT EXISTS idx_aa_due ON agent_appointment ("STATUS", "IS_EVALUATED", "CHALLENGE_END");

-- Nếu bảng đã tồn tại từ trước (chưa có 2 cột mới) thì bổ sung:
ALTER TABLE agent_appointment ADD COLUMN IF NOT EXISTS "ACHIEVED_LEVEL" INT;
ALTER TABLE agent_appointment ADD COLUMN IF NOT EXISTS "IS_EVALUATED" BOOLEAN DEFAULT FALSE;

-- ─── Seed quy tắc mặc định (khớp app/compensation.py::default_rule_seed) ──────
INSERT INTO commission_rule ("CATEGORY", "RULE_KEY", "LEVEL", "THRESHOLD", "RATE", "NOTE") VALUES
    ('PERSONAL_COMMISSION', 'personal_rate_level_1', 1, NULL, 0.25, '% thù lao cá nhân cho cấp 1'),
    ('PERSONAL_COMMISSION', 'personal_rate_level_2', 2, NULL, 0.25, '% thù lao cá nhân cho cấp 2'),
    ('PERSONAL_COMMISSION', 'personal_rate_level_3', 3, NULL, 0.45, '% thù lao cá nhân cho cấp 3'),
    ('PERSONAL_COMMISSION', 'personal_rate_level_4', 4, NULL, 0.50, '% thù lao cá nhân cho cấp 4'),
    ('PERSONAL_COMMISSION', 'personal_rate_level_5', 5, NULL, 0.56, '% thù lao cá nhân cho cấp 5'),
    ('PERSONAL_COMMISSION', 'personal_rate_level_6', 6, NULL, 0.60, '% thù lao cá nhân cho cấp 6'),
    ('SXN_BONUS', 'SXN_1_7', NULL, NULL, 0.01, 'Thưởng SXN: nộp ngày 1-7, phát hành đến ngày 14'),
    ('SXN_BONUS', 'SXN_8_15', NULL, NULL, 0.01, 'Thưởng SXN: nộp ngày 8-15, phát hành đến ngày 21'),
    ('MONTHLY_BONUS', 'monthly_bonus', NULL, 50000000, 0.01, 'Thưởng tháng khi FYP quy đổi >= 50tr'),
    ('QUARTERLY_BONUS', 'quarterly_100000000', NULL, 100000000, 0.03, 'Thưởng quý bậc >= 100,000,000'),
    ('QUARTERLY_BONUS', 'quarterly_60000000', NULL, 60000000, 0.02, 'Thưởng quý bậc >= 60,000,000'),
    ('QUARTERLY_BONUS', 'quarterly_15000000', NULL, 15000000, 0.01, 'Thưởng quý bậc >= 15,000,000'),
    ('YEARLY_BONUS', 'yearly_bonus', NULL, 300000000, 0.02, 'Thưởng năm khi FYP quy đổi >= 300tr/năm'),
    ('DEDUCTION', 'pit', NULL, NULL, 0.10, 'Thuế TNCN 10%'),
    ('DEDUCTION', 'guarantee_fund', NULL, NULL, 0.03, 'Quỹ đảm bảo 3%'),
    ('RECRUITMENT_BONUS', 'recruitment_15tr_30d', NULL, 15000000, NULL, 'Tuyển TVM có SX >= 15tr FYP quy đổi trong 30 ngày')
ON CONFLICT ("RULE_KEY") DO NOTHING;

-- ─── 5. Snapshot hiệu suất theo tháng (job sinh định kỳ) ──────────────────────
CREATE TABLE IF NOT EXISTS agent_monthly_snapshot (
    id SERIAL PRIMARY KEY,
    "AGENT_ID" INT NOT NULL,
    "PERIOD" VARCHAR(7) NOT NULL,
    "PERSONAL_FYP" NUMERIC(18,2) DEFAULT 0,
    "TEAM_FYP" NUMERIC(18,2) DEFAULT 0,
    "PERSONAL_CONTRACTS" INT DEFAULT 0,
    "TOTAL_MEMBERS" INT DEFAULT 0,
    "ACTIVE_MEMBERS" INT DEFAULT 0,
    "FM_PLUS_MEMBERS" INT DEFAULT 0,
    "DOWNLINE_BY_LEVEL" TEXT,
    "AGENT_CODES" INT DEFAULT 0,
    "BIG_BRANCH_PCT" NUMERIC(6,4) DEFAULT 0,
    "K2" NUMERIC(6,4) DEFAULT 1,
    "RANK_AT_SNAPSHOT" VARCHAR(20),
    "CREATED_DATETIME" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_snapshot_agent_period
    ON agent_monthly_snapshot ("AGENT_ID", "PERIOD");
CREATE INDEX IF NOT EXISTS idx_snapshot_period ON agent_monthly_snapshot ("PERIOD");

-- ─── 6. Dòng thu nhập chi tiết (truy vết nguồn gốc) ───────────────────────────
CREATE TABLE IF NOT EXISTS income_line (
    id SERIAL PRIMARY KEY,
    "PAYROLL_PERIOD" VARCHAR(7) NOT NULL,
    "AGENT_ID" INT NOT NULL,
    "CATEGORY" VARCHAR(40) NOT NULL,
    "AMOUNT" NUMERIC(18,2) DEFAULT 0,
    "RATE" NUMERIC(6,4),
    "BASE_FYP" NUMERIC(18,2),
    "SOURCE_CONTRACT_ID" INT,
    "SOURCE_CUSTOMER_NAME" VARCHAR(200),
    "SOURCE_AGENT_ID" INT,
    "SOURCE_AGENT_NAME" VARCHAR(200),
    "SOURCE_LEVEL" INT,
    "DESCRIPTION" VARCHAR(500),
    "CREATED_DATETIME" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_income_period_agent ON income_line ("PAYROLL_PERIOD", "AGENT_ID");
CREATE INDEX IF NOT EXISTS idx_income_category ON income_line ("CATEGORY");
CREATE INDEX IF NOT EXISTS idx_income_source_contract ON income_line ("SOURCE_CONTRACT_ID");
