import sys
import os
import paramiko
import shutil
import tempfile
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox
from PyQt5 import QtCore
from PyQt5.QtCore import QThread, pyqtSignal
from login import Ui_MainWindow as Log_ui
from rsync import Ui_MainWindow as Rsync_ui

def find_best_rsync():
    import subprocess
    import shutil
    import glob

    candidates = []
    seen = set()

    # 扫描 PATH 中所有 rsync
    paths = os.environ.get("PATH", "").split(":")
    for dir_path in paths:
        full_path = os.path.join(dir_path, "rsync")
        if full_path not in seen and os.path.isfile(full_path) and os.access(full_path, os.X_OK):
            seen.add(full_path)
            candidates.append(full_path)

    # 扫描额外目录和非标准命名
    extra_dirs = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/opt/local/bin",
        "/usr/bin",
        os.path.expanduser("~/bin")
    ]
    for dir_path in extra_dirs:
        if os.path.isdir(dir_path):
            for path in glob.glob(os.path.join(dir_path, "rsync*")):
                if path not in seen and os.path.isfile(path) and os.access(path, os.X_OK):
                    seen.add(path)
                    candidates.append(path)

    versioned = []
    for path in candidates:
        try:
            result = subprocess.run([path, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            first_line = result.stdout.splitlines()[0]
            if "version" in first_line:
                version_str = first_line.split()[2]
                version_tuple = tuple(map(int, version_str.split(".")))
                versioned.append((version_tuple, path))
        except Exception:
            continue

    if not versioned:
        return shutil.which("rsync") or "rsync"

    versioned.sort(reverse=True)
    return versioned[0][1]

class RsyncWorker(QThread):
    output_line = pyqtSignal(str)

    def __init__(self, cmd, file_index, total_files, file_name):
        super().__init__()
        self.cmd = cmd
        self.file_index = file_index
        self.total_files = total_files
        self.file_name = file_name

    def run(self):
        import subprocess
        import re
        # self.output_line.emit(f"🚀 开始传输文件：\n{self.file_name}（{self.file_index - 1}/{self.total_files}）")
        process = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace'
        )

        while True:
            line = process.stdout.readline()
            if not line:
                break
            self.output_line.emit(line.strip())

        process.wait()
        self.output_line.emit("")  # 输出空行隔开
        # self.output_line.emit(f"✅ 完成文件：{self.file_name}（{self.file_index}/{self.total_files}）")

class LoginWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Log_ui()
        self.ui.setupUi(self)
        self.temp_key_path = None

        self.ui.Connect_Button.clicked.connect(self.ssh_connect)
        self.ui.Clear_Button.clicked.connect(self.clear_inputs)

    def clear_inputs(self):
        self.ui.ip.clear()
        self.ui.usr.clear()
        self.ui.pwd.clear()

    def ssh_connect(self):
        ip = self.ui.ip.text()
        username = self.ui.usr.text()
        password = self.ui.pwd.text()

        if not ip or not username or not password:
            QMessageBox.warning(self, "提示", "请填写所有字段")
            return

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(ip, username=username, password=password, timeout=5)

            key = paramiko.RSAKey.generate(2048)
            private_key_str = self._get_private_key_str(key)
            public_key_str = f"{key.get_name()} {key.get_base64()}"

            ssh.exec_command("mkdir -p ~/.ssh && chmod 700 ~/.ssh")
            ssh.exec_command(f'echo "{public_key_str}" >> ~/.ssh/authorized_keys')
            ssh.exec_command("chmod 600 ~/.ssh/authorized_keys")
            ssh.close()

            temp_key_path = os.path.join(tempfile.gettempdir(), "temp_id_rsa")
            with open(temp_key_path, "w") as f:
                f.write(private_key_str)
            os.chmod(temp_key_path, 0o600)
            self.temp_key_path = temp_key_path

            QMessageBox.information(self, "成功", f"SSH 连接成功！点击跳转")
            self.main_win = RsyncWindow(ip, username, self.temp_key_path)
            self.main_win.setWindowTitle("Rsync传输")
            self.main_win.show()
            #self.close()

        except paramiko.AuthenticationException:
            QMessageBox.critical(self, "连接失败", "SSH 连接失败，请检查用户名或密码")
        except paramiko.SSHException:
            QMessageBox.critical(self, "连接失败", "SSH 异常，可能是服务未开启")
        except Exception as e:
            QMessageBox.critical(self, "连接失败", f"发生错误！\n{str(e)}")

    def _get_private_key_str(self, key):
        import io
        private_buf = io.StringIO()
        key.write_private_key(private_buf)
        return private_buf.getvalue()


class RsyncWindow(QMainWindow):
    def __init__(self, ip, username, key_path):
        super().__init__()
        self.ui = Rsync_ui()
        self.ui.setupUi(self)
        self.remote_ip = ip
        self.remote_user = username
        self.key_path = key_path
        self.workers = []

        self.ui.Addfiles_Button.clicked.connect(self.Addfiles)
        self.ui.Addfloder_Button.clicked.connect(self.Addfloder)
        self.ui.Clear_Button.clicked.connect(self.Clear)
        self.ui.Start_Button.clicked.connect(self.run_rsync)

    def Addfiles(self):
        from PyQt5.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件")
        if files:
            existing = self.ui.Paths.toPlainText().strip()
            joined = ", ".join(files)
            if existing:
                self.ui.Paths.setPlainText(existing + ", " + joined)
            else:
                self.ui.Paths.setPlainText(joined)

    def Addfloder(self):
        from PyQt5.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            existing = self.ui.Paths.toPlainText().strip()
            if existing:
                self.ui.Paths.setPlainText(existing + ", " + folder)
            else:
                self.ui.Paths.setPlainText(folder)

    def Clear(self):
        self.ui.Paths.clear()

    def run_rsync(self):
        from PyQt5.QtWidgets import QMessageBox
        import subprocess

        dest_path = self.ui.Add.text().strip()
        sources = self.ui.Paths.toPlainText().strip()

        if not dest_path or not sources:
            QMessageBox.warning(self, "提示", "请填写目标路径并添加至少一个文件或文件夹")
            return

        try:
            check_cmd = [
                "ssh", "-i", self.key_path,
                f"{self.remote_user}@{self.remote_ip}",
                f"test -d '{dest_path}'"
            ]
            result = subprocess.run(check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode != 0:
                QMessageBox.critical(self, "路径错误", f"目标路径在远程服务器上不存在! ")
                return
        except Exception as e:
            QMessageBox.critical(self, "路径检查失败", f"无法验证远程路径是否存在：\n{str(e)}")
            return

        self.ui.Status.append("🚀 开始传输文件…")

        src_list = [s.strip() for s in sources.split(",") if s.strip()]
        # 定义传输完成计数器
        self.completed_files = 0
        for idx, src in enumerate(src_list, start=1):
            file_name = os.path.basename(src.strip("/")) or src.strip("/")
            rsync_path = find_best_rsync()
            cmd = [
                rsync_path,
                "-av",
                "--info=progress2",
                "-e", f"ssh -i {self.key_path}",
                src,
                f"{self.remote_user}@{self.remote_ip}:{dest_path}"
            ]

            worker = RsyncWorker(cmd, idx, len(src_list), file_name)
            worker.output_line.connect(lambda line: self.ui.Status.append(line))

            def mark_complete():
                self.completed_files += 1
                if self.completed_files == len(src_list):
                    self.ui.Status.append("✅ 所有文件传输完成！")
            worker.finished.connect(mark_complete)

            worker.start()

            self.workers.append(worker)



if __name__ == "__main__":
    app = QApplication(sys.argv)
    main = LoginWindow()
    main.setWindowTitle("SSH连接")
    main.show()
    sys.exit(app.exec_())