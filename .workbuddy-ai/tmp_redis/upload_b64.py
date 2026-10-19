"""Byte-exact file upload to app server via base64 exec channel."""
import base64
import sys

import app_run


def upload(local: str, remote: str) -> None:
    data = open(local, "rb").read()
    b64 = base64.b64encode(data).decode()
    cmd = "printf '%%s' '%s' | base64 -d > %s && sha1sum %s && wc -c < %s" % (
        b64, remote, remote, remote)
    rc, out, err = app_run.run_data("#!/bin/bash\nset -e\n" + cmd + "\n")
    print(out.strip())
    if err.strip():
        print("STDERR:", err[:800])
    print("[exit %s]" % rc)
    if rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    upload(sys.argv[1], sys.argv[2])
