#!/usr/bin/env python3
"""Run an explicitly supplied PowerShell script on the user's Windows build host.

The encoded argument avoids nested Unix shell/cmd/PowerShell quoting. This is a
developer helper, never imported or bundled by the desktop application.
"""
import argparse
import base64
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('script', type=Path)
parser.add_argument('--host', required=True, help='Explicit SSH user@host; no personal host is embedded in source releases')
args = parser.parse_args()
script = '[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; $OutputEncoding = [Console]::OutputEncoding;\n' + args.script.read_text(encoding='utf-8-sig')
# Windows OpenSSH invokes a command shell with an 8191-character command-line
# limit. Keep the command short and send the explicitly supplied script through
# stdin as ASCII Base64, preserving Chinese text without shell interpolation.
wrapper = 'Invoke-Expression ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd())))'
encoded = base64.b64encode(wrapper.encode('utf-16le')).decode('ascii')
command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', args.host,
           'powershell', '-NoProfile', '-NonInteractive', '-OutputFormat', 'Text', '-EncodedCommand', encoded]
payload = base64.b64encode(script.encode('utf-8'))
raise SystemExit(subprocess.run(command, input=payload).returncode)
