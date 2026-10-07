#!/bin/bash
set -e
APP_DIR=/www/auth-server
PY=/usr/bin/python3.11

echo "==> 规整目录结构"
cd "$APP_DIR"
if [ -d "$APP_DIR/auth-server" ]; then
  shopt -s dotglob
  mv "$APP_DIR"/auth-server/* "$APP_DIR"/
  shopt -u dotglob
  rmdir "$APP_DIR/auth-server" 2>/dev/null || true
fi
echo "目录:"
ls -la "$APP_DIR"

echo "==> 重建 venv（若存在则复用）"
cd "$APP_DIR"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  "$PY" -m venv .venv
fi

echo "==> 安装 Python 依赖"
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
"$APP_DIR/.venv/bin/pip" install -r requirements.txt 2>&1 | tail -6

echo "==> 初始化数据库"
mysql -uroot -p'windows10' < "$APP_DIR/sql/init.sql" 2>&1 | grep -v 'Using a password' || true
echo "数据库初始化完成"

echo "==> 验证导入"
cd "$APP_DIR"
"$APP_DIR/.venv/bin/python" -c "import app.main; print('IMPORT OK')"

echo "DEPLOY_DONE"