"""Compile the real native entry; verify icon and relocated launch commands."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import build_desktop_entry as builder

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native entry')


def test_compiled_entry_is_a_gui_executable_with_an_icon(tmp_path):
    exe = builder.build(tmp_path)
    data = exe.read_bytes()
    assert data[:2] == b'MZ'
    pe = int.from_bytes(data[0x3c:0x40], 'little')
    assert data[pe:pe + 4] == b'PE\0\0'
    optional = pe + 24
    # PE subsystem 2 = Windows GUI; 3 would open a console window.
    assert int.from_bytes(data[optional + 68:optional + 70], 'little') == 2
    extract = ctypes.windll.shell32.ExtractIconExW
    extract.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    extract.restype = ctypes.c_uint
    assert extract(str(exe), -1, None, None, 0) > 0


def test_native_command_builder_handles_relocated_unicode_paths(tmp_path):
    root = tmp_path / '搬移 安装目录 & test'
    python = root / '.runtime/venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.write_bytes(b'fixture')
    harness = tmp_path / 'Smoke.cs'
    harness.write_text(r'''
using System;
using System.IO;
using ToolkitDesktop;
public static class Smoke {
 public static int Main(string[] args) {
  string root=args[0]; int calls=0;
  LaunchCommand command=Entry.FindPython(root, c=>{calls++;return true;});
  if(calls!=1 || command.FileName!=Path.Combine(root,".runtime","venv","Scripts","python.exe")) return 1;
  string line=Entry.BootstrapArguments(command,root,new string[0]);
  if(line!=Entry.Quote(Path.Combine(root,"bootstrap.py"))+" --install") return 2;
  string check=Entry.BootstrapArguments(command,root,new string[]{"--check","--log-dir","C:\\test space\\"});
  if(!check.Contains("\"--check\"") || check.Contains("--install") || !check.EndsWith(Entry.Quote("C:\\test space\\"))) return 3;
  command=Entry.FindPython(root,c=>c.FileName=="py.exe" && c.Prefix=="-3.12 ");
  if(command==null || command.Prefix!="-3.12 ") return 4;
  if(Entry.FindPython(root,c=>false)!=null) return 5;
  if(Entry.Quote("a\"b")!="\"a\\\"b\"") return 6;
  return 0;
 }
}''', encoding='utf-8')
    target = tmp_path / 'Smoke.exe'
    result = subprocess.run([
        str(builder.find_compiler()), '/nologo', '/target:exe', '/main:Smoke',
        '/codepage:65001', '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
        f'/out:{target}', str(builder.PROJECT_ROOT / 'scripts/desktop_entry/Launcher.cs'), str(harness),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    completed = subprocess.run([str(target), str(root)], capture_output=True)
    assert completed.returncode == 0, completed.returncode


def test_low_workspace_build_can_write_standard_integrity_bootstrap_logs(tmp_path):
    root = tmp_path / 'Low 工作区'
    root.mkdir()
    subprocess.run(['icacls.exe', str(root), '/setintegritylevel', '(OI)(CI)Low'], check=True, capture_output=True)
    logs = root / 'logs'
    logs.mkdir()
    subprocess.run(['icacls.exe', str(logs), '/setintegritylevel', '(OI)(CI)Medium'], check=True, capture_output=True)
    exe = builder.build(root)
    # Exercise the real GUI entry and real BootstrapLogger, without installing
    # dependencies or launching the business app/account processes.
    stub = (
        'import sys\nfrom pathlib import Path\n'
        'sys.path.insert(0, ' + repr(str(builder.PROJECT_ROOT)) + ')\n'
        'import bootstrap\n'
        'with bootstrap.BootstrapLogger(Path(__file__).parent / "logs") as logger:\n'
        '    logger.info("medium-integrity-write-ok")\n'
    )
    (root / 'bootstrap.py').write_text(stub, encoding='utf-8')
    env = dict(os.environ)
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen([str(exe)], env=env, cwd=tmp_path, startupinfo=startup)
    try:
        assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    files = list(logs.glob('bootstrap_*.log'))
    assert len(files) == 1
    assert 'medium-integrity-write-ok' in files[0].read_text(encoding='utf-8')
