import json, os, subprocess, sys
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog, QComboBox, QMessageBox, QProgressBar, QFrame, QCheckBox)

STYLE = '''
QWidget#root { background:#10151f; color:#edf3ff; font-family:Sans; font-size:14px; }
QLabel { color:#e8eef9; background:transparent; }
QLabel#muted { color:#98a8bf; }
QLabel#heading { font-size:29px; font-weight:800; }
QLabel#section { font-size:17px; font-weight:700; }
QFrame#card { background:#1b2433; border:1px solid #2b384c; border-radius:18px; }
QPushButton { background:#29384c; border:1px solid #40536c; color:#edf3ff; padding:12px 18px; border-radius:10px; font-weight:700; }
QPushButton:hover { background:#354966; }
QPushButton#primary { background:#3d83fa; border:1px solid #5799ff; color:white; font-size:15px; }
QPushButton#primary:hover { background:#5594ff; }
QPushButton:disabled { background:#283448; color:#73849b; border-color:#344156; }
QComboBox { background:#111a28; color:white; border:1px solid #41516b; padding:12px; border-radius:10px; }
QComboBox QAbstractItemView { background:#1b2433; color:white; selection-background-color:#3d83fa; }
QProgressBar { background:#101a28; border:0; border-radius:7px; height:13px; text-align:center; color:transparent; }
QProgressBar::chunk { background:#4b92ff; border-radius:7px; }
QCheckBox { color:#b7c6da; spacing:9px; }
'''

def mounted(node):
    return bool(any(node.get('mountpoints') or [])) or any(mounted(c) for c in node.get('children', []))

def disks():
    data=json.loads(subprocess.check_output(['lsblk','-J','-b','-o','NAME,PATH,SIZE,MODEL,TRAN,TYPE,RM,MOUNTPOINTS'],text=True))
    return {d['path']:d for d in data.get('blockdevices',[]) if d.get('type')=='disk' and d.get('tran')=='usb' and d.get('path')}

class Worker(QThread):
    progress=Signal(int)
    result=Signal(bool,str)
    def __init__(self,image,device):
        super().__init__(); self.image=image; self.device=device
    def run(self):
        try:
            # Recheck immediately before invoking the privileged writer.
            current=disks().get(self.device)
            if not current or mounted(current):
                self.result.emit(False,'USB drive disappeared or has mounted partitions.'); return
            size=os.path.getsize(self.image)
            if not size or size>int(current.get('size') or 0):
                self.result.emit(False,'Invalid image size or USB drive too small.'); return
            # pkexec requires an installed graphical polkit authentication agent.
            cmd=['pkexec','/usr/bin/python3','/usr/lib/macaque-usb-writer/write_image.py',self.image,self.device]
            proc=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,bufsize=1)
            import re
            tail=[]
            for line in proc.stderr:
                tail.append(line.strip()); tail=tail[-8:]
                match=re.search(r'\b(\d+) bytes\b',line)
                if match:
                    self.progress.emit(min(99,int(int(match.group(1))*100/size)))
            code=proc.wait()
            if code:
                self.result.emit(False,'\n'.join(tail) or f'Write failed (exit {code}).'); return
            self.progress.emit(100)
            self.result.emit(True,'Image written and verified successfully. Safely eject the USB drive before unplugging it.')
        except Exception as exc:
            self.result.emit(False,str(exc))

class App(QWidget):
    def __init__(self):
        super().__init__(); self.setObjectName('root'); self.setWindowTitle('Macaque USB Writer 0.3'); self.resize(680,660)
        self.image_path=None; self.drive_map={}; self.worker=None
        self.setStyleSheet(STYLE)
        outer=QVBoxLayout(self); outer.setContentsMargins(32,26,32,25); outer.setSpacing(17)
        header=QHBoxLayout()
        logo=QLabel('●'); logo.setStyleSheet('font-size:38px;color:#64a2ff;font-weight:900;'); header.addWidget(logo)
        titles=QVBoxLayout(); titles.setSpacing(2)
        title=QLabel('Macaque USB Writer'); title.setObjectName('heading'); titles.addWidget(title)
        subtitle=QLabel('Flash your next operating system.'); subtitle.setObjectName('muted'); titles.addWidget(subtitle)
        header.addLayout(titles); header.addStretch(); outer.addLayout(header)
        outer.addWidget(self.card_image())
        outer.addWidget(self.card_drive())
        outer.addWidget(self.card_progress())
        self.write=QPushButton('Write image to USB  →'); self.write.setObjectName('primary'); self.write.setMinimumHeight(52)
        self.write.clicked.connect(self.start); outer.addWidget(self.write)
        foot=QLabel('⚠ Writing erases everything on the selected USB drive.'); foot.setObjectName('muted'); foot.setAlignment(Qt.AlignCenter); outer.addWidget(foot)
        self.refresh()
    def make_card(self):
        card=QFrame(); card.setObjectName('card'); box=QVBoxLayout(card); box.setContentsMargins(22,20,22,20); box.setSpacing(12); return card,box
    def card_image(self):
        card,box=self.make_card(); h=QLabel('01  ·  CHOOSE AN IMAGE'); h.setObjectName('section'); box.addWidget(h)
        info=QLabel('Select a bootable ISO or IMG file from your computer.'); info.setObjectName('muted'); box.addWidget(info)
        row=QHBoxLayout(); self.image_label=QLabel('No image selected'); self.image_label.setWordWrap(True); self.image_label.setObjectName('muted'); row.addWidget(self.image_label,1)
        btn=QPushButton('Browse files'); self.browse=btn; btn.clicked.connect(self.choose); row.addWidget(btn); box.addLayout(row); return card
    def card_drive(self):
        card,box=self.make_card(); h=QLabel('02  ·  SELECT USB DRIVE'); h.setObjectName('section'); box.addWidget(h)
        info=QLabel('Only USB-connected disks are listed. Check the capacity carefully.'); info.setObjectName('muted'); info.setWordWrap(True); box.addWidget(info)
        row=QHBoxLayout(); self.combo=QComboBox(); row.addWidget(self.combo,1); refresh=QPushButton('↻ Refresh'); self.refresh_button=refresh; refresh.clicked.connect(self.refresh); row.addWidget(refresh); box.addLayout(row); return card
    def card_progress(self):
        card,box=self.make_card(); h=QLabel('03  ·  WRITE IMAGE'); h.setObjectName('section'); box.addWidget(h)
        self.status=QLabel('Ready when you are'); self.status.setObjectName('muted'); box.addWidget(self.status)
        self.bar=QProgressBar(); self.bar.setValue(0); box.addWidget(self.bar); return card
    def choose(self):
        path,_=QFileDialog.getOpenFileName(self,'Select disk image',str(Path.home()),'Disk images (*.iso *.img);;All files (*)')
        if path:
            self.image_path=path; self.image_label.setText(Path(path).name + f'  ·  {Path(path).stat().st_size/1e9:.2f} GB')
    def refresh(self):
        try:
            self.drive_map=disks(); self.combo.clear()
            for path,d in self.drive_map.items():
                model=(d.get('model') or 'USB drive').strip(); self.combo.addItem(f'{model}  ·  {int(d.get("size") or 0)/1e9:.1f} GB  ·  {path}',path)
            if not self.drive_map: self.combo.addItem('No USB drives detected',None)
        except Exception as exc:
            QMessageBox.warning(self,'USB detection failed',str(exc))
    def start(self):
        path=self.combo.currentData()
        if not self.image_path or not Path(self.image_path).is_file() or not path:
            QMessageBox.warning(self,'Missing selection','Choose an image and a USB drive first.'); return
        try:
            fresh=disks().get(path)
            if not fresh or mounted(fresh):
                QMessageBox.warning(self,'Drive unavailable','The USB drive is missing or has mounted partitions. Unmount all partitions and refresh.'); return
            if os.path.getsize(self.image_path)==0 or os.path.getsize(self.image_path)>int(fresh.get('size') or 0):
                QMessageBox.warning(self,'Invalid image','Image is empty or too large for this USB drive.'); return
        except Exception as exc:
            QMessageBox.warning(self,'Check failed',str(exc)); return
        answer=QMessageBox.warning(self,'Erase USB drive?',f'ALL FILES ON {path} WILL BE DESTROYED.\n\nThis cannot be undone. Continue?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        self.write.setEnabled(False); self.combo.setEnabled(False); self.selectors_locked(True); self.status.setText('Writing and verifying — do not unplug the USB drive…'); self.bar.setValue(0)
        self.worker=Worker(self.image_path,path); self.worker.progress.connect(self.bar.setValue); self.worker.result.connect(self.done); self.worker.start()
    def selectors_locked(self,locked):
        self.browse.setEnabled(not locked)
        self.refresh_button.setEnabled(not locked)
    def done(self,success,message):
        self.write.setEnabled(True); self.combo.setEnabled(True); self.selectors_locked(False); self.status.setText('Completed' if success else 'Write failed')
        (QMessageBox.information if success else QMessageBox.critical)(self,'Complete' if success else 'Error',message)
        self.refresh()

if __name__=='__main__':
    app=QApplication(sys.argv); window=App(); window.show(); sys.exit(app.exec())
