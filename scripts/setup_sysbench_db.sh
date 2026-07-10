#!/usr/bin/env bash
#
# scripts/setup_sysbench_db.sh
#
# Sets up PostgreSQL database for sysbench:
#  1) Creates admin user with no password (if doesn't exist)
#  2) Creates benchdb database (if doesn't exist)
#  3) Grants all privileges on benchdb to admin
#  4) Updates pg_hba.conf to allow passwordless connections for admin user

set -euo pipefail

PGVER="14"
INSTANCE="main"
PGHBA_FILE="/etc/postgresql/${PGVER}/${INSTANCE}/pg_hba.conf"

echo "Setting up PostgreSQL database for sysbench..."

# Check if PostgreSQL is running
if ! sudo systemctl is-active --quiet postgresql; then
    echo "PostgreSQL is not running. Starting PostgreSQL..."
    sudo systemctl start postgresql
fi

# Wait a moment for PostgreSQL to be ready
sleep 2

# Create admin user with no password if it doesn't exist
echo "Creating/updating admin user..."
sudo -u postgres psql <<EOF
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admin') THEN
        CREATE ROLE admin WITH SUPERUSER LOGIN;
    ELSE
        -- Update existing admin user to remove password
        ALTER ROLE admin WITH PASSWORD NULL;
    END IF;
END
\$\$;
EOF

# Create benchdb database if it doesn't exist
echo "Creating benchdb database..."
sudo -u postgres psql <<EOF
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'benchdb') THEN
        CREATE DATABASE benchdb;
    END IF;
END
\$\$;
EOF

# Grant all privileges on benchdb to admin
echo "Granting privileges to admin user..."
sudo -u postgres psql <<EOF
GRANT ALL PRIVILEGES ON DATABASE benchdb TO admin;
\c benchdb
GRANT ALL ON SCHEMA public TO admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO admin;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO admin;
EOF

# Update pg_hba.conf to allow passwordless local connections for admin
echo "Configuring pg_hba.conf for passwordless connections..."
if ! grep -q "admin.*trust" "${PGHBA_FILE}" 2>/dev/null; then
    # Add a line for admin user to use trust authentication
    # This allows admin to connect without a password on localhost
    sudo sed -i '/# "local" is for Unix domain socket connections only/a\
local   all             admin                                   trust\
host    all             admin           127.0.0.1/32            trust\
host    all             admin           ::1/128                 trust' "${PGHBA_FILE}"
    
    echo "Reloading PostgreSQL configuration..."
    sudo systemctl reload postgresql
fi

# Verify the setup
echo ""
echo "Verifying setup..."
sudo -u postgres psql -c "\du admin" | grep -q admin && echo "✓ Admin user exists" || echo "✗ Admin user not found"
sudo -u postgres psql -c "\l" | grep -q benchdb && echo "✓ benchdb database exists" || echo "✗ benchdb database not found"

echo ""
echo "Testing connection as admin user..."
PGPASSWORD="" psql -h 127.0.0.1 -p 5432 -U admin -d benchdb -c "SELECT version();" && echo "✓ Connection successful!" || echo "✗ Connection failed"

echo ""
echo "Setup complete! You can now run:"
echo "sysbench /usr/share/sysbench/oltp_read_write.lua --db-driver=pgsql --pgsql-host=127.0.0.1 --pgsql-port=5432 --pgsql-user=admin --pgsql-password= --pgsql-db=benchdb --tables=4 --table-size=100000 prepare"
