"""Route local desktop entry points to the existing workbench window."""
import hashlib
import json
from pathlib import Path

from PySide6 import QtCore, QtNetwork


def server_name(preference):
    identity = str(Path(preference).resolve()).casefold().encode('utf-8')
    return 'asgsv-workbench-' + hashlib.sha256(identity).hexdigest()[:20]


def request_existing(preference, game_id, *, library=False):
    socket = QtNetwork.QLocalSocket()
    socket.connectToServer(server_name(preference))
    if not socket.waitForConnected(250):
        return False
    data = json.dumps(dict(game_id=game_id,library=bool(library))).encode('utf-8') + b'\n'
    socket.write(data)
    sent = socket.bytesToWrite()==0 or socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return sent


class WorkbenchRequests(QtCore.QObject):
    requested = QtCore.Signal(str, bool)

    def __init__(self, preference, parent=None):
        super().__init__(parent)
        self.server = QtNetwork.QLocalServer(self)
        self.server.setSocketOptions(QtNetwork.QLocalServer.UserAccessOption)
        name = server_name(preference)
        QtNetwork.QLocalServer.removeServer(name)
        self.server.listen(name)
        self.server.newConnection.connect(self.accept)

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()
            def read(socket=socket, buffer=buffer):
                buffer.extend(bytes(socket.readAll()))
                if len(buffer)>4096:
                    socket.abort()
                    return
                if b'\n' not in buffer:
                    return
                try:
                    request = json.loads(buffer.split(b'\n',1)[0])
                    if request.get('game_id') in ('nte','wuwa'):
                        self.requested.emit(request['game_id'], request.get('library') is True)
                except (ValueError,AttributeError):
                    pass
                socket.disconnectFromServer()
            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                read()

    def close(self):
        self.server.close()
