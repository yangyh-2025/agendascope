-- 数据库公网访问账号初始化 SQL
-- 用法：在服务器上以 PG 超管身份执行
--   docker exec -i agendascope-db-1 psql -U agenda -d agendascope < deploy/postgresql/init_public_users.sql
--
-- 密码必须改成强密码（≥20 位随机），并同步到：
--   - 本地 local_workers/.env 的 DATABASE_URL
--   - 服务器 /root/agendascope/.env 的 PG_WRITE_PASSWORD / PG_READ_PASSWORD（仅供自己参考）

-- ============================================
-- 1. 写入账号（本地 worker 用）
-- ============================================
CREATE USER agendascope_write WITH PASSWORD 'CHANGE_ME_TO_STRONG_PASSWORD_32_CHARS';

-- 现有表的全部 DML 权限
GRANT CONNECT ON DATABASE agendascope TO agendascope_write;
GRANT USAGE ON SCHEMA public TO agendascope_write;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agendascope_write;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agendascope_write;

-- 未来新建表自动授权
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agendascope_write;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO agendascope_write;

-- ============================================
-- 2. 只读账号（备份/调试/BI 工具用）
-- ============================================
CREATE USER agendascope_read WITH PASSWORD 'CHANGE_ME_TO_ANOTHER_STRONG_PASSWORD';

GRANT CONNECT ON DATABASE agendascope TO agendascope_read;
GRANT USAGE ON SCHEMA public TO agendascope_read;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agendascope_read;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO agendascope_read;

-- ============================================
-- 3. 验证
-- ============================================
-- 应看到三行：agenda / agendascope_write / agendascope_read
SELECT usename, usecreatedb, usesuper FROM pg_user ORDER BY usename;
