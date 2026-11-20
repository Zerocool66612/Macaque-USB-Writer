#!/usr/bin/python3
"""Privileged image writer; intended for execution via pkexec only."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def disk_info(device):
    raw = subprocess.check_output(['lsblk', '-J', '-b', '-o', 'PATH,TYPE,TRAN,SIZE,MOUNTPOINTS,PKNAME'], text=True)
    data = json.loads(raw)
    def walk(nodes):
        for n in nodes:
            yield n
            yield from walk(n.get('children', []))
    nodes = list(walk(data.get('blockdevices', [])))
    d = next((n for n in nodes if n.get('path') == device and n.get('type') == 'disk'), None)
    if not d or d.get('tran') != 'usb':
        raise RuntimeError('Target is not a USB disk')
    def mounted(n):
        return any(n.get('mountpoints') or []) or any(mounted(c) for c in n.get('children', []))
    if mounted(d):
        raise RuntimeError('USB disk contains mounted filesystems')
    # Refuse any disk that is an ancestor of the filesystem containing /.
    root = subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '--target', '/'], text=True).strip()
    if root.startswith('/dev/'):
        root_real = os.path.realpath(root)
        root_disk = subprocess.run(['lsblk','-n','-s','-o','PATH',root_real],capture_output=True,text=True)
        if device in root_disk.stdout.splitlines():
            raise RuntimeError('Refusing to overwrite the system disk')
    return d


def main():
    if len(sys.argv) != 3:
        raise RuntimeError('Usage: write_image.py IMAGE DEVICE')
    image, device = sys.argv[1:]
    if not os.path.isfile(image) or os.path.islink(image):
        raise RuntimeError('Image must be an existing regular file, not a symlink')
    if not device.startswith('/dev/') or os.path.islink(device):
        raise RuntimeError('Invalid device')
    import stat
    if not stat.S_ISBLK(os.stat(device).st_mode):
        raise RuntimeError('Target is not a block device')
    info = disk_info(device)
    total = os.path.getsize(image)
    if not total or total > int(info.get('size') or 0):
        raise RuntimeError('Image empty or too large for USB drive')
    digest = hashlib.sha256()
    written = 0
    with open(image, 'rb', buffering=0) as source, open(device, 'wb', buffering=0) as target:
        while chunk := source.read(4 * 1024 * 1024):
            target.write(chunk)
            digest.update(chunk)
            written += len(chunk)
            print(f'{written} bytes written', file=sys.stderr, flush=True)
        os.fsync(target.fileno())
    print('Verifying written image…', file=sys.stderr, flush=True)
    readback = hashlib.sha256()
    remaining = total
    with open(device, 'rb', buffering=0) as target:
        while remaining:
            chunk = target.read(min(4 * 1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError('Unexpected end of USB device during verification')
            readback.update(chunk)
            remaining -= len(chunk)
    if digest.digest() != readback.digest():
        raise RuntimeError('Verification failed: USB data does not match image')
    print('Verification successful', file=sys.stderr, flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
