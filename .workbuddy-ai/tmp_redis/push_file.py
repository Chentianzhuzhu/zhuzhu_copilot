import sys
import rs
import app_run

MARK = "___WB_TRANSFER_END___"

def wrapper_for(remote, body, mode):
    assert MARK not in body
    return (
        "#!/bin/bash\nset -e\n"
        "cat > %s <<'%s'\n%s%s\n"
        "chmod %s %s\nls -l %s\nsha1sum %s\n"
    ) % (remote, MARK, body, MARK, mode, remote, remote, remote)

def main():
    local, remote, mode, target = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    body = open(local, encoding="utf-8").read()
    w = wrapper_for(remote, body, mode)
    if target == "redis":
        rc, out, err = rs.run_data(w)
    else:
        rc, out, err = app_run.run_data(w)
    print(out)
    if err.strip():
        print("STDERR:", err[:1500])
    print("[exit %s]" % rc)
    sys.exit(rc)

main()
