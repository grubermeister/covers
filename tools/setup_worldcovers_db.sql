-- Run once as MySQL root (or another user with CREATE DATABASE and GRANT):
--   sudo mysql < tools/setup_worldcovers_db.sql
--   # or, if root has a password: mysql -u root -p < tools/setup_worldcovers_db.sql
-- Or from the mysql client: source /path/to/tools/setup_worldcovers_db.sql

CREATE DATABASE IF NOT EXISTS worldcovers
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- Grant the same app user full access to the app and Django test databases.
-- Django uses test_${DB_NAME}; with the default DB_NAME=worldcovers, that is
-- test_worldcovers.
GRANT ALL PRIVILEGES ON worldcovers.* TO 'wocod'@'localhost';
GRANT ALL PRIVILEGES ON test_worldcovers.* TO 'wocod'@'localhost';
FLUSH PRIVILEGES;
