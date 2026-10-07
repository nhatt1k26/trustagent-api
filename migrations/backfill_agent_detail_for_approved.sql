-- Migration: Backfill AGENT_DETAIL cho các CTV đã duyệt còn thiếu tài khoản
-- ---------------------------------------------------------------------------
-- Bối cảnh:
--   - Danh sách "Đội ngũ" (admin sửa cấp bậc + leader) đọc từ bảng "AGENT_DETAIL"
--     (endpoint /api/v1/agent/admin/agents, lọc delete_flag = false).
--   - manage_id (leader) và cấp bậc được liên kết agent <-> agent_register QUA EMAIL.
--   - Một số CTV có agent_register.STATUS = 'APPROVED' nhưng CHƯA có bản ghi
--     trong "AGENT_DETAIL" (chưa tạo tài khoản) => không hiện trong danh sách,
--     không sửa được level/manage_id.
--
-- Mục tiêu:
--   1. Gộp các bản ghi agent_register TRÙNG EMAIL đang APPROVED: giữ bản ghi mới
--      nhất, các bản còn lại chuyển REJECTED (đánh dấu trùng) để cây đội ngũ không nhân đôi.
--   2. Tạo "AGENT_DETAIL" + "AGENT_CREDENTIAL" cho mỗi email APPROVED còn thiếu,
--      với username = email, refer_code mới duy nhất dạng TA-XXXX, rank = NULL (UNRANKED).
--
-- An toàn: idempotent — chạy lại nhiều lần không tạo trùng.
-- Chạy: docker exec -i trustagent-postgres psql -U trustagent -d trustagent -f <file>

BEGIN;

-- 1) Gộp bản ghi agent_register trùng email đang APPROVED: giữ id lớn nhất (mới nhất).
UPDATE agent_register ar
SET "STATUS" = 'REJECTED',
    "REASON" = COALESCE(NULLIF("REASON", ''), 'Trùng lặp - đã gộp bản ghi đăng ký')
WHERE ar."STATUS" = 'APPROVED'
  AND ar."EMAIL" IS NOT NULL
  AND ar.id < (
      SELECT MAX(ar2.id)
      FROM agent_register ar2
      WHERE ar2."STATUS" = 'APPROVED'
        AND lower(ar2."EMAIL") = lower(ar."EMAIL")
  );

-- 2) Tạo AGENT_DETAIL cho các email APPROVED chưa có tài khoản.
--    Sinh refer_code duy nhất dạng TA-XXXX cho từng bản ghi mới.
DO $$
DECLARE
    reg RECORD;
    new_code VARCHAR(50);
    code_exists BOOLEAN;
BEGIN
    FOR reg IN
        SELECT DISTINCT ON (lower(ar."EMAIL"))
               ar.id, ar."FULLNAME", ar."EMAIL", ar."PHONE",
               ar."GENDER", ar."BIRTHDAY", ar."CREATED_DATETIME"
        FROM agent_register ar
        WHERE ar."STATUS" = 'APPROVED'
          AND ar."EMAIL" IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM "AGENT_DETAIL" ad
              WHERE lower(ad.email) = lower(ar."EMAIL")
          )
        ORDER BY lower(ar."EMAIL"), ar.id DESC
    LOOP
        -- Sinh refer_code duy nhất
        LOOP
            new_code := 'TA-' || LPAD(FLOOR(RANDOM() * 9000 + 1000)::TEXT, 4, '0');
            SELECT EXISTS(
                SELECT 1 FROM "AGENT_DETAIL" WHERE refer_code = new_code
            ) INTO code_exists;
            EXIT WHEN NOT code_exists;
        END LOOP;

        INSERT INTO "AGENT_DETAIL" (
            username, "FULL_NAME", email, phone, "GENDER", "BIRTHDAY",
            refer_code, rank, "REGISTER_ID", create_datetime, delete_flag
        ) VALUES (
            reg."EMAIL", reg."FULLNAME", reg."EMAIL", reg."PHONE",
            reg."GENDER", reg."BIRTHDAY",
            new_code, NULL, reg.id,
            COALESCE(reg."CREATED_DATETIME", NOW()), FALSE
        );

        -- Tạo credential tương ứng nếu chưa có (username = email).
        -- Mật khẩu để trống-hash placeholder: buộc reset khi cần; đăng nhập thực tế
        -- do service authen quản lý. Không đặt secret trong migration.
        IF NOT EXISTS (
            SELECT 1 FROM "AGENT_CREDENTIAL" WHERE username = reg."EMAIL"
        ) THEN
            INSERT INTO "AGENT_CREDENTIAL" (username, password, role)
            VALUES (reg."EMAIL", '!', 'ROLE_AGENT');
        END IF;
    END LOOP;
END $$;

COMMIT;

-- Kiểm tra kết quả: mọi CTV APPROVED giờ đều có AGENT_DETAIL tương ứng.
SELECT ar.id AS reg_id, ar."FULLNAME", ar."EMAIL", ar."STATUS", ar.manage_id,
       ad.id AS agent_id, ad.rank, ad.refer_code
FROM agent_register ar
LEFT JOIN "AGENT_DETAIL" ad ON lower(ad.email) = lower(ar."EMAIL")
WHERE ar."STATUS" = 'APPROVED'
ORDER BY ar.id;
