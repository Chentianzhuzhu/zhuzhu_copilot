import base64
import app_ssh

def app_run_data(body):
    import paramiko
    from app_ssh import HOST, USER, PWD
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, PWD, timeout=25, allow_agent=False,
              look_for_keys=False, banner_timeout=30, auth_timeout=30)
    try:
        stdin, stdout, stderr = c.exec_command("bash -s", timeout=600)
        stdin.write(body)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err
    finally:
        c.close()

b64 = base64.b64encode(open('patch_config.py', 'rb').read()).decode()
w = ("#!/bin/bash\nset -e\ncd /www/auth-server\n"
     "printf '%%s' '%s' | base64 -d > /tmp/patch_config.py\n"
     "/www/auth-server/.venv/bin/python /tmp/patch_config.py\n"
     "echo '--- 1) default behaviour unchanged ---'\n"
     "/www/auth-server/.venv/bin/python -c 'import sys; sys.path.insert(0,\"/www/auth-server\"); from app.config import settings; print(settings.redis_url)'\n"
     "echo '--- 2) new ACL path (simulated via env, .env untouched) ---'\n"
     "REDIS_USER=api_app REDIS_PASSWORD=hKo0bU6C6GCeufmwLaUJguC8Fs8ud9bZ REDIS_HOST=122.228.216.138 "
     "/www/auth-server/.venv/bin/python -c 'import sys; sys.path.insert(0,\"/www/auth-server\"); from app.config import settings; print(settings.redis_url)'\n"
     "echo '--- 3) compile check ---'\n"
     "/www/auth-server/.venv/bin/python -m py_compile /www/auth-server/app/config.py && echo COMPILE_OK\n"
     "rm -f /tmp/patch_config.py\n") % b64

rc, out, err = app_run_data(w)
print(out)
if err.strip():
    print('STDERR:', err[:1500])
print('[exit %s]' % rc)
