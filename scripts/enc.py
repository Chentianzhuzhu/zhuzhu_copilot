import base64, sys
sys.stdout.write(base64.b64encode(open(sys.argv[1],'rb').read()).decode())