import ipaddress
import sys

def guard(event, args):
    if event in ('winreg.CreateKey', 'winreg.DeleteKey', 'winreg.DeleteValue',
                 'winreg.SetValue', 'winreg.SaveKey', 'winreg.LoadKey'):
        raise RuntimeError('audit forbids registry writes')
    if event in ('socket.connect', 'socket.getaddrinfo'):
        host = args[1][0] if event == 'socket.connect' else args[0]
        if host == 'localhost':
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise RuntimeError('audit permits loopback network only')

sys.addaudithook(guard)
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
