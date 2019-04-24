import paramiko
import ptftplib.tftpserver as tftp
import scp

class DownloadFromVendor():
    def __init__(self, vendor, username, password):
        self.vendor = vendor
        self.username = username
        self.password = password

    def download_cisco(self):
        pass

    def download_juniper(self):
        pass

    def download_brocade(self):
        pass