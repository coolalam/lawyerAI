#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
output="${1:-artifacts/docker-package}"
database="${LAW_DB_PATH:-$PWD/data/jw-db-records-20261113.sqlite3}"

if ! command -v docker >/dev/null 2>&1; then
  echo '未找到 docker，请先安装 Docker Desktop 或 Docker Engine。' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Docker daemon 未运行，请先启动 Docker Desktop 或 Docker Engine。' >&2
  exit 1
fi
if [ ! -f "$database" ]; then
  echo "数据库不存在：$database" >&2
  exit 1
fi

mkdir -p "$output"
echo '构建 Docker 镜像…'
docker compose build
echo '导出 Docker 镜像…'
docker save lawyer-ai-office:latest | gzip -c > "$output/lawyer-ai-office-image.tar.gz"
echo '压缩当前数据库和附件目录…'
tar -czf "$output/lawyer-ai-data.tar.gz" -C "$(dirname "$database")" .
cp docker-compose.yml "$output/docker-compose.yml"
(cd "$output" && shasum -a 256 lawyer-ai-office-image.tar.gz lawyer-ai-data.tar.gz docker-compose.yml > SHA256SUMS)
echo "已生成部署包目录：$output"
ls -lh "$output"
