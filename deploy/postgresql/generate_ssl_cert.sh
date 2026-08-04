#!/bin/bash
# 生成 PostgreSQL 自签 SSL 证书（10 年有效）
# 用法：bash deploy/postgresql/generate_ssl_cert.sh
#
# 生产建议：用阿里云 SSL 证书服务签发正式证书替换；自签仅供内网/测试

set -e

DIR="$(cd "$(dirname "$0")" && pwd)/ssl"
mkdir -p "$DIR"
cd "$DIR"

# 生成 CA（自签根）
openssl req -new -x509 -days 3650 -nodes \
  -subj "/C=CN/ST=Beijing/L=Beijing/O=AgendaScope/CN=AgendaScope-RootCA" \
  -keyout ca.key -out ca.crt

# 生成服务器私钥 + CSR
openssl req -new -nodes \
  -subj "/C=CN/ST=Beijing/L=Beijing/O=AgendaScope/CN=39.106.2.28" \
  -keyout server.key -out server.csr

# 用 CA 签服务器证书
openssl x509 -req -in server.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 3650 -out server.crt

# 权限（PG 要求 server.key 0600）
chmod 600 server.key
chmod 644 server.crt ca.crt

echo "OK: 证书生成于 $DIR"
echo "  - server.crt / server.key（挂到 PG 容器 /etc/ssl/pg/）"
echo "  - ca.crt（分发给本地 worker 客户端验证用）"
echo ""
echo "本地 worker 连接串示例："
echo "  postgresql+psycopg2://agendascope_write:***@39.106.2.28:5432/agendascope?sslmode=require&sslrootcert=ca.crt"
